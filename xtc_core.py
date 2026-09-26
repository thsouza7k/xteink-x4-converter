#!/usr/bin/env python3
"""
xtc_core.py - Shared building blocks for the Xteink X4 / X4 Pro converters.

- CBZ/ZIP image loading (in memory, natural page order, alpha flattened on white)
- Speech bubble / text row protection used by both slicers
- XTG page encoding and 56-byte-header XTC container writer
- Batch runner with progress reporting, cancellation and per-file error collection
"""

import os
import re
import sys
import hashlib
import struct
import zipfile
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import numpy as np
import cv2
from PIL import Image

DEFAULT_WIDTH = 480
DEFAULT_HEIGHT = 800

READ_LTR = 0  # Manhwa / Webtoon
READ_RTL = 1  # Japanese Manga

ARCHIVE_EXTS = (".cbz", ".zip")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def enable_utf8_console():
    """Windows consoles/pipes may use cp1252; never crash on ✓ / ✗ / — in log lines."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


class ConversionCancelled(Exception):
    """Raised inside a conversion when the user asks to stop."""


def resolution(horizontal: bool) -> Tuple[int, int]:
    return (DEFAULT_HEIGHT, DEFAULT_WIDTH) if horizontal else (DEFAULT_WIDTH, DEFAULT_HEIGHT)


def natural_key(text: str):
    """Sort key so that 'page2' comes before 'page10'."""
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", text)]


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def _to_bgr(img: np.ndarray) -> np.ndarray:
    """Normalizes any decoded image (gray, 16-bit, alpha) to 8-bit BGR."""
    if img.dtype != np.uint8:
        img = cv2.convertScaleAbs(img, alpha=255.0 / max(1, int(img.max())))
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        # Transparent areas would otherwise decode as black; flatten on white paper
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32)
        return (rgb * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    return img


def _is_page_entry(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    if any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return False
    return name.lower().endswith(IMAGE_EXTS)


def load_archive_images(archive_path: str) -> List[np.ndarray]:
    """Decodes every page image of a CBZ/ZIP in natural reading order."""
    images = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        names = sorted((n for n in zf.namelist() if _is_page_entry(n)), key=natural_key)
        for name in names:
            buf = np.frombuffer(zf.read(name), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
            if img is not None:
                images.append(_to_bgr(img))

    if not images:
        raise ValueError(f"No readable images found in {os.path.basename(archive_path)}")
    return images


# ---------------------------------------------------------------------------
# Shared analysis
# ---------------------------------------------------------------------------

def detect_speech_bubbles_and_text(gray_img: np.ndarray, pad_ratio: float = 0.35) -> np.ndarray:
    """Returns a per-row boolean mask of rows that must never be used as a cut line."""
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
            _, y, _, box_h = cv2.boundingRect(cnt)
            pad_y = max(25, int(box_h * pad_ratio))
            protected_rows[max(0, y - pad_y):min(h, y + box_h + pad_y)] = True

    return protected_rows


def row_background_stats(gray: np.ndarray, white: int = 240, black: int = 20):
    """Per-row ratio of flat background pixels and per-row edge density."""
    row_bg_ratio = np.maximum(np.mean(gray >= white, axis=1), np.mean(gray <= black, axis=1))
    row_edge_density = np.mean(cv2.Canny(gray, 50, 150) > 0, axis=1)
    return row_bg_ratio, row_edge_density


def resize_interpolation(src_w: int, dst_w: int) -> int:
    """Lanczos when enlarging (sharp text), area averaging when shrinking (no moire)."""
    return cv2.INTER_LANCZOS4 if dst_w > src_w else cv2.INTER_AREA


# ---------------------------------------------------------------------------
# XTG / XTC encoding
# ---------------------------------------------------------------------------

XTC_HEADER_SIZE = 56
XTC_INDEX_ENTRY_SIZE = 16


def page_to_xtg_bytes(img: Image.Image) -> bytes:
    """Encodes a page as an uncompressed 1-bit XTG blob (22-byte header + bitmap)."""
    if img.mode != "1":
        img = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    w, h = img.size
    data = img.tobytes()
    header = struct.pack(
        "<4sHHBBI8s",
        b"XTG\x00",
        w,
        h,
        0,  # colorMode = monochrome
        0,  # compression = uncompressed
        len(data),
        hashlib.md5(data).digest()[:8],
    )
    return header + data


def build_xtc(pages: List[Image.Image], out_path: str, read_direction: int = READ_LTR) -> int:
    """Packages 1-bit pages into an XTC container. Written atomically; returns page count."""
    if not pages:
        raise ValueError("No pages were generated")
    if len(pages) > 0xFFFF:
        raise ValueError(f"Too many pages for one XTC file ({len(pages)} > 65535)")

    size = pages[0].size
    blobs = []
    for img in pages:
        if img.size != size:
            img = img.resize(size, Image.LANCZOS)
        blobs.append(page_to_xtg_bytes(img))

    page_count = len(blobs)
    index_offset = XTC_HEADER_SIZE
    data_offset = index_offset + page_count * XTC_INDEX_ENTRY_SIZE

    header = struct.pack(
        "<4sBBHBBBB IQ Q Q Q Q",
        b"XTC\x00",
        1,               # versionMajor
        0,               # versionMinor
        page_count,      # pageCount
        read_direction,  # readDirection (0 = LTR, 1 = RTL)
        0,               # hasMetadata
        0,               # hasThumbnails
        0,               # hasChapters
        1,               # currentPage
        0,               # metadataOffset
        index_offset,    # indexOffset
        data_offset,     # dataOffset
        0,               # thumbOffset
        0,               # chapterOffset
    )
    assert len(header) == XTC_HEADER_SIZE

    tmp_path = out_path + ".part"
    try:
        with open(tmp_path, "wb") as f:
            f.write(header)
            offset = data_offset
            for blob in blobs:
                f.write(struct.pack("<QIHH", offset, len(blob), size[0], size[1]))
                offset += len(blob)
            for blob in blobs:
                f.write(blob)
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return page_count


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def find_archives(input_path: str) -> List[str]:
    """A single archive, or every archive under a folder (recursive, natural order)."""
    if os.path.isfile(input_path):
        return [input_path]
    if not os.path.isdir(input_path):
        raise FileNotFoundError(f"Input path not found: {input_path}")

    found = []
    for root, dirs, files in os.walk(input_path):
        dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=natural_key)
        for name in sorted(files, key=natural_key):
            if name.lower().endswith(ARCHIVE_EXTS) and not name.startswith("."):
                found.append(os.path.join(root, name))
    return found


def default_output_dir(input_path: str) -> str:
    if os.path.isfile(input_path):
        return os.path.dirname(os.path.abspath(input_path))
    return os.path.join(input_path, "output_xtc")


@dataclass
class BatchResult:
    output_dir: str
    converted: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


# progress(fraction 0..1, current file name)
ProgressFn = Callable[[float, str], None]
# generate(archive_path, on_step(done, total)) -> pages
GenerateFn = Callable[..., List[Image.Image]]


def run_batch(
    input_path: str,
    generate: GenerateFn,
    read_direction: int,
    output_dir: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
    log: Callable[[str], None] = print,
    cancel_event: Optional[threading.Event] = None,
) -> BatchResult:
    """Converts one archive or a folder tree of archives, mirroring subfolders in the output."""
    archives = find_archives(input_path)
    output_dir = output_dir or default_output_dir(input_path)
    result = BatchResult(output_dir=output_dir)
    base_dir = input_path if os.path.isdir(input_path) else (os.path.dirname(input_path) or ".")
    total = len(archives)

    log(f"Found {total} file(s). Output: {output_dir}")
    if total == 0:
        return result

    for idx, archive in enumerate(archives):
        rel_path = os.path.splitext(os.path.relpath(archive, base_dir))[0]
        out_path = os.path.join(output_dir, rel_path + ".xtc")
        target_dir = os.path.dirname(out_path)
        name = rel_path.replace(os.sep, " / ")

        def on_step(done, steps, _idx=idx, _name=name):
            if cancel_event is not None and cancel_event.is_set():
                raise ConversionCancelled()
            if progress:
                progress((_idx + done / float(max(1, steps))) / total, _name)

        try:
            on_step(0, 1)
            pages = generate(archive, on_step=on_step)
            os.makedirs(target_dir, exist_ok=True)
            count = build_xtc(pages, out_path, read_direction=read_direction)
            result.converted.append(out_path)
            log(f"✓ {name}  —  {count} pages")
        except ConversionCancelled:
            result.cancelled = True
            log("Cancelled by user.")
            break
        except Exception as e:  # keep going with the rest of the batch
            result.failed.append((name, str(e)))
            log(f"✗ {name}  —  {e}")

    if progress and not result.cancelled:
        progress(1.0, "")
    return result
