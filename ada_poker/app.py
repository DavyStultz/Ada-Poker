"""Stage 1 desktop UI. Launch with python -m ada_poker."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import shutil
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import windows
from .recorder import CaptureRegion, Recorder, RecordingOptions

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / ".local" / "settings.json"
ACTIVE_STATES = {"starting", "recording", "stopping"}


def find_encoder() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        candidate = shutil.which("ffmpeg")
        if candidate:
            return candidate
        raise RuntimeError("The recording component is missing. Run setup.ps1 in the project folder.")


def clock_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def release_awake() -> None:
    try:
        windows.keep_awake(False)
    except OSError:
        # Windows also releases the execution-state request when this thread exits.
        # Cleanup must not hide the original error or prevent closing the UI.
        pass


class TableSelector(tk.Toplevel):
    """Select physical pixels from a monitor snapshot without covering the table."""

    def __init__(self, parent, monitor: windows.Monitor, on_select):
        super().__init__(parent)
        self.withdraw()
        self.title("Select your table")
        self.resizable(False, False)
        self.monitor, self.on_select = monitor, on_select
        self.start = None
        self.region = None
        self.box = None
        ttk.Label(self, text="Drag a box around the complete ClubGG window, including player names and action buttons.",
                  padding=(16, 14)).pack(anchor="w")
        try:
            data, self.pw, self.ph = windows.preview_ppm(monitor.x, monitor.y, monitor.width,
                                                       monitor.height, 1000, 560)
        except Exception:
            self.destroy()
            raise
        self.photo = tk.PhotoImage(data=data, format="PPM")
        self.canvas = tk.Canvas(self, width=self.pw, height=self.ph, highlightthickness=0,
                                cursor="crosshair", bg="#101a23")
        self.canvas.pack(padx=16)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.note = tk.StringVar(value="Include the whole table. The recording keeps the original resolution.")
        ttk.Label(self, textvariable=self.note, padding=(16, 10)).pack(anchor="w")
        actions = ttk.Frame(self, padding=(16, 0, 16, 16))
        actions.pack(fill="x")
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        self.use_button = ttk.Button(actions, text="Use this area", command=self.accept, state="disabled")
        self.use_button.pack(side="right", padx=8)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)
        self.update_idletasks()
        self.geometry(f"+{max(0, parent.winfo_x())}+{max(0, parent.winfo_y())}")
        self.deiconify()
        self.grab_set()

    def press(self, event):
        self.start = (event.x, event.y)
        self.region = None
        self.use_button.configure(state="disabled")
        if self.box:
            self.canvas.delete(self.box)
        self.box = self.canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                 outline="#63f5c0", width=3)

    def drag(self, event):
        if self.start is None:
            return
        ex, ey = max(0, min(self.pw, event.x)), max(0, min(self.ph, event.y))
        self.canvas.coords(self.box, *self.start, ex, ey)
        values = windows.selection_pixels(self.monitor, (self.pw, self.ph), self.start, (ex, ey))
        self.note.set(f"Selected area: {values[2]} × {values[3]} pixels")

    def release(self, event):
        if self.start is None:
            return
        self.drag(event)
        values = windows.selection_pixels(self.monitor, (self.pw, self.ph), self.start, (event.x, event.y))
        try:
            self.region = CaptureRegion(*values)
            # Keep accidental tiny drags out of the product flow.
            if values[2] < 160 or values[3] < 120:
                raise ValueError("Select a larger area containing the full table.")
            self.use_button.configure(state="normal")
        except ValueError as exc:
            self.region = None
            self.note.set(str(exc))

    def accept(self):
        if self.region is not None:
            self.on_select(self.region)
            self.destroy()


class RecorderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.withdraw()
        root.title("Ada Poker · Session recorder")
        root.geometry("860x850")
        root.minsize(800, 790)
        self.recorder = None
        self.region = None
        self.monitor = None
        self.encoder = None
        self.closing = False
        self.tick_id = None
        self.selector = None
        self.preview_busy = False
        self.preview_generation = 0
        self.preview_queue = queue.Queue(maxsize=2)
        self.last_preview = 0.0
        self.last_monitor_check = 0.0
        self.last_state = "idle"
        self.last_session = None
        self.health_error = None
        self.settings_notice = None
        self.settings = self.load_settings()
        self.screens = windows.monitors()
        self.monitor_var = tk.StringVar()
        self.area_var = tk.StringVar(value="Choose your monitor, then select the table area.")
        self.folder_var = tk.StringVar(value=self.settings.get("output_root", str(Path.home() / "Videos" / "Ada Poker")))
        self.duration_var = tk.StringVar(value="3 hours")
        self.state_var = tk.StringVar(value="Ready to select a table")
        self.elapsed_var = tk.StringVar(value="00:00:00")
        self.detail_var = tk.StringVar(value="")
        self.preview_note = tk.StringVar(value="Your table preview will appear here.")
        self._build_ui()
        self.refresh_monitors(initial=True)
        try:
            self.encoder = find_encoder()
        except RuntimeError as exc:
            self.detail_var.set(str(exc))
        self.restore_region()
        if self.settings_notice:
            self.detail_var.set(self.settings_notice)
        primary = next(m for m in self.screens if m.primary)
        root.geometry(f"+{primary.work_x + 35}+{primary.work_y + 35}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.deiconify()
        self.tick()

    def _build_ui(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f7f6")
        style.configure("TLabel", background="#f4f7f6", foreground="#172a26", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 23))
        style.configure("Time.TLabel", font=("Consolas", 28))
        style.configure("Accent.TButton", background="#14654b", foreground="white")
        style.map("Accent.TButton", background=[("active", "#0d503a"), ("disabled", "#d7e1dc")])
        main = ttk.Frame(self.root, padding=24)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)
        ttk.Label(main, text="Ada Poker", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="Session recorder  /  Stage 1", padding=(0, 3, 0, 16)).grid(row=1, column=0, sticky="w")
        source = ttk.Frame(main)
        source.grid(row=2, column=0, sticky="ew")
        self.monitor_combo = ttk.Combobox(source, textvariable=self.monitor_var, state="readonly", width=44)
        self.monitor_combo.pack(side="left", fill="x", expand=True)
        self.monitor_combo.bind("<<ComboboxSelected>>", self.monitor_changed)
        self.refresh_button = ttk.Button(source, text="Refresh", command=self.refresh_monitors)
        self.refresh_button.pack(side="left", padx=(8, 0))
        self.select_button = ttk.Button(source, text="Select table area", command=self.select_table)
        self.select_button.pack(side="left", padx=(8, 0))
        ttk.Label(main, textvariable=self.area_var, padding=(0, 10)).grid(row=3, column=0, sticky="w")
        # Only the preview yields space when the window gets smaller. A loaded
        # image must never increase its requested size and displace the controls.
        preview = ttk.Frame(main, height=240)
        preview.grid(row=4, column=0, sticky="nsew")
        preview.pack_propagate(False)
        self.preview_label = tk.Label(preview, bg="#12271f", fg="#d6e4dc", text="Select your table to begin",
                                     font=("Segoe UI", 13))
        self.preview_label.pack(fill="both", expand=True)
        ttk.Label(main, textvariable=self.preview_note, wraplength=740, padding=(0, 6, 0, 12)).grid(row=5, column=0, sticky="w")
        folder = ttk.Frame(main)
        folder.grid(row=6, column=0, sticky="ew")
        ttk.Label(folder, text="Save to").pack(side="left", padx=(0, 8))
        self.folder_entry = ttk.Entry(folder, textvariable=self.folder_var)
        self.folder_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(folder, text="Browse…", command=self.choose_folder)
        self.browse_button.pack(side="left", padx=(8, 0))
        limit = ttk.Frame(main, padding=(0, 10, 0, 8))
        limit.grid(row=7, column=0, sticky="ew")
        ttk.Label(limit, text="Stop automatically after").pack(side="left", padx=(0, 8))
        self.duration_combo = ttk.Combobox(limit, textvariable=self.duration_var, state="readonly",
                                           values=["1 minute (test)", "10 minutes", "1 hour", "2 hours", "3 hours"], width=20)
        self.duration_combo.pack(side="left")
        ttk.Label(limit, text="15 fps · Original size · No audio").pack(side="right")
        status = ttk.Frame(main)
        status.grid(row=8, column=0, sticky="ew", pady=(2, 10))
        ttk.Label(status, textvariable=self.elapsed_var, style="Time.TLabel").pack(side="left")
        ttk.Label(status, textvariable=self.state_var).pack(side="right")
        buttons = ttk.Frame(main)
        buttons.grid(row=9, column=0, sticky="ew")
        self.start_button = ttk.Button(buttons, text="Start recording", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop & save", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(buttons, text="Open session folder", command=self.open_session, state="disabled")
        self.open_button.pack(side="right")
        detail = ttk.Label(main, textvariable=self.detail_var, wraplength=740, padding=(0, 10, 0, 0))
        detail.grid(row=10, column=0, sticky="ew")
        main.bind("<Configure>", lambda event: detail.configure(wraplength=max(100, event.width - 48)))

    def load_settings(self):
        try:
            value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("output_root", ""), str):
                raise ValueError("Invalid settings")
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            self.settings_notice = "Previous settings could not be read. Please select the table again."
            return {}

    def save_settings(self):
        value = {"output_root": self.folder_var.get()}
        if self.region and self.monitor:
            value.update(region=self.region.to_dict(), monitor=asdict(self.monitor))
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp = SETTINGS_PATH.with_suffix(".tmp")
            temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temp.replace(SETTINGS_PATH)
        except OSError as exc:
            self.detail_var.set(f"Settings could not be saved: {exc}")

    def restore_region(self):
        try:
            saved = windows.Monitor(**self.settings["monitor"])
            match = next(m for m in self.screens if m.matches_capture(saved))
            region = CaptureRegion(**self.settings["region"])
            if match.contains(region.x, region.y, region.width, region.height):
                self.monitor = match
                self.monitor_combo.current(self.screens.index(match))
                self.set_region(region)
        except (KeyError, StopIteration, TypeError, ValueError):
            pass

    def refresh_monitors(self, initial=False):
        try:
            screens = windows.monitors()
        except OSError as exc:
            messagebox.showerror("Displays unavailable", str(exc), parent=self.root)
            return
        old = self.monitor
        self.screens = screens
        self.monitor_combo.configure(values=[f"Monitor {i+1} — {m.width} × {m.height}" +
                                             (" (primary)" if m.primary else "") for i, m in enumerate(screens)])
        selected = next((m for m in screens if old and m.matches_capture(old)),
                        next((m for m in screens if not m.primary), screens[0]))
        self.monitor_combo.current(screens.index(selected))
        self.monitor = selected
        if not initial and old and not old.matches_capture(selected):
            self.clear_region()

    def clear_region(self):
        self.region = None
        self.preview_generation += 1
        self.area_var.set("Select the table area on this monitor.")
        self.preview_label.configure(image="", text="Select your table to begin")
        self.preview_label.image = None
        self.preview_note.set("Your table preview will appear here.")
        self.save_settings()

    def monitor_changed(self, _event=None):
        self.monitor = self.screens[self.monitor_combo.current()]
        self.clear_region()

    def select_table(self):
        if self.selector and self.selector.winfo_exists():
            self.selector.lift()
            return
        try:
            self.selector = TableSelector(self.root, self.monitor, self.set_region)
        except (OSError, tk.TclError) as exc:
            messagebox.showerror("Cannot preview this monitor", str(exc), parent=self.root)

    def set_region(self, region):
        self.region = region
        self.preview_generation += 1
        self.area_var.set(f"Table area: {region.width} × {region.height} pixels on Monitor {self.screens.index(self.monitor)+1}")
        self.state_var.set("Ready to record")
        self.last_preview = 0
        self.save_settings()

    def choose_folder(self):
        value = filedialog.askdirectory(parent=self.root, title="Choose a local recordings folder",
                                         initialdir=str(Path.home()))
        if value:
            self.folder_var.set(value)
            self.save_settings()

    def start(self):
        if self.recorder and self.recorder.snapshot()["state"] in ACTIVE_STATES:
            return
        if not self.region:
            return
        try:
            if not any(m.matches_capture(self.monitor) for m in windows.monitors()):
                raise ValueError("The monitor layout changed. Refresh displays and select the table again.")
            if not self.monitor.contains(self.region.x, self.region.y, self.region.width, self.region.height):
                raise ValueError("The selected area no longer fits the monitor. Select it again.")
            # Keep the recorder and its dialogs away from the pixels being recorded.
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            r = self.region
            if rx < r.x+r.width and rx+rw > r.x and ry < r.y+r.height and ry+rh > r.y:
                raise ValueError("Move this recorder window onto the laptop screen before starting.")
            folder = Path(self.folder_var.get().strip()).expanduser()
            if not folder.is_absolute():
                raise ValueError("Choose an absolute folder path for recordings.")
            durations = {"1 minute (test)": 60, "10 minutes": 600, "1 hour": 3600, "2 hours": 7200, "3 hours": 10800}
            self.encoder = find_encoder()
            self.health_error = None
            windows.keep_awake(True)
            recorder = Recorder(self.encoder, self.region, folder,
                                RecordingOptions(duration_seconds=durations[self.duration_var.get()]))
            session = recorder.start()
            self.recorder = recorder
            self.last_session = Path(session)
            self.last_state = "starting"
            self.state_var.set("Starting capture…")
            self.detail_var.set("Waiting for the first video frames…")
            self.save_settings()
        except (OSError, ValueError, RuntimeError) as exc:
            release_awake()
            messagebox.showerror("Recording could not start", str(exc), parent=self.root)

    def stop(self, reason="user_requested", error=None):
        if self.recorder:
            self.recorder.stop(reason=reason, error=error)
            self.state_var.set("Saving recording…")

    def open_session(self):
        if self.last_session and self.last_session.is_dir():
            os.startfile(str(self.last_session))

    def preview_worker(self, region, generation, max_width=760, max_height=400):
        try:
            result = windows.preview_ppm(region.x, region.y, region.width, region.height, max_width, max_height)
            self.preview_queue.put_nowait((generation, result, None))
        except Exception as exc:
            try:
                self.preview_queue.put_nowait((generation, None, str(exc)))
            except queue.Full:
                pass

    def tick(self):
        now = time.monotonic()
        state = self.recorder.snapshot() if self.recorder else {"state": "idle"}
        active = state["state"] in ACTIVE_STATES
        while True:
            try:
                generation, result, error = self.preview_queue.get_nowait()
            except queue.Empty:
                break
            self.preview_busy = False
            if generation != self.preview_generation:
                continue
            if error:
                self.preview_label.configure(image="", text="Preview unavailable")
                self.preview_label.image = None
                self.preview_note.set(f"Preview unavailable: {error}")
            else:
                self.photo = tk.PhotoImage(data=result[0], format="PPM")
                self.preview_label.configure(image=self.photo, text="")
                self.preview_label.image = self.photo
                self.preview_note.set("Live preview · " + datetime.now().strftime("%H:%M:%S") + " · Video saves at original size")
        if self.region and not self.preview_busy and now - self.last_preview > 1.0 and not self.closing:
            self.last_preview, self.preview_busy = now, True
            # Read widget dimensions on the Tk thread; capture scales the whole
            # table to fit this space without changing the recorded resolution.
            bounds = (max(1, min(760, self.preview_label.winfo_width() - 4)),
                      max(1, min(400, self.preview_label.winfo_height() - 4)))
            threading.Thread(target=self.preview_worker, args=(self.region, self.preview_generation, *bounds), daemon=True).start()
        if active and not getattr(self, 'window_target', None) and now - self.last_monitor_check > 2:
            self.last_monitor_check = now
            try:
                if not any(m.matches_capture(self.monitor) for m in windows.monitors()):
                    self.health_error = "Monitor configuration changed during capture. Check the final video part."
                    self.stop(reason="display_changed", error=self.health_error)
            except OSError as exc:
                self.health_error = f"Display connection lost: {exc}"
                self.stop(reason="display_lost", error=self.health_error)
        if self.recorder:
            self.elapsed_var.set(clock_text(state.get("elapsed_seconds", 0)))
            labels = {"starting": "Starting capture…", "recording": "Recording", "stopping": "Saving recording…",
                      "complete": "Session saved", "error": "Recording needs attention"}
            self.state_var.set(labels.get(state["state"], "Ready"))
            if state["state"] == "recording":
                self.detail_var.set(f"{state.get('frames', 0):,} frames encoded · Video {clock_text(state.get('media_seconds', 0))} · "
                                    "Keep the table visible. Stop & save finishes the current part.")
            if state["state"] != self.last_state and state["state"] in {"complete", "error"}:
                release_awake()
                self.detail_var.set(self.health_error or state.get("error") or
                                    f"Saved to {self.last_session}. Open the session folder to review the video.")
                if self.health_error:
                    self.state_var.set("Capture stopped — check video")
            self.last_state = state["state"]
        for widget in (self.refresh_button, self.select_button, self.browse_button, self.folder_entry):
            widget.configure(state="disabled" if active else "normal")
        self.monitor_combo.configure(state="disabled" if active else "readonly")
        self.duration_combo.configure(state="disabled" if active else "readonly")
        self.start_button.configure(state="normal" if self.region and self.encoder and not active else "disabled")
        self.stop_button.configure(state="normal" if state["state"] in {"starting", "recording"} else "disabled")
        self.open_button.configure(state="normal" if self.last_session else "disabled")
        if self.closing and not active:
            self.destroy()
            return
        self.tick_id = self.root.after(200, self.tick)

    def destroy(self):
        self.closing = True
        if self.tick_id is not None:
            try:
                self.root.after_cancel(self.tick_id)
            except tk.TclError:
                pass
            self.tick_id = None
        release_awake()
        self.root.destroy()

    def close(self):
        if self.recorder and self.recorder.snapshot()["state"] in ACTIVE_STATES:
            if not messagebox.askyesno("Finish recording?", "Stop and save the current recording before closing?", parent=self.root):
                return
            self.closing = True
            self.stop()
            return
        self.destroy()


def main():
    windows.enable_dpi_awareness()
    root = tk.Tk()
    try:
        from .window_app import WindowRecorderApp
        WindowRecorderApp(root)
        root.mainloop()
    except Exception as exc:
        messagebox.showerror("Ada Poker", f"The recorder could not open:\n\n{exc}", parent=root)
        raise
    finally:
        release_awake()
