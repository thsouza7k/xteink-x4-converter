#!/usr/bin/env python3
"""
manhwa2xtc.py - Smart Manhwa/Webtoon Slicer & XTC Generator for Xteink X4 Pro
Target Display: 480x800 e-ink display

Features:
- Single CBZ or Batch Folder processing
- Custom Output Directory support
- Speech bubble & dialogue text contour protection with top & bottom headroom buffers
- Top-margin pullback to eliminate dialogue clipping at top of pages
- Adaptive canvas background matching & standard deviation thresholding to eliminate black/white bottom bars
- Strict adherence to official 56-byte XTC binary spec
- No intermediate CBZ backup files generated
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

def build_xtc(png_images, out_path):
    """Packages PIL Images into 56-byte header XTC container file."""
    xtg_blobs = [png_to_xtg_bytes(img) for img in png_images]
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
        1,             # versionMajor
        0,             # versionMinor
        page_count,    # pageCount
        0,             # readDirection (0 = Left to Right)
        0,             # hasMetadata
        0,             # hasThumbnails
        0,             # hasChapters
        1,             # currentPage
        0,             # metadataOffset
        index_offset,  # indexOffset (uint64)
        data_offset,   # dataOffset (uint64)
        0,             # thumbOffset
        0              # chapterOffset
    )

    assert len(header) == 56

    with open(out_path, "wb") as f:
        f.write(header)
        for ie in index_entries:
            f.write(ie)
        for blob in xtg_blobs:
            f.write(blob)

    print(f"[XTC] Generated '{out_path}' with {page_count} pages.")

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
            pad_y = max(25, int(box_h * 0.35))
            y_start = max(0, y - pad_y)
            y_end = min(h, y + box_h + pad_y)
            protected_rows[y_start:y_end] = True

    return protected_rows

def smart_slice_manhwa(img, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    row_white_ratio = np.mean(gray >= 240, axis=1)
    row_black_ratio = np.mean(gray <= 20, axis=1)
    row_bg_ratio = np.maximum(row_white_ratio, row_black_ratio)

    edges = cv2.Canny(gray, 50, 150)
    row_edge_density = np.mean(edges > 0, axis=1)

    protected_rows = detect_speech_bubbles_and_text(gray)

    scale_factor = target_width / float(w)
    ideal_slice_h = int(target_height / scale_factor)
    min_slice_h = int(ideal_slice_h * 0.60)
    top_margin_buffer = int(30 / scale_factor)

    y = 0
    slices = []

    while y < h:
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
                best_cut = None
                best_score = -float('inf')

                for candidate_y in range(search_end, search_start - 1, -1):
                    score = 0.0
                    bg_ratio = row_bg_ratio[candidate_y]
                    score += bg_ratio * 2000.0

                    if protected_rows[candidate_y]:
                        score -= 1e6

                    score -= row_edge_density[candidate_y] * 3000.0
                    row_var = np.var(gray[candidate_y, :])
                    score -= row_var * 3.0
                    dist_to_ideal = abs(candidate_y - max_y)
                    score -= (dist_to_ideal / float(ideal_slice_h)) * 150.0

                    if score > best_score:
                        best_score = score
                        best_cut = candidate_y

                cut_y = best_cut if best_cut is not None else max_y

        slice_img = img[y:cut_y, :]
        sh, sw = slice_img.shape[:2]

        if sh > 20:
            slices.append(slice_img)

        y = cut_y

    return slices

def prepare_eink_page(slice_img, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    sh, sw = slice_img.shape[:2]
    scale = target_width / float(sw)
    new_h = int(sh * scale)
    resized = cv2.resize(slice_img, (target_width, new_h), interpolation=cv2.INTER_AREA)

    if new_h <= target_height:
        bottom_sample = resized[max(0, new_h - 5):new_h, :]
        sample_std = np.std(bottom_sample)

        if sample_std < 15.0:
            median_val = np.median(bottom_sample, axis=(0, 1))
            val = int(median_val) if np.isscalar(median_val) else int(median_val[0])
            fill_color = (val, val, val)
        else:
            fill_color = (255, 255, 255)

        canvas = np.full((target_height, target_width, 3), fill_color, dtype=np.uint8)
        canvas[0:new_h, 0:target_width] = resized
    else:
        canvas = cv2.resize(slice_img, (target_width, target_height), interpolation=cv2.INTER_AREA)

    pil_img = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)).convert("L")
    pil_img = ImageOps.autocontrast(pil_img, cutoff=(1, 1))
    dithered = pil_img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return dithered

def generate_manhwa_pages(cbz_path, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    """Generates and returns PIL page images in memory for a CBZ Manhwa file."""
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

        loaded_imgs = []
        for img_path in image_files:
            img = cv2.imread(img_path)
            if img is not None:
                loaded_imgs.append(img)

        max_w = max(img.shape[1] for img in loaded_imgs)
        standardized_imgs = []
        for img in loaded_imgs:
            h, w = img.shape[:2]
            if w != max_w:
                new_h = int(h * (max_w / float(w)))
                img = cv2.resize(img, (max_w, new_h), interpolation=cv2.INTER_AREA)
            standardized_imgs.append(img)

        full_strip = np.vstack(standardized_imgs)
        raw_slices = smart_slice_manhwa(full_strip, target_width=target_width, target_height=target_height)

        final_pages = []
        for slice_img in raw_slices:
            eink_page = prepare_eink_page(slice_img, target_width=target_width, target_height=target_height)
            final_pages.append(eink_page)

        return final_pages

def process_single_manhwa(cbz_path, output_xtc_path, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT):
    print(f"[Processing Manhwa] {os.path.basename(cbz_path)}")
    final_pages = generate_manhwa_pages(cbz_path, target_width=target_width, target_height=target_height)
    build_xtc(final_pages, output_xtc_path)

def process_manhwa_batch(input_path, output_dir=None, target_width=DEFAULT_WIDTH, target_height=DEFAULT_HEIGHT, progress_callback=None):
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
    print(f"[Batch Manhwa] Found {total_files} file(s) to process. Output directory: {output_dir}")

    for idx, cbz_file in enumerate(cbz_files):
        base_name = os.path.splitext(os.path.basename(cbz_file))[0]
        out_xtc = os.path.join(output_dir, f"{base_name}.xtc")

        if progress_callback:
            progress_callback(idx, total_files, base_name)

        try:
            process_single_manhwa(cbz_file, out_xtc, target_width=target_width, target_height=target_height)
        except Exception as e:
            print(f"[Error] Failed to process {base_name}: {e}")

    if progress_callback:
        progress_callback(total_files, total_files, "Done")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Manhwa CBZ files to XTC for Xteink X4 Pro")
    parser.add_argument("input", help="Path to input .cbz file or directory containing .cbz files")
    parser.add_argument("-o", "--output", help="Output directory for generated .xtc files")
    parser.add_argument("--horizontal", action="store_true", help="Use Horizontal (800x480) orientation instead of Vertical (480x800)")
    args = parser.parse_args()

    tw = 800 if args.horizontal else 480
    th = 480 if args.horizontal else 800

    process_manhwa_batch(args.input, output_dir=args.output, target_width=tw, target_height=th)
