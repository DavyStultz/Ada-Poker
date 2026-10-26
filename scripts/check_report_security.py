"""Check report rendering and hostile evidence URLs in a real headless browser."""
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ada_poker.report import write_report


def main():
    output = ROOT / '.dev/report-security' / uuid.uuid4().hex
    output.mkdir(parents=True)
    attack = '</script><script>window.injected=true</script><img src=x onerror="window.injected=true">'
    path = write_report(output, {'session_id': attack, 'frames_analyzed': 0, 'hands': 0}, [], [])
    checks = r'''
<script>
try {
  if(window.injected || document.querySelector('#source img')) throw Error('HTML injection');
  if(!document.getElementById('source').textContent.includes('</script' + '>')) throw Error('Text lost');
  const bad=['javascript:alert(1)','data:image/svg+xml,test','https://example.invalid/x.jpg',
    '//example.invalid/x.jpg','../private.jpg','frames/../../private.jpg',
    'frames/%2e%2e/private.jpg','frames/a.jpg?x=1','frames/a.svg','frames\\a.jpg',null,{}];
  for(const value of bad){
    if(evidenceImage(value)!=='') throw Error('Unsafe URL accepted');
    data.observations=[{time:0,evidence:{image:value,part:'synthetic'},table_visible:false}];
    showFrame(0);
    if(document.getElementById('frame').getAttribute('src')!=='') throw Error('Unsafe URL rendered');
  }
  data.observations=[{time:0,evidence:{image:'frames/p000-000001.jpg',part:'synthetic'},table_visible:false}];
  showFrame(0);
  if(document.getElementById('frame').getAttribute('src')!=='frames/p000-000001.jpg') throw Error('Valid frame rejected');
  document.documentElement.dataset.securityResult='pass';
} catch(error) { document.documentElement.dataset.securityResult='fail'; }
</script>
'''
    path.write_text(path.read_text(encoding='utf-8').replace('</html>', checks + '</html>'), encoding='utf-8')
    edge = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    result = subprocess.run([str(edge), '--headless', '--disable-gpu', '--no-first-run',
                             '--disable-background-networking', '--disable-component-update',
                             '--user-data-dir=' + str(output / 'profile'), '--virtual-time-budget=1500',
                             '--dump-dom', path.as_uri()], capture_output=True, timeout=45,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (output / 'rendered.html').write_bytes(result.stdout)
    if result.returncode or b'data-security-result="pass"' not in result.stdout:
        raise SystemExit('Report browser security check failed; inspect local test artifacts.')
    print('PASS report text injection, hostile evidence URLs and valid local JPEG rendering')


if __name__ == '__main__':
    main()
