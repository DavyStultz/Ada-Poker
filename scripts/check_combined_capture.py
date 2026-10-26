"""Check the real app's close/save/offline-analysis flow using synthetic pixels."""
from pathlib import Path
import ctypes as ct
from ctypes import wintypes as wt
import json
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def host(folder):
    from ada_poker import ui, recording_service
    original_service=recording_service.RecordingService
    recording_service.RecordingService=lambda analysis,state: original_service(analysis,folder/'state.json')
    original_launch=ui.launch_window
    def launch(url):
        owner=original_launch(url)
        if owner is None:raise RuntimeError('Could not find the test app window')
        (folder/'connection.json').write_text(json.dumps({'url':url,'hwnd':owner.hwnd}))
        return owner
    ui.launch_window=launch
    sys.argv=['ada_poker','--analysis-root',str(folder/'analysis')]
    ui.main()

def wait_for(predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=predicate()
        if result:return result
        time.sleep(.2)
    raise TimeoutError('Timed out waiting for test stage')

def main():
    from ada_poker.app import find_encoder
    from ada_poker.verify import verify_session
    folder=(ROOT/'.dev/combined-capture-check'/uuid.uuid4().hex).resolve()
    folder.mkdir(parents=True)
    child=subprocess.Popen([sys.executable,str(ROOT/'scripts/check_window_capture.py'),'--target',str(folder)],creationflags=subprocess.CREATE_NO_WINDOW)
    app=None;connection=None
    user32=ct.WinDLL('user32')
    user32.PostMessageW.argtypes=[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM]
    user32.MoveWindow.argtypes=[wt.HWND,ct.c_int,ct.c_int,ct.c_int,ct.c_int,wt.BOOL]
    try:
        wait_for(lambda:(folder/'target.json').exists())
        target=json.loads((folder/'target.json').read_text())
        with (folder/'host.log').open('w') as log:
            app=subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'--host',str(folder)],stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            wait_for(lambda:(folder/'connection.json').exists())
            connection=json.loads((folder/'connection.json').read_text())
            origin,token=connection['url'].split('/#')
            def api(path,data=None):
                request=Request(origin+path,data=None if data is None else json.dumps(data).encode(),headers={'X-Ada-Token':token,'Content-Type':'application/json'})
                with urlopen(request,timeout=10) as response:return json.load(response)
            user32.MoveWindow(connection['hwnd'],850,40,960,860,True)
            listed=api('/api/recording/windows')
            assert any(w['id']==str(target['hwnd']) for w in listed)
            api('/api/recording/start',{'window':str(target['hwnd']),'output_root':str(folder/'recordings'),'seconds':60,'mode':'visible_window'})
            wait_for(lambda:api('/api/recording/status')['state']=='recording')
            time.sleep(2)
            assert not (folder/'analysis').exists(),'Analysis started during capture'
            user32.PostMessageW(connection['hwnd'],0x10,0,0)
            print('Closed test app during capture; waiting for save and local analysis.',flush=True)
            wait_for(lambda:app.poll() is not None,180)
        assert app.returncode==0,(folder/'host.log').read_text()
        state=json.loads((folder/'state.json').read_text())
        assert state['state']=='complete',state
        session=Path(state['session'])
        metadata=json.loads((session/'session.json').read_text())
        assert metadata['snapshot']['stop_reason']=='app_closed',metadata
        assert metadata['capture_source']['mode']=='visible_window',metadata
        verification=verify_session(session,find_encoder())
        assert verification['frames_decoded']>0,verification
        assert (folder/'analysis'/session.name/'summary.json').is_file()
        (folder/'verification.json').write_text(json.dumps({'status':state,'video':verification},indent=2))
        print('Native close/save/analysis PASS:',verification,flush=True)
        print('Artifacts:',folder,flush=True)
    finally:
        if app and app.poll() is None:
            if connection:user32.PostMessageW(connection['hwnd'],0x10,0,0)
            try:app.wait(20)
            except subprocess.TimeoutExpired:app.terminate();app.wait(5)
        (folder/'command.txt').write_text('close')
        try:child.wait(5)
        except subprocess.TimeoutExpired:child.terminate();child.wait(5)

if __name__=='__main__':
    if '--host' in sys.argv:host(Path(sys.argv[-1]))
    else:main()
