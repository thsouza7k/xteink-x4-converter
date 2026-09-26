#!/usr/bin/env python3
"""
gui_converter.py - Desktop converter & live e-ink preview for Xteink X4 / X4 Pro
Built with CustomTkinter. Converts Manhwa & Manga CBZ files to the XTC e-ink format.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

import customtkinter as ctk

import manga2xtc
import manhwa2xtc
import xtc_core

APP_NAME = "Xteink Converter"


def settings_path():
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "xteink-converter", "settings.json")


SETTINGS_PATH = settings_path()

# (light, dark) color tokens
BG = ("#F4F4F5", "#0E0E10")
SURFACE = ("#FFFFFF", "#17171A")
FIELD = ("#F4F4F5", "#202024")
FIELD_HOVER = ("#E4E4E7", "#2A2A2F")
BORDER = ("#E4E4E7", "#27272B")
TEXT = ("#18181B", "#F4F4F5")
MUTED = ("#71717A", "#8E8E96")
ACCENT = ("#4F46E5", "#6366F1")
ACCENT_HOVER = ("#4338CA", "#565AE0")
PILL = ("#FFFFFF", "#34343B")
PILL_HOVER = ("#FAFAFA", "#3C3C44")
SUCCESS = ("#15803D", "#4ADE80")
DANGER = ("#DC2626", "#F87171")
DANGER_FILL = ("#DC2626", "#B91C1C")
DANGER_HOVER = ("#B91C1C", "#991B1B")
BEZEL = ("#27272A", "#050506")
PAPER = ("#E9E7E1", "#D9D7D0")

CONTENT_TYPES = ["Manhwa", "Manga"]
CONTENT_HINTS = {
    "Manhwa": "Webtoon strip sliced into pages without cutting speech bubbles.",
    "Manga": "Right-to-left pages. Double spreads are split in reading order.",
}
ORIENTATIONS = ["Portrait", "Landscape"]


def pick_font(candidates, fallback):
    available = set(tkfont.families())
    return next((f for f in candidates if f in available), fallback)


def open_in_file_manager(path):
    if sys.platform.startswith("win"):
        os.startfile(path)  # Windows only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class EntryField:
    """get/set wrapper for a CTkEntry; textvariable would disable its placeholder."""

    def __init__(self, entry, on_change):
        self.entry, self.on_change = entry, on_change
        entry.bind("<KeyRelease>", lambda e: on_change(), add="+")

    def get(self):
        return self.entry.get()

    def set(self, value):
        self.entry.delete(0, "end")
        if value:
            self.entry.insert(0, value)
        self.on_change()


class Card(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=BORDER, **kw)


class XteinkConverterApp(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.title(f"{APP_NAME} — Xteink X4 / X4 Pro")
        self.geometry("1100x700")
        self.minsize(640, 560)

        self.sans = pick_font(["Inter", "Segoe UI", "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue",
                               "Cantarell", "Noto Sans", "Ubuntu"], "TkDefaultFont")
        self.mono = pick_font(["JetBrains Mono", "JetBrainsMono Nerd Font", "Cascadia Mono", "Consolas",
                               "Menlo", "DejaVu Sans Mono"], "TkFixedFont")

        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = None  # None | "preview" | "convert"
        self.preview_pages = []
        self.preview_index = 0
        self.preview_photo = None
        self.preview_meta = ""
        self.wrap_labels = []  # labels whose wraplength follows the window width
        self.last_output_dir = None
        self.settings = self.load_settings()

        self.build_ui()
        self.apply_settings()
        self.bind("<Left>", lambda e: self.step_preview(-1))
        self.bind("<Right>", lambda e: self.step_preview(1))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(60, self.drain_events)

    # ------------------------------------------------------------------ fonts
    def font(self, size=13, weight="normal", mono=False):
        return ctk.CTkFont(family=self.mono if mono else self.sans, size=size, weight=weight)

    # --------------------------------------------------------------- layout
    def build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self.build_header()

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(20, 8), pady=(0, 12))
        left.grid_columnconfigure(0, weight=1)

        self.build_source_card(left).grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.build_format_card(left).grid(row=1, column=0, sticky="ew", pady=(0, 12))

        self.preview_card = self.build_preview_card(self)
        self.preview_card.grid(row=1, column=1, sticky="nsew", padx=(8, 20), pady=(0, 12))

        self.build_footer(self).grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 20))
        self.preview_card.grid_propagate(False)
        self.bind("<Configure>", self.on_window_resize, add="+")

    def on_window_resize(self, event):
        if event.widget is not self:
            return
        # The preview column takes ~36% of the window, within sensible bounds
        width = max(230, min(440, int(event.width * 0.36)))
        if self.preview_card.cget("width") != width:
            self.preview_card.configure(width=width)
        wrap = max(200, event.width - width - 150)
        for label in self.wrap_labels:
            label.configure(wraplength=wrap)

    def build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(14, 12))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text=APP_NAME, font=self.font(22, "bold"), text_color=TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="Manga & Manhwa  →  XTC  ·  Xteink X4 / X4 Pro",
                     font=self.font(12), text_color=MUTED).grid(row=1, column=0, sticky="w")

        self.theme_menu = ctk.CTkSegmentedButton(
            header, values=["System", "Light", "Dark"], command=self.on_theme_change,
            font=self.font(12), height=28, fg_color=FIELD, unselected_color=FIELD,
            unselected_hover_color=FIELD_HOVER, selected_color=PILL, selected_hover_color=PILL_HOVER,
            text_color=TEXT,
        )
        self.theme_menu.grid(row=0, column=1, rowspan=2, sticky="e")

    def section_title(self, parent, text):
        return ctk.CTkLabel(parent, text=text.upper(), font=self.font(11, "bold"), text_color=MUTED)

    def field_entry(self, parent, placeholder):
        return ctk.CTkEntry(parent, placeholder_text=placeholder, height=34, corner_radius=8, border_width=1,
                            fg_color=FIELD, border_color=BORDER, text_color=TEXT, font=self.font(12))

    def ghost_button(self, parent, text, command, width=84):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=36, corner_radius=8,
                             fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT, border_width=1,
                             border_color=BORDER, font=self.font(12))

    def link_button(self, parent, text, command):
        return ctk.CTkButton(parent, text=text, command=command, width=10, height=26, corner_radius=6,
                             fg_color="transparent", hover_color=FIELD, text_color=ACCENT,
                             font=self.font(12, "bold"))

    def segmented(self, parent, values, command=None):
        return ctk.CTkSegmentedButton(
            parent, values=values, command=command, height=34, font=self.font(12),
            fg_color=FIELD, unselected_color=FIELD, unselected_hover_color=FIELD_HOVER,
            selected_color=PILL, selected_hover_color=PILL_HOVER, text_color=TEXT, border_width=3,
        )

    def build_source_card(self, parent):
        card = Card(parent)
        card.grid_columnconfigure(0, weight=1)

        self.section_title(card, "Source").grid(row=0, column=0, sticky="w", padx=18, pady=(12, 4))
        self.link_button(card, "File…", self.browse_file).grid(row=0, column=1, pady=(12, 4))
        self.link_button(card, "Folder…", self.browse_folder).grid(row=0, column=2, padx=(0, 12), pady=(12, 4))

        self.input_entry = self.field_entry(card, "Choose a .cbz file or a folder of chapters")
        self.input_var = EntryField(self.input_entry, self.refresh_source_info)
        self.input_entry.grid(row=1, column=0, columnspan=3, sticky="ew", padx=18)

        self.source_info = ctk.CTkLabel(card, text="", font=self.font(11), text_color=MUTED, anchor="w",
                                        justify="left")
        self.wrap_labels.append(self.source_info)
        self.source_info.grid(row=2, column=0, columnspan=3, sticky="w", padx=20, pady=(2, 2))

        self.section_title(card, "Output folder").grid(row=3, column=0, sticky="w", padx=18, pady=(2, 6))
        self.link_button(card, "Change…", self.browse_output).grid(row=3, column=2, padx=(0, 12), pady=(2, 6))
        self.output_entry = self.field_entry(card, "Automatic (next to the source)")
        self.output_var = EntryField(self.output_entry, self.refresh_source_info)
        self.output_entry.grid(row=4, column=0, columnspan=3, sticky="ew", padx=18, pady=(0, 14))
        return card

    def build_format_card(self, parent):
        card = Card(parent)
        card.grid_columnconfigure(1, weight=1)

        self.section_title(card, "Format").grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 8))

        ctk.CTkLabel(card, text="Content", font=self.font(13), text_color=TEXT).grid(
            row=1, column=0, sticky="w", padx=(18, 16))
        self.mode_seg = self.segmented(card, CONTENT_TYPES, self.on_mode_change)
        self.mode_seg.grid(row=1, column=1, sticky="ew", padx=(0, 18))

        self.mode_hint = ctk.CTkLabel(card, text="", font=self.font(11), text_color=MUTED, anchor="w",
                                      justify="left")
        self.wrap_labels.append(self.mode_hint)
        self.mode_hint.grid(row=2, column=1, sticky="w", padx=(2, 18), pady=(2, 6))

        ctk.CTkLabel(card, text="Screen", font=self.font(13), text_color=TEXT).grid(
            row=3, column=0, sticky="w", padx=(18, 16))
        self.orient_seg = self.segmented(card, ORIENTATIONS, lambda _: self.invalidate_preview())
        self.orient_seg.grid(row=3, column=1, sticky="ew", padx=(0, 18))

        self.manga_opts = ctk.CTkFrame(card, fg_color="transparent")
        self.manga_opts.grid(row=4, column=0, columnspan=2, sticky="ew", padx=18, pady=(12, 0))
        self.manga_opts.grid_columnconfigure(0, weight=1)
        self.var_smart_zoom = tk.BooleanVar(value=True)
        self.var_fill_screen = tk.BooleanVar(value=True)
        self.option_row(self.manga_opts, 0, "Panel re-stacking",
                        "Drops blank gutters between panel rows so the art is larger",
                        self.var_smart_zoom)
        self.option_row(self.manga_opts, 1, "Fill screen",
                        "Stretches up to 5% to remove thin bars at the edges",
                        self.var_fill_screen)

        ctk.CTkFrame(card, fg_color="transparent", width=1, height=10).grid(row=5, column=0, columnspan=2)
        return card

    def option_row(self, parent, row, title, desc, var):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text=title, font=self.font(13), text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        desc_label = ctk.CTkLabel(box, text=desc, font=self.font(11), text_color=MUTED, anchor="w", justify="left")
        desc_label.grid(row=1, column=0, sticky="w")
        self.wrap_labels.append(desc_label)
        ctk.CTkSwitch(box, text="", variable=var, onvalue=True, offvalue=False, width=44,
                      progress_color=ACCENT, fg_color=FIELD_HOVER, button_color=("#FFFFFF", "#E4E4E7"),
                      button_hover_color=("#F4F4F5", "#FFFFFF"),
                      command=self.invalidate_preview).grid(row=0, column=1, rowspan=2, sticky="e")

    def build_footer(self, parent):
        card = Card(parent)
        card.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.grid(row=0, column=0, rowspan=2, sticky="ew", padx=(18, 12))
        info.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(info, text="Ready", font=self.font(12), text_color=MUTED, anchor="w")
        self.status.grid(row=0, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(info, height=4, corner_radius=2, fg_color=FIELD, progress_color=ACCENT)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.progress.set(0)
        self.progress.grid_remove()

        self.btn_open = self.link_button(card, "Open folder", self.open_output)

        self.btn_preview = ctk.CTkButton(
            card, text="Preview", width=96, height=38, corner_radius=10, command=self.start_preview,
            fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT, border_width=1, border_color=BORDER,
            font=self.font(13, "bold"),
        )
        self.btn_preview.grid(row=0, column=2, rowspan=2, padx=(0, 8), pady=12)

        self.btn_convert = ctk.CTkButton(
            card, text="Convert", width=132, height=38, corner_radius=10, command=self.on_convert_clicked,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF", font=self.font(13, "bold"),
        )
        self.btn_convert.grid(row=0, column=3, rowspan=2, padx=(0, 14), pady=12)
        return card

    def build_preview_card(self, parent):
        card = Card(parent)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 0))
        top.grid_columnconfigure(0, weight=1)
        self.view_seg = ctk.CTkSegmentedButton(
            top, values=["Preview", "Activity"], command=self.show_view, height=28, font=self.font(12),
            fg_color=FIELD, unselected_color=FIELD, unselected_hover_color=FIELD_HOVER,
            selected_color=PILL, selected_hover_color=PILL_HOVER, text_color=TEXT,
        )
        self.view_seg.grid(row=0, column=0, sticky="w")
        self.view_seg.set("Preview")
        self.btn_clear = self.link_button(top, "Clear", lambda: self.log_box.delete("1.0", "end"))
        self.btn_clear.configure(text_color=MUTED, font=self.font(11))

        # --- Preview view
        self.preview_view = ctk.CTkFrame(card, fg_color="transparent")
        self.preview_view.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 10))
        self.preview_view.grid_columnconfigure(0, weight=1)
        self.preview_view.grid_rowconfigure(1, weight=1)

        self.preview_title = ctk.CTkLabel(self.preview_view, text="Nothing loaded", font=self.font(12),
                                          text_color=MUTED, anchor="w")
        self.preview_title.grid(row=0, column=0, sticky="ew", padx=18, pady=(10, 6))

        # The area the device mockup is centered in; its size drives the preview scale
        self.preview_area = ctk.CTkFrame(self.preview_view, fg_color="transparent")
        self.preview_area.grid(row=1, column=0, sticky="nsew", padx=18)
        self.preview_area.bind("<Configure>", lambda e: self.schedule_preview_render())

        self.bezel = ctk.CTkFrame(self.preview_area, fg_color=BEZEL, corner_radius=18)
        self.bezel.place(relx=0.5, rely=0.5, anchor="center")
        self.screen = ctk.CTkLabel(self.bezel, text="", fg_color=PAPER, corner_radius=4,
                                   text_color=("#6B6B6B", "#6B6B6B"), font=self.font(12))
        self.screen.pack(padx=12, pady=(14, 22))

        nav = ctk.CTkFrame(self.preview_view, fg_color="transparent")
        nav.grid(row=2, column=0, pady=(10, 16))
        self.btn_prev = self.ghost_button(nav, "‹", lambda: self.step_preview(-1), width=40)
        self.btn_prev.grid(row=0, column=0)
        self.page_label = ctk.CTkLabel(nav, text="– / –", width=96, font=self.font(13, "bold"), text_color=TEXT)
        self.page_label.grid(row=0, column=1, padx=8)
        self.btn_next = self.ghost_button(nav, "›", lambda: self.step_preview(1), width=40)
        self.btn_next.grid(row=0, column=2)

        # --- Activity view
        self.log_box = ctk.CTkTextbox(card, fg_color=FIELD, text_color=TEXT, corner_radius=8, border_width=0,
                                      font=self.font(11, mono=True), wrap="word")
        return card

    def show_view(self, name):
        self.view_seg.set(name)
        if name == "Preview":
            self.log_box.grid_remove()
            self.btn_clear.grid_remove()
            self.preview_view.grid()
        else:
            self.preview_view.grid_remove()
            self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
            self.btn_clear.grid(row=0, column=1, sticky="e")

    # -------------------------------------------------------------- settings
    def load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        data = {
            "input": self.input_var.get(),
            "output": self.output_var.get(),
            "mode": self.mode_seg.get(),
            "orientation": self.orient_seg.get(),
            "smart_zoom": self.var_smart_zoom.get(),
            "fill_screen": self.var_fill_screen.get(),
            "theme": self.theme_menu.get(),
        }
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def apply_settings(self):
        s = self.settings
        theme = s.get("theme") if s.get("theme") in ("System", "Light", "Dark") else "System"
        self.theme_menu.set(theme)
        ctk.set_appearance_mode(theme)
        self.mode_seg.set(s.get("mode") if s.get("mode") in CONTENT_TYPES else "Manhwa")
        self.orient_seg.set(s.get("orientation") if s.get("orientation") in ORIENTATIONS else ORIENTATIONS[0])
        self.var_smart_zoom.set(bool(s.get("smart_zoom", True)))
        self.var_fill_screen.set(bool(s.get("fill_screen", True)))
        if s.get("input") and os.path.exists(s["input"]):
            self.input_var.set(s["input"])
        if s.get("output"):
            self.output_var.set(s["output"])
        self.on_mode_change(self.mode_seg.get())
        self.render_preview()

    def on_close(self):
        self.cancel_event.set()
        self.save_settings()
        self.destroy()

    # -------------------------------------------------------------- handlers
    def on_theme_change(self, value):
        ctk.set_appearance_mode(value)

    def on_mode_change(self, value):
        self.mode_hint.configure(text=CONTENT_HINTS[value])
        if value == "Manga":
            self.manga_opts.grid()
        else:
            self.manga_opts.grid_remove()
        self.invalidate_preview()

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Choose a comic archive",
            initialdir=self.initial_dir(),
            filetypes=[("Comic archives", "*.cbz *.zip"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)

    def browse_folder(self):
        path = filedialog.askdirectory(title="Choose a folder of chapters", initialdir=self.initial_dir())
        if path:
            self.input_var.set(path)

    def browse_output(self):
        path = filedialog.askdirectory(title="Choose where the .xtc files go", initialdir=self.initial_dir())
        if path:
            self.output_var.set(path)

    def initial_dir(self):
        current = self.input_var.get().strip()
        if os.path.isdir(current):
            return current
        if os.path.isfile(current):
            return os.path.dirname(current)
        return os.path.expanduser("~")

    def refresh_source_info(self):
        path = self.input_var.get().strip()
        output = self.output_var.get().strip()
        if not path:
            text, color = "", MUTED
        elif not os.path.exists(path):
            text, color = "Path not found", DANGER
        else:
            try:
                count = len(xtc_core.find_archives(path))
            except OSError:
                count = 0
            if os.path.isfile(path):
                size_mb = os.path.getsize(path) / 1e6
                text = f"Single file  ·  {size_mb:.1f} MB"
            else:
                text = f"{count} chapter{'s' if count != 1 else ''} found" if count else "No .cbz files in this folder"
            color = MUTED if count else DANGER
            if count and not output:
                text += f"  ·  saves to {self.short_path(xtc_core.default_output_dir(path))}"
        self.source_info.configure(text=text, text_color=color)
        self.invalidate_preview()

    @staticmethod
    def clip(text, limit=70):
        return text if len(text) <= limit else text[:limit - 1] + "…"

    @staticmethod
    def short_path(path, parts=2):
        pieces = os.path.normpath(path).split(os.sep)
        return path if len(pieces) <= parts else os.path.join("…", *pieces[-parts:])

    def invalidate_preview(self):
        """Settings changed: the current preview no longer represents the output."""
        if self.preview_pages and self.busy != "preview":
            self.preview_title.configure(text=f"{self.preview_meta}  ·  outdated, press Preview")

    # ------------------------------------------------------------- job setup
    def collect_job(self):
        """Reads every setting on the UI thread; workers never touch Tk widgets."""
        input_path = self.input_var.get().strip()
        if not input_path or not os.path.exists(input_path):
            self.set_status("Choose a valid .cbz file or folder first.", DANGER)
            return None
        archives = xtc_core.find_archives(input_path)
        if not archives:
            self.set_status("No .cbz files found in that folder.", DANGER)
            return None
        tw, th = xtc_core.resolution(self.orient_seg.get() == ORIENTATIONS[1])
        return {
            "input": input_path,
            "archives": archives,
            "output": self.output_var.get().strip() or None,
            "mode": self.mode_seg.get(),
            "tw": tw,
            "th": th,
            "smart_zoom": self.var_smart_zoom.get(),
            "fill_screen": self.var_fill_screen.get(),
        }

    def set_busy(self, kind):
        self.busy = kind
        if kind:
            self.progress.set(0)
            self.progress.grid()
        else:
            self.progress.grid_remove()
        if kind == "convert":
            self.btn_convert.configure(text="Cancel", fg_color=DANGER_FILL, hover_color=DANGER_HOVER)
            self.btn_preview.configure(state="disabled")
        elif kind == "preview":
            self.btn_preview.configure(state="disabled", text="Rendering…")
            self.btn_convert.configure(state="disabled")
        else:
            self.btn_convert.configure(text="Convert", fg_color=ACCENT, hover_color=ACCENT_HOVER, state="normal")
            self.btn_preview.configure(text="Preview", state="normal")

    def set_status(self, text, color=MUTED):
        self.status.configure(text=self.clip(text), text_color=color)
        self.btn_open.grid_remove()

    # --------------------------------------------------------------- preview
    def start_preview(self):
        if self.busy:
            return
        job = self.collect_job()
        if not job:
            return
        self.cancel_event.clear()
        self.set_busy("preview")
        target = job["archives"][0]
        self.set_status(f"Rendering preview of {os.path.basename(target)}…")
        self.progress.set(0)
        threading.Thread(target=self.preview_worker, args=(job, target), daemon=True).start()

    def preview_worker(self, job, target):
        def on_step(done, total):
            if self.cancel_event.is_set():
                raise xtc_core.ConversionCancelled()
            self.events.put(("progress", (done / float(max(1, total)), "")))

        try:
            if job["mode"] == "Manhwa":
                pages = manhwa2xtc.generate_manhwa_pages(target, job["tw"], job["th"], on_step=on_step)
            else:
                pages = manga2xtc.generate_manga_pages(target, smart_zoom=job["smart_zoom"],
                                                       fill_screen=job["fill_screen"], target_width=job["tw"],
                                                       target_height=job["th"], on_step=on_step)
            self.events.put(("preview_done", (target, [p.convert("L") for p in pages])))
        except xtc_core.ConversionCancelled:
            self.events.put(("idle", None))
        except Exception as e:
            self.events.put(("preview_error", str(e)))

    def step_preview(self, delta):
        if isinstance(self.focus_get(), (tk.Entry, tk.Text)):
            return
        if self.preview_pages:
            self.preview_index = max(0, min(len(self.preview_pages) - 1, self.preview_index + delta))
            self.render_preview()

    def schedule_preview_render(self):
        if getattr(self, "_render_job", None):
            self.after_cancel(self._render_job)
        self._render_job = self.after(40, self.render_preview)

    def render_preview(self):
        self._render_job = None
        area_w = max(120, self.preview_area.winfo_width())
        area_h = max(160, self.preview_area.winfo_height())
        avail_w, avail_h = area_w - 24 - 8, area_h - 36 - 8  # bezel padding

        if self.preview_pages:
            page = self.preview_pages[self.preview_index]
            pw, ph = page.size
        else:
            page = None
            pw, ph = xtc_core.resolution(self.orient_seg.get() == ORIENTATIONS[1])

        scale = min(avail_w / float(pw), avail_h / float(ph))
        size = (max(1, int(pw * scale)), max(1, int(ph * scale)))

        if page is not None:
            self.preview_photo = ctk.CTkImage(light_image=page, dark_image=page, size=size)
            self.screen.configure(image=self.preview_photo, text="", width=size[0], height=size[1])
            self.page_label.configure(text=f"{self.preview_index + 1} / {len(self.preview_pages)}")
        else:
            self.screen.configure(image=None, text=f"{pw} × {ph}\n\nNo preview yet",
                                  width=size[0], height=size[1])
            self.page_label.configure(text="– / –")

        has = bool(self.preview_pages)
        self.btn_prev.configure(state="normal" if has and self.preview_index > 0 else "disabled")
        self.btn_next.configure(state="normal" if has and self.preview_index < len(self.preview_pages) - 1
                                else "disabled")

    # --------------------------------------------------------------- convert
    def on_convert_clicked(self):
        if self.busy == "convert":
            self.cancel_event.set()
            self.btn_convert.configure(text="Cancelling…", state="disabled")
            return
        if self.busy:
            return
        job = self.collect_job()
        if not job:
            return
        self.save_settings()
        self.cancel_event.clear()
        self.set_busy("convert")
        self.progress.set(0)
        self.set_status("Starting…")
        self.show_view("Activity")
        self.log(f"— {job['mode']} · {job['tw']}×{job['th']} · {len(job['archives'])} file(s)")
        threading.Thread(target=self.convert_worker, args=(job,), daemon=True).start()

    def convert_worker(self, job):
        common = dict(
            output_dir=job["output"],
            target_width=job["tw"],
            target_height=job["th"],
            progress=lambda frac, name: self.events.put(("progress", (frac, name))),
            log=lambda msg: self.events.put(("log", msg)),
            cancel_event=self.cancel_event,
        )
        try:
            if job["mode"] == "Manhwa":
                result = manhwa2xtc.process_manhwa_batch(job["input"], **common)
            else:
                result = manga2xtc.process_manga_batch(job["input"], smart_zoom=job["smart_zoom"],
                                                       fill_screen=job["fill_screen"], **common)
            self.events.put(("convert_done", result))
        except Exception as e:
            self.events.put(("convert_error", str(e)))

    def open_output(self):
        if self.last_output_dir and os.path.isdir(self.last_output_dir):
            open_in_file_manager(self.last_output_dir)

    # ------------------------------------------------------ event dispatcher
    def log(self, text):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self.handle_event(kind, payload)
        except queue.Empty:
            pass
        self.after(60, self.drain_events)

    def handle_event(self, kind, payload):
        if kind == "log":
            self.log(payload)
        elif kind == "progress":
            frac, name = payload
            self.progress.set(frac)
            if self.busy == "convert" and name:
                self.status.configure(text=self.clip(f"{int(frac * 100)}%  ·  {name}"), text_color=MUTED)
        elif kind == "preview_done":
            target, pages = payload
            self.preview_pages, self.preview_index = pages, 0
            self.preview_meta = f"{len(pages)} pages  ·  {os.path.basename(target)}"
            self.preview_title.configure(text=self.preview_meta)
            self.show_view("Preview")
            self.set_busy(None)
            self.set_status("Preview ready  ·  use ← → to flip pages")
            self.progress.set(0)
            self.render_preview()
        elif kind == "preview_error":
            self.set_busy(None)
            self.progress.set(0)
            self.set_status(f"Preview failed: {payload}", DANGER)
            self.log(f"✗ Preview: {payload}")
        elif kind == "convert_done":
            self.finish_conversion(payload)
        elif kind == "convert_error":
            self.set_busy(None)
            self.set_status(f"Conversion failed: {payload}", DANGER)
            self.log(f"✗ {payload}")
        elif kind == "idle":
            self.set_busy(None)
            self.set_status("Ready")

    def finish_conversion(self, result):
        self.set_busy(None)
        self.last_output_dir = result.output_dir
        ok, bad = len(result.converted), len(result.failed)
        if result.cancelled:
            self.set_status(f"Cancelled  ·  {ok} converted", MUTED)
        elif bad:
            self.set_status(f"Finished with errors  ·  {ok} converted, {bad} failed", DANGER)
        else:
            self.set_status(f"Done  ·  {ok} file{'s' if ok != 1 else ''} converted", SUCCESS)
        if ok:
            self.btn_open.grid(row=0, column=1, rowspan=2, padx=(0, 8))


if __name__ == "__main__":
    app = XteinkConverterApp()
    app.mainloop()
