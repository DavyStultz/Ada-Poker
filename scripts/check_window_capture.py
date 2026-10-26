"""Exercise native client capture using two synthetic windows, never poker data.

Run from the project root in the normal desktop session. No dependencies added.
"""
from pathlib import Path
import ctypes as ct
from ctypes import wintypes as wt
import json
import subprocess
import sys
import time
import tkinter as tk
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ada_poker import windows
from ada_poker.app import find_encoder
from ada_poker.recorder import CaptureRegion, Recorder, RecordingOptions
from ada_poker.verify import verify_session, read_segments


def target(folder):
    windows.enable_dpi_awareness()
    root=tk.Tk();root.title('Ada capture test');root.geometry('320x240+80+80')
    root.configure(bg='#207840')
    label=tk.Label(root,text='Window A',bg='#207840',fg='white',font=('Segoe UI',22));label.place(x=60,y=85)
    other=tk.Toplevel(root);other.title('Ada capture test');other.geometry('320x240+470+80');other.configure(bg='#804080')
    root.update()
    u=ct.WinDLL('user32');u.GetAncestor.argtypes=[wt.HWND,wt.UINT];u.GetAncestor.restype=wt.HWND
    hwnd=int(u.GetAncestor(root.winfo_id(),2));second=int(u.GetAncestor(other.winfo_id(),2))
    (folder/'target.json').write_text(json.dumps({'hwnd':hwnd,'second':second}),encoding='utf-8')
    previous='';counter=0
    def tick():
        nonlocal previous,counter
        counter+=1;label.configure(text=f'Window A\n{counter}')
        try:command=(folder/'command.txt').read_text(encoding='utf-8')
        except OSError:command=''
        if command!=previous:
            previous=command
            if command=='move':root.geometry('+850+150');root.title('Ada capture test renamed')
            elif command=='resize':root.geometry('360x260')
            elif command=='restore':root.deiconify();root.geometry('320x240+850+150')
            elif command=='minimize':root.iconify()
            elif command=='close':root.destroy();return
            root.update_idletasks()
            (folder/'ack.txt').write_text(command,encoding='utf-8')
        root.after(100,tick)
    tick();root.mainloop()


def main():
    windows.enable_dpi_awareness()
    folder=(Path('.dev/window-capture-check')/uuid.uuid4().hex).resolve();folder.mkdir(parents=True)
    child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--target',str(folder)],creationflags=subprocess.CREATE_NO_WINDOW)
    recorder=None
    def command(value):
        (folder/'command.txt').write_text(value,encoding='utf-8')
        for _ in range(40):
            try:
                if (folder/'ack.txt').read_text(encoding='utf-8')==value:return
            except OSError:pass
            time.sleep(.05)
        raise TimeoutError('Test window did not acknowledge '+value)
    def finish(rec,deadline=15):
        stop=time.monotonic()+deadline
        while rec.snapshot()['state'] in ('starting','recording','stopping') and time.monotonic()<stop:
            rec.join(.1)
        rec.join(1)
        return rec.snapshot()
    def wait_frames(rec):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            state=rec.snapshot()
            if state['frames']>=15:return
            if state['state']=='error':raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError('Encoder did not produce initial frames')
    try:
        for _ in range(100):
            if (folder/'target.json').exists():break
            time.sleep(.05)
        info=json.loads((folder/'target.json').read_text(encoding='utf-8'))
        candidates=windows.capture_windows()
        same=[w for w in candidates if w.title=='Ada capture test']
        assert {w.hwnd for w in same}=={info['hwnd'],info['second']},'Duplicate titles not distinct'
        selected=windows.window_info(info['hwnd'])
        ppm,w,h=windows.window_preview_ppm(selected)
        assert (w,h)==(320,240),'Client size includes decorations or incorrect DPI'
        rgb=ppm.split(b'\n',3)[3]
        assert tuple(rgb[(20*w+20)*3:(20*w+20)*3+3])==(32,120,64),'Preview source mismatch'
        encoder=find_encoder()
        recorder=Recorder(encoder,CaptureRegion(0,0,w,h),folder,RecordingOptions(duration_seconds=8,segment_seconds=3),selected)
        session=recorder.start();wait_frames(recorder)
        command('move')
        result=finish(recorder)
        if result['state']!='complete':
            print((session/'encoder.log').read_text(errors='replace')[-1800:])
            raise AssertionError(result)
        verification=verify_session(session,encoder)
        assert abs(verification['video_seconds']-8)<.3,verification
        samples=[]
        for part in (read_segments(session)[0],read_segments(session)[-1]):
            decoded=subprocess.run([encoder,'-v','error','-i',str(part['path']),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
            assert decoded.returncode==0 and len(decoded.stdout)==w*h*3,'Decode dimensions'
            color=tuple(decoded.stdout[(20*w+20)*3:(20*w+20)*3+3]);samples.append(color)
            assert all(abs(a-b)<8 for a,b in zip(color,(32,120,64))),'Recording switched away from selected window'
        metadata=json.loads((session/'session.json').read_text())
        assert metadata['capture_source']['window']['hwnd']==selected.hwnd
        assert metadata['capture_source']['mode']=='window_client'
        print('Move/title-change recording PASS:',verification,'sample colors:',samples,flush=True)
        for action in ('resize','minimize'):
            command('restore');time.sleep(.2)
            selected=windows.window_info(info['hwnd'])
            recorder=Recorder(encoder,CaptureRegion(0,0,320,240),folder,RecordingOptions(duration_seconds=12),selected)
            interrupted=recorder.start();wait_frames(recorder);command(action)
            result=finish(recorder)
            assert result['state']=='error' and result['stop_reason']=='window_unavailable',result
            assert result['elapsed_seconds']<8,result
            assert verify_session(interrupted,encoder)['decode_errors']==0
            print(action,'stop/save PASS',flush=True)
        (folder/'verification.json').write_text(json.dumps({'moving':verification,'stops':['resize','minimize'],'session':str(session)},indent=2))
        print('Artifacts:',folder)
    finally:
        if recorder:recorder.stop();recorder.join(20)
        if child.poll() is None:
            (folder/'command.txt').write_text('close',encoding='utf-8')
            try:child.wait(5)
            except subprocess.TimeoutExpired:child.terminate();child.wait(5)


if __name__=='__main__':
    if '--target' in sys.argv:target(Path(sys.argv[-1]))
    else:main()
