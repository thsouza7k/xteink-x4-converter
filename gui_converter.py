#!/usr/bin/env python3
"""
gui_converter.py - Modern Desktop GUI Converter & Live Preview for Xteink X4 Pro
Built with CustomTkinter. Converts Manhwa & Manga CBZ files to XTC binary e-ink format.
"""

import os
import sys
import glob
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image

import manhwa2xtc
import manga2xtc

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class XteinkConverterApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Xteink X4 Pro - Manhwa & Manga XTC Converter")
        self.geometry("980x680")
        self.minsize(880, 600)

        self.input_mode = "folder"
        self.is_converting = False
        self.is_previewing = False

        # Live Preview state
        self.preview_pages = []
        self.preview_index = 0

        self.create_widgets()

    def create_widgets(self):
        # Header Frame
        self.header_frame = ctk.CTkFrame(self, corner_radius=10)
        self.header_frame.pack(fill="x", padx=15, pady=(15, 10))

        self.title_label = ctk.CTkLabel(
            self.header_frame,
            text="Xteink X4 Pro Converter & Live Visual Preview",
            font=ctk.CTkFont(size=22, weight="bold")
        )
        self.title_label.pack(anchor="w", padx=15, pady=(12, 2))

        self.subtitle_label = ctk.CTkLabel(
            self.header_frame,
            text="Convert Manhwas & Mangas to optimized .XTC binary format with real-time visual slice preview",
            font=ctk.CTkFont(size=12)
        )
        self.subtitle_label.pack(anchor="w", padx=15, pady=(0, 12))

        # Main Paned Layout: Left Controls, Right Preview
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Left Column (Controls + Log)
        self.left_column = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.left_column.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # Right Column (Live Preview Box)
        self.right_column = ctk.CTkFrame(self.main_container, corner_radius=10, width=320)
        self.right_column.pack(side="right", fill="both", padx=(0, 0))

        # Build Left Column Widgets
        self.build_controls(self.left_column)
        # Build Right Column Preview Widgets
        self.build_preview_pane(self.right_column)

    def build_controls(self, parent):
        self.controls_frame = ctk.CTkFrame(parent, corner_radius=10)
        self.controls_frame.pack(fill="x", pady=(0, 10))

        # 1. Reading Type (Manhwa vs Manga)
        self.mode_label = ctk.CTkLabel(self.controls_frame, text="Content Type:", font=ctk.CTkFont(weight="bold"))
        self.mode_label.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.mode_segmented = ctk.CTkSegmentedButton(
            self.controls_frame,
            values=["Manhwa (Webtoon Strip)", "Manga (Japanese RTL)"],
            command=self.on_mode_change
        )
        self.mode_segmented.set("Manhwa (Webtoon Strip)")
        self.mode_segmented.grid(row=0, column=1, columnspan=2, sticky="ew", padx=12, pady=(10, 4))

        # 2. Screen Orientation (Vertical vs Horizontal)
        self.orient_label = ctk.CTkLabel(self.controls_frame, text="Display Orientation:", font=ctk.CTkFont(weight="bold"))
        self.orient_label.grid(row=1, column=0, sticky="w", padx=12, pady=4)

        self.orient_segmented = ctk.CTkSegmentedButton(
            self.controls_frame,
            values=["Vertical (Portrait 480x800)", "Horizontal (Landscape 800x480)"]
        )
        self.orient_segmented.set("Vertical (Portrait 480x800)")
        self.orient_segmented.grid(row=1, column=1, columnspan=2, sticky="ew", padx=12, pady=4)

        # 3. Input Selection Type
        self.input_type_label = ctk.CTkLabel(self.controls_frame, text="Input Selection:", font=ctk.CTkFont(weight="bold"))
        self.input_type_label.grid(row=2, column=0, sticky="w", padx=12, pady=4)

        self.input_type_segmented = ctk.CTkSegmentedButton(
            self.controls_frame,
            values=["Folder with CBZs", "Single .CBZ File"],
            command=self.on_input_type_change
        )
        self.input_type_segmented.set("Folder with CBZs")
        self.input_type_segmented.grid(row=2, column=1, columnspan=2, sticky="ew", padx=12, pady=4)

        # 4. Input Path Entry
        self.input_path_label = ctk.CTkLabel(self.controls_frame, text="Input Path:")
        self.input_path_label.grid(row=3, column=0, sticky="w", padx=12, pady=4)

        self.input_path_entry = ctk.CTkEntry(self.controls_frame, placeholder_text="Select input file or folder...")
        self.input_path_entry.grid(row=3, column=1, sticky="ew", padx=(12, 4), pady=4)

        self.input_browse_btn = ctk.CTkButton(self.controls_frame, text="Browse", width=80, command=self.browse_input)
        self.input_browse_btn.grid(row=3, column=2, padx=(4, 12), pady=4)

        # 5. Output Path Entry
        self.output_path_label = ctk.CTkLabel(self.controls_frame, text="Output Folder:")
        self.output_path_label.grid(row=4, column=0, sticky="w", padx=12, pady=4)

        self.output_path_entry = ctk.CTkEntry(self.controls_frame, placeholder_text="Select output destination directory...")
        self.output_path_entry.grid(row=4, column=1, sticky="ew", padx=(12, 4), pady=4)

        self.output_browse_btn = ctk.CTkButton(self.controls_frame, text="Browse", width=80, command=self.browse_output)
        self.output_browse_btn.grid(row=4, column=2, padx=(4, 12), pady=4)

        # Manga Options
        self.options_frame = ctk.CTkFrame(self.controls_frame, fg_color="transparent")
        self.options_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(6, 10))

        self.var_fill_screen = ctk.BooleanVar(value=True)
        self.chk_fill_screen = ctk.CTkCheckBox(self.options_frame, text="Fill 100% Screen Height", variable=self.var_fill_screen)
        self.chk_fill_screen.pack(side="left", padx=(0, 15))

        self.var_smart_zoom = ctk.BooleanVar(value=True)
        self.chk_smart_zoom = ctk.CTkCheckBox(self.options_frame, text="Smart Panel Zoom (2x Manga)", variable=self.var_smart_zoom)
        self.chk_smart_zoom.pack(side="left")

        self.controls_frame.columnconfigure(1, weight=1)

        # Action Buttons & Status Frame
        self.action_frame = ctk.CTkFrame(parent, corner_radius=10)
        self.action_frame.pack(fill="x", pady=(0, 10))

        self.btn_box = ctk.CTkFrame(self.action_frame, fg_color="transparent")
        self.btn_box.pack(fill="x", padx=12, pady=(10, 6))

        self.btn_preview = ctk.CTkButton(
            self.btn_box,
            text="PREVIEW SLICES",
            fg_color="#3B82F6",
            hover_color="#2563EB",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            command=self.start_preview
        )
        self.btn_preview.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_convert = ctk.CTkButton(
            self.btn_box,
            text="START BATCH CONVERSION",
            fg_color="#10B981",
            hover_color="#059669",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            command=self.start_conversion
        )
        self.btn_convert.pack(side="right", fill="x", expand=True, padx=(6, 0))

        self.progress_bar = ctk.CTkProgressBar(self.action_frame)
        self.progress_bar.pack(fill="x", padx=12, pady=(0, 6))
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(self.action_frame, text="Status: Ready", font=ctk.CTkFont(size=12))
        self.status_label.pack(anchor="w", padx=12, pady=(0, 8))

        # Log Output Box
        self.log_frame = ctk.CTkFrame(parent, corner_radius=10)
        self.log_frame.pack(fill="both", expand=True)

        self.log_label = ctk.CTkLabel(self.log_frame, text="Conversion Log Output:", font=ctk.CTkFont(weight="bold"))
        self.log_label.pack(anchor="w", padx=12, pady=(6, 2))

        self.log_textbox = ctk.CTkTextbox(self.log_frame, font=ctk.CTkFont(family="Consolas", size=11))
        self.log_textbox.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    def build_preview_pane(self, parent):
        self.preview_header = ctk.CTkLabel(parent, text="Visual E-Ink Preview", font=ctk.CTkFont(size=14, weight="bold"))
        self.preview_header.pack(anchor="center", pady=(12, 4))

        # Image Canvas Frame
        self.preview_canvas_frame = ctk.CTkFrame(parent, fg_color="#1E293B", corner_radius=8, width=260, height=420)
        self.preview_canvas_frame.pack(padx=12, pady=8, fill="both", expand=True)
        self.preview_canvas_frame.pack_propagate(False)

        self.img_label = ctk.CTkLabel(self.preview_canvas_frame, text="Select a .CBZ file or folder\nand click 'PREVIEW SLICES'")
        self.img_label.pack(expand=True)

        # Preview Nav Controls
        self.preview_nav_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.preview_nav_frame.pack(fill="x", padx=12, pady=(0, 12))

        self.btn_prev_page = ctk.CTkButton(self.preview_nav_frame, text="< Prev", width=70, command=self.prev_preview_page)
        self.btn_prev_page.pack(side="left", padx=4)

        self.lbl_page_num = ctk.CTkLabel(self.preview_nav_frame, text="Page 0 / 0", font=ctk.CTkFont(weight="bold"))
        self.lbl_page_num.pack(side="left", expand=True)

        self.btn_next_page = ctk.CTkButton(self.preview_nav_frame, text="Next >", width=70, command=self.next_preview_page)
        self.btn_next_page.pack(side="right", padx=4)

    def on_mode_change(self, value):
        if "Manhwa" in value:
            self.chk_smart_zoom.configure(state="disabled")
            self.chk_fill_screen.configure(state="disabled")
        else:
            self.chk_smart_zoom.configure(state="normal")
            self.chk_fill_screen.configure(state="normal")

    def on_input_type_change(self, value):
        self.input_mode = "folder" if "Folder" in value else "file"
        self.input_path_entry.delete(0, "end")

    def browse_input(self):
        if self.input_mode == "folder":
            path = filedialog.askdirectory(title="Select Input Folder Containing CBZs")
        else:
            path = filedialog.askopenfilename(
                title="Select .CBZ File",
                filetypes=[("CBZ files", "*.cbz"), ("All files", "*.*")]
            )
        if path:
            self.input_path_entry.delete(0, "end")
            self.input_path_entry.insert(0, path)

            if not self.output_path_entry.get():
                if os.path.isfile(path):
                    self.output_path_entry.insert(0, os.path.dirname(path))
                else:
                    self.output_path_entry.insert(0, os.path.join(path, "output_xtc"))

    def browse_output(self):
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self.output_path_entry.delete(0, "end")
            self.output_path_entry.insert(0, path)

    def log(self, text):
        self.log_textbox.insert("end", text + "\n")
        self.log_textbox.see("end")

    def get_target_resolution(self):
        orient = self.orient_segmented.get()
        if "Horizontal" in orient:
            return 800, 480
        return 480, 800

    def start_preview(self):
        if self.is_previewing or self.is_converting:
            return

        input_path = self.input_path_entry.get().strip()
        if not input_path or not os.path.exists(input_path):
            messagebox.showerror("Error", "Please select a valid input file or directory.")
            return

        # Find target CBZ file for preview
        if os.path.isdir(input_path):
            files = sorted(glob.glob(os.path.join(input_path, "*.cbz")))
            if not files:
                files = sorted(glob.glob(os.path.join(input_path, "**", "*.cbz"), recursive=True))
            if not files:
                messagebox.showerror("Error", "No .CBZ files found in selected directory.")
                return
            target_cbz = files[0]
        else:
            target_cbz = input_path

        self.is_previewing = True
        self.btn_preview.configure(state="disabled", text="GENERATING PREVIEW...")
        self.status_label.configure(text=f"Status: Generating preview for {os.path.basename(target_cbz)}...")

        threading.Thread(target=self.run_preview_worker, args=(target_cbz,), daemon=True).start()

    def run_preview_worker(self, cbz_file):
        mode = self.mode_segmented.get()
        tw, th = self.get_target_resolution()

        try:
            if "Manhwa" in mode:
                self.after(0, lambda: self.log(f"[Preview] Generating Manhwa slices for {os.path.basename(cbz_file)}..."))
                pages = manhwa2xtc.generate_manhwa_pages(cbz_file, target_width=tw, target_height=th)
            else:
                self.after(0, lambda: self.log(f"[Preview] Generating Manga slices for {os.path.basename(cbz_file)}..."))
                fill_scr = self.var_fill_screen.get()
                smart_zm = self.var_smart_zoom.get()
                pages = manga2xtc.generate_manga_pages(
                    cbz_file, smart_zoom=smart_zm, fill_screen=fill_scr,
                    target_width=tw, target_height=th
                )

            self.preview_pages = pages
            self.preview_index = 0
            self.after(0, self.update_preview_display)
            self.after(0, lambda: self.log(f"[Preview] Generated {len(pages)} slice frames for visual review."))

        except Exception as e:
            self.after(0, lambda err=str(e): self.log(f"[Preview Error] {err}"))
            self.after(0, lambda err=str(e): messagebox.showerror("Preview Error", err))

        finally:
            self.is_previewing = False
            self.after(0, lambda: self.btn_preview.configure(state="normal", text="PREVIEW SLICES"))
            self.after(0, lambda: self.status_label.configure(text="Status: Ready"))

    def update_preview_display(self):
        if not self.preview_pages:
            self.img_label.configure(text="No preview available", image="")
            self.lbl_page_num.configure(text="Page 0 / 0")
            return

        total = len(self.preview_pages)
        pil_page = self.preview_pages[self.preview_index]

        tw, th = self.get_target_resolution()
        # Scale for UI Preview frame box (max 240x380)
        scale = min(240.0 / float(tw), 380.0 / float(th))
        preview_w = int(tw * scale)
        preview_h = int(th * scale)

        ctk_img = ctk.CTkImage(light_image=pil_page, dark_image=pil_page, size=(preview_w, preview_h))
        self.img_label.configure(image=ctk_img, text="")
        self.lbl_page_num.configure(text=f"Page {self.preview_index + 1} / {total}")

    def prev_preview_page(self):
        if self.preview_pages and self.preview_index > 0:
            self.preview_index -= 1
            self.update_preview_display()

    def next_preview_page(self):
        if self.preview_pages and self.preview_index < len(self.preview_pages) - 1:
            self.preview_index += 1
            self.update_preview_display()

    def start_conversion(self):
        if self.is_converting or self.is_previewing:
            return

        input_path = self.input_path_entry.get().strip()
        output_dir = self.output_path_entry.get().strip()

        if not input_path or not os.path.exists(input_path):
            messagebox.showerror("Error", "Please select a valid input file or directory.")
            return

        if not output_dir:
            output_dir = os.path.dirname(input_path) if os.path.isfile(input_path) else os.path.join(input_path, "output_xtc")
            self.output_path_entry.insert(0, output_dir)

        self.is_converting = True
        self.btn_convert.configure(state="disabled", text="CONVERTING...")
        self.log_textbox.delete("1.0", "end")
        self.log("--- Starting Batch Conversion ---")

        threading.Thread(target=self.run_conversion_worker, args=(input_path, output_dir), daemon=True).start()

    def run_conversion_worker(self, input_path, output_dir):
        mode = self.mode_segmented.get()
        tw, th = self.get_target_resolution()

        def update_progress(current, total, filename):
            progress_frac = current / float(total) if total > 0 else 1.0
            self.after(0, lambda: self.progress_bar.set(progress_frac))
            msg = f"Processing ({current+1}/{total}): {filename}" if current < total else "Conversion completed!"
            self.after(0, lambda m=msg: self.status_label.configure(text=f"Status: {m}"))
            self.after(0, lambda f=filename: self.log(f"[Batch] {f}"))

        try:
            if "Manhwa" in mode:
                self.after(0, lambda: self.log(f"[Mode] Manhwa (Webtoon Strip) | Resolution: {tw}x{th}"))
                manhwa2xtc.process_manhwa_batch(
                    input_path,
                    output_dir=output_dir,
                    target_width=tw,
                    target_height=th,
                    progress_callback=update_progress
                )
            else:
                self.after(0, lambda: self.log(f"[Mode] Manga (Japanese RTL) | Resolution: {tw}x{th}"))
                fill_scr = self.var_fill_screen.get()
                smart_zm = self.var_smart_zoom.get()
                manga2xtc.process_manga_batch(
                    input_path,
                    output_dir=output_dir,
                    fill_screen=fill_scr,
                    smart_zoom=smart_zm,
                    target_width=tw,
                    target_height=th,
                    progress_callback=update_progress
                )

            self.after(0, lambda: self.log("\n[Success] All files converted successfully!"))
            self.after(0, lambda: messagebox.showinfo("Completed", f"Conversion finished!\nOutput saved to:\n{output_dir}"))

        except Exception as e:
            self.after(0, lambda err=str(e): self.log(f"\n[ERROR] Conversion failed: {err}"))
            self.after(0, lambda err=str(e): messagebox.showerror("Error", f"An error occurred:\n{err}"))

        finally:
            self.is_converting = False
            self.after(0, lambda: self.btn_convert.configure(state="normal", text="START BATCH CONVERSION"))

if __name__ == "__main__":
    app = XteinkConverterApp()
    app.mainloop()
