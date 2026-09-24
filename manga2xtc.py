#!/usr/bin/env python3
"""
manga2xtc.py - Smart Manga Processor & XTC Generator for Xteink X4 Pro
Target Display: 480x800 e-ink display (Vertical Portrait Mode)

Features:
- Single CBZ or Batch Folder processing
- Custom Output Directory support
- Cover & Splash Page Preservation: Full 480x800 vertical screen rendering for covers and epic tall scenes
- Single-Page Vertical Re-Stacking: Cuts 3 (or 2) panel rows, crops out blank vertical whitespace between rows, and stacks them ONE BELOW THE OTHER ON THE SAME 480x800 PAGE
- Zero-Crop Aspect Preservation: Guaranteed 0% speech bubble clipping with non-destructive aspect scaling
- Unsharp Masking & Lanczos4 Resampling for crystal-clear font & lineart rendering
- Double-Page Spread Handling (Japanese RTL order)
- Strict 56-byte XTC binary header (readDirection = 1)
"""

import os
import sys
import glob
import zipfile
import tempfile
import hashlib
import struct
import argparse
import numpy as np
import cv2
from PIL import Image, ImageOps

DEFAULT_WIDTH = 480
DEFAULT_HEIGHT = 800

def png_to_xtg_bytes(img: Image.Image, force_size=(DEFAULT_WIDTH, DEFAULT_HEIGHT)):
    if img.size != force_size:
        img = img.resize(force_size, Image.LANCZOS)
    w, h = img.size
    if img.mode != "1":
        img = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    data = img.tobytes()
    data_size = len(data)
    md5digest = hashlib.md5(data).digest()[:8]

    header = struct.pack(
        "<4sHHBBI8s",
        b"XTG\x00",
        w,
        h,
        0,  # colorMode = monochrome
        0,  # compression = uncompressed
        data_size,
        md5digest,
    )
    return header + data

def build_xtc(png_images, out_path, read_direction=1):
    xtg_blobs = [png_to_xtg_bytes(img, force_size=png_images[0].size if png_images else (DEFAULT_WIDTH, DEFAULT_HEIGHT)) for img in png_images]
    page_count = len(xtg_blobs)
    header_size = 56
    index_entry_size = 16
    index_offset = header_size
    index_size = page_count * index_entry_size
    data_offset = index_offset + index_size

    index_entries = []
    curr_offset = data_offset
    for blob in xtg_blobs:
        w, h = struct.unpack_from("<HH", blob, 4)
        blob_len = len(blob)
        index_entries.append(struct.pack("<QIHH", curr_offset, blob_len, w, h))
        curr_offset += blob_len

    header = struct.pack(
        "<4sBBHBBBB IQ Q Q Q Q",
        b"XTC\x00",
        1,               # versionMajor
        0,               # versionMinor
        page_count,      # pageCount
        read_direction,  # readDirection (1 = Right to Left for Manga)
        0,               # hasMetadata
        0,               # hasThumbnails
        0,               # hasChapters
        1,               # currentPage
        0,               # metadataOffset
        index_offset,    # indexOffset
        data_offset,     # dataOffset
        0,               # thumbOffset
        0                # chapterOffset
    )

    assert len(header) == 56

    with open(out_path, "wb") as f:
        f.write(header)
        for ie in index_entries:
            f.write(ie)
        for blob in xtg_blobs:
            f.write(blob)

    print(f"[XTC] Generated Manga '{out_path}' with {page_count} pages (RTL mode={read_direction}).")

def crop_manga_outer_margins(img):
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    mask = (gray < 242) & (gray > 12)
    coords = np.argwhere(mask)

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

def detect_speech_bubbles_and_text(gray_img):
    h, w = gray_img.shape
    blur = cv2.GaussianBlur(gray_img, (3, 3), 0)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )

    k_text = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    dilated_text = cv2.dilate(thresh, k_text, iterations=2)
    k_bubble = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    dilated_bubble = cv2.dilate(thresh, k_bubble, iterations=2)

    combined = cv2.bitwise_or(dilated_text, dilated_bubble)
    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    protected_rows = np.zeros(h, dtype=bool)
    min_area = (w * h) * 0.00005
    max_area = (w * h) * 0.45

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if min_area < area < max_area:
            x, y, box_w, box_h = cv2.boundingRect(cnt)
            pad_y = max(25, int(box_h * 0.40))
            y_start = max(0, y - pad_y)
            y_end = min(h, y + box_h + pad_y)
            protected_rows[y_start:y_end] = True

    return protected_rows

def is_cover_or_full_splash(img, page_idx):
    if page_idx == 0:
        return True

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    search_start = int(h * 0.30)
    search_end = int(h * 0.70)

    row_white_ratio = np.mean(gray >= 240, axis=1)
    row_black_ratio = np.mean(gray <= 20, axis=1)
    row_bg_ratio = np.maximum(row_white_ratio, row_black_ratio)

    max_bg_in_mid = np.max(row_bg_ratio[search_start:search_end])
    if max_bg_in_mid < 0.85:
        return True

    return False

def trim_row_content(row_img):
    """Trims unused blank top and bottom background margins from an extracted panel row."""
    h, w = row_img.shape[:2]
    gray = cv2.cvtColor(row_img, cv2.COLOR_BGR2GRAY)

    mask = (gray < 238) & (gray > 15)
    coords = np.argwhere(mask)

    if len(coords) > 0:
        ymin = coords.min(axis=0)[0]
        ymax = coords.max(axis=0)[0]

        pad = 6
        ymin = max(0, ymin - pad)
        ymax = min(h, ymax + pad)

        if (ymax - ymin) > 30:
            return row_img[ymin:ymax, :]

    return row_img

def restack_manga_page_single(page):
    """Identifies panel rows, crops out blank vertical whitespace between rows, and restacks them ONE BELOW THE OTHER ON THE SAME PAGE."""
    h, w = page.shape[:2]
    if h <= w * 1.15:
        return page

    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    protected_rows = detect_speech_bubbles_and_text(gray)

    row_white_ratio = np.mean(gray >= 238, axis=1)
    row_black_ratio = np.mean(gray <= 18, axis=1)
    row_bg_ratio = np.maximum(row_white_ratio, row_black_ratio)

    edges = cv2.Canny(gray, 50, 150)
    row_edge_density = np.mean(edges > 0, axis=1)

    # 1. Search for 2 clean horizontal cut lines (3 panel rows)
    search1_start = int(h * 0.22)
    search1_end = int(h * 0.45)
    ideal1 = int(h * 0.33)

    search2_start = int(h * 0.55)
    search2_end = int(h * 0.78)
    ideal2 = int(h * 0.66)

    best_cut1 = None
    best_score1 = -float('inf')

    for y in range(search1_start, search1_end):
        if protected_rows[y]:
            continue
        bg = row_bg_ratio[y]
        ed = row_edge_density[y]
        if bg < 0.70 or ed > 0.05:
            continue
        score = bg * 2000.0 - ed * 3000.0 - (abs(y - ideal1) / float(h)) * 200.0
        if score > best_score1:
            best_score1 = score
            best_cut1 = y

    best_cut2 = None
    best_score2 = -float('inf')

    for y in range(search2_start, search2_end):
        if protected_rows[y]:
            continue
        bg = row_bg_ratio[y]
        ed = row_edge_density[y]
        if bg < 0.70 or ed > 0.05:
            continue
        score = bg * 2000.0 - ed * 3000.0 - (abs(y - ideal2) / float(h)) * 200.0
        if score > best_score2:
            best_score2 = score
            best_cut2 = y

    if best_cut1 is not None and best_cut2 is not None and best_score1 > -5000 and best_score2 > -5000:
        row1 = page[0:best_cut1, :]
        row2 = page[best_cut1:best_cut2, :]
        row3 = page[best_cut2:h, :]

        trim1 = trim_row_content(row1)
        trim2 = trim_row_content(row2)
        trim3 = trim_row_content(row3)

        gap = 8
        sep = np.full((gap, w, 3), 255, dtype=np.uint8)
        stacked = np.vstack([trim1, sep, trim2, sep, trim3])
        return stacked

    # 2. Search for 1 clean horizontal cut line (2 panel rows)
    search_start = int(h * 0.30)
    search_end = int(h * 0.70)
    ideal_cut = h // 2

    best_cut = None
    best_score = -float('inf')

    for candidate_y in range(search_start, search_end):
        if protected_rows[candidate_y]:
            continue
        bg = row_bg_ratio[candidate_y]
        ed = row_edge_density[candidate_y]
        score = bg * 2000.0 - ed * 3000.0 - abs(candidate_y - ideal_cut) * 2.0
        if score > best_score:
            best_score = score
            best_cut = candidate_y

    if best_cut is not None and best_score > -5000:
        row1 = page[0:best_cut, :]
        row2 = page[best_cut:h, :]

        trim1 = trim_row_content(row1)
        trim2 = trim_row_content(row2)

        gap = 8
        sep = np.full((gap, w, 3), 255, dtype=np.uint8)
        stacked = np.vstack([trim1, sep, trim2])
        return stacked

    return page

def prepare_eink_page(slice_img, fill_screen=True, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    sh, sw = slice_img.shape[:2]
    scale_w = target_width / float(sw)
    scale_h = target_height / float(sh)

    # NON-DESTRUCTIVE ASPECT PRESERVATION:
    # Scale slice so height fits within target_height (<= 800px) and width fits target_width (<= 480px)
    # ZERO top/bottom cropping (crop_y = 0), guaranteeing 100% speech bubble & artwork visibility!
    scale = min(scale_w, scale_h)

    new_w = max(1, int(sw * scale))
    new_h = max(1, int(sh * scale))

    interp = cv2.INTER_LANCZOS4 if new_w > sw else cv2.INTER_AREA
    resized = cv2.resize(slice_img, (new_w, new_h), interpolation=interp)

    top_sample = resized[0:min(5, new_h), :]
    bottom_sample = resized[max(0, new_h - 5):new_h, :]
    margin_sample = np.concatenate([top_sample.ravel(), bottom_sample.ravel()])
    avg_bg_val = np.median(margin_sample)
    fill_color = (255, 255, 255) if avg_bg_val > 128 else (0, 0, 0)

    canvas = np.full((target_height, target_width, 3), fill_color, dtype=np.uint8)

    pad_x = max(0, (target_width - new_w) // 2)
    pad_y = max(0, (target_height - new_h) // 2)

    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

    # Grayscale conversion & CLAHE (Contrast Limited Adaptive Histogram Equalization) for small text & TL notes
    gray_canvas = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    clahe_gray = clahe.apply(gray_canvas)

    # Micro-font precision unsharp mask (sigma=0.8, weight=1.65) for crisp 6-8pt letter stems
    blur = cv2.GaussianBlur(clahe_gray, (0, 0), 0.8)
    sharpened = cv2.addWeighted(clahe_gray, 1.65, blur, -0.65, 0)

    pil_img = Image.fromarray(sharpened).convert("L")
    pil_img = ImageOps.autocontrast(pil_img, cutoff=(1.5, 0.5))
    dithered = pil_img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return dithered

def generate_manga_pages(cbz_path, smart_zoom=True, fill_screen=True, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    """Generates and returns PIL page images in memory for a CBZ Manga file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with zipfile.ZipFile(cbz_path, 'r') as zip_ref:
            zip_ref.extractall(tmp_dir)

        valid_exts = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
        image_files = []
        for root, _, files in os.walk(tmp_dir):
            for file in files:
                if file.lower().endswith(valid_exts):
                    image_files.append(os.path.join(root, file))

        image_files.sort()
        if not image_files:
            raise ValueError(f"No valid images found in {cbz_path}")

        page_slices = []

        for p_idx, img_path in enumerate(image_files):
            img = cv2.imread(img_path)
            if img is None:
                continue

            img = crop_manga_outer_margins(img)
            h, w = img.shape[:2]

            if w > h * 1.15:
                mid_x = w // 2
                right_half = img[:, mid_x:w]
                left_half = img[:, 0:mid_x]
                pages_to_process = [right_half, left_half]
            else:
                pages_to_process = [img]

            for page in pages_to_process:
                ph, pw = page.shape[:2]

                if is_cover_or_full_splash(page, p_idx):
                    page_slices.append(page)
                elif smart_zoom and ph > pw * 1.15:
                    restacked = restack_manga_page_single(page)
                    page_slices.append(restacked)
                else:
                    page_slices.append(page)

        final_pages = []
        for slice_img in page_slices:
            eink_page = prepare_eink_page(
                slice_img, fill_screen=fill_screen,
                target_width=target_width, target_height=target_height
            )
            final_pages.append(eink_page)

        return final_pages

def process_single_manga(cbz_path, output_xtc_path, smart_zoom=True, fill_screen=True, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    print(f"[Processing Manga] {os.path.basename(cbz_path)}")
    final_pages = generate_manga_pages(cbz_path, smart_zoom=smart_zoom, fill_screen=fill_screen, target_width=target_width, target_height=target_height)
    build_xtc(final_pages, output_xtc_path, read_direction=1)

def process_manga_batch(input_path, output_dir=None, smart_zoom=True, fill_screen=True, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT, progress_callback=None):
    if os.path.isfile(input_path):
        cbz_files = [input_path]
        if output_dir is None:
            output_dir = os.path.dirname(input_path) or "."
    elif os.path.isdir(input_path):
        cbz_files = sorted(glob.glob(os.path.join(input_path, "*.cbz")))
        if not cbz_files:
            cbz_files = sorted(glob.glob(os.path.join(input_path, "**", "*.cbz"), recursive=True))
        if output_dir is None:
            output_dir = os.path.join(input_path, "output_xtc")
    else:
        raise FileNotFoundError(f"Input path not found: {input_path}")

    os.makedirs(output_dir, exist_ok=True)
    total_files = len(cbz_files)
    print(f"[Batch Manga] Found {total_files} file(s) to process. Output directory: {output_dir}")

    for idx, cbz_file in enumerate(cbz_files):
        base_name = os.path.splitext(os.path.basename(cbz_file))[0]
        out_xtc = os.path.join(output_dir, f"{base_name}.xtc")

        if progress_callback:
            progress_callback(idx, total_files, base_name)

        try:
            process_single_manga(
                cbz_file, out_xtc, smart_zoom=smart_zoom, fill_screen=fill_screen,
                target_width=target_width, target_height=target_height
            )
        except Exception as e:
            print(f"[Error] Failed to process {base_name}: {e}")

    if progress_callback:
        progress_callback(total_files, total_files, "Done")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Manga CBZ files to XTC for Xteink X4 Pro")
    parser.add_argument("input", help="Path to input .cbz file or directory containing .cbz files")
    parser.add_argument("-o", "--output", help="Output directory for generated .xtc files")
    parser.add_argument("--horizontal", action="store_true", help="Use Horizontal (800x480) orientation instead of Vertical (480x800)")
    parser.add_argument("--no-smart-zoom", action="store_true", help="Disable 2x Top/Bottom panel zoom")
    parser.add_argument("--no-fill-screen", action="store_true", help="Disable 100%% full screen fill")
    args = parser.parse_args()

    tw = 800 if args.horizontal else 480
    th = 480 if args.horizontal else 800

    process_manga_batch(
        args.input,
        output_dir=args.output,
        smart_zoom=not args.no_smart_zoom,
        fill_screen=not args.no_fill_screen,
        target_width=tw,
        target_height=th
    )
