#!/usr/bin/env python3
"""
manhwa2xtc.py - Smart Manhwa/Webtoon Slicer & XTC Generator for Xteink X4 Pro
Target Display: 480x800 e-ink display (portrait) or 800x480 (landscape)

Features:
- Single CBZ or batch folder processing (recursive, subfolders mirrored in the output)
- Continuous strip assembly, then slicing into screen-sized pages
- Speech bubble & dialogue text contour protection with top & bottom headroom buffers
- Top-margin pullback to eliminate dialogue clipping at top of pages
- Adaptive canvas background matching to avoid harsh black/white bottom bars
- 56-byte XTC header with readDirection = 0 (left to right)
"""

import argparse

import numpy as np
import cv2
from PIL import Image, ImageOps

import xtc_core
from xtc_core import DEFAULT_WIDTH, DEFAULT_HEIGHT


def smart_slice_manhwa(img, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    row_bg_ratio, row_edge_density = xtc_core.row_background_stats(gray)
    row_variance = np.var(gray, axis=1)
    protected_rows = xtc_core.detect_speech_bubbles_and_text(gray, pad_ratio=0.35)

    scale_factor = target_width / float(w)
    ideal_slice_h = int(target_height / scale_factor)
    min_slice_h = int(ideal_slice_h * 0.60)
    top_margin_buffer = int(30 / scale_factor)

    y = 0
    slices = []

    while y < h:
        # Skip blank gutter between scenes
        while y < h and row_bg_ratio[y] > 0.98:
            y += 1
        if y >= h:
            break

        y = max(0, y - top_margin_buffer)
        max_y = min(y + ideal_slice_h, h)

        if max_y == h:
            cut_y = h
        else:
            search_start = y + min_slice_h
            search_end = max_y

            if search_start >= search_end:
                cut_y = max_y
            else:
                candidates = np.arange(search_end, search_start - 1, -1)
                scores = (
                    row_bg_ratio[candidates] * 2000.0
                    - protected_rows[candidates] * 1e6
                    - row_edge_density[candidates] * 3000.0
                    - row_variance[candidates] * 3.0
                    - (np.abs(candidates - max_y) / float(ideal_slice_h)) * 150.0
                )
                cut_y = int(candidates[int(np.argmax(scores))])

        if cut_y - y > 20:
            slices.append(img[y:cut_y, :])

        y = cut_y

    return slices


def prepare_eink_page(slice_img, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    sh, sw = slice_img.shape[:2]
    new_h = max(1, int(sh * target_width / float(sw)))
    resized = cv2.resize(slice_img, (target_width, new_h),
                         interpolation=xtc_core.resize_interpolation(sw, target_width))

    if new_h >= target_height:
        # Only happens through rounding (slices are sized for the screen); trim, never squash
        canvas = resized[:target_height]
    else:
        bottom_sample = resized[-5:]
        if np.std(bottom_sample) < 15.0:
            val = int(np.median(bottom_sample))
            fill_color = (val, val, val)
        else:
            fill_color = (255, 255, 255)

        canvas = np.full((target_height, target_width, 3), fill_color, dtype=np.uint8)
        canvas[:new_h] = resized

    pil_img = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY))
    pil_img = ImageOps.autocontrast(pil_img, cutoff=(1, 1))
    return pil_img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def generate_manhwa_pages(cbz_path, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT, on_step=None):
    """Generates 1-bit PIL pages in memory for a manhwa/webtoon CBZ file."""
    images = xtc_core.load_archive_images(cbz_path)

    # Every strip image is brought to the same width before stacking
    max_w = max(img.shape[1] for img in images)
    standardized = []
    for img in images:
        h, w = img.shape[:2]
        if w != max_w:
            img = cv2.resize(img, (max_w, int(h * max_w / float(w))),
                             interpolation=xtc_core.resize_interpolation(w, max_w))
        standardized.append(img)

    full_strip = np.vstack(standardized)
    del standardized, images
    raw_slices = smart_slice_manhwa(full_strip, target_width=target_width, target_height=target_height)

    total = len(raw_slices)
    final_pages = []
    for idx, slice_img in enumerate(raw_slices):
        if on_step:
            on_step(idx, total)
        final_pages.append(prepare_eink_page(slice_img, target_width=target_width, target_height=target_height))

    if on_step:
        on_step(total, total)
    return final_pages


def process_single_manhwa(cbz_path, output_xtc_path, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    pages = generate_manhwa_pages(cbz_path, target_width=target_width, target_height=target_height)
    return xtc_core.build_xtc(pages, output_xtc_path, read_direction=xtc_core.READ_LTR)


def process_manhwa_batch(input_path, output_dir=None, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT,
                         progress=None, log=print, cancel_event=None):
    def generate(path, on_step=None):
        return generate_manhwa_pages(path, target_width=target_width, target_height=target_height, on_step=on_step)

    return xtc_core.run_batch(input_path, generate, xtc_core.READ_LTR, output_dir=output_dir,
                              progress=progress, log=log, cancel_event=cancel_event)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Manhwa/Webtoon CBZ files to XTC for Xteink X4 / X4 Pro")
    parser.add_argument("input", help="Path to a .cbz file or a folder containing .cbz files (searched recursively)")
    parser.add_argument("-o", "--output", help="Output directory for generated .xtc files")
    parser.add_argument("--horizontal", action="store_true", help="Landscape 800x480 instead of portrait 480x800")
    args = parser.parse_args()
    xtc_core.enable_utf8_console()

    tw, th = xtc_core.resolution(args.horizontal)
    result = process_manhwa_batch(args.input, output_dir=args.output, target_width=tw, target_height=th)
    raise SystemExit(1 if result.failed else 0)
