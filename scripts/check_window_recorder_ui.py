"""Verify the actual recorder controls with a separate synthetic client."""
from pathlib import Path
from dataclasses import replace
import json
import subprocess
import sys
import time
import tkinter as tk
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ada_poker import app, windows, window_app
from ada_poker.recorder import RecordingOptions
from ada_poker.verify import verify_session


def main():
    windows.enable_dpi_awareness()
    folder=(Path('.dev/window-ui-check')/uuid.uuid4().hex).resolve();folder.mkdir(parents=True)
    child=subprocess.Popen([sys.executable,str(Path('scripts/check_window_capture.py').resolve()),'--target',str(folder)],creationflags=subprocess.CREATE_NO_WINDOW)
    root=None;ui=None
    try:
        for _ in range(100):
            if (folder/'target.json').exists():break
            time.sleep(.05)
        info=json.loads((folder/'target.json').read_text(encoding='utf-8'))
        with patch.object(app,'SETTINGS_PATH',folder/'settings.json'),patch.object(app.messagebox,'showerror',side_effect=AssertionError('Unexpected UI error dialog')):
            root=tk.Tk();ui=window_app.WindowRecorderApp(root)
            root.geometry('860x850+1040+50')
            def pump(until,timeout=8):
                end=time.monotonic()+timeout
                while time.monotonic()<end:
                    root.update()
                    if until():return
                    time.sleep(.03)
                raise TimeoutError('UI check timed out')
            assert ui.region is None and ui.window_target is None,'Window auto-selected'
            assert not ui.select_button.winfo_manager(),'Drag selector still visible'
            index=next(i for i,w in enumerate(ui.available_windows) if w.hwnd==info['hwnd'])
            ui.monitor_combo.current(index);ui.monitor_changed()
            pump(lambda:ui.preview_label.image is not None)
            assert ui.window_target.hwnd==info['hwnd']
            for width,height in [(860,850),(800,790)]:
                root.geometry(f'{width}x{height}+1040+50');root.update()
                for button in (ui.start_button,ui.stop_button,ui.open_button):
                    assert button.winfo_ismapped()
                    assert button.winfo_rooty()+button.winfo_height()<=root.winfo_rooty()+root.winfo_height(),'Clipped controls'
            selected=ui.window_target
            with patch.object(windows,'window_info',return_value=replace(selected,affinity=0x11)):
                ui.monitor_changed();pump(lambda:str(ui.start_button['state'])=='disabled')
                assert ui.region is None and str(ui.start_button['state'])=='disabled'
                assert 'blocking Windows capture' in ui.preview_note.get()
            ui.monitor_combo.current(index);ui.monitor_changed()
            pump(lambda:ui.preview_label.image is not None)
            ui.folder_var.set(str(folder))
            with patch.object(window_app,'RecordingOptions',side_effect=lambda **kw:RecordingOptions(duration_seconds=3,segment_seconds=2)):
                ui.start()
            pump(lambda:ui.recorder.snapshot()['state'] in ('complete','error'),30)
            assert ui.recorder.snapshot()['state']=='complete',ui.recorder.snapshot()
            assert json.loads((ui.last_session/'session.json').read_text())['capture_source']['mode']=='window_client'
            check=verify_session(ui.last_session,ui.encoder)
            assert check['frames_decoded']>0
            saved=json.loads((folder/'settings.json').read_text())
            assert saved['capture_mode']=='window_client' and 'hwnd' not in saved
            print('Window selector, preview, controls at both sizes, capture-exclusion UI, Start/save: PASS',flush=True)
            print(check,flush=True)
            (folder/'verification.json').write_text(json.dumps(check,indent=2))
            ui.destroy();root=None
    finally:
        if ui and ui.recorder:ui.recorder.stop();ui.recorder.join(20)
        if root:
            try:ui.destroy() if ui else root.destroy()
            except tk.TclError:pass
        if child.poll() is None:
            (folder/'command.txt').write_text('close',encoding='utf-8')
            try:child.wait(5)
            except subprocess.TimeoutExpired:child.terminate();child.wait(5)


if __name__=='__main__':main()
