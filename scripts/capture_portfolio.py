"""Capture reproducible portfolio screenshots using only synthetic UI fixtures."""
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from ui_fixtures import examples
from ada_poker.ui import AppServer, ASSETS, Handler
from ada_poker.ui_data import Library
from demo import prepare_demo


class ScreenshotHandler(Handler):
    def do_GET(self):
        if self.path == '/app.js':
            script = (ASSETS / 'app.js').read_bytes()
            script += b'\n' + (ROOT / 'tests' / 'browser_workflows.js').read_bytes()
            self.send(200, script, 'text/javascript; charset=utf-8')
        else:
            super().do_GET()


def main():
    output = ROOT / '.dev' / 'portfolio-images'
    prepare_demo(output)
    edge = next((path for path in (
        Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
        Path('C:/Program Files/Microsoft/Edge/Application/msedge.exe'),
    ) if path.is_file()), None)
    if edge is None:
        raise SystemExit('Microsoft Edge is required for screenshots.')
    server = AppServer(Library(output, examples()))
    server.RequestHandlerClass = ScreenshotHandler
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for mode, filename, height in (
            ('detail', 'hand-detail.png', 1800),
            ('results', 'player-search.png', 1100),
        ):
            command = [str(edge), '--headless', '--disable-gpu', '--no-first-run',
                       '--disable-background-networking', '--disable-component-update',
                       '--no-default-browser-check', '--user-data-dir=' + str(output / 'profile'),
                       f'--window-size=1220,{height}', '--virtual-time-budget=12000',
                       '--dump-dom', '--screenshot=' + str(output / filename),
                       server.origin + '/?test=' + mode + '#' + server.token]
            result = subprocess.run(command, capture_output=True, timeout=45,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode or b'data-test-result="pass"' not in result.stdout:
                raise RuntimeError('Synthetic browser checks failed for ' + filename)
            print('PASS ' + filename, flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == '__main__':
    main()
