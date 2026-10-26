"""One local recording job, followed by offline analysis after final save."""
from pathlib import Path
import json
import os
import struct
import threading
import zlib

from . import windows
from .app import find_encoder
from .recorder import CaptureRegion, Recorder, RecordingOptions, _atomic_write_json
from .verify import verify_session
from .analyze import analyze

BUSY = {'starting','recording','stopping','analyzing'}


def preview_png(target,visible_window=False):
    ppm,width,height=windows.window_preview_ppm(target,720,320,visible_window=visible_window)
    rgb=ppm.split(b'\n',3)[3]
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
    pixels=b''.join(b'\0'+rgb[y*width*3:(y+1)*width*3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(pixels,3))+chunk(b'IEND',b''))


class RecordingService:
    def __init__(self, analysis_root, state_path=None):
        self.analysis_root=Path(analysis_root).resolve()
        self.state_path=Path(state_path) if state_path else None
        self.lock=threading.RLock()
        self.recorder=None
        self.worker=None
        self.targets={}
        self.closing=False
        self.stop_requested=False
        self.revision=0
        self.data={'state':'idle','message':'Ready to record','elapsed':0,'session':None,
                   'output_root':str(Path.home()/'Videos'/'Ada Poker'),'warning':None,'hands':None}
        if self.state_path and self.state_path.is_file():
            try:
                saved=json.loads(self.state_path.read_text(encoding='utf-8'))
                for key in ('output_root','session','state','message','hands','warning'):
                    if key in saved:self.data[key]=saved[key]
                if self.data['state'] in BUSY:
                    self.data.update(state='error',message='The previous task was interrupted. Its saved files were kept.')
            except (OSError,ValueError,TypeError):pass

    def _persist(self):
        if self.state_path:
            try:
                self.state_path.parent.mkdir(parents=True,exist_ok=True)
                _atomic_write_json(self.state_path,self.data)
            except OSError as exc:
                self.data['warning']=f'Could not save app status: {exc}'

    def snapshot(self):
        with self.lock:
            data=dict(self.data)
            if self.recorder and data['state'] in {'starting','recording','stopping'}:
                snap=self.recorder.snapshot()
                data['elapsed']=snap.get('elapsed_seconds',0)
                if snap['state'] in BUSY:data['state']=snap['state']
            data['busy']=self.worker is not None and self.worker.is_alive()
            data['revision']=self.revision
            data['can_retry']=data['state']=='error' and bool(data['session']) and not data['busy']
            return data

    def list_windows(self):
        targets=windows.capture_windows()
        with self.lock:self.targets={str(w.hwnd):w for w in targets}
        return [{'id':str(w.hwnd),'title':windows.window_label(w),'width':w.width,'height':w.height,
                 'minimized':w.minimized,'blocked':bool(w.affinity)} for w in targets]

    def target(self, identifier):
        with self.lock:
            target=self.targets.get(str(identifier))
        if target is None:raise ValueError('Refresh windows and select the table window.')
        return windows.validate_window(target, allow_resize=True)

    @staticmethod
    def visible_mode(mode):
        if mode not in ('window_client','visible_window'):raise ValueError('Choose a valid capture method.')
        return mode=='visible_window'

    def preview(self, identifier, mode='window_client'):
        return preview_png(self.target(identifier),self.visible_mode(mode))

    def start(self, identifier, output_root, seconds, mode='window_client'):
        with self.lock:
            if self.closing:raise ValueError('This window is closing. Reopen Ada Poker to record.')
            if self.worker and self.worker.is_alive():raise ValueError('Wait for recording or analysis to finish.')
            target=self.target(identifier)
            visible=self.visible_mode(mode)
            region=CaptureRegion(*windows.window_client_bounds(target)) if visible else CaptureRegion(0,0,target.width,target.height)
            folder=Path(output_root).expanduser()
            if not folder.is_absolute():raise ValueError('Choose an absolute recordings folder.')
            options=RecordingOptions(duration_seconds=int(seconds))
            self.stop_requested=False
            self.recorder=None
            self.data.update(state='starting',message='Starting capture...',elapsed=0,session=None,
                             output_root=str(folder),warning=None,hands=None)
            self._persist()
            self.worker=threading.Thread(target=self._record,args=(target,folder,options,region,visible),daemon=False)
            self.worker.start()
        return self.snapshot()

    def stop(self):
        with self.lock:
            self.stop_requested=True
            if self.recorder:self.recorder.stop(reason='app_closed' if self.closing else 'user_requested')
            if self.data['state'] in {'starting','recording','stopping'}:
                self.data.update(state='stopping',message='Saving recording...')
        return self.snapshot()

    def close(self):
        with self.lock:self.closing=True
        return self.stop()

    def _progress(self, message):
        with self.lock:self.data['message']=str(message)

    def _record(self, target, folder, options, region, visible):
        awake=False
        try:
            windows.keep_awake(True);awake=True
            encoder=find_encoder()
            recorder=Recorder(encoder,region,folder,options,window=target,visible_window=visible)
            with self.lock:
                self.recorder=recorder
                session=recorder.start()
                self.data['session']=str(session)
                self._persist()
                if self.stop_requested:recorder.stop(reason='app_closed' if self.closing else 'user_requested')
            while recorder.snapshot()['state'] in {'starting','recording','stopping'}:
                recorder.join(.2)
            recorder.join()
            snap=recorder.snapshot()
            with self.lock:
                self.data['elapsed']=snap['elapsed_seconds']
                self.data['warning']=snap.get('error')
            # Verification and analysis can only see the final saved segments.
            self._analyze_saved(session,encoder)
        except Exception as exc:
            with self.lock:
                self.data.update(state='error',message=str(exc))
                self._persist()
        finally:
            if awake:
                try:windows.keep_awake(False)
                except OSError:pass

    def _analyze_saved(self, session, encoder):
        with self.lock:
            self.data.update(state='analyzing',message='Checking the saved video...')
            self._persist()
        verify_session(Path(session),encoder)
        analyze(Path(session),self.analysis_root/Path(session).name,self._progress)
        summary=json.loads((self.analysis_root/Path(session).name/'summary.json').read_text(encoding='utf-8'))
        with self.lock:
            count=summary.get('hands',0)
            self.data.update(state='complete',hands=count,message=f'Saved and analyzed. {count} hands added to your library.')
            self.revision+=1
            self._persist()

    def retry(self):
        with self.lock:
            if self.closing or not self.snapshot()['can_retry']:raise ValueError('No saved recording is ready to retry.')
            session=Path(self.data['session'])
            self.data.update(state='analyzing',message='Retrying saved recording analysis...')
            def work():
                awake=False
                try:
                    windows.keep_awake(True);awake=True
                    self._analyze_saved(session,find_encoder())
                except Exception as exc:
                    with self.lock:self.data.update(state='error',message=str(exc));self._persist()
                finally:
                    if awake:
                        try:windows.keep_awake(False)
                        except OSError:pass
            self.worker=threading.Thread(target=work,daemon=False);self.worker.start()
        return self.snapshot()

    def choose_folder(self):
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
        try:return filedialog.askdirectory(parent=root,title='Save recordings to',initialdir=self.data['output_root']) or None
        finally:root.destroy()

    def open_folder(self):
        with self.lock:session=self.data['session']
        if session and Path(session).is_dir():os.startfile(session)
