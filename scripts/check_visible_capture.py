"""Verify visible-client capture with synthetic pixels, or --live-table locally.

The optional live check records eight seconds of the only open ClubGG table into
ignored .dev, without analysis or any player-library writes. No images are sent
to external services. Keep the selected table unobstructed throughout the check.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ada_poker import windows
from ada_poker.app import find_encoder
from ada_poker.recorder import CaptureRegion,Recorder,RecordingOptions
from ada_poker.verify import verify_session,read_segments


def wait_for(predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.1)
    raise TimeoutError('Capture check timed out')


def motion_samples(session,encoder):
    hashes=[]
    for part in read_segments(session):
        result=subprocess.run([encoder,'-v','error','-i',str(part['path']),'-vf','fps=2,scale=160:120',
            '-pix_fmt','rgb24','-f','rawvideo','pipe:1'],capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode==0,result.stderr.decode(errors='replace')
        size=160*120*3
        assert len(result.stdout)%size==0
        hashes.extend(hashlib.sha256(result.stdout[i:i+size]).hexdigest() for i in range(0,len(result.stdout),size))
    return {'sampled_frames':len(hashes),'unique_frames':len(set(hashes)),
            'changed_adjacent_pairs':sum(a!=b for a,b in zip(hashes,hashes[1:]))}


def main():
    windows.enable_dpi_awareness()
    live='--live-table' in sys.argv
    folder=(ROOT/'.dev/visible-capture-check'/uuid.uuid4().hex).resolve();folder.mkdir(parents=True)
    child=None;recorder=None
    try:
        if live:
            targets=[w for w in windows.capture_windows() if w.application.casefold()=='clubgg' and w.title.casefold()!='clubgg']
            assert len(targets)==1,'Need exactly one open ClubGG table'
            target=targets[0]
        else:
            child=subprocess.Popen([sys.executable,str(ROOT/'scripts/check_window_capture.py'),'--target',str(folder)],creationflags=subprocess.CREATE_NO_WINDOW)
            wait_for(lambda:(folder/'target.json').exists())
            target=windows.window_info(json.loads((folder/'target.json').read_text())['hwnd'])
        encoder=find_encoder()
        region=CaptureRegion(*windows.window_client_bounds(target))
        preview=windows.window_preview_ppm(target,360,240,visible_window=True)
        assert preview[0].startswith(b'P6\n')
        recorder=Recorder(encoder,region,folder,RecordingOptions(duration_seconds=8,segment_seconds=3),target,True)
        session=recorder.start()
        wait_for(lambda:recorder.snapshot()['state'] not in ('starting','recording','stopping'))
        recorder.join(5)
        assert recorder.snapshot()['state']=='complete',recorder.snapshot()
        verification=verify_session(session,encoder)
        motion=motion_samples(session,encoder)
        assert motion['unique_frames']>=4 and motion['changed_adjacent_pairs']>=3,motion
        metadata=json.loads((session/'session.json').read_text())
        assert metadata['capture_source']['mode']=='visible_window'
        print('Visible capture PASS:',verification,motion,flush=True)
        if not live:
            recorder=Recorder(encoder,region,folder,RecordingOptions(duration_seconds=15),target,True)
            moved=recorder.start();wait_for(lambda:recorder.snapshot()['frames']>=15)
            (folder/'command.txt').write_text('move')
            wait_for(lambda:recorder.snapshot()['state'] not in ('starting','recording','stopping'))
            recorder.join(5)
            assert recorder.snapshot()['stop_reason']=='window_unavailable',recorder.snapshot()
            assert 'moved' in recorder.snapshot()['error']
            assert verify_session(moved,encoder)['decode_errors']==0
            print('Moved-window stop/save PASS',flush=True)
        (folder/'verification.json').write_text(json.dumps({'live_table':live,'video':verification,'motion':motion,'session':str(session)},indent=2))
        print('Artifacts:',folder,flush=True)
    finally:
        if recorder:recorder.stop();recorder.join(20)
        if child and child.poll() is None:
            (folder/'command.txt').write_text('close')
            try:child.wait(5)
            except subprocess.TimeoutExpired:child.terminate();child.wait(5)


if __name__=='__main__':main()
