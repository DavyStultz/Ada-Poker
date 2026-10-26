"""Window/client source selection for the existing recorder controls."""
from collections import Counter
from pathlib import Path
import json
import queue
from tkinter import messagebox

from . import windows
from . import app
from .recorder import CaptureRegion, Recorder, RecordingOptions


class WindowRecorderApp(app.RecorderApp):
    def __init__(self, root):
        self.window_target = None
        self.available_windows = []
        super().__init__(root)

    def _build_ui(self):
        super()._build_ui()
        self.select_button.pack_forget()
        self.refresh_button.configure(text='Refresh windows')
        self.monitor_combo.configure(width=66)
        self.area_var.set('Choose the ClubGG table window from the list above.')
        self.state_var.set('Choose a window')
        self.preview_label.configure(text='Choose a window to preview')

    def refresh_monitors(self, initial=False):
        # Called by the inherited Refresh button. Display discovery is only
        # needed to place the recorder UI, not to select its capture source.
        try:
            self.available_windows = windows.capture_windows()
        except OSError as exc:
            messagebox.showerror('Windows unavailable', str(exc), parent=self.root)
            return
        counts = Counter(w.title for w in self.available_windows)
        self.monitor_combo.configure(values=[
            f'{windows.window_label(w)[:90]}  |  {w.width} x {w.height}'
            + (f' (window {i+1})' if counts[w.title] > 1 else '')
            + (' (minimized)' if w.minimized else '') for i,w in enumerate(self.available_windows)])
        match = next((i for i,w in enumerate(self.available_windows)
                      if self.window_target and (w.hwnd,w.pid,w.class_name) ==
                      (self.window_target.hwnd,self.window_target.pid,self.window_target.class_name)),None)
        if match is not None:
            self.monitor_combo.current(match)
            self.monitor_changed()
        else:
            self.monitor_var.set('Select an open window' if self.available_windows else 'No open windows found - open ClubGG, then Refresh windows')
            self.clear_region()

    def restore_region(self):
        # A saved desktop rectangle or stale HWND must never become a target.
        pass

    def clear_region(self):
        self.window_target = None
        self.region = None
        self.preview_generation += 1
        self.preview_label.configure(image='', text='Choose a window to preview')
        self.preview_label.image = None
        self.preview_note.set('The preview shows only the selected window content.')
        self.area_var.set('Choose the ClubGG table window from the list above.')
        self.state_var.set('Choose a window')

    def monitor_changed(self, _event=None):
        index = self.monitor_combo.current()
        if not 0 <= index < len(self.available_windows):
            self.clear_region()
            return
        selected = self.available_windows[index]
        self.clear_region()
        try:
            current = windows.window_info(selected.hwnd)
            if (current.pid,current.class_name) != (selected.pid,selected.class_name):
                raise OSError('This window was replaced. Refresh windows and choose it again.')
            windows.validate_window(current)
            self.window_target = current
            self.region = CaptureRegion(0,0,current.width,current.height)
            self.area_var.set(f'Window content: {current.width} x {current.height} pixels - follows window movement')
            self.state_var.set('Ready to record')
            self.detail_var.set('Keep the selected window visible and at this size. OS borders and title bar are excluded.')
            self.last_preview = 0
        except (OSError,ValueError) as exc:
            self.state_var.set('Window unavailable')
            self.preview_note.set(str(exc))
            self.detail_var.set(str(exc))

    def save_settings(self):
        # Preserve older region settings for the legacy tools, but never restore
        # them into this UI. A window is explicitly selected each launch.
        value = dict(self.settings)
        value.update(output_root=self.folder_var.get(),capture_mode='window_client')
        try:
            app.SETTINGS_PATH.parent.mkdir(parents=True,exist_ok=True)
            temp = app.SETTINGS_PATH.with_suffix('.tmp')
            temp.write_text(json.dumps(value,indent=2),encoding='utf-8')
            temp.replace(app.SETTINGS_PATH)
        except OSError as exc:
            self.detail_var.set(f'Settings could not be saved: {exc}')

    def preview_worker(self, region, generation, max_width=760, max_height=400):
        target = self.window_target
        try:
            if target is None:
                raise OSError('Choose a window to preview.')
            result = windows.window_preview_ppm(target,max_width,max_height)
            self.preview_queue.put_nowait((generation,result,None))
        except Exception as exc:
            try:
                self.preview_queue.put_nowait((generation,None,str(exc)))
            except queue.Full:
                pass

    def start(self):
        if self.recorder and self.recorder.snapshot()['state'] in app.ACTIVE_STATES:
            return
        if not self.window_target:
            return
        try:
            self.window_target = windows.validate_window(self.window_target, allow_resize=True)
            self.region = CaptureRegion(0,0,self.window_target.width,self.window_target.height)
            folder = Path(self.folder_var.get().strip()).expanduser()
            if not folder.is_absolute():
                raise ValueError('Choose an absolute folder path for recordings.')
            durations = {'1 minute (test)':60,'10 minutes':600,'1 hour':3600,'2 hours':7200,'3 hours':10800}
            self.encoder = app.find_encoder()
            self.health_error = None
            windows.keep_awake(True)
            recorder = Recorder(self.encoder,self.region,folder,
                                RecordingOptions(duration_seconds=durations[self.duration_var.get()]),
                                window=self.window_target)
            session = recorder.start()
            self.recorder = recorder
            self.last_session = Path(session)
            self.last_state = 'starting'
            self.state_var.set('Starting window capture...')
            self.detail_var.set('Waiting for the first video frames...')
            self.save_settings()
        except (OSError,ValueError,RuntimeError) as exc:
            app.release_awake()
            messagebox.showerror('Recording could not start',str(exc),parent=self.root)
