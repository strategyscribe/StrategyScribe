"""Okno programu (Start/Stop, nastavenia, priebeh, log)."""

import ctypes
import os
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path

import anthropic
import customtkinter as ctk
import mss
from tkinter import filedialog, messagebox

from . import analyzer, capture, config as config_module, documents, docx_export, paths, security, transcribe

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

LANGUAGE_OPTIONS = [
    ("Automaticky", "auto"),
    ("Slovenčina", "sk"),
    ("Čeština", "cs"),
    ("Angličtina", "en"),
]
AUDIO_MODE_OPTIONS = [
    ("Zvukové zariadenie (celý systém)", "device"),
    ("Konkrétny program (napr. prehliadač)", "process"),
]
VIDEO_MODE_OPTIONS = [
    ("Celá obrazovka (všetky monitory)", "all_monitors"),
    ("Konkrétny monitor", "monitor"),
    ("Vybraná oblasť", "region"),
]
VIDEO_QUALITY_OPTIONS = [
    ("Nízka (menší súbor)", "low"),
    ("Stredná", "medium"),
    ("Vysoká (väčší súbor)", "high"),
]
VIDEO_FPS_OPTIONS = ["5", "10", "15", "30"]
APP_MODE_OPTIONS = [
    ("Nahrávanie + AI analýza stratégie", "full"),
    ("Výskum (video → Word zhrnutie)", "research"),
    ("Len nahrávanie videa (bez AI)", "video_only"),
    ("Len nahrávanie zvuku (bez AI)", "audio_only"),
]
WHISPER_MODEL_OPTIONS = ["small", "medium", "large"]
SILENCE_THRESHOLD = 0.02
SILENCE_WARNING_SECONDS = 5
BLOCK_SECONDS = 180
SCREENSHOTS_PER_BLOCK = 3


class RegionSelector(ctk.CTkToplevel):
    """Priehľadný celoobrazovkový overlay — ťahaním myši vyznačí obdĺžnikovú
    oblasť obrazovky. Výsledok (dict left/top/width/height, alebo None pri
    zrušení cez Esc) je v self.result po zatvorení okna."""

    def __init__(self, master):
        super().__init__(master)
        self.result = None
        self.overrideredirect(True)
        self.attributes("-alpha", 0.35)
        self.attributes("-topmost", True)
        self.configure(fg_color="gray20")

        with mss.mss() as sct:
            bounds = sct.monitors[0]
        self._origin_x = bounds["left"]
        self._origin_y = bounds["top"]
        self.geometry(f"{bounds['width']}x{bounds['height']}+{bounds['left']}+{bounds['top']}")

        self.canvas = tk.Canvas(self, cursor="cross", bg="gray20", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._start = None
        self._rect_id = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda e: self._finish(None))

        ctk.CTkLabel(
            self, text="Ťahaním myši vyznač oblasť. Esc = zrušiť.",
            fg_color="black", text_color="white",
        ).place(relx=0.5, rely=0.03, anchor="n")

        self.grab_set()
        self.focus_force()

    def _on_press(self, event):
        self._start = (event.x, event.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=2
        )

    def _on_drag(self, event):
        if self._start:
            x0, y0 = self._start
            self.canvas.coords(self._rect_id, x0, y0, event.x, event.y)

    def _on_release(self, event):
        if not self._start:
            return
        x0, y0 = self._start
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        if right - left > 5 and bottom - top > 5:
            self._finish({
                "left": self._origin_x + left,
                "top": self._origin_y + top,
                "width": right - left,
                "height": bottom - top,
            })
        else:
            self._start = None

    def _finish(self, region):
        self.result = region
        self.grab_release()
        self.destroy()


def select_region_interactively(master):
    selector = RegionSelector(master)
    master.wait_window(selector)
    return selector.result


class RecordingIndicator(ctk.CTkToplevel):
    """Malá červená bodka vpravo hore, viditeľná len počas nahrávania — aby
    používateľ vždy vedel, že appka práve nahráva. Cez Windows API
    (SetWindowDisplayAffinity / WDA_EXCLUDEFROMCAPTURE) je vylúčená z
    akéhokoľvek snímania obrazovky, takže sa nikdy neobjaví v samotnom zázname,
    len na živej obrazovke."""

    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    GA_ROOT = 2

    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        size = 22
        screen_w = self.winfo_screenwidth()
        self.geometry(f"{size}x{size}+{screen_w - size - 16}+16")
        self.configure(fg_color="black")
        canvas = tk.Canvas(self, width=size, height=size, bg="black", highlightthickness=0)
        canvas.pack()
        canvas.create_oval(3, 3, size - 3, size - 3, fill="#e03131", outline="")
        self.withdraw()
        self.after(50, self._exclude_from_capture)

    def _exclude_from_capture(self):
        try:
            raw_hwnd = self.winfo_id()
            hwnd = ctypes.windll.user32.GetAncestor(raw_hwnd, self.GA_ROOT)
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, self.WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass  # na staršom Windows API neexistuje — appka funguje aj bez indikátora

    def show(self):
        self.deiconify()
        self.lift()

    def hide(self):
        self.withdraw()


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, cfg, on_save):
        super().__init__(master)
        self.title("Nastavenia")
        self.geometry("480x600")
        self.minsize(420, 300)
        self.cfg = dict(cfg)
        self.on_save = on_save
        self.transient(master)
        self.grab_set()

        pad = {"padx": 16, "pady": (10, 0)}

        # Obsah je v posúvateľnom paneli, aby sa zmestil aj na menšie obrazovky —
        # tlačidlá Uložiť/Zrušiť sú mimo neho, vždy viditeľné dole.
        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_frame.pack(fill="both", expand=True)
        body = self.scroll_frame

        ctk.CTkLabel(body, text="Claude API kľúč").pack(anchor="w", **pad)
        self.api_key_entry = ctk.CTkEntry(body, show="*", width=420)
        self.api_key_entry.insert(0, self.cfg.get("api_key", ""))
        self.api_key_entry.pack(**pad)
        ctk.CTkButton(
            body, text="Skontrolovať zostatok kreditu (otvorí prehliadač)",
            fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
            command=lambda: webbrowser.open("https://console.anthropic.com/settings/billing"),
        ).pack(anchor="w", padx=16, pady=(6, 0))
        ctk.CTkLabel(
            body,
            text="Appka nevie zobraziť zostatok priamo (Claude API to neposkytuje) — "
                 "toto tlačidlo len otvorí stránku s fakturáciou.",
            text_color="gray", wraplength=420, justify="left", font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=16, pady=(2, 0))

        ctk.CTkLabel(body, text="Priečinok na ukladanie výstupov").pack(anchor="w", **pad)
        out_frame = ctk.CTkFrame(body, fg_color="transparent")
        out_frame.pack(fill="x", **pad)
        self.output_dir_entry = ctk.CTkEntry(out_frame, width=320)
        self.output_dir_entry.insert(0, self.cfg.get("output_dir", ""))
        self.output_dir_entry.pack(side="left")
        ctk.CTkButton(out_frame, text="Vybrať...", width=80, command=self._browse_output_dir).pack(
            side="left", padx=(8, 0)
        )

        ctk.CTkLabel(body, text="Interval snímok obrazovky (sekundy)").pack(anchor="w", **pad)
        self.interval_entry = ctk.CTkEntry(body, width=100)
        self.interval_entry.insert(0, str(self.cfg.get("screenshot_interval", 10)))
        self.interval_entry.pack(anchor="w", **pad)

        ctk.CTkLabel(body, text="Veľkosť Whisper modelu").pack(anchor="w", **pad)
        self.model_var = ctk.StringVar(value=self.cfg.get("whisper_model", "medium"))
        ctk.CTkOptionMenu(body, values=WHISPER_MODEL_OPTIONS, variable=self.model_var).pack(anchor="w", **pad)

        ctk.CTkLabel(body, text="Jazyk").pack(anchor="w", **pad)
        current_lang = self.cfg.get("language", "auto")
        current_lang_label = next(
            (label for label, code in LANGUAGE_OPTIONS if code == current_lang), "Automaticky"
        )
        self.language_var = ctk.StringVar(value=current_lang_label)
        ctk.CTkOptionMenu(
            body, values=[label for label, _ in LANGUAGE_OPTIONS], variable=self.language_var
        ).pack(anchor="w", **pad)

        ctk.CTkLabel(body, text="AI model (spracovanie kurzu)").pack(anchor="w", **pad)
        current_ai_model = self.cfg.get("ai_model", analyzer.DEFAULT_MODEL)
        current_ai_model_label = next(
            (label for label, code in analyzer.MODEL_OPTIONS if code == current_ai_model),
            analyzer.MODEL_OPTIONS[0][0],
        )
        self.ai_model_var = ctk.StringVar(value=current_ai_model_label)
        ctk.CTkOptionMenu(
            body, values=[label for label, _ in analyzer.MODEL_OPTIONS], variable=self.ai_model_var, width=420,
        ).pack(**pad)

        ctk.CTkLabel(body, text="Limit ceny relácie v USD (0 = bez limitu)").pack(anchor="w", **pad)
        self.cost_limit_entry = ctk.CTkEntry(body, width=120)
        self.cost_limit_entry.insert(0, str(self.cfg.get("cost_limit_usd", 0.0)))
        self.cost_limit_entry.pack(anchor="w", **pad)
        ctk.CTkLabel(
            body,
            text="Keď odhadovaná cena dosiahne limit, appka prestane analyzovať ďalšie bloky "
                 "(dokončí syntézu z toho, čo už má).",
            text_color="gray", wraplength=420, justify="left", font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=16, pady=(2, 0))

        ctk.CTkLabel(body, text="Zdroj zvuku").pack(anchor="w", **pad)
        current_mode = self.cfg.get("audio_source_mode", "device")
        current_mode_label = next(
            (label for label, code in AUDIO_MODE_OPTIONS if code == current_mode),
            AUDIO_MODE_OPTIONS[0][0],
        )
        self.audio_mode_var = ctk.StringVar(value=current_mode_label)
        ctk.CTkOptionMenu(
            body, values=[label for label, _ in AUDIO_MODE_OPTIONS], variable=self.audio_mode_var,
            command=self._on_audio_mode_change, width=420,
        ).pack(**pad)

        self.audio_source_container = ctk.CTkFrame(body, fg_color="transparent")
        self.audio_source_container.pack(fill="x", **pad)

        self.device_frame = ctk.CTkFrame(self.audio_source_container, fg_color="transparent")
        devices = capture.list_output_devices()
        current_device = self.cfg.get("audio_device") or capture.default_output_device()
        if current_device not in devices:
            devices = devices + [current_device]
        self.device_var = ctk.StringVar(value=current_device)
        ctk.CTkOptionMenu(self.device_frame, values=devices, variable=self.device_var, width=420).pack()

        self.process_frame = ctk.CTkFrame(self.audio_source_container, fg_color="transparent")
        ctk.CTkLabel(
            self.process_frame,
            text="Zaškrtni programy, z ktorých sa má nahrávať zvuk (dá sa vybrať aj viac naraz):",
        ).pack(anchor="w")
        processes = capture.list_audio_processes()
        selected_names = set(self.cfg.get("audio_process_names") or [])
        for name in selected_names:
            if name not in processes:
                processes.append(name)
        self.process_vars = {}
        if processes:
            for name in processes:
                var = ctk.BooleanVar(value=name in selected_names)
                ctk.CTkCheckBox(self.process_frame, text=name, variable=var).pack(anchor="w", pady=(4, 0))
                self.process_vars[name] = var
        else:
            ctk.CTkLabel(
                self.process_frame, text="(žiadny program s zvukom momentálne nebeží)", text_color="gray",
            ).pack(anchor="w", pady=(4, 0))
        ctk.CTkLabel(
            self.process_frame,
            text="Ak program v zozname chýba, spusti v ňom zvuk/video a otvor Nastavenia znova.",
            text_color="gray", wraplength=420, justify="left",
        ).pack(anchor="w", pady=(6, 0))

        self._show_audio_mode_panel(current_mode)

        ctk.CTkLabel(body, text="Zdroj obrazu").pack(anchor="w", **pad)
        current_video_mode = self.cfg.get("video_source_mode", "all_monitors")
        current_video_mode_label = next(
            (label for label, code in VIDEO_MODE_OPTIONS if code == current_video_mode),
            VIDEO_MODE_OPTIONS[0][0],
        )
        self.video_mode_var = ctk.StringVar(value=current_video_mode_label)
        ctk.CTkOptionMenu(
            body, values=[label for label, _ in VIDEO_MODE_OPTIONS], variable=self.video_mode_var,
            command=self._on_video_mode_change, width=420,
        ).pack(**pad)

        self.video_source_container = ctk.CTkFrame(body, fg_color="transparent")
        self.video_source_container.pack(fill="x", **pad)

        self.monitor_frame = ctk.CTkFrame(self.video_source_container, fg_color="transparent")
        self._monitors = capture.list_monitors()
        monitor_labels = [label for _, label in self._monitors] or ["(nenašiel sa žiadny monitor)"]
        current_monitor_index = self.cfg.get("monitor_index", 1)
        current_monitor_label = next(
            (label for idx, label in self._monitors if idx == current_monitor_index), monitor_labels[0]
        )
        self.monitor_var = ctk.StringVar(value=current_monitor_label)
        ctk.CTkOptionMenu(self.monitor_frame, values=monitor_labels, variable=self.monitor_var, width=420).pack()

        self.region_frame = ctk.CTkFrame(self.video_source_container, fg_color="transparent")
        self.selected_region = self.cfg.get("screen_region")
        self.region_status_label = ctk.CTkLabel(self.region_frame, text=self._region_status_text())
        self.region_status_label.pack(anchor="w")
        ctk.CTkButton(self.region_frame, text="Vybrať oblasť...", command=self._pick_region).pack(
            anchor="w", pady=(6, 0)
        )

        self._show_video_mode_panel(current_video_mode)

        ctk.CTkLabel(body, text="Kvalita videa (pre režim \"Len nahrávanie videa\")").pack(anchor="w", **pad)
        video_settings_frame = ctk.CTkFrame(body, fg_color="transparent")
        video_settings_frame.pack(fill="x", padx=16, pady=(4, 0))
        ctk.CTkLabel(video_settings_frame, text="FPS:").pack(side="left")
        self.video_fps_var = ctk.StringVar(value=str(self.cfg.get("video_fps", 10)))
        ctk.CTkOptionMenu(
            video_settings_frame, values=VIDEO_FPS_OPTIONS, variable=self.video_fps_var, width=80
        ).pack(side="left", padx=(6, 16))
        ctk.CTkLabel(video_settings_frame, text="Kvalita:").pack(side="left")
        current_quality = self.cfg.get("video_quality", "medium")
        current_quality_label = next(
            (label for label, code in VIDEO_QUALITY_OPTIONS if code == current_quality), "Stredná"
        )
        self.video_quality_var = ctk.StringVar(value=current_quality_label)
        ctk.CTkOptionMenu(
            video_settings_frame, values=[label for label, _ in VIDEO_QUALITY_OPTIONS],
            variable=self.video_quality_var, width=200,
        ).pack(side="left", padx=(6, 0))

        self.video_include_audio_var = ctk.BooleanVar(value=self.cfg.get("video_include_audio", False))
        ctk.CTkCheckBox(
            body, text="Nahrávať aj zvuk (podľa nastavenia \"Zdroj zvuku\" vyššie)",
            variable=self.video_include_audio_var,
        ).pack(anchor="w", padx=16, pady=(8, 0))

        ctk.CTkLabel(body, text="Zabezpečenie výstupu").pack(anchor="w", **pad)
        self.encrypt_var = ctk.BooleanVar(value=self.cfg.get("encrypt_output", False))
        ctk.CTkCheckBox(
            body, text="Zašifrovať výstupný súbor (otvorí len Bot Z / Aurion)",
            variable=self.encrypt_var, command=self._on_encrypt_toggle,
        ).pack(anchor="w", padx=16, pady=(4, 0))

        self.encrypt_frame = ctk.CTkFrame(body, fg_color="transparent")
        ctk.CTkLabel(
            self.encrypt_frame, text="Verejný kľúč Bot Z / Aurion (RSA, PEM text — vlož celý)",
        ).pack(anchor="w")
        self.public_key_textbox = ctk.CTkTextbox(self.encrypt_frame, width=420, height=110)
        self.public_key_textbox.insert("1.0", self.cfg.get("encryption_public_key_pem", ""))
        self.public_key_textbox.pack(anchor="w", pady=(4, 0))
        ctk.CTkLabel(
            self.encrypt_frame,
            text="Toto je VEREJNÝ kľúč (bezpečné vložiť/zdieľať) — s ním sa dá LEN šifrovať. "
                 "Súkromný kľúč sem nikdy nepatrí.",
            text_color="gray", wraplength=420, justify="left", font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(2, 0))
        if self.encrypt_var.get():
            self.encrypt_frame.pack(fill="x", padx=16, pady=(6, 0))

        self.keep_temp_var = ctk.BooleanVar(value=self.cfg.get("keep_temp_files", False))
        ctk.CTkCheckBox(
            body, text="Ponechať dočasné súbory (screenshoty, zvuk)", variable=self.keep_temp_var
        ).pack(anchor="w", pady=(10, 10), padx=16)

        ctk.CTkButton(
            body, text="Vytvoriť odkaz na pracovnej ploche", width=260,
            fg_color="gray35", hover_color="gray25", command=self._on_create_shortcut,
        ).pack(anchor="w", pady=(4, 20), padx=16)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(8, 16))
        ctk.CTkButton(btn_frame, text="Zrušiť", fg_color="gray", command=self.destroy).pack(side="right")
        ctk.CTkButton(btn_frame, text="Uložiť", command=self._save).pack(side="right", padx=(0, 8))

    def _on_create_shortcut(self):
        try:
            shortcut_path = paths.create_desktop_shortcut()
            messagebox.showinfo(
                "Odkaz na ploche", f"Odkaz vytvorený: {shortcut_path}", parent=self
            )
        except Exception as exc:
            messagebox.showerror("Odkaz na ploche", str(exc), parent=self)

    def _on_encrypt_toggle(self):
        if self.encrypt_var.get():
            self.encrypt_frame.pack(fill="x", padx=16, pady=(6, 0))
        else:
            self.encrypt_frame.pack_forget()

    def _on_audio_mode_change(self, label):
        mode = next((code for lbl, code in AUDIO_MODE_OPTIONS if lbl == label), "device")
        self._show_audio_mode_panel(mode)

    def _show_audio_mode_panel(self, mode):
        self.device_frame.pack_forget()
        self.process_frame.pack_forget()
        if mode == "process":
            self.process_frame.pack(fill="x")
        else:
            self.device_frame.pack(fill="x")

    def _region_status_text(self):
        if self.selected_region:
            r = self.selected_region
            return f"Vybraná oblasť: {r['width']}×{r['height']} px, pozícia ({r['left']}, {r['top']})"
        return "Zatiaľ nie je vybraná žiadna oblasť."

    def _pick_region(self):
        self.withdraw()
        region = select_region_interactively(self.master)
        self.deiconify()
        self.lift()
        if region:
            self.selected_region = region
            self.region_status_label.configure(text=self._region_status_text())

    def _on_video_mode_change(self, label):
        mode = next((code for lbl, code in VIDEO_MODE_OPTIONS if lbl == label), "all_monitors")
        self._show_video_mode_panel(mode)

    def _show_video_mode_panel(self, mode):
        self.monitor_frame.pack_forget()
        self.region_frame.pack_forget()
        if mode == "monitor":
            self.monitor_frame.pack(fill="x")
        elif mode == "region":
            self.region_frame.pack(fill="x")

    def _browse_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.output_dir_entry.get() or str(Path.home()))
        if chosen:
            self.output_dir_entry.delete(0, "end")
            self.output_dir_entry.insert(0, chosen)

    def _save(self):
        try:
            interval = int(self.interval_entry.get())
            if interval < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Chyba", "Interval snímok musí byť celé číslo väčšie ako 0.")
            return

        lang_label = self.language_var.get()
        lang_code = next((code for label, code in LANGUAGE_OPTIONS if label == lang_label), "auto")

        mode_label = self.audio_mode_var.get()
        audio_mode = next((code for label, code in AUDIO_MODE_OPTIONS if label == mode_label), "device")
        selected_processes = [name for name, var in self.process_vars.items() if var.get()]
        if audio_mode == "process" and not selected_processes:
            messagebox.showerror("Chyba", "Zaškrtni aspoň jeden program, z ktorého sa má nahrávať zvuk.")
            return

        video_mode_label = self.video_mode_var.get()
        video_mode = next(
            (code for label, code in VIDEO_MODE_OPTIONS if label == video_mode_label), "all_monitors"
        )
        if video_mode == "region" and not self.selected_region:
            messagebox.showerror("Chyba", "Najprv klikni na \"Vybrať oblasť...\" a vyznač oblasť myšou.")
            return
        monitor_label = self.monitor_var.get()
        monitor_index = next((idx for idx, label in self._monitors if label == monitor_label), 1)

        ai_model_label = self.ai_model_var.get()
        ai_model = next(
            (code for label, code in analyzer.MODEL_OPTIONS if label == ai_model_label), analyzer.DEFAULT_MODEL
        )

        try:
            cost_limit_usd = float(self.cost_limit_entry.get().replace(",", "."))
            if cost_limit_usd < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Chyba", "Limit ceny musí byť číslo 0 alebo väčšie.")
            return

        video_fps = int(self.video_fps_var.get())
        quality_label = self.video_quality_var.get()
        video_quality = next(
            (code for label, code in VIDEO_QUALITY_OPTIONS if label == quality_label), "medium"
        )
        video_include_audio = self.video_include_audio_var.get()

        encrypt_output = self.encrypt_var.get()
        public_key_pem = self.public_key_textbox.get("1.0", "end").strip()
        if encrypt_output:
            if not public_key_pem:
                messagebox.showerror(
                    "Chyba", "Vlož verejný kľúč Bot Z / Aurion pre šifrovanie výstupu."
                )
                return
            try:
                security.load_public_key(public_key_pem)
            except Exception:
                messagebox.showerror(
                    "Chyba",
                    "Toto nevyzerá ako platný RSA verejný kľúč (PEM formát). "
                    "Skontroluj, že si vložil celý text vrátane -----BEGIN/END----- riadkov.",
                )
                return

        self.cfg.update({
            "api_key": self.api_key_entry.get().strip(),
            "output_dir": self.output_dir_entry.get().strip(),
            "screenshot_interval": interval,
            "whisper_model": self.model_var.get(),
            "language": lang_code,
            "ai_model": ai_model,
            "cost_limit_usd": cost_limit_usd,
            "audio_source_mode": audio_mode,
            "audio_device": self.device_var.get(),
            "audio_process_names": selected_processes,
            "video_source_mode": video_mode,
            "monitor_index": monitor_index,
            "screen_region": self.selected_region,
            "video_fps": video_fps,
            "video_quality": video_quality,
            "video_include_audio": video_include_audio,
            "encrypt_output": encrypt_output,
            "encryption_public_key_pem": public_key_pem,
            "keep_temp_files": self.keep_temp_var.get(),
        })
        config_module.save(self.cfg)
        self.on_save(self.cfg)
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("StrategyScribe")
        self.geometry("640x560")
        self.minsize(560, 460)

        self.cfg = config_module.load()
        self.msg_queue = queue.Queue()
        self.recording = False
        self.processing = False
        self.screenshot_capture = None
        self.audio_capture = None
        self.video_capture = None
        self.app_mode_for_run = "full"
        self.run_temp_dir = None
        self.course_name_for_run = ""
        self.append_target_for_run = None
        self.last_sound_time = None
        self.silence_warning_shown = False
        self.output_path = None
        self.session_cost = 0.0

        self._build_widgets()
        self.recording_indicator = RecordingIndicator(self)
        self.after(50, self._poll_queue)
        self.after(800, self._maybe_offer_desktop_shortcut)

    def _maybe_offer_desktop_shortcut(self):
        """Pri úplne prvom spustení zabalenej appky raz ponúkne vytvorenie odkazu
        na pracovnej ploche (nič sa nevytvára bez súhlasu používateľa)."""
        if not getattr(sys, "frozen", False) or self.cfg.get("desktop_shortcut_offered"):
            return
        self.cfg["desktop_shortcut_offered"] = True
        config_module.save(self.cfg)
        if messagebox.askyesno(
            "Odkaz na ploche",
            "Chceš vytvoriť odkaz na StrategyScribe na pracovnej ploche?\n\n"
            "(Kedykoľvek neskôr sa dá vytvoriť v Nastaveniach.)",
        ):
            self._create_desktop_shortcut()

    def _create_desktop_shortcut(self):
        try:
            shortcut_path = paths.create_desktop_shortcut()
            self._append_log(f"Odkaz na ploche vytvorený: {shortcut_path.name}")
            return True
        except Exception as exc:
            messagebox.showerror("Odkaz na ploche", str(exc))
            return False

    def _build_widgets(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(top, text="Názov kurzu (nepovinné)").pack(side="left")
        self.course_name_entry = ctk.CTkEntry(top, width=280)
        self.course_name_entry.pack(side="left", padx=(8, 0))
        ctk.CTkButton(top, text="Nastavenia", width=100, command=self._open_settings).pack(side="right")

        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(mode_frame, text="Režim:").pack(side="left")
        current_app_mode_label = next(
            (label for label, code in APP_MODE_OPTIONS if code == self.cfg.get("app_mode", "full")),
            APP_MODE_OPTIONS[0][0],
        )
        self.app_mode_var = ctk.StringVar(value=current_app_mode_label)
        ctk.CTkOptionMenu(
            mode_frame, values=[label for label, _ in APP_MODE_OPTIONS], variable=self.app_mode_var,
            command=self._on_app_mode_change, width=320,
        ).pack(side="left", padx=(8, 0))

        self.append_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.append_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self.append_frame, text="Pridať do existujúcej stratégie (namiesto novej)",
            variable=self.append_var, command=self._on_append_toggle,
        ).pack(side="left")
        self.append_file_btn = ctk.CTkButton(
            self.append_frame, text="Vybrať súbor...", width=110, command=self._pick_append_target,
        )
        self.docs_btn = ctk.CTkButton(
            self.append_frame, text="Doplniť z dokumentov (PDF/Word)...", width=230,
            fg_color="gray35", hover_color="gray25", command=self._on_add_documents,
        )
        self.docs_btn.pack(side="right")
        self.append_target_path = None
        self.append_target_label = ctk.CTkLabel(self, text="", text_color="gray70")
        self._update_append_visibility()

        self.start_stop_btn = ctk.CTkButton(
            self, text="Štart", height=48, font=ctk.CTkFont(size=18, weight="bold"),
            command=self._on_start_stop,
        )
        self.start_stop_btn.pack(fill="x", padx=20, pady=10)
        self._default_btn_fg = self.start_stop_btn.cget("fg_color")
        self._default_btn_hover = self.start_stop_btn.cget("hover_color")

        vu_frame = ctk.CTkFrame(self, fg_color="transparent")
        vu_frame.pack(fill="x", padx=20, pady=(0, 4))
        ctk.CTkLabel(vu_frame, text="Hlasitosť zvuku").pack(anchor="w")
        self.vu_meter = ctk.CTkProgressBar(vu_frame)
        self.vu_meter.set(0)
        self.vu_meter.pack(fill="x", pady=(4, 0))

        self.silence_warning_label = ctk.CTkLabel(
            self,
            text="Nezachytávam žiadny zvuk — skontroluj zvolený zdroj zvuku v Nastaveniach.",
            text_color="orange",
        )

        self.status_label = ctk.CTkLabel(self, text="Pripravené.", font=ctk.CTkFont(size=14))
        self.status_label.pack(anchor="w", padx=20, pady=(10, 4))

        self.cost_label = ctk.CTkLabel(
            self, text="Odhadovaná cena tejto relácie: $0.000", text_color="gray70",
        )
        self.cost_label.pack(anchor="w", padx=20, pady=(0, 4))

        self.log_box = ctk.CTkTextbox(self, height=220)
        self.log_box.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.log_box.configure(state="disabled")

        self.output_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.output_label = ctk.CTkLabel(self.output_frame, text="")
        self.output_label.pack(side="left")
        ctk.CTkButton(
            self.output_frame, text="Otvoriť priečinok s výstupom", command=self._open_output_folder,
        ).pack(side="right")

    # ---------- helpers callable from the worker thread ----------

    def _log(self, text):
        self.msg_queue.put(("log", text))

    def _set_status(self, text):
        self.msg_queue.put(("status", text))

    def _add_cost(self, delta_usd):
        self.msg_queue.put(("cost", delta_usd))

    # ---------- mode selection ----------

    def _on_app_mode_change(self, label):
        mode = next((code for lbl, code in APP_MODE_OPTIONS if lbl == label), "full")
        self.cfg["app_mode"] = mode
        config_module.save(self.cfg)
        self._update_append_visibility()

    def _update_append_visibility(self):
        mode = self.cfg.get("app_mode", "full")
        if mode == "full":
            self.append_frame.pack(fill="x", padx=20, pady=(0, 4))
            if self.append_var.get():
                self.append_file_btn.pack(side="left", padx=(10, 0))
                self.append_target_label.pack(anchor="w", padx=20, pady=(0, 8))
        else:
            self.append_frame.pack_forget()
            self.append_file_btn.pack_forget()
            self.append_target_label.pack_forget()

    def _on_append_toggle(self):
        if self.append_var.get():
            self.append_file_btn.pack(side="left", padx=(10, 0))
            if self.append_target_path:
                self.append_target_label.pack(anchor="w", padx=20, pady=(0, 8))
        else:
            self.append_file_btn.pack_forget()
            self.append_target_label.pack_forget()

    def _on_add_documents(self):
        """Doplnenie existujúcej stratégie z dokumentov (PDF/Word/text) — bez nahrávania."""
        if self.recording or self.processing:
            messagebox.showinfo(
                "Prebieha práca", "Počkaj, kým skončí aktuálne nahrávanie alebo spracovanie."
            )
            return
        if not self.cfg.get("api_key"):
            messagebox.showwarning("Chýba API kľúč", "Najprv zadaj Claude API kľúč v Nastaveniach.")
            return

        if self.append_var.get() and self.append_target_path:
            target = self.append_target_path
        else:
            chosen = filedialog.askopenfilename(
                title="Vyber súbor so stratégiou, ktorú chceš doplniť",
                filetypes=[("Markdown súbory", "*.md"), ("Textové súbory", "*.txt"), ("Všetky súbory", "*.*")],
            )
            if not chosen:
                return
            target = Path(chosen)
        if target.suffix == security.FILE_EXTENSION:
            messagebox.showerror(
                "Zašifrovaný súbor",
                "Vybraný súbor je zašifrovaný — appka ho nevie späť načítať (nemá súkromný "
                "kľúč). Dopĺňanie funguje len s nešifrovanými .md súbormi.",
            )
            return

        chosen_docs = filedialog.askopenfilenames(
            title="Vyber dokumenty na doplnenie stratégie (môžeš označiť viac naraz)",
            filetypes=[
                ("Dokumenty (PDF, Word, text)", "*.pdf *.docx *.txt *.md"),
                ("PDF súbory", "*.pdf"),
                ("Word dokumenty", "*.docx"),
                ("Textové súbory", "*.txt *.md"),
            ],
        )
        doc_paths = [Path(d) for d in chosen_docs if Path(d) != target]
        if not doc_paths:
            return

        self.processing = True
        self.output_frame.pack_forget()
        self.start_stop_btn.configure(state="disabled")
        self.docs_btn.configure(state="disabled")
        self.session_cost = 0.0
        self.cost_label.configure(text="Odhadovaná cena tejto relácie: $0.000")
        self._set_status("Spracúvam dokumenty...")
        self._log(f"Dopĺňam stratégiu {target.name} z {len(doc_paths)} dokumentov...")
        threading.Thread(
            target=self._run_documents_pipeline, args=(target, doc_paths), daemon=True
        ).start()

    def _run_documents_pipeline(self, target, doc_paths):
        try:
            self._process_documents(target, doc_paths)
        except Exception as exc:
            self.msg_queue.put(("error", str(exc)))
        finally:
            self.msg_queue.put(("pipeline_done", None))

    def _process_documents(self, target, doc_paths):
        run_cfg = dict(self.cfg)
        try:
            previous_text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"Nepodarilo sa načítať súbor so stratégiou: {exc}")

        client = anthropic.Anthropic(api_key=run_cfg["api_key"])
        ai_model = run_cfg.get("ai_model", analyzer.DEFAULT_MODEL)
        cost_limit = run_cfg.get("cost_limit_usd", 0.0)
        worker_cost_total = 0.0
        notes = []
        stopped_on_limit = False
        for doc_path in doc_paths:
            self._log(f"Čítam dokument: {doc_path.name}...")
            parts = documents.load_document_parts(doc_path)
            for j, part in enumerate(parts, start=1):
                if cost_limit > 0 and worker_cost_total >= cost_limit:
                    self._log(
                        f"Dosiahnutý limit ceny (${cost_limit:.2f}) — zvyšok dokumentov "
                        "sa nebude analyzovať."
                    )
                    stopped_on_limit = True
                    break
                self._log(f"Analyzujem {doc_path.name} — časť {j}/{len(parts)}...")
                note, usage = analyzer.analyze_document_part(
                    client, part, doc_path.name, model=ai_model
                )
                cost_delta = analyzer.estimate_cost_usd(usage, ai_model)
                worker_cost_total += cost_delta
                self._add_cost(cost_delta)
                if note:
                    notes.append(f"Z dokumentu „{doc_path.name}“:\n{note}")
            if stopped_on_limit:
                break

        if not notes:
            raise RuntimeError(
                "Z dokumentov sa nepodarilo získať žiadne konkrétne pravidlá stratégie."
            )

        self._log(f"Získaných poznámok: {len(notes)}. Zlučujem do stratégie {target.name}...")
        final_text, merge_usage = analyzer.merge_notes(client, previous_text, notes, model=ai_model)
        self._add_cost(analyzer.estimate_cost_usd(merge_usage, ai_model))

        self._log("Zapisujem zhrnutie zmien tejto aktualizácie...")
        changelog_text, changelog_usage = analyzer.summarize_changes(
            client, previous_text, final_text, model=ai_model
        )
        self._add_cost(analyzer.estimate_cost_usd(changelog_usage, ai_model))

        date_part = datetime.now().strftime("%Y-%m-%d_%H-%M")
        safe_name = re.sub(r"[^\w\-]+", "_", target.stem, flags=re.UNICODE).strip("_") or "strategia"
        target.write_text(final_text, encoding="utf-8")

        if run_cfg.get("encrypt_output"):
            encrypted_path = target.with_name(f"{safe_name}_{date_part}{security.FILE_EXTENSION}")
            encrypted = security.encrypt_text(final_text, run_cfg["encryption_public_key_pem"])
            encrypted_path.write_bytes(encrypted)
            self._log(
                f"Zašifrovaná kópia pre Bot Z / Aurion uložená: {encrypted_path.name} "
                "(pracovná .md kópia zostáva čitateľná, aby sa dalo nabudúce pridávať ďalej)."
            )

        changelog_path = target.with_name(f"{safe_name}_zmeny_{date_part}.docx")
        docx_export.save_as_docx(changelog_text, changelog_path)
        self._log(f"Zhrnutie zmien uložené: {changelog_path.name}")

        self.msg_queue.put(("done", str(target)))

    def _pick_append_target(self):
        chosen = filedialog.askopenfilename(
            title="Vyber existujúci súbor so stratégiou",
            filetypes=[("Markdown", "*.md"), ("Všetky súbory", "*.*")],
        )
        if chosen:
            self.append_target_path = Path(chosen)
            self.append_target_label.configure(text=f"Pridá sa do: {self.append_target_path.name}")
            self.append_target_label.pack(anchor="w", padx=20, pady=(0, 8))

    # ---------- Start / Stop ----------

    def _on_start_stop(self):
        if self.recording:
            self._stop_recording()
        elif not self.processing:
            self._start_recording()

    def _start_recording(self):
        app_mode = self.cfg.get("app_mode", "full")
        if app_mode in ("full", "research") and not self.cfg.get("api_key"):
            messagebox.showwarning("Chýba API kľúč", "Najprv zadaj Claude API kľúč v Nastaveniach.")
            return

        self.output_frame.pack_forget()
        self.run_temp_dir = paths.get_temp_dir() / datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.run_temp_dir.mkdir(parents=True, exist_ok=True)
        self.app_mode_for_run = app_mode

        needs_audio = app_mode in ("full", "research", "audio_only") or (
            app_mode == "video_only" and self.cfg.get("video_include_audio")
        )
        self.audio_capture = None
        if needs_audio:
            audio_mode = self.cfg.get("audio_source_mode", "device")
            if audio_mode == "process":
                self.audio_capture = capture.MultiProcessAudioCapture(
                    self.run_temp_dir / "audio.wav",
                    process_names=self.cfg.get("audio_process_names") or [],
                    on_level=self._on_audio_level,
                )
            else:
                self.audio_capture = capture.AudioCapture(
                    self.run_temp_dir / "audio.wav",
                    device_name=self.cfg.get("audio_device"),
                    on_level=self._on_audio_level,
                )
            try:
                self.audio_capture.start()
            except RuntimeError as exc:
                messagebox.showerror("Chyba zvuku", str(exc))
                self.audio_capture = None
                return

        self.screenshot_capture = None
        self.video_capture = None
        if app_mode in ("full", "research"):
            interval = self.cfg.get("screenshot_interval", 10)
            video_mode = self.cfg.get("video_source_mode", "all_monitors")
            if video_mode == "region" and self.cfg.get("screen_region"):
                self.screenshot_capture = capture.ScreenshotCapture(
                    self.run_temp_dir, interval_seconds=interval, region=self.cfg["screen_region"]
                )
            elif video_mode == "monitor":
                self.screenshot_capture = capture.ScreenshotCapture(
                    self.run_temp_dir, interval_seconds=interval, monitor_index=self.cfg.get("monitor_index", 1)
                )
            else:
                self.screenshot_capture = capture.ScreenshotCapture(self.run_temp_dir, interval_seconds=interval)
            self.screenshot_capture.start()
        elif app_mode == "video_only":
            video_mode = self.cfg.get("video_source_mode", "all_monitors")
            region = self.cfg.get("screen_region") if video_mode == "region" else None
            monitor_index = self.cfg.get("monitor_index", 1) if video_mode == "monitor" else 0
            self.video_capture = capture.VideoCapture(
                self.run_temp_dir / "video.mp4",
                fps=self.cfg.get("video_fps", 10),
                quality=self.cfg.get("video_quality", "medium"),
                monitor_index=monitor_index,
                region=region,
            )
            self.video_capture.start()

        self.recording = True
        self.recording_indicator.show()
        self.last_sound_time = time.monotonic()
        self.silence_warning_shown = False
        self.silence_warning_label.pack_forget()
        self.start_stop_btn.configure(text="Stop", fg_color="#b3261e", hover_color="#8c1d17")
        self.docs_btn.configure(state="disabled")
        self.session_cost = 0.0
        self.cost_label.configure(text="Odhadovaná cena tejto relácie: $0.000")
        self._set_status("Nahrávam...")
        self._log("Nahrávanie spustené.")

    def _on_audio_level(self, level):
        self.msg_queue.put(("level", level))

    def _stop_recording(self):
        self.recording = False
        self.recording_indicator.hide()
        self.course_name_for_run = self.course_name_entry.get().strip()
        self.append_target_for_run = self.append_target_path if self.append_var.get() else None
        self.start_stop_btn.configure(state="disabled", text="Spracúvam...")
        self._set_status("Zastavujem nahrávanie...")
        if self.screenshot_capture:
            self.screenshot_capture.stop()
        if self.video_capture:
            self.video_capture.stop()
        if self.audio_capture:
            self.audio_capture.stop()
        self.processing = True
        threading.Thread(target=self._run_pipeline, daemon=True).start()

    # ---------- background pipeline (runs in a worker thread) ----------

    def _run_pipeline(self):
        try:
            if self.app_mode_for_run == "audio_only":
                self._process_audio_only()
            elif self.app_mode_for_run == "video_only":
                self._process_video_only()
            elif self.app_mode_for_run == "research":
                self._process_research_recording()
            else:
                self._process_full_recording()
        except Exception as exc:
            self.msg_queue.put(("error", str(exc)))
        finally:
            self.msg_queue.put(("pipeline_done", None))

    def _output_filename(self, run_cfg, extension):
        course_name = self.course_name_for_run or "zaznam"
        safe_name = re.sub(r"[^\w\-]+", "_", course_name, flags=re.UNICODE).strip("_") or "zaznam"
        date_part = datetime.now().strftime("%Y-%m-%d_%H-%M")
        output_dir = Path(run_cfg.get("output_dir") or paths.get_default_output_dir())
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{safe_name}_{date_part}{extension}"

    def _process_audio_only(self):
        run_cfg = dict(self.cfg)
        audio_path = self.run_temp_dir / "audio.wav"
        if not audio_path.exists():
            raise RuntimeError("Nezaznamenal sa žiadny zvuk.")

        self._set_status("Ukladám zvukový súbor...")
        self._log("Konvertujem zvuk do MP3...")
        output_path = self._output_filename(run_cfg, ".mp3")
        capture.convert_wav_to_mp3(audio_path, output_path)

        if not run_cfg.get("keep_temp_files", False):
            shutil.rmtree(self.run_temp_dir, ignore_errors=True)

        self.msg_queue.put(("done", str(output_path)))

    def _process_video_only(self):
        run_cfg = dict(self.cfg)
        video_path = self.run_temp_dir / "video.mp4"
        if not video_path.exists():
            raise RuntimeError("Nezaznamenalo sa žiadne video.")

        output_path = self._output_filename(run_cfg, ".mp4")
        audio_path = self.run_temp_dir / "audio.wav"
        if run_cfg.get("video_include_audio") and audio_path.exists():
            self._set_status("Spájam video so zvukom...")
            self._log("Spájam video a zvuk do jedného MP4 súboru...")
            capture.mux_video_audio(video_path, audio_path, output_path)
        else:
            self._set_status("Ukladám video...")
            shutil.move(str(video_path), str(output_path))

        if not run_cfg.get("keep_temp_files", False):
            shutil.rmtree(self.run_temp_dir, ignore_errors=True)

        self.msg_queue.put(("done", str(output_path)))

    def _transcribe_and_analyze_blocks(self, run_cfg, audio_path, screenshots, note_system_prompt):
        """Zdieľaná logika pre 'full' aj 'research' režim: prepis + AI analýza po
        blokoch (s limitom ceny a odhadom zostávajúceho rozpočtu). Vráti
        (notes, client, ai_model)."""
        self._set_status("Prepisujem reč...")
        self._log("Načítavam Whisper model a prepisujem zvuk (môže to chvíľu trvať)...")
        language = run_cfg.get("language", "auto")
        whisper_language = None if language == "auto" else language
        transcriber = transcribe.Transcriber(
            model_size=run_cfg.get("whisper_model", "medium"),
            language=whisper_language,
        )
        segments, detected_language = transcriber.transcribe(audio_path)
        if not segments:
            raise RuntimeError(
                "V zvukovej stope sa nenašla žiadna reč — skontroluj zvolené zvukové zariadenie."
            )
        self._log(f"Prepis hotový ({len(segments)} úsekov, jazyk: {detected_language}).")

        blocks = self._chunk_segments(segments)
        self._set_status("Analyzujem s AI...")
        client = anthropic.Anthropic(api_key=run_cfg["api_key"])
        ai_model = run_cfg.get("ai_model", analyzer.DEFAULT_MODEL)
        cost_limit = run_cfg.get("cost_limit_usd", 0.0)
        worker_cost_total = 0.0
        notes = []
        for i, (block_text, block_start, block_end) in enumerate(blocks, start=1):
            if cost_limit > 0 and worker_cost_total >= cost_limit:
                self._log(
                    f"Dosiahnutý limit ceny (${cost_limit:.2f}) — ďalšie bloky sa "
                    f"nebudú analyzovať ({len(blocks) - i + 1} zostáva)."
                )
                break
            block_screenshots = self._select_screenshots(screenshots, block_start, block_end)
            self._log(f"Analyzujem blok {i}/{len(blocks)} ({len(block_screenshots)} snímky)...")
            note, usage = analyzer.analyze_segment(
                client, block_text, block_screenshots, model=ai_model, system_prompt=note_system_prompt
            )
            cost_delta = analyzer.estimate_cost_usd(usage, ai_model)
            worker_cost_total += cost_delta
            self._add_cost(cost_delta)
            if note:
                notes.append(note)

            if cost_limit > 0:
                rate_per_second = worker_cost_total / block_end if block_end > 0 else 0
                if rate_per_second > 0:
                    remaining_seconds = (cost_limit - worker_cost_total) / rate_per_second
                    remaining_minutes = max(remaining_seconds, 0) / 60
                    self._log(
                        f"Pri tomto tempe: rozpočet (${cost_limit:.2f}) pokryje ešte "
                        f"cca {remaining_minutes:.0f} min obsahu videa."
                    )

        return notes, client, ai_model

    def _process_full_recording(self):
        run_cfg = dict(self.cfg)
        audio_path = self.run_temp_dir / "audio.wav"
        screenshots = sorted(self.run_temp_dir.glob("frame_*.png"))
        if not screenshots:
            raise RuntimeError("Nezachytil sa žiadny screenshot — nahrávka bola príliš krátka.")

        notes, client, ai_model = self._transcribe_and_analyze_blocks(
            run_cfg, audio_path, screenshots, analyzer.NOTE_SYSTEM_PROMPT
        )
        if not notes:
            raise RuntimeError("Z videa sa nepodarilo získať žiadne konkrétne pravidlá stratégie.")

        append_target = self.append_target_for_run
        previous_text = ""
        if append_target:
            if append_target.suffix == security.FILE_EXTENSION:
                raise RuntimeError(
                    "Vybraný súbor je zašifrovaný — appka ho nevie späť načítať "
                    "(nemá súkromný kľúč). Pridávanie funguje len s nešifrovanými .md súbormi."
                )
            try:
                previous_text = append_target.read_text(encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(f"Nepodarilo sa načítať existujúci súbor: {exc}")
            self._log(f"Pridávam k existujúcej stratégii: {append_target.name}...")
            final_text, synth_usage = analyzer.merge_notes(
                client, previous_text, notes, model=ai_model
            )
        else:
            self._log(f"Priebežných poznámok: {len(notes)}. Vytváram finálnu syntézu...")
            final_text, synth_usage = analyzer.synthesize_notes(
                client, notes, model=ai_model, system_prompt=analyzer.SYNTHESIS_SYSTEM_PROMPT
            )
        self._add_cost(analyzer.estimate_cost_usd(synth_usage, ai_model))

        self._log("Zapisujem zhrnutie zmien tejto aktualizácie...")
        changelog_text, changelog_usage = analyzer.summarize_changes(
            client, previous_text, final_text, model=ai_model
        )
        self._add_cost(analyzer.estimate_cost_usd(changelog_usage, ai_model))

        date_part = datetime.now().strftime("%Y-%m-%d_%H-%M")

        if append_target:
            output_path = append_target
            safe_name = re.sub(r"[^\w\-]+", "_", append_target.stem, flags=re.UNICODE).strip("_") or "strategia"
        else:
            output_dir = Path(run_cfg.get("output_dir") or paths.get_default_output_dir())
            output_dir.mkdir(parents=True, exist_ok=True)
            course_name = self.course_name_for_run or "kurz"
            safe_name = re.sub(r"[^\w\-]+", "_", course_name, flags=re.UNICODE).strip("_") or "kurz"
            output_path = output_dir / f"{safe_name}_{date_part}.md"

        # Vždy sa zapíše čitateľná .md verzia — je to "pracovná" kópia, z ktorej sa
        # dá nabudúce pridávať ďalej (appka nevie čítať vlastný zašifrovaný výstup).
        output_path.write_text(final_text, encoding="utf-8")

        if run_cfg.get("encrypt_output"):
            encrypted_path = output_path.with_name(f"{safe_name}_{date_part}{security.FILE_EXTENSION}")
            encrypted = security.encrypt_text(final_text, run_cfg["encryption_public_key_pem"])
            encrypted_path.write_bytes(encrypted)
            self._log(
                f"Zašifrovaná kópia pre Bot Z / Aurion uložená: {encrypted_path.name} "
                "(pracovná .md kópia zostáva čitateľná, aby sa dalo nabudúce pridávať ďalej)."
            )

        changelog_path = output_path.with_name(f"{safe_name}_zmeny_{date_part}.docx")
        docx_export.save_as_docx(changelog_text, changelog_path)
        self._log(f"Zhrnutie zmien uložené: {changelog_path.name}")

        if not run_cfg.get("keep_temp_files", False):
            shutil.rmtree(self.run_temp_dir, ignore_errors=True)

        self.msg_queue.put(("done", str(output_path)))

    def _process_research_recording(self):
        run_cfg = dict(self.cfg)
        audio_path = self.run_temp_dir / "audio.wav"
        screenshots = sorted(self.run_temp_dir.glob("frame_*.png"))
        if not screenshots:
            raise RuntimeError("Nezachytil sa žiadny screenshot — nahrávka bola príliš krátka.")

        notes, client, ai_model = self._transcribe_and_analyze_blocks(
            run_cfg, audio_path, screenshots, analyzer.RESEARCH_NOTE_SYSTEM_PROMPT
        )
        if not notes:
            raise RuntimeError("Z videa sa nepodarilo získať žiadny podstatný obsah.")

        self._log(f"Priebežných poznámok: {len(notes)}. Vytváram finálne zhrnutie...")
        final_text, synth_usage = analyzer.synthesize_notes(
            client, notes, model=ai_model, system_prompt=analyzer.RESEARCH_SYNTHESIS_SYSTEM_PROMPT
        )
        self._add_cost(analyzer.estimate_cost_usd(synth_usage, ai_model))

        output_path = self._output_filename(run_cfg, ".docx")
        docx_export.save_as_docx(final_text, output_path)
        self._log("Zhrnutie uložené ako Word dokument.")

        if not run_cfg.get("keep_temp_files", False):
            shutil.rmtree(self.run_temp_dir, ignore_errors=True)

        self.msg_queue.put(("done", str(output_path)))

    def _chunk_segments(self, segments, block_seconds=BLOCK_SECONDS):
        blocks = []
        current_texts = []
        block_start = None
        last_end = 0
        for seg in segments:
            if block_start is None:
                block_start = seg.start
            current_texts.append(seg.text)
            last_end = seg.end
            if seg.end - block_start >= block_seconds:
                blocks.append((" ".join(current_texts), block_start, last_end))
                current_texts = []
                block_start = None
        if current_texts:
            blocks.append((" ".join(current_texts), block_start, last_end))
        return blocks

    def _nearest_screenshot(self, screenshots, target_seconds):
        def elapsed_of(path):
            return float(path.stem.split("_", 1)[1])
        return min(screenshots, key=lambda p: abs(elapsed_of(p) - target_seconds))

    def _select_screenshots(self, screenshots, block_start, block_end, count=SCREENSHOTS_PER_BLOCK):
        if count <= 1 or block_end <= block_start:
            return [self._nearest_screenshot(screenshots, (block_start + block_end) / 2)]
        chosen = []
        for i in range(count):
            target = block_start + (block_end - block_start) * (i + 0.5) / count
            shot = self._nearest_screenshot(screenshots, target)
            if shot not in chosen:
                chosen.append(shot)
        return chosen

    # ---------- settings ----------

    def _open_settings(self):
        SettingsDialog(self, self.cfg, on_save=self._on_settings_saved)

    def _on_settings_saved(self, new_cfg):
        self.cfg = new_cfg
        self._log("Nastavenia uložené.")

    # ---------- output folder ----------

    def _open_output_folder(self):
        if self.output_path:
            os.startfile(str(Path(self.output_path).parent))

    # ---------- queue polling (runs on the main/GUI thread) ----------

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                self._handle_message(kind, payload)
        except queue.Empty:
            pass

        if self.recording and self.audio_capture is not None and self.last_sound_time is not None:
            silent_for = time.monotonic() - self.last_sound_time
            if silent_for > SILENCE_WARNING_SECONDS and not self.silence_warning_shown:
                self.silence_warning_label.pack(anchor="w", padx=20, pady=(0, 4))
                self.silence_warning_shown = True

        self.after(50, self._poll_queue)

    def _handle_message(self, kind, payload):
        if kind == "log":
            self._append_log(payload)
        elif kind == "status":
            self.status_label.configure(text=payload)
        elif kind == "level":
            self.vu_meter.set(min(max(payload, 0.0), 1.0))
            if payload > SILENCE_THRESHOLD:
                self.last_sound_time = time.monotonic()
                if self.silence_warning_shown:
                    self.silence_warning_label.pack_forget()
                    self.silence_warning_shown = False
        elif kind == "cost":
            self.session_cost += payload
            self.cost_label.configure(text=f"Odhadovaná cena tejto relácie: ${self.session_cost:.3f}")
        elif kind == "done":
            self.output_path = payload
            self._set_status("Hotovo!")
            self._append_log(f"Výstup uložený: {payload}")
            self._append_log(f"Celková odhadovaná cena tejto relácie: ${self.session_cost:.3f}")
            self.output_label.configure(text=f"Výstup: {payload}")
            self.output_frame.pack(fill="x", padx=20, pady=(0, 16))
        elif kind == "error":
            self._set_status("Chyba.")
            self._append_log(f"CHYBA: {payload}")
            messagebox.showerror("Chyba", payload)
        elif kind == "pipeline_done":
            self.processing = False
            self.start_stop_btn.configure(
                state="normal", text="Štart",
                fg_color=self._default_btn_fg, hover_color=self._default_btn_hover,
            )
            self.docs_btn.configure(state="normal")
            self.vu_meter.set(0)
            self.silence_warning_label.pack_forget()
            self.silence_warning_shown = False

    def _append_log(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{timestamp}] {text}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


def run():
    app = App()
    app.mainloop()
