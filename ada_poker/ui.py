"""Local player-history window: Python stdlib service and installed Edge."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .ui_data import Library

ASSETS=Path(__file__).with_name('web')


class AppServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False

    def __init__(self, library, token=None, recording=None):
        self.library=library
        self.recording=recording
        self.detached_at=None
        self.token=token or secrets.token_urlsafe(32)
        self.last_access=time.monotonic()
        super().__init__(('127.0.0.1',0),Handler)

    @property
    def origin(self):return f'http://127.0.0.1:{self.server_port}'


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def send(self,status,body,kind='application/json; charset=utf-8'):
        if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cross-Origin-Resource-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def do_GET(self):
        parsed=urlsplit(self.path)
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':
            self.send(403,{'error':'Invalid local host.'});return
        assets={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/styles.css':'styles.css','/recording.js':'recording.js'}
        if parsed.path in assets:
            path=ASSETS/assets[parsed.path]
            types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}
            self.send(200,path.read_bytes(),types[path.suffix]);return
        origin=self.headers.get('Origin')
        if (origin and origin!=self.server.origin) or not secrets.compare_digest(self.headers.get('X-Ada-Token',''),self.server.token):
            self.send(403,{'error':'Open this interface from the Ada Poker launcher.'});return
        self.server.last_access=time.monotonic()
        params={k:v[-1] for k,v in parse_qs(parsed.query).items()}
        library=self.server.library
        try:
            recorder=self.server.recording
            if parsed.path=='/api/ping':self.server.detached_at=None
            if parsed.path.startswith('/api/recording/'):
                if recorder is None:raise ValueError('Recording is unavailable in this example window.')
                action=parsed.path.rsplit('/',1)[-1]
                if action=='status':self.send(200,recorder.snapshot())
                elif action=='windows':self.send(200,recorder.list_windows())
                elif action=='preview':self.send(200,recorder.preview(params.get('window',''),params.get('mode','window_client')),'image/png')
                else:self.send(404,{'error':'Not found.'})
                return
            with library.lock:
                if parsed.path=='/api/config':
                    self.server.detached_at=None
                    self.send(200,{'examples':library.fixture is not None,'recording':recorder is not None})
                elif parsed.path=='/api/ping':self.send(200,{'ok':True})
                elif parsed.path=='/api/search':self.send(200,library.search(params.get('q','')))
                elif parsed.path=='/api/hands':self.send(200,library.results(params.get('player',''),params,params.get('page',0)))
                elif parsed.path=='/api/hand':self.send(200,library.hand(params.get('id',''),params.get('player','')))
                elif parsed.path=='/api/evidence':
                    hand_id=params.get('id','')
                    library.hand(hand_id,params.get('player',''))
                    paths=library.evidence(hand_id,params.get('street',''))
                    index=int(params.get('index',0))
                    if not 0<=index<len(paths):raise KeyError('No screenshot was supplied for this street.')
                    path=paths[index]
                    self.send(200,path.read_bytes(),'image/png' if path.suffix.lower()=='.png' else 'image/jpeg')
                else:self.send(404,{'error':'Not found.'})
        except KeyError as exc:self.send(404,{'error':str(exc.args[0])})
        except OSError as exc:self.send(400,{'error':str(exc)})
        except (ValueError,TypeError):self.send(400,{'error':'The supplied hand data or filter value is invalid.'})
        except Exception:self.send(500,{'error':'Could not read the local hand library. Check that the database is available.'})


    def do_POST(self):
        path=urlsplit(self.path).path
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=8192:raise ValueError('Invalid request size.')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError('Invalid request.')
            supplied=self.headers.get('X-Ada-Token','')
            if path=='/api/detach':supplied=supplied or data.get('token','')
            if (self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}' or
                self.headers.get('Origin',self.server.origin)!=self.server.origin or
                not isinstance(supplied,str) or not secrets.compare_digest(supplied,self.server.token)):
                self.send(403,{'error':'Open this interface from the Ada Poker launcher.'});return
            if path=='/api/detach':
                self.server.detached_at=time.monotonic();self.send(200,{'ok':True});return
            recorder=self.server.recording
            if recorder is None:raise ValueError('Recording is unavailable in this example window.')
            if path=='/api/recording/start':
                value=recorder.start(data.get('window',''),data.get('output_root',''),data.get('seconds',10800),data.get('mode','window_client'))
            elif path=='/api/recording/stop':value=recorder.stop()
            elif path=='/api/recording/retry':value=recorder.retry()
            elif path=='/api/recording/folder':value={'folder':recorder.choose_folder()}
            elif path=='/api/recording/open':recorder.open_folder();value={'ok':True}
            else:self.send(404,{'error':'Not found.'});return
            self.send(200,value)
        except (ValueError,TypeError,OSError) as exc:self.send(400,{'error':str(exc)})
        except Exception:self.send(500,{'error':'The recording request failed. Your existing saved files were kept.'})


def owner_alive(owner):
    import ctypes as ct
    from ctypes import wintypes as wt
    u=ct.WinDLL('user32');u.IsWindow.argtypes=[wt.HWND]
    u.GetWindowThreadProcessId.argtypes=[wt.HWND,ct.POINTER(wt.DWORD)]
    pid=wt.DWORD();u.GetWindowThreadProcessId(owner.hwnd,ct.byref(pid))
    return bool(u.IsWindow(owner.hwnd)) and pid.value==owner.pid


def launch_window(url):
    candidates=[Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                Path('C:/Program Files/Microsoft/Edge/Application/msedge.exe')]
    edge=next((p for p in candidates if p.exists()),None)
    if edge:
        from .windows import capture_windows
        before={w.hwnd for w in capture_windows()}
        profile=Path(__file__).resolve().parents[1]/'.local'/'ui-browser'
        subprocess.Popen([str(edge),'--app='+url,'--window-size=1220,860',
            '--user-data-dir='+str(profile),'--no-first-run','--no-default-browser-check',
            '--disable-background-networking'],creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(150):
            owner=next((w for w in capture_windows() if w.hwnd not in before and w.title=='Ada Poker'),None)
            if owner:return owner
            time.sleep(.1)
    else:webbrowser.open(url)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis-root',type=Path,default=Path.home()/'Videos'/'Ada Poker Analysis')
    parser.add_argument('--fixture',type=Path,help='Design/test example records, kept separate from the database.')
    parser.add_argument('--no-browser',action='store_true',help='Print the local address for development checks.')
    args=parser.parse_args()
    records=json.loads(args.fixture.read_text(encoding='utf-8-sig')) if args.fixture else None
    from .windows import enable_dpi_awareness
    from .recording_service import RecordingService
    enable_dpi_awareness()
    recording=None if records is not None else RecordingService(args.analysis_root,Path(__file__).resolve().parents[1]/'.local'/'recording-state.json')
    server=AppServer(Library(args.analysis_root,records),recording=recording)
    url=server.origin+'/#'+server.token
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    owner=None
    try:
        if args.no_browser:print(url,flush=True)
        else:owner=launch_window(url)
        closing=False
        while True:
            now=time.monotonic()
            lost_owner=owner is not None and not owner_alive(owner)
            detached=server.detached_at is not None and now-server.detached_at>3
            idle=owner is None and now-server.last_access>90
            if not closing and (lost_owner or detached or idle):
                closing=True
                if recording:recording.close()
            if closing and (recording is None or not recording.snapshot()['busy']):break
            time.sleep(.2)
    except KeyboardInterrupt:
        if recording:recording.close()
    finally:
        if recording:
            recording.close()
            if recording.worker:recording.worker.join()
        server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':main()
