"""Open the review interface with isolated synthetic hands and no recorder."""
from pathlib import Path
import argparse
import json
import struct
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from ui_fixtures import examples


def prepare_demo(output):
    """Create fictional examples and an illustrative, non-recorded evidence image."""
    output = Path(output).resolve()
    frames = output / 'example-session' / 'frames'
    frames.mkdir(parents=True, exist_ok=True)

    def chunk(kind, data):
        checksum = struct.pack('!I', zlib.crc32(kind + data))
        return struct.pack('!I', len(data)) + kind + data + checksum

    pixels = b''.join(
        b'\0' + b''.join(bytes((25, 65 + ((x // 40 + y // 40) % 2) * 20, 45))
                          for x in range(640)) for y in range(360))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('!2I5B', 640, 360, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))
    (frames / 'example.png').write_bytes(png)
    fixture = output / 'fixtures.json'
    fixture.write_text(json.dumps(examples(), indent=2), encoding='utf-8')
    return fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-browser', action='store_true',
                        help='Print the temporary local URL instead of opening Edge.')
    args = parser.parse_args()
    output = ROOT / '.dev' / 'portfolio-demo'
    fixture = prepare_demo(output)
    command = [sys.executable, '-m', 'ada_poker.ui', '--fixture', str(fixture),
               '--analysis-root', str(output)]
    if args.no_browser:
        command.append('--no-browser')
    print('Synthetic demonstration. Search for Mira11. Recording is disabled.', flush=True)
    try:
        return subprocess.call(command, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
