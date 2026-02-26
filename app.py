"""
Voice Assistant Pro — Complete Application
A single-file voice-to-text desktop application with:
  - CustomTkinter UI with dark theme
  - Global hotkey capture
  - Microphone selection
  - Floating waveform overlay
  - System tray support
  - Autostart with Windows
  - Lazy AI model loading (non-blocking)
"""

import customtkinter as ctk
import sounddevice as sd
import numpy as np
import threading
import json
import os
import sys
import time
import tempfile
import re
import math
import glob
import tkinter as tk
from tkinter import filedialog
from datetime import datetime, timedelta
import ctypes
import traceback

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug.txt")

def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} - {msg}\n")
    except Exception:
        pass
    print(msg)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    log("Uncaught exception:")
    log("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))

sys.excepthook = handle_exception
# ═══════════════════════════════════════════════════════════════
# CONFIG MANAGER
# ═══════════════════════════════════════════════════════════════

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "hotkey": "ctrl+shift+space",
    "language": "auto",
    "delay_before_stop_ms": 300,
    "timeout_without_holding_min": 9,
    "duration_threshold_ms": 400,
    "silence_threshold_ms": 500,
    "auto_paste": True,
    "auto_enable_mic": True,
    "show_floating_button": True,
    "floating_btn_size": 60,
    "floating_btn_opacity": 0.85,
    "floating_btn_position": "Правый нижний",
    "save_recordings_path": os.path.join(os.path.expanduser("~"), "VoiceAssistant", "recordings"),
    "microphone": "По умолчанию",
    "replacements": [],
    "ai_enabled": True,
    "ai_api_key": "",
    "ai_model": "gpt-4o-mini",
    "ai_prompt": "Ты — корректор текста. Тебе дают расшифровку голосовой записи. Твоя задача — ТОЛЬКО исправить грамматику, пунктуацию и опечатки. Верни ТОЛЬКО исправленный текст, ничего не добавляй, не отвечай на содержимое, не комментируй. Сохрани оригинальный смысл и стиль.",
    "autostart": False,
    "recording_retention_days": 0,
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            merged = {**DEFAULT_CONFIG, **saved}
            return merged
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


CFG = load_config()

# ═══════════════════════════════════════════════════════════════
# AUTOSTART (Windows Registry)
# ═══════════════════════════════════════════════════════════════

def _get_app_path():
    """Return the command to launch this app."""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --autostart'
    else:
        # Use pythonw.exe to avoid showing a console window
        python = sys.executable
        if python.lower().endswith('python.exe'):
            pythonw = python[:-len('python.exe')] + 'pythonw.exe'
            if os.path.exists(pythonw):
                python = pythonw
        script = os.path.abspath(__file__)
        return f'"{python}" "{script}" --autostart'


def set_autostart(enable: bool):
    """Add/remove this app from Windows autostart via registry."""
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "VoiceAssistantPro"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, _get_app_path())
        else:
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        print(f"[Autostart] {'Enabled' if enable else 'Disabled'}")
    except Exception as e:
        print(f"[Autostart] Error: {e}")

def is_autostart_enabled() -> bool:
    """Check if app is in Windows autostart registry."""
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "VoiceAssistantPro"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, app_name)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        print(f"[Autostart] Check error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# AUDIO RECORDER
# ═══════════════════════════════════════════════════════════════

class AudioRecorder:
    def __init__(self):
        self.sample_rate = 16000
        self.channels = 1
        self.is_recording = False
        self.frames = []
        self.stream = None
        self.temp_file = os.path.join(tempfile.gettempdir(), "voice_assistant_temp.wav")
        self._current_level = 0.0
        self._level_lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        if self.is_recording:
            self.frames.append(indata.copy())
            # Calculate RMS level for waveform visualization
            rms = float(np.sqrt(np.mean(indata ** 2)))
            with self._level_lock:
                self._current_level = rms

    def get_current_level(self):
        """Return current audio RMS level (0.0 to ~1.0)."""
        with self._level_lock:
            return self._current_level

    def start(self, device=None):
        if self.is_recording:
            return
        self.is_recording = True
        self.frames = []
        self._current_level = 0.0
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self._callback,
                device=device
            )
            self.stream.start()
            print("[Recorder] Recording started.")
        except Exception as e:
            print(f"[Recorder] Error starting: {e}")
            self.is_recording = False

    def stop(self):
        if not self.is_recording:
            return None
        self.is_recording = False
        self._current_level = 0.0
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if not self.frames:
            return None

        audio = np.concatenate(self.frames, axis=0)
        audio_int16 = (audio * 32767).astype(np.int16).tobytes()
        
        import wave
        with wave.open(self.temp_file, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 2 bytes for int16
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_int16)
            
        print(f"[Recorder] Saved to {self.temp_file}")
        return self.temp_file

    @staticmethod
    def get_devices():
        """Return list of input device names."""
        devices = sd.query_devices()
        input_devs = ["По умолчанию"]
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                input_devs.append(f"{d['name']}")
        return input_devs

    @staticmethod
    def get_device_index(name):
        """Return the sounddevice index for a given device name, or None for default."""
        if not name or name == "По умолчанию":
            return None
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d['name'] == name and d['max_input_channels'] > 0:
                return i
        return None


# ═══════════════════════════════════════════════════════════════
# FLOATING WAVEFORM OVERLAY
# ═══════════════════════════════════════════════════════════════

class WaveformOverlay:
    """A sleek black rounded-rectangle overlay showing live audio waveform."""

    WIDTH = 280
    HEIGHT = 56
    BAR_COUNT = 25
    BAR_WIDTH = 6
    BAR_GAP = 3
    CORNER_RADIUS = 18
    BG_COLOR = "#111111"
    BAR_COLOR_LOW = "#3a86ff"
    BAR_COLOR_HIGH = "#ff006e"

    def __init__(self, master, recorder: AudioRecorder, stop_callback=None):
        self.master = master
        self.recorder = recorder
        self.stop_callback = stop_callback
        self.win = None
        self.canvas = None
        self.is_visible = False
        self._bar_ids = []
        self._levels_history = [0.0] * self.BAR_COUNT

    def show(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass

        self.win = tk.Toplevel(self.master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.92)
        self.win.configure(bg="#000000")

        # Try to make pure black transparent so we can simulate rounded corners
        try:
            self.win.wm_attributes("-transparentcolor", "#000000")
        except Exception:
            pass

        # Make non-focusable so it doesn't steal focus from target app
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style | 0x08000000)  # WS_EX_NOACTIVATE
        except Exception:
            pass

        # Position: bottom center of screen
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = (sw - self.WIDTH) // 2
        y = sh - self.HEIGHT - 60
        self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.win, width=self.WIDTH, height=self.HEIGHT,
            bg="#000000", highlightthickness=0
        )
        self.canvas.pack()

        # Draw rounded rectangle background
        self._draw_rounded_rect(0, 0, self.WIDTH, self.HEIGHT, self.CORNER_RADIUS, self.BG_COLOR)

        # Create bar rectangles
        self._bar_ids = []
        total_bars_width = self.BAR_COUNT * self.BAR_WIDTH + (self.BAR_COUNT - 1) * self.BAR_GAP
        start_x = (self.WIDTH - total_bars_width) / 2
        max_bar_height = self.HEIGHT - 20

        for i in range(self.BAR_COUNT):
            bx = start_x + i * (self.BAR_WIDTH + self.BAR_GAP)
            # Start with minimal height
            bar_h = 3
            by = (self.HEIGHT - bar_h) / 2
            bar_id = self.canvas.create_rectangle(
                bx, by, bx + self.BAR_WIDTH, by + bar_h,
                fill=self.BAR_COLOR_LOW, outline="", width=0
            )
            self._bar_ids.append(bar_id)

        self._levels_history = [0.0] * self.BAR_COUNT
        self.is_visible = True

        # Click anywhere on overlay to stop recording
        self.canvas.bind("<Button-1>", self._on_click)

        self._animate()

    def hide(self):
        self.is_visible = False
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def _draw_rounded_rect(self, x1, y1, x2, y2, r, color):
        """Draw a rounded rectangle on the canvas."""
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]
        self.canvas.create_polygon(points, fill=color, outline=color, smooth=True)

    def _animate(self):
        if not self.is_visible or not self.win:
            return

        try:
            level = self.recorder.get_current_level()
            # Normalize level (typical mic RMS is 0-0.1, amplify for visibility)
            normalized = min(level * 15.0, 1.0)

            # Shift history left and add new level
            self._levels_history.pop(0)
            self._levels_history.append(normalized)

            total_bars_width = self.BAR_COUNT * self.BAR_WIDTH + (self.BAR_COUNT - 1) * self.BAR_GAP
            start_x = (self.WIDTH - total_bars_width) / 2
            max_bar_height = self.HEIGHT - 16

            for i, lvl in enumerate(self._levels_history):
                bx = start_x + i * (self.BAR_WIDTH + self.BAR_GAP)
                bar_h = max(3, lvl * max_bar_height)
                by = (self.HEIGHT - bar_h) / 2

                self.canvas.coords(
                    self._bar_ids[i],
                    bx, by, bx + self.BAR_WIDTH, by + bar_h
                )

                # Color gradient based on level
                if lvl > 0.7:
                    color = self.BAR_COLOR_HIGH
                elif lvl > 0.35:
                    # Blend between blue and pink
                    t = (lvl - 0.35) / 0.35
                    r = int(58 + t * (255 - 58))
                    g = int(134 - t * 134)
                    b = int(255 - t * (255 - 110))
                    color = f"#{r:02x}{g:02x}{b:02x}"
                else:
                    color = self.BAR_COLOR_LOW

                self.canvas.itemconfig(self._bar_ids[i], fill=color)

            self.win.after(50, self._animate)
        except Exception:
            pass

    def _on_click(self, event=None):
        """Stop recording when the overlay is clicked."""
        if self.stop_callback:
            self.stop_callback()


# ═══════════════════════════════════════════════════════════════
# FLOATING MICROPHONE BUTTON
# ═══════════════════════════════════════════════════════════════

class FloatingButton:
    """A persistent, draggable, always-on-top floating mic button."""

    def __init__(self, master, toggle_callback):
        self.master = master
        self.toggle_callback = toggle_callback
        self.win = None
        self.is_recording = False
        self._drag_data = {"x": 0, "y": 0}

    def show(self):
        if self.win is not None:
            return  # Already visible

        size = CFG.get("floating_btn_size", 60)
        opacity = CFG.get("floating_btn_opacity", 0.85)
        position = CFG.get("floating_btn_position", "Правый нижний")

        self.win = tk.Toplevel(self.master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", opacity)
        self.win.configure(bg="#000000")
        try:
            self.win.wm_attributes("-transparentcolor", "#000000")
        except Exception:
            pass

        # Make non-focusable so clicking doesn't steal focus from target app
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style | 0x08000000)  # WS_EX_NOACTIVATE
        except Exception:
            pass

        # Position on screen
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        margin = 20
        if position == "Правый нижний":
            x, y = sw - size - margin, sh - size - margin - 50
        elif position == "Правый верхний":
            x, y = sw - size - margin, margin
        elif position == "Левый нижний":
            x, y = margin, sh - size - margin - 50
        elif position == "Левый верхний":
            x, y = margin, margin
        else:
            x, y = sw - size - margin, sh - size - margin - 50

        self.win.geometry(f"{size}x{size}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.win, width=size, height=size,
            bg="#000000", highlightthickness=0
        )
        self.canvas.pack()

        # Draw circle background
        pad = 2
        self.bg_circle = self.canvas.create_oval(
            pad, pad, size - pad, size - pad,
            fill="#2d2d2d", outline="#444444", width=2
        )

        # Mic icon text
        font_size = max(14, size // 3)
        self.mic_text = self.canvas.create_text(
            size // 2, size // 2,
            text="🎙", font=("Segoe UI Emoji", font_size),
            fill="white"
        )

        # Click to toggle recording
        self.canvas.bind("<Button-1>", self._on_click)
        # Drag support
        self.canvas.bind("<Button-3>", self._on_drag_start)
        self.canvas.bind("<B3-Motion>", self._on_drag_motion)

    def hide(self):
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def set_recording(self, is_recording: bool):
        """Update visual state to reflect recording status."""
        self.is_recording = is_recording
        if self.win and self.canvas:
            try:
                if is_recording:
                    self.canvas.itemconfig(self.bg_circle, fill="#cc0000", outline="#ff4444")
                    self.canvas.itemconfig(self.mic_text, text="⏹")
                else:
                    self.canvas.itemconfig(self.bg_circle, fill="#2d2d2d", outline="#444444")
                    self.canvas.itemconfig(self.mic_text, text="🎙")
            except Exception:
                pass

    def rebuild(self):
        """Rebuild the button with current settings."""
        was_visible = self.win is not None
        self.hide()
        if was_visible:
            self.show()

    def _on_click(self, event):
        self.toggle_callback()

    def _on_drag_start(self, event):
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _on_drag_motion(self, event):
        if self.win:
            x = self.win.winfo_x() + (event.x - self._drag_data["x"])
            y = self.win.winfo_y() + (event.y - self._drag_data["y"])
            self.win.geometry(f"+{x}+{y}")


# ═══════════════════════════════════════════════════════════════
# SYSTEM TRAY
# ═══════════════════════════════════════════════════════════════

class TrayManager:
    """Manages system tray icon using pystray."""

    def __init__(self, app):
        self.app = app
        self.icon = None
        self._thread = None

    def setup(self):
        try:
            import pystray
            from PIL import Image, ImageDraw

            # Create a simple microphone icon
            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # Mic body
            draw.rounded_rectangle([20, 8, 44, 38], radius=10, fill="#3a86ff")
            # Mic stand
            draw.arc([14, 22, 50, 50], start=0, end=180, fill="#3a86ff", width=3)
            draw.line([32, 50, 32, 58], fill="#3a86ff", width=3)
            draw.line([22, 58, 42, 58], fill="#3a86ff", width=3)

            menu = pystray.Menu(
                pystray.MenuItem("Показать", self._on_show, default=True),
                pystray.MenuItem("Выход", self._on_quit),
            )

            self.icon = pystray.Icon("VoiceAssistant", img, "Voice Assistant Pro", menu)
            self._thread = threading.Thread(target=self.icon.run, daemon=True)
            self._thread.start()
            print("[Tray] System tray icon created.")
        except Exception as e:
            print(f"[Tray] Error: {e}")

    def _on_show(self, icon=None, item=None):
        self.app.after(0, self.app._show_window)

    def _on_quit(self, icon=None, item=None):
        self.app.after(0, self.app._quit_app)

    def destroy(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class VoiceAssistantApp(ctk.CTk):
    def __init__(self, start_hidden=False):
        super().__init__()
        self._start_hidden = start_hidden

        self.title("Voice Assistant Pro")
        self.geometry("860x620")
        self.minsize(700, 500)

        self.recorder = AudioRecorder()
        self.overlay = WaveformOverlay(self, self.recorder, stop_callback=self._stop_recording)
        self.floating_btn = FloatingButton(self, self._toggle_recording)
        self.transcriber = None  # Lazy-loaded
        self._model_loading = False
        self._openai_client = None  # Cached OpenAI client
        self._is_recording = False
        self._current_hotkey_id = None

        # ── System tray ──
        self.tray = TrayManager(self)
        self.tray.setup()

        # Override close/minimize behavior
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.bind("<Unmap>", self._on_minimize)

        # ── Layout ──
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # ── Sidebar ──
        self.sidebar = ctk.CTkFrame(self, width=210, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(8, weight=1)

        ctk.CTkLabel(self.sidebar, text="🎙 Voice Assistant",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, padx=20, pady=(25, 20))

        self.nav_buttons = {}
        pages = [
            ("main", "🏠 Главное"),
            ("audio", "🎤 Настройки записи"),
            ("overlay", "💬 Плавающая кнопка"),
            ("replacements", "🔄 Замены"),
            ("ai", "🤖 ИИ"),
        ]
        for i, (key, label) in enumerate(pages):
            btn = ctk.CTkButton(
                self.sidebar, text=label, anchor="w",
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray70", "gray30"),
                height=36,
                command=lambda k=key: self.show_page(k)
            )
            btn.grid(row=i + 1, column=0, padx=15, pady=4, sticky="ew")
            self.nav_buttons[key] = btn



        # ── Content area ──
        self.pages = {}
        self._build_main_page()
        self._build_audio_page()
        self._build_overlay_page()
        self._build_replacements_page()
        self._build_ai_page()

        self.show_page("main")

        # ── Register hotkeys after UI is up ──
        self.after(500, self._register_hotkeys)

        # ── Show floating button if enabled ──
        if CFG.get("show_floating_button", True):
            self.after(600, self.floating_btn.show)

        # ── Cleanup old recordings ──
        self.after(800, self._cleanup_old_recordings)

        # ── Start lazy AI model loading ──
        self.after(1000, self._lazy_load_model)

        # ── If launched via autostart, hide to tray immediately ──
        if self._start_hidden:
            self.after(100, self._hide_to_tray)

    # ─────────────────────────────────────────────────────────
    # Window management (tray)
    # ─────────────────────────────────────────────────────────
    def _hide_to_tray(self):
        """Hide window to system tray instead of closing."""
        self.withdraw()
        print("[App] Hidden to tray.")

    def _on_minimize(self, event=None):
        """Intercept minimize to hide to tray instead."""
        if self.state() == 'iconic':
            self.after(10, self._hide_to_tray)

    def _show_window(self):
        """Restore window from tray."""
        self.deiconify()
        self.state('normal')
        self.lift()
        self.focus_force()
        print("[App] Window restored.")

    def _quit_app(self):
        """Fully quit the application."""
        self.tray.destroy()
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        self.destroy()

    # ─────────────────────────────────────────────────────────
    # PAGE: Главное (Hotkeys + Language + Autostart)
    # ─────────────────────────────────────────────────────────
    def _build_main_page(self):
        page = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")

        ctk.CTkLabel(page, text="Горячие клавиши и Язык",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(20, 15), padx=25)

        # --- Hotkey Section ---
        hk_frame = ctk.CTkFrame(page)
        hk_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(hk_frame, text="Горячая клавиша записи:",
                     font=ctk.CTkFont(size=13)).grid(row=0, column=0, padx=15, pady=12)

        self.hk_display = ctk.CTkLabel(hk_frame, text=CFG["hotkey"],
                                        font=ctk.CTkFont(size=14, weight="bold"),
                                        fg_color=("gray85", "gray25"),
                                        corner_radius=6, width=200)
        self.hk_display.grid(row=0, column=1, padx=10, pady=12)

        self.hk_capture_btn = ctk.CTkButton(hk_frame, text="Задать клавишу", width=140,
                                             command=self._start_hotkey_capture)
        self.hk_capture_btn.grid(row=0, column=2, padx=10, pady=12)

        # --- Language Section ---
        lang_frame = ctk.CTkFrame(page)
        lang_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(lang_frame, text="Язык распознавания:",
                     font=ctk.CTkFont(size=13)).grid(row=0, column=0, padx=15, pady=12)

        self.lang_menu = ctk.CTkOptionMenu(
            lang_frame,
            values=["auto", "ru", "en", "de", "fr", "es", "uk", "lv"],
            command=self._on_language_change,
            width=160
        )
        self.lang_menu.set(CFG["language"])
        self.lang_menu.grid(row=0, column=1, padx=10, pady=12)

        # --- Checkboxes ---
        self.autopaste_var = ctk.BooleanVar(value=CFG.get("auto_paste", True))
        ctk.CTkCheckBox(page, text="Вставлять текст в приложение, где была нажата клавиша",
                        variable=self.autopaste_var,
                        command=lambda: self._save("auto_paste", self.autopaste_var.get())
                        ).pack(anchor="w", padx=25, pady=8)

        self.automic_var = ctk.BooleanVar(value=CFG.get("auto_enable_mic", True))
        ctk.CTkCheckBox(page, text="Автоматически включить микрофон",
                        variable=self.automic_var,
                        command=lambda: self._save("auto_enable_mic", self.automic_var.get())
                        ).pack(anchor="w", padx=25, pady=8)

        # --- Autostart with Windows ---
        self.autostart_var = ctk.BooleanVar(value=is_autostart_enabled())
        ctk.CTkCheckBox(page, text="Запускать при старте Windows",
                        variable=self.autostart_var,
                        command=self._on_autostart_toggle
                        ).pack(anchor="w", padx=25, pady=8)

        self.pages["main"] = page

    def _on_autostart_toggle(self):
        enabled = self.autostart_var.get()
        self._save("autostart", enabled)
        set_autostart(enabled)

    # ─────────────────────────────────────────────────────────
    # PAGE: Настройки записи
    # ─────────────────────────────────────────────────────────
    def _build_audio_page(self):
        page = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")

        ctk.CTkLabel(page, text="Настройки записи",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(20, 15), padx=25)

        # --- Microphone Selection ---
        mic_frame = ctk.CTkFrame(page)
        mic_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(mic_frame, text="Микрофон:",
                     font=ctk.CTkFont(size=13)).grid(row=0, column=0, padx=15, pady=12)

        devices = AudioRecorder.get_devices()
        self.mic_menu = ctk.CTkOptionMenu(mic_frame, values=devices, width=280,
                                           command=lambda v: self._save("microphone", v))
        current_mic = CFG.get("microphone", "По умолчанию")
        if current_mic in devices:
            self.mic_menu.set(current_mic)
        self.mic_menu.grid(row=0, column=1, padx=10, pady=12)

        ctk.CTkButton(mic_frame, text="🔄", width=40,
                      command=self._refresh_mic_list).grid(row=0, column=2, padx=5, pady=12)

        # --- Save path ---
        path_frame = ctk.CTkFrame(page)
        path_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(path_frame, text="Папка для записей:",
                     font=ctk.CTkFont(size=13)).grid(row=0, column=0, padx=15, pady=12)

        self.path_var = ctk.StringVar(value=CFG.get("save_recordings_path", ""))
        path_entry = ctk.CTkEntry(path_frame, textvariable=self.path_var, width=320)
        path_entry.grid(row=0, column=1, padx=10, pady=12)

        ctk.CTkButton(path_frame, text="📁", width=40,
                      command=self._browse_folder).grid(row=0, column=2, padx=5, pady=12)

        # --- Timing settings ---
        ctk.CTkLabel(page, text="Параметры таймингов",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(20, 10), padx=25)

        timing_frame = ctk.CTkFrame(page)
        timing_frame.pack(fill="x", padx=25, pady=8)

        timings = [
            ("Задержка перед остановкой (мс):", "delay_before_stop_ms", 0),
            ("Таймаут без удержания (мин):", "timeout_without_holding_min", 1),
            ("Пороговое значение длительности (мс):", "duration_threshold_ms", 2),
            ("Порог тишины (мс):", "silence_threshold_ms", 3),
        ]

        self.timing_vars = {}
        for label, key, row in timings:
            ctk.CTkLabel(timing_frame, text=label, font=ctk.CTkFont(size=12)).grid(
                row=row, column=0, padx=15, pady=8, sticky="w")
            var = ctk.StringVar(value=str(CFG.get(key, 0)))
            self.timing_vars[key] = var
            entry = ctk.CTkEntry(timing_frame, textvariable=var, width=120)
            entry.grid(row=row, column=1, padx=10, pady=8)

        ctk.CTkButton(page, text="💾 Сохранить настройки записи", width=250,
                      command=self._save_audio_settings).pack(anchor="w", padx=25, pady=15)

        # --- Recording retention ---
        ctk.CTkLabel(page, text="Хранение записей",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(20, 10), padx=25)

        ret_frame = ctk.CTkFrame(page)
        ret_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(ret_frame, text="Удалять записи старше:",
                     font=ctk.CTkFont(size=13)).grid(row=0, column=0, padx=15, pady=12)

        retention_options = ["Не удалять", "1 день", "3 дня", "7 дней", "30 дней"]
        retention_map = {0: "Не удалять", 1: "1 день", 3: "3 дня", 7: "7 дней", 30: "30 дней"}
        current_days = CFG.get("recording_retention_days", 0)
        current_label = retention_map.get(current_days, "Не удалять")

        self.retention_menu = ctk.CTkOptionMenu(
            ret_frame, values=retention_options, width=180,
            command=self._on_retention_change
        )
        self.retention_menu.set(current_label)
        self.retention_menu.grid(row=0, column=1, padx=10, pady=12)

        self.pages["audio"] = page

    def _refresh_mic_list(self):
        """Refresh the microphone dropdown with current devices."""
        devices = AudioRecorder.get_devices()
        self.mic_menu.configure(values=devices)
        current = CFG.get("microphone", "По умолчанию")
        if current in devices:
            self.mic_menu.set(current)
        else:
            self.mic_menu.set("По умолчанию")
        self.status_label.configure(text="✅ Список микрофонов обновлён")
        self.after(3000, lambda: self.status_label.configure(text="Готово"))

    # ─────────────────────────────────────────────────────────
    # PAGE: Плавающая кнопка
    # ─────────────────────────────────────────────────────────
    def _build_overlay_page(self):
        page = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")

        ctk.CTkLabel(page, text="Плавающая кнопка",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(20, 15), padx=25)

        # Show / hide floating button
        self.show_float_var = ctk.BooleanVar(value=CFG.get("show_floating_button", True))
        ctk.CTkCheckBox(page, text="Показывать плавающую кнопку микрофона",
                        variable=self.show_float_var,
                        command=self._on_float_toggle
                        ).pack(anchor="w", padx=25, pady=10)

        # Size slider
        size_frame = ctk.CTkFrame(page)
        size_frame.pack(fill="x", padx=25, pady=8)
        ctk.CTkLabel(size_frame, text="Размер кнопки:").pack(anchor="w", padx=15, pady=(10, 0))
        self.size_value_label = ctk.CTkLabel(size_frame, text=f"{CFG.get('floating_btn_size', 60)}px")
        self.size_value_label.pack(anchor="e", padx=15)
        self.size_slider = ctk.CTkSlider(size_frame, from_=30, to=200,
                                          command=self._on_size_change)
        self.size_slider.set(CFG.get("floating_btn_size", 60))
        self.size_slider.pack(fill="x", padx=15, pady=(0, 10))

        # Opacity slider
        opac_frame = ctk.CTkFrame(page)
        opac_frame.pack(fill="x", padx=25, pady=8)
        ctk.CTkLabel(opac_frame, text="Прозрачность кнопки:").pack(anchor="w", padx=15, pady=(10, 0))
        self.opac_value_label = ctk.CTkLabel(opac_frame,
                                              text=f"{int(CFG.get('floating_btn_opacity', 0.85) * 100)}%")
        self.opac_value_label.pack(anchor="e", padx=15)
        self.opac_slider = ctk.CTkSlider(opac_frame, from_=10, to=100,
                                          command=self._on_opacity_change)
        self.opac_slider.set(int(CFG.get("floating_btn_opacity", 0.85) * 100))
        self.opac_slider.pack(fill="x", padx=15, pady=(0, 10))

        # Position dropdown
        pos_frame = ctk.CTkFrame(page)
        pos_frame.pack(fill="x", padx=25, pady=8)
        ctk.CTkLabel(pos_frame, text="Позиция:", font=ctk.CTkFont(size=13)).grid(row=0, column=0, padx=15, pady=12)
        positions = ["Правый нижний", "Правый верхний", "Левый нижний", "Левый верхний"]
        self.pos_menu = ctk.CTkOptionMenu(pos_frame, values=positions, width=180,
                                           command=self._on_position_change)
        self.pos_menu.set(CFG.get("floating_btn_position", "Правый нижний"))
        self.pos_menu.grid(row=0, column=1, padx=10, pady=12)

        ctk.CTkLabel(page, text="💡 Правая кнопка мыши — перетащить кнопку",
                     text_color="gray50", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=25, pady=(10, 5))

        self.pages["overlay"] = page

    # ─────────────────────────────────────────────────────────
    # PAGE: Замены
    # ─────────────────────────────────────────────────────────
    def _build_replacements_page(self):
        page = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")

        ctk.CTkLabel(page, text="Автозамены текста",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(20, 15), padx=25)

        ctk.CTkLabel(page, text="Слова или фразы, которые будут автоматически заменяться\n"
                                "после распознавания речи.",
                     text_color="gray50").pack(anchor="w", padx=25, pady=(0, 10))

        add_frame = ctk.CTkFrame(page)
        add_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(add_frame, text="Найти:").grid(row=0, column=0, padx=10, pady=8)
        self.rep_find_var = ctk.StringVar()
        ctk.CTkEntry(add_frame, textvariable=self.rep_find_var, width=200,
                     placeholder_text="Текст для сопоставления").grid(row=0, column=1, padx=5, pady=8)

        ctk.CTkLabel(add_frame, text="Заменить на:").grid(row=0, column=2, padx=10, pady=8)
        self.rep_replace_var = ctk.StringVar()
        ctk.CTkEntry(add_frame, textvariable=self.rep_replace_var, width=200,
                     placeholder_text="Текст замены").grid(row=0, column=3, padx=5, pady=8)

        ctk.CTkButton(add_frame, text="➕ Добавить", width=100,
                      command=self._add_replacement).grid(row=0, column=4, padx=10, pady=8)

        # List of existing replacements
        self.rep_list_frame = ctk.CTkFrame(page)
        self.rep_list_frame.pack(fill="x", padx=25, pady=10)
        self._refresh_replacements_list()

        self.pages["replacements"] = page

    # ─────────────────────────────────────────────────────────
    # PAGE: ИИ Settings
    # ─────────────────────────────────────────────────────────
    def _build_ai_page(self):
        page = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")

        ctk.CTkLabel(page, text="Настройки ИИ",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(20, 15), padx=25)

        ctk.CTkLabel(page, text="Опциональный GPT для улучшения текста после распознавания.",
                     text_color="gray50").pack(anchor="w", padx=25, pady=(0, 10))

        # Enable/disable AI toggle
        self.ai_enabled_var = ctk.BooleanVar(value=CFG.get("ai_enabled", True))
        ctk.CTkCheckBox(page, text="Включить обработку текста через ИИ",
                        variable=self.ai_enabled_var,
                        command=lambda: self._save("ai_enabled", self.ai_enabled_var.get()),
                        font=ctk.CTkFont(size=14, weight="bold")
                        ).pack(anchor="w", padx=25, pady=(5, 12))

        ai_frame = ctk.CTkFrame(page)
        ai_frame.pack(fill="x", padx=25, pady=8)

        ctk.CTkLabel(ai_frame, text="API Ключ:", font=ctk.CTkFont(size=13)).grid(
            row=0, column=0, padx=15, pady=12, sticky="w")
        self.api_key_var = ctk.StringVar(value=CFG.get("ai_api_key", ""))
        ctk.CTkEntry(ai_frame, textvariable=self.api_key_var, width=350,
                     placeholder_text="sk-...", show="•").grid(row=0, column=1, padx=10, pady=12)

        ctk.CTkLabel(ai_frame, text="Модель:", font=ctk.CTkFont(size=13)).grid(
            row=1, column=0, padx=15, pady=12, sticky="w")
        self.ai_model_menu = ctk.CTkOptionMenu(
            ai_frame, values=["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"], width=200,
            command=lambda v: self._save("ai_model", v)
        )
        self.ai_model_menu.set(CFG.get("ai_model", "gpt-4o-mini"))
        self.ai_model_menu.grid(row=1, column=1, padx=10, pady=12, sticky="w")

        ctk.CTkLabel(ai_frame, text="Системный промпт:", font=ctk.CTkFont(size=13)).grid(
            row=2, column=0, padx=15, pady=12, sticky="nw")
        self.ai_prompt_var = ctk.StringVar(value=CFG.get("ai_prompt", ""))
        prompt_entry = ctk.CTkEntry(ai_frame, textvariable=self.ai_prompt_var, width=350)
        prompt_entry.grid(row=2, column=1, padx=10, pady=12)

        ctk.CTkButton(page, text="💾 Сохранить настройки ИИ", width=250,
                      command=self._save_ai_settings).pack(anchor="w", padx=25, pady=15)

        self.pages["ai"] = page

    # ═══════════════════════════════════════════════════════════
    # Navigation
    # ═══════════════════════════════════════════════════════════

    def show_page(self, name):
        for key, frame in self.pages.items():
            frame.grid_forget()
        for key, btn in self.nav_buttons.items():
            if key == name:
                btn.configure(fg_color=("gray75", "gray25"))
            else:
                btn.configure(fg_color="transparent")
        self.pages[name].grid(row=0, column=1, sticky="nsew")

    # ═══════════════════════════════════════════════════════════
    # Callbacks & Helpers
    # ═══════════════════════════════════════════════════════════

    def _save(self, key, value):
        CFG[key] = value
        save_config(CFG)
        print(f"[Config] {key} = {value}")

    def _on_language_change(self, val):
        self._save("language", val)

    def _on_size_change(self, val):
        v = int(val)
        self.size_value_label.configure(text=f"{v}px")
        self._save("floating_btn_size", v)
        self.floating_btn.rebuild()

    def _on_opacity_change(self, val):
        v = int(val)
        self.opac_value_label.configure(text=f"{v}%")
        self._save("floating_btn_opacity", v / 100.0)
        self.floating_btn.rebuild()

    def _on_float_toggle(self):
        enabled = self.show_float_var.get()
        self._save("show_floating_button", enabled)
        if enabled:
            self.floating_btn.show()
        else:
            self.floating_btn.hide()

    def _on_position_change(self, val):
        self._save("floating_btn_position", val)
        self.floating_btn.rebuild()

    def _on_retention_change(self, val):
        label_to_days = {"Не удалять": 0, "1 день": 1, "3 дня": 3, "7 дней": 7, "30 дней": 30}
        days = label_to_days.get(val, 0)
        self._save("recording_retention_days", days)

    def _browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path_var.set(folder)
            self._save("save_recordings_path", folder)

    def _save_audio_settings(self):
        for key, var in self.timing_vars.items():
            try:
                val = int(var.get()) if "min" not in key else int(var.get())
                self._save(key, val)
            except ValueError:
                pass
        print("[Settings] Audio settings saved.")

    def _save_ai_settings(self):
        self._save("ai_api_key", self.api_key_var.get())
        self._save("ai_model", self.ai_model_menu.get())
        self._save("ai_prompt", self.ai_prompt_var.get())
        print("[Settings] AI settings saved.")

    def _cleanup_audio_file(self, file_path):
        retention_days = int(CFG.get("recording_retention_days", 0))
        if retention_days <= 0:
            try:
                os.remove(file_path)
                log(f"Removed temp chunk: {file_path}")
            except Exception as e:
                log(f"Failed to remove temp chunk {file_path}: {e}")
        else:
            self._cleanup_old_recordings()

    def _cleanup_old_recordings(self):
        """Delete recordings older than configured retention period."""
        days = CFG.get("recording_retention_days", 0)
        if days <= 0:
            return  # 0 = keep forever

        rec_path = CFG.get("save_recordings_path", "")
        if not rec_path or not os.path.isdir(rec_path):
            return

        cutoff = time.time() - (days * 86400)
        deleted = 0
        for f in glob.glob(os.path.join(rec_path, "*.wav")):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
                    deleted += 1
            except Exception:
                pass
        if deleted:
            print(f"[Cleanup] Deleted {deleted} old recording(s).")

    # ── Hotkey Capture ──
    def _start_hotkey_capture(self):
        self.hk_display.configure(text="Нажмите комбинацию клавиш...",
                                   fg_color=("#ffcc00", "#665200"))
        self.hk_capture_btn.configure(state="disabled", text="Ожидание...")

        def listen():
            try:
                import keyboard
                hk = keyboard.read_hotkey(suppress=False)
                self.after(0, lambda: self._finish_hotkey_capture(hk))
            except Exception as e:
                self.after(0, lambda: self._finish_hotkey_capture(None, str(e)))

        threading.Thread(target=listen, daemon=True).start()

    def _finish_hotkey_capture(self, hk, error=None):
        self.hk_capture_btn.configure(state="normal", text="Задать клавишу")
        if error:
            self.hk_display.configure(text=f"Ошибка: {error}", fg_color=("gray85", "gray25"))
            return
        if hk:
            self.hk_display.configure(text=hk, fg_color=("gray85", "gray25"))
            self._save("hotkey", hk)
            self._register_hotkeys()

    # ── Hotkey Registration ──
    def _register_hotkeys(self):
        try:
            import keyboard
            # Remove only the previously registered hotkey, not all hooks
            if self._current_hotkey_id is not None:
                try:
                    keyboard.remove_hotkey(self._current_hotkey_id)
                except Exception:
                    pass
                self._current_hotkey_id = None

            hk = CFG.get("hotkey", "ctrl+shift+space")
            # suppress=False prevents keyboard from freezing after toggle
            # Use lambda + self.after to ensure thread-safe tkinter calls
            self._current_hotkey_id = keyboard.add_hotkey(
                hk, lambda: self.after(0, self._toggle_recording), suppress=False
            )
            print(f"[Hotkey] Registered: {hk}")
        except Exception as e:
            print(f"[Hotkey] Registration error: {e}")

    def _toggle_recording(self):
        log(f"[_toggle_recording] Triggered. Current state: {self._is_recording}")
        if self._is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        self._is_recording = True
        # Remember which window had focus so we can paste into it later
        try:
            self._prev_hwnd = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            self._prev_hwnd = None
        try:
            mic_name = CFG.get("microphone", "По умолчанию")
            device_idx = AudioRecorder.get_device_index(mic_name)
            self.recorder.start(device=device_idx)
            self.after(0, self.overlay.show)
            self.after(0, lambda: self.floating_btn.set_recording(True))
            log("[Recording] Started successfully.")
        except Exception as e:
            log(f"[Recording] Failed to start: {e}\n{traceback.format_exc()}")

    def _stop_recording(self):
        log("[Recording] Stopping recording...")
        self._is_recording = False
        try:
            audio_file = self.recorder.stop()
        except Exception as e:
            log(f"[Recording] Error stopping: {e}\n{traceback.format_exc()}")
            audio_file = None

        self.after(0, self.overlay.hide)
        self.after(0, lambda: self.floating_btn.set_recording(False))
        log(f"[Recording] Stopped. Audio file: {audio_file}")

        if audio_file:
            log("[Recording] Starting transcription thread.")
            threading.Thread(target=self._transcribe, args=(audio_file,), daemon=True).start()
        else:
            log("[Recording] No audio captured.")

    def _transcribe(self, audio_file):
        log(f"[_transcribe] Started for {audio_file}")
        try:
            if self.transcriber is None:
                log("[AI] Loading model...")
                from faster_whisper import WhisperModel
                # Using 'tiny' model for fast CPU inference in standalone exe
                self.transcriber = WhisperModel("tiny", device="auto", compute_type="int8")
                log("[AI] Model loaded successfully.")

            lang = CFG.get("language", "auto")
            whisper_lang = None if lang == "auto" else lang

            log(f"[_transcribe] Transcribing audio with language={whisper_lang}...")
            segments, info = self.transcriber.transcribe(
                audio_file, language=whisper_lang, beam_size=1,
                vad_filter=True, vad_parameters=dict(min_silence_duration_ms=CFG.get("silence_threshold_ms", 500))
            )

            text = " ".join(s.text for s in segments).strip()

            if lang == "auto":
                log(f"[Whisper] Detected: {info.language} ({info.language_probability:.0%})")
            
            log(f"[Whisper Result] {text}")

            if not text:
                log("[_transcribe] No text detected.")
                return

            # Apply replacements
            for rep in CFG.get("replacements", []):
                match = rep.get("match", "")
                replace = rep.get("replace", "")
                if match:
                    try:
                        text = re.sub(match, replace, text, flags=re.IGNORECASE)
                    except re.error:
                        pass

            # ── GPT post-processing (if API key is set) ──
            ai_enabled = CFG.get("ai_enabled", True)
            api_key = CFG.get("ai_api_key", "").strip()
            if text and api_key and ai_enabled:
                try:
                    # Reuse cached client for faster requests
                    if self._openai_client is None:
                        from openai import OpenAI
                        self._openai_client = OpenAI(api_key=api_key)
                    model = CFG.get("ai_model", "gpt-4o-mini")
                    prompt = CFG.get("ai_prompt", "Ты — корректор текста.")

                    print(f"[GPT] Sending to {model} for post-processing...")
                    response = self._openai_client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": text}
                        ],
                        temperature=0.3,
                        max_tokens=500,
                    )
                    polished = response.choices[0].message.content.strip()
                    if polished:
                        print(f"[GPT Result] {polished}")
                        text = polished
                    else:
                        print("[GPT] Empty response, using original text.")
                except Exception as e:
                    print(f"[GPT Error] {e} — using original Whisper text.")
            elif not ai_enabled:
                print("[GPT] AI disabled by user, skipping post-processing.")
            elif not api_key:
                print("[GPT] No API key configured, skipping post-processing.")

            log(f"[Final Result] {text}")

            if text and CFG.get("auto_paste", True):
                import pyperclip
                import keyboard as kb

                log("[Paste] Copying to clipboard...")
                old_clip = pyperclip.paste()
                pyperclip.copy(text)

                log(f"[Paste] Restoring focus to previous window: {getattr(self, '_prev_hwnd', None)}")
                try:
                    hwnd = getattr(self, '_prev_hwnd', None)
                    if hwnd:
                        user32 = ctypes.windll.user32
                        kernel32 = ctypes.windll.kernel32
                        our_tid = kernel32.GetCurrentThreadId()
                        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
                        if our_tid != target_tid:
                            user32.AttachThreadInput(our_tid, target_tid, True)
                        user32.SetForegroundWindow(hwnd)
                        if our_tid != target_tid:
                            user32.AttachThreadInput(our_tid, target_tid, False)
                        time.sleep(0.02)
                except Exception as e:
                    log(f"[Paste] Focus restore error: {e}")

                log("[Paste] Sending Ctrl+V...")
                time.sleep(0.02)
                kb.press_and_release('ctrl+v')
                time.sleep(0.02)
                # Option to restore clipboard omitted for now to not break pasting
                # pyperclip.copy(old_clip)

            log("[_transcribe] Done.")

        except Exception as e:
            log(f"[_transcribe] Error: {e}\n{traceback.format_exc()}")
        finally:
            log(f"[_transcribe] Cleaning up audio file {audio_file}")
            self._cleanup_audio_file(audio_file)



    # ── Lazy Model Loading ──
    def _lazy_load_model(self):
        if self._model_loading:
            return
        self._model_loading = True
        print("[AI] Loading model in background...")

        def load():
            try:
                from faster_whisper import WhisperModel
                self.transcriber = WhisperModel("tiny", device="auto", compute_type="int8")
                print("[AI] Model loaded successfully.")
            except Exception as e:
                print(f"[AI] Model loading error: {e}")

        threading.Thread(target=load, daemon=True).start()

    # ── Replacements Helpers ──
    def _add_replacement(self):
        find = self.rep_find_var.get().strip()
        replace = self.rep_replace_var.get().strip()
        if not find:
            return
        reps = CFG.get("replacements", [])
        reps.append({"match": find, "replace": replace, "ignore_case": True})
        self._save("replacements", reps)
        self.rep_find_var.set("")
        self.rep_replace_var.set("")
        self._refresh_replacements_list()

    def _remove_replacement(self, index):
        reps = CFG.get("replacements", [])
        if 0 <= index < len(reps):
            reps.pop(index)
            self._save("replacements", reps)
            self._refresh_replacements_list()

    def _refresh_replacements_list(self):
        for w in self.rep_list_frame.winfo_children():
            w.destroy()

        reps = CFG.get("replacements", [])
        if not reps:
            ctk.CTkLabel(self.rep_list_frame, text="Нет замен. Добавьте выше.",
                         text_color="gray50").pack(padx=15, pady=10)
            return

        for i, rep in enumerate(reps):
            row = ctk.CTkFrame(self.rep_list_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=3)
            ctk.CTkLabel(row, text=f'"{rep["match"]}" → "{rep["replace"]}"',
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=10)
            ctk.CTkButton(row, text="🗑", width=30, fg_color="transparent",
                          hover_color=("#ff4444", "#cc0000"),
                          command=lambda idx=i: self._remove_replacement(idx)).pack(side="right", padx=5)


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    start_hidden = "--autostart" in sys.argv
    app = VoiceAssistantApp(start_hidden=start_hidden)
    app.mainloop()
