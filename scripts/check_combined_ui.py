"""Synthetic browser checks of the integrated recorder/search controls."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import threading
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from ui_fixtures import examples
from ada_poker.ui import AppServer,Handler,ASSETS
from ada_poker.ui_data import Library

class ExampleRecording:
    def __init__(self):self.state='idle';self.polls=0;self.starts=0;self.stops=0;self.list_calls=0
    def snapshot(self):
        if self.state=='analyzing':
            self.polls+=1
            if self.polls>=4:self.state='complete'
        return {'state':self.state,'busy':self.state in ('recording','analyzing'),'message':self.state,
                'elapsed':12,'session':None if self.state=='idle' else 'synthetic-session',
                'output_root':'C:/example recordings','warning':None,'can_retry':False,'hands':3,
                'revision':1 if self.state=='complete' else 0}
    def list_windows(self):
        self.list_calls+=1
        if self.list_calls==1:return []  # Table opens after the panel.
        entries=[{'id':'42','title':'ClubGG - Example table window','width':640,'height':360,'minimized':False,'blocked':False}]
        if self.list_calls>=3:entries.append({'id':'43','title':'ClubGG - Another table','width':800,'height':600,'minimized':False,'blocked':False})
        return entries
    def preview(self,identifier,mode='window_client'):
        assert identifier=='42'
        assert mode=='visible_window'
        def chunk(t,d):return struct.pack('!I',len(d))+t+d+struct.pack('!I',zlib.crc32(t+d))
        return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',640,360,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes([28,65,43])*640)*360))+chunk(b'IEND',b'')
    def start(self,identifier,folder,seconds,mode='window_client'):
        assert mode=='visible_window'
        assert identifier=='42';self.starts+=1;self.state='recording';return self.snapshot()
    def stop(self):self.stops+=1;self.state='analyzing';return self.snapshot()

class TestHandler(Handler):
    def do_GET(self):
        if self.path.startswith('/app.js'):
            self.send(200,(ASSETS/'app.js').read_bytes()+b'\n'+Path('tests/browser_recording.js').read_bytes(),'text/javascript; charset=utf-8')
        else:super().do_GET()

def main():
    out=Path('.dev/combined-ui-check').resolve();out.mkdir(parents=True,exist_ok=True)
    for width in (1220,960):
        recorder=ExampleRecording();server=AppServer(Library(out,examples()),recording=recorder);server.RequestHandlerClass=TestHandler
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            command=[r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe','--headless','--disable-gpu',
                     '--no-first-run','--disable-background-networking','--no-default-browser-check','--disable-component-update',
                     '--user-data-dir='+str(out/'profile'),f'--window-size={width},1100','--virtual-time-budget=15000',
                     '--dump-dom','--screenshot='+str(out/f'combined-{width}.png'),server.origin+'/#'+server.token]
            result=subprocess.run(command,capture_output=True,timeout=45,creationflags=subprocess.CREATE_NO_WINDOW)
            (out/f'combined-{width}.html').write_bytes(result.stdout)
            passed=b'data-test-result="pass"' in result.stdout
            print(width,'PASS' if passed else 'FAIL',flush=True)
            if not passed:
                import re
                print(re.findall(rb'data-test-result="[^"]*',result.stdout));raise SystemExit(1)
            assert (recorder.starts,recorder.stops)==(1,1)
        finally:server.shutdown();server.server_close();thread.join(5)

if __name__=='__main__':main()
