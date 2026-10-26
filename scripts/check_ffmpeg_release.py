"""Fail when the pinned native runtime needs review against the publisher's release."""
import json
from pathlib import Path
import re
from urllib.request import urlopen


def main():
    root = Path(__file__).resolve().parents[1]
    pinned = json.loads((root / 'ada_poker/ffmpeg-build.json').read_text())['version']
    with urlopen('https://www.gyan.dev/ffmpeg/builds/release-version', timeout=30) as response:
        latest = response.read(100).decode('ascii').strip()
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', latest):
        raise SystemExit('Unexpected release response; review the publisher manually.')
    if latest != pinned:
        raise SystemExit(f'FFmpeg {latest} is available; review and update pinned {pinned}, hashes and tests.')
    print(f'FFmpeg {pinned} matches the publisher release.')


if __name__ == '__main__':
    main()
