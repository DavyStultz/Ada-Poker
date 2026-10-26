"""Exercise the real browser UI against synthetic, isolated records."""
from pathlib import Path
import json
import subprocess
import struct
import sys
import threading
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from ui_fixtures import examples
from ada_poker.ui import AppServer, Handler, ASSETS
from ada_poker.ui_data import Library

output=Path('.dev/search-ui-check').resolve();output.mkdir(parents=True,exist_ok=True)
frames=output/'example-session'/'frames';frames.mkdir(parents=True,exist_ok=True)
def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
pixels=b''.join(b'\x00'+bytes([25,65+y//8,45])*640 for y in range(360))
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',640,360,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
(frames/'example.png').write_bytes(png)
(frames/'example-2.png').write_bytes(png)
(output/'fixtures.json').write_text(json.dumps(examples()),encoding='utf-8')
test_js=Path('tests/browser_workflows.js').read_bytes()+b'\n'+Path('tests/browser_edge_cases.js').read_bytes()
class TestHandler(Handler):
    def do_GET(self):
        if self.path.startswith('/app.js'):
            self.send(200,(ASSETS/'app.js').read_bytes()+b'\n'+test_js,'text/javascript; charset=utf-8')
        else:super().do_GET()

records=examples()
records[1]['hand']['street_evidence']['turn'].append({'image':'frames/example-2.png'})
server=AppServer(Library(output,records))
server.RequestHandlerClass=TestHandler
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
edge=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
failed=[]
try:
    for mode,width,height in [('results',1220,1000),('detail',1220,1500),('compact',960,950),('narrow',780,900),('landing',1000,800),('edge-cases',1000,850)]:
        command=[edge,'--headless','--disable-gpu','--no-first-run','--disable-background-networking',
                 '--disable-component-update','--no-default-browser-check','--user-data-dir='+str(output/'profile'),
                 f'--window-size={width},{height}','--virtual-time-budget=12000','--dump-dom',
                 '--screenshot='+str(output/(mode+'.png')),server.origin+'/?test='+mode+'#'+server.token]
        result=subprocess.run(command,capture_output=True,timeout=45,creationflags=subprocess.CREATE_NO_WINDOW)
        (output/(mode+'.html')).write_bytes(result.stdout)
        passed=b'data-test-result="pass"' in result.stdout
        print(mode,result.returncode,'PASS' if passed else 'FAIL',flush=True)
        if not passed:
            import re
            print(re.findall(rb'data-test-[^>]+',result.stdout)[:2])
            print(result.stderr.decode(errors='replace')[-1200:])
            failed.append(mode)
finally:server.shutdown();server.server_close();thread.join(5)

if failed:raise SystemExit(1)
