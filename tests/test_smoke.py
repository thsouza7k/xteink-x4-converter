"""
Cross-platform smoke tests: build synthetic CBZ files (unicode paths, nested folders,
transparent PNGs), convert them with both converters in both orientations and validate
every XTC byte-for-byte. Run with:  python -m unittest discover -s tests -v
"""

import hashlib
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import manga2xtc  # noqa: E402
import manhwa2xtc  # noqa: E402
import xtc_core  # noqa: E402


def _png(arr, mode):
    buf = io.BytesIO()
    Image.fromarray(arr, mode).save(buf, "PNG")
    return buf.getvalue()


def _jpg(arr):
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def make_manga_cbz(path, pages=4):
    """Pages with three panel rows separated by white gutters and some 'text'."""
    rng = np.random.default_rng(0)
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(1, pages + 1):
            page = np.full((1600, 1000, 3), 255, np.uint8)
            for top in (60, 580, 1100):
                page[top:top + 440, 60:940] = rng.integers(0, 255, (440, 880, 3), dtype=np.uint8)
            zf.writestr(f"page{i}.jpg", _jpg(page))
        spread = np.full((1000, 1600, 3), 128, np.uint8)
        zf.writestr(f"page{pages + 1}.jpg", _jpg(spread))  # double-page spread
        zf.writestr("__MACOSX/._page1.jpg", b"junk")
        zf.writestr("ComicInfo.xml", b"<ComicInfo/>")


def make_manhwa_cbz(path, strips=3):
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(1, strips + 1):
            strip = np.full((3000, 800, 4), 0, np.uint8)  # transparent background
            for y in range(200, 2800, 700):
                strip[y:y + 400, 50:750] = (40, 40, 40, 255)
            zf.writestr(f"{i}.png", _png(strip, "RGBA"))


def validate_xtc(test, path, size, read_direction):
    with open(path, "rb") as f:
        data = f.read()
    magic, _, _, count, rtl, _, _, _, _, _, index_off, data_off, _, _ = struct.unpack_from(
        "<4sBBHBBBBIQQQQQ", data, 0)
    test.assertEqual(magic, b"XTC\x00")
    test.assertEqual(rtl, read_direction)
    test.assertEqual(index_off, 56)
    test.assertEqual(data_off, 56 + 16 * count)
    test.assertGreater(count, 0)
    end = data_off
    for i in range(count):
        off, length, w, h = struct.unpack_from("<QIHH", data, index_off + 16 * i)
        test.assertEqual((w, h), size)
        magic, pw, ph, _, _, dsize, md5 = struct.unpack_from("<4sHHBBI8s", data, off)
        test.assertEqual(magic, b"XTG\x00")
        test.assertEqual((pw, ph), size)
        test.assertEqual(dsize, w * h // 8)
        test.assertEqual(length, 22 + dsize)
        test.assertEqual(hashlib.md5(data[off + 22:off + 22 + dsize]).digest()[:8], md5)
        end = off + length
    test.assertEqual(end, len(data))
    return count


class ConverterSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Biblioteca mangá")  # non-ASCII path
        os.makedirs(os.path.join(self.root, "Vol 1"))
        os.makedirs(os.path.join(self.root, "Vol 2"))
        make_manga_cbz(os.path.join(self.root, "Vol 1", "Capítulo 1.cbz"))
        make_manga_cbz(os.path.join(self.root, "Vol 2", "Capítulo 1.cbz"))
        with open(os.path.join(self.root, "quebrado.cbz"), "wb") as f:
            f.write(b"not a zip")
        self.manhwa = os.path.join(self.tmp.name, "Webtoon ç", "ep 1.cbz")
        os.makedirs(os.path.dirname(self.manhwa))
        make_manhwa_cbz(self.manhwa)

    def tearDown(self):
        self.tmp.cleanup()

    def test_natural_order_and_filtering(self):
        names = ["p10.png", "p2.png", "__MACOSX/._p1.png", ".hidden.png", "p1.png", "info.xml"]
        pages = sorted((n for n in names if xtc_core._is_page_entry(n)), key=xtc_core.natural_key)
        self.assertEqual(pages, ["p1.png", "p2.png", "p10.png"])

    def test_transparent_png_becomes_white(self):
        img = xtc_core.load_archive_images(self.manhwa)[0]
        self.assertEqual(tuple(img[0, 0]), (255, 255, 255))
        self.assertEqual(tuple(img[300, 400]), (40, 40, 40))

    def test_manga_batch_both_orientations(self):
        for horizontal in (False, True):
            size = xtc_core.resolution(horizontal)
            out = os.path.join(self.tmp.name, f"out_manga_{size[0]}")
            result = manga2xtc.process_manga_batch(self.root, output_dir=out, target_width=size[0],
                                                   target_height=size[1], log=lambda m: None)
            self.assertEqual(len(result.converted), 2)
            self.assertEqual([name for name, _ in result.failed], ["quebrado"])
            for sub in ("Vol 1", "Vol 2"):  # same file name in two folders: both kept
                path = os.path.join(out, sub, "Capítulo 1.xtc")
                validate_xtc(self, path, size, xtc_core.READ_RTL)

    def test_manhwa_single_file_both_orientations(self):
        for horizontal in (False, True):
            size = xtc_core.resolution(horizontal)
            out = os.path.join(self.tmp.name, f"out_manhwa_{size[0]}")
            result = manhwa2xtc.process_manhwa_batch(self.manhwa, output_dir=out, target_width=size[0],
                                                     target_height=size[1], log=lambda m: None)
            self.assertEqual(result.failed, [])
            validate_xtc(self, os.path.join(out, "ep 1.xtc"), size, xtc_core.READ_LTR)

    def test_default_output_dir_next_to_file(self):
        result = manhwa2xtc.process_manhwa_batch(self.manhwa, log=lambda m: None)
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(self.manhwa), "ep 1.xtc")))
        self.assertEqual(result.failed, [])

    def test_cancel_stops_batch(self):
        import threading
        event = threading.Event()
        event.set()
        result = manga2xtc.process_manga_batch(self.root, output_dir=os.path.join(self.tmp.name, "c"),
                                               log=lambda m: None, cancel_event=event)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.converted, [])


class GuiSmokeTest(unittest.TestCase):
    """Builds the real window on the platform's Tk (skipped when there is no display)."""

    def test_window_builds_and_switches_modes(self):
        import tkinter
        try:
            import gui_converter
            gui_converter.SETTINGS_PATH = os.path.join(tempfile.mkdtemp(), "settings.json")
            app = gui_converter.XteinkConverterApp()
        except tkinter.TclError as e:
            self.skipTest(f"No display available: {e}")
        try:
            for mode in gui_converter.CONTENT_TYPES:
                app.mode_seg.set(mode)
                app.on_mode_change(mode)
                app.update()
            app.show_view("Activity")
            app.show_view("Preview")
            app.input_var.set(os.path.abspath(__file__))
            app.render_preview()
            app.update()
            app.save_settings()
            self.assertTrue(os.path.isfile(gui_converter.SETTINGS_PATH))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
