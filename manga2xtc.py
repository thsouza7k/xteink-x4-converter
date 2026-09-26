#!/usr/bin/env python3
"""
manga2xtc.py - Smart Manga Processor & XTC Generator for Xteink X4 Pro
Target Display: 480x800 e-ink display (portrait) or 800x480 (landscape)

Features:
- Single CBZ or batch folder processing (recursive, subfolders mirrored in the output)
- Cover & splash page preservation: rendered whole, never split
- Panel-row re-stacking: finds 2 or 3 panel rows, removes the blank gutters between
  them and stacks them on the same page so the artwork is rendered larger
- Zero-crop aspect preservation: speech bubbles are never clipped
- CLAHE + unsharp masking and Lanczos resampling for crisp lettering
- Double-page spread handling (Japanese RTL order)
- 56-byte XTC header with readDirection = 1 (right to left)
"""

import argparse

import numpy as np
import cv2
from PIL import Image, ImageOps

import xtc_core
from xtc_core import DEFAULT_WIDTH, DEFAULT_HEIGHT

# Pages taller than this ratio (h / w) are candidates for panel-row re-stacking
TALL_PAGE_RATIO = 1.15
# Pages wider than this ratio (w / h) are treated as double-page spreads
SPREAD_RATIO = 1.15
# "Fill screen" may stretch a page by at most this much to remove thin letterbox bars
FILL_MAX_STRETCH = 0.05
# Row must be at least this flat and at most this busy to be used as a gutter cut
CUT_MIN_BG = 0.70
CUT_MAX_EDGES = 0.05
ROW_GAP_PX = 8


def crop_manga_outer_margins(img):
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    coords = np.argwhere((gray < 242) & (gray > 12))
    if len(coords) > 0:
        ymin, xmin = coords.min(axis=0)
        ymax, xmax = coords.max(axis=0)

        ymin = max(0, ymin - 5)
        xmin = max(0, xmin - 5)
        ymax = min(h, ymax + 5)
        xmax = min(w, xmax + 5)

        if (ymax - ymin) > h * 0.5 and (xmax - xmin) > w * 0.5:
            return img[ymin:ymax, xmin:xmax]

    return img


def is_cover_or_full_splash(img, page_idx):
    """First page, or a page with no horizontal gutter in its middle band."""
    if page_idx == 0:
        return True

    h = img.shape[0]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    row_bg_ratio = np.maximum(np.mean(gray >= 240, axis=1), np.mean(gray <= 20, axis=1))
    return np.max(row_bg_ratio[int(h * 0.30):int(h * 0.70)]) < 0.85


def trim_row_content(row_img):
    """Trims unused blank top and bottom background margins from an extracted panel row."""
    h = row_img.shape[0]
    gray = cv2.cvtColor(row_img, cv2.COLOR_BGR2GRAY)

    coords = np.argwhere((gray < 238) & (gray > 15))
    if len(coords) > 0:
        pad = 6
        ymin = max(0, coords[:, 0].min() - pad)
        ymax = min(h, coords[:, 0].max() + pad)
        if (ymax - ymin) > 30:
            return row_img[ymin:ymax, :]

    return row_img


def _best_gutter(row_bg_ratio, row_edge_density, protected_rows, start, end, ideal, dist_weight):
    """Best clean gutter row in [start, end), or None when there is no safe place to cut."""
    best_cut, best_score = None, -float("inf")
    for y in range(start, end):
        if protected_rows[y]:
            continue
        bg, ed = row_bg_ratio[y], row_edge_density[y]
        if bg < CUT_MIN_BG or ed > CUT_MAX_EDGES:
            continue
        score = bg * 2000.0 - ed * 3000.0 - abs(y - ideal) * dist_weight
        if score > best_score:
            best_score, best_cut = score, y
    return best_cut


def _stack_rows(page, cuts):
    h, w = page.shape[:2]
    bounds = [0] + cuts + [h]
    rows = [trim_row_content(page[a:b, :]) for a, b in zip(bounds, bounds[1:])]
    sep = np.full((ROW_GAP_PX, w, 3), 255, dtype=np.uint8)
    parts = []
    for i, row in enumerate(rows):
        if i:
            parts.append(sep)
        parts.append(row)
    return np.vstack(parts)


def restack_manga_page_single(page):
    """Splits a tall page at its panel gutters, drops the blank space and restacks the rows."""
    h, w = page.shape[:2]
    if h <= w * TALL_PAGE_RATIO:
        return page

    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    protected_rows = xtc_core.detect_speech_bubbles_and_text(gray, pad_ratio=0.40)
    row_bg_ratio, row_edge_density = xtc_core.row_background_stats(gray, white=238, black=18)
    stats = (row_bg_ratio, row_edge_density, protected_rows)

    # 1. Two gutters -> three panel rows
    per_px = 200.0 / h
    cut1 = _best_gutter(*stats, int(h * 0.22), int(h * 0.45), int(h * 0.33), per_px)
    cut2 = _best_gutter(*stats, int(h * 0.55), int(h * 0.78), int(h * 0.66), per_px)
    if cut1 is not None and cut2 is not None:
        return _stack_rows(page, [cut1, cut2])

    # 2. One gutter -> two panel rows
    cut = _best_gutter(*stats, int(h * 0.30), int(h * 0.70), h // 2, 2.0)
    if cut is not None:
        return _stack_rows(page, [cut])

    return page


def prepare_eink_page(slice_img, fill_screen=True, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    sh, sw = slice_img.shape[:2]
    scale_w = target_width / float(sw)
    scale_h = target_height / float(sh)

    # Non-destructive fit: the whole slice is always visible (no cropping)
    scale = min(scale_w, scale_h)
    new_w = max(1, int(sw * scale))
    new_h = max(1, int(sh * scale))

    # Fill screen: when the page is only slightly off the screen aspect, stretch it
    # imperceptibly instead of leaving thin white/black bars
    if fill_screen and max(scale_w, scale_h) / scale <= 1.0 + FILL_MAX_STRETCH:
        new_w, new_h = target_width, target_height

    resized = cv2.resize(slice_img, (new_w, new_h), interpolation=xtc_core.resize_interpolation(sw, new_w))

    margin_sample = np.concatenate([resized[:5].ravel(), resized[-5:].ravel()])
    fill_color = (255, 255, 255) if np.median(margin_sample) > 128 else (0, 0, 0)

    canvas = np.full((target_height, target_width, 3), fill_color, dtype=np.uint8)
    pad_x = (target_width - new_w) // 2
    pad_y = (target_height - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

    # CLAHE lifts small text & TL notes out of screentone
    gray_canvas = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    clahe_gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray_canvas)

    # Micro-font unsharp mask (sigma=0.8, weight=1.65) for crisp 6-8pt letter stems
    blur = cv2.GaussianBlur(clahe_gray, (0, 0), 0.8)
    sharpened = cv2.addWeighted(clahe_gray, 1.65, blur, -0.65, 0)

    pil_img = ImageOps.autocontrast(Image.fromarray(sharpened), cutoff=(1.5, 0.5))
    return pil_img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def generate_manga_pages(cbz_path, smart_zoom=True, fill_screen=True,
                         target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT, on_step=None):
    """Generates 1-bit PIL pages in memory for a manga CBZ file."""
    images = xtc_core.load_archive_images(cbz_path)
    total = len(images)
    final_pages = []

    for p_idx, img in enumerate(images):
        if on_step:
            on_step(p_idx, total)

        img = crop_manga_outer_margins(img)
        h, w = img.shape[:2]

        if w > h * SPREAD_RATIO:
            mid_x = w // 2
            halves = [img[:, mid_x:], img[:, :mid_x]]  # RTL: right half is read first
        else:
            halves = [img]

        for page in halves:
            if smart_zoom and not is_cover_or_full_splash(page, p_idx):
                page = restack_manga_page_single(page)
            final_pages.append(prepare_eink_page(
                page, fill_screen=fill_screen, target_width=target_width, target_height=target_height
            ))

    if on_step:
        on_step(total, total)
    return final_pages


def process_single_manga(cbz_path, output_xtc_path, smart_zoom=True, fill_screen=True,
                         target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    pages = generate_manga_pages(cbz_path, smart_zoom=smart_zoom, fill_screen=fill_screen,
                                 target_width=target_width, target_height=target_height)
    return xtc_core.build_xtc(pages, output_xtc_path, read_direction=xtc_core.READ_RTL)


def process_manga_batch(input_path, output_dir=None, smart_zoom=True, fill_screen=True,
                        target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT,
                        progress=None, log=print, cancel_event=None):
    def generate(path, on_step=None):
        return generate_manga_pages(path, smart_zoom=smart_zoom, fill_screen=fill_screen,
                                    target_width=target_width, target_height=target_height,
                                    on_step=on_step)

    return xtc_core.run_batch(input_path, generate, xtc_core.READ_RTL, output_dir=output_dir,
                              progress=progress, log=log, cancel_event=cancel_event)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Manga CBZ files to XTC for Xteink X4 / X4 Pro")
    parser.add_argument("input", help="Path to a .cbz file or a folder containing .cbz files (searched recursively)")
    parser.add_argument("-o", "--output", help="Output directory for generated .xtc files")
    parser.add_argument("--horizontal", action="store_true", help="Landscape 800x480 instead of portrait 480x800")
    parser.add_argument("--no-smart-zoom", action="store_true", help="Disable panel-row re-stacking (render whole pages)")
    parser.add_argument("--no-fill-screen", action="store_true", help="Never stretch pages to remove thin letterbox bars")
    args = parser.parse_args()
    xtc_core.enable_utf8_console()

    tw, th = xtc_core.resolution(args.horizontal)
    result = process_manga_batch(
        args.input,
        output_dir=args.output,
        smart_zoom=not args.no_smart_zoom,
        fill_screen=not args.no_fill_screen,
        target_width=tw,
        target_height=th,
    )
    raise SystemExit(1 if result.failed else 0)
