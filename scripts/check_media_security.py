"""Real FFmpeg checks using harmless synthetic media; no recordings or player data."""
from pathlib import Path
import csv
import json
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ada_poker.media import get_ffmpeg
from ada_poker.verify import verify_session
from ada_poker.analyze import extract_frames
from ada_poker.cards import image_pixels, image_sequence, WIDTH, HEIGHT


def main():
    ffmpeg = get_ffmpeg()
    root = ROOT / '.dev/media-security' / uuid.uuid4().hex
    root.mkdir(parents=True)
    checks = []

    def generate(path, *options):
        result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                                 '-i', 'color=c=blue:s=32x32:r=5', *options, '-y', str(path)],
                                capture_output=True, text=True, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(result.stderr[-1000:])

    def session(name):
        folder = root / name
        folder.mkdir()
        with (folder / 'segments.csv').open('w', newline='') as stream:
            csv.writer(stream).writerow(['part-0000.mkv', 0, 1])
        return folder

    valid = session('h264')
    generate(valid / 'part-0000.mkv', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p')
    assert verify_session(valid, ffmpeg)['frames_decoded'] == 5
    frames = extract_frames(valid, root / 'valid-analysis', ffmpeg)
    assert len(frames) == 5
    jpeg = root / 'valid-analysis' / frames[0]['evidence']['image']
    assert len(image_pixels(jpeg, ffmpeg)) == WIDTH * HEIGHT * 3
    assert len(list(image_sequence([jpeg, jpeg], ffmpeg))) == 2
    checks.append('H264 Matroska verifies and extracts; JPEG decoding works')

    image = root / 'harmless.exr'
    generate(image, '-frames:v', '1', '-pix_fmt', 'gbrpf32le')
    disguised = session('renamed-image')
    (disguised / 'part-0000.mkv').write_bytes(image.read_bytes())
    unsupported = session('unsupported-codec')
    generate(unsupported / 'part-0000.mkv', '-t', '1', '-c:v', 'ffv1')
    for folder in (disguised, unsupported):
        for operation in ('verify', 'extract'):
            try:
                if operation == 'verify':
                    verify_session(folder, ffmpeg)
                else:
                    extract_frames(folder, root / (folder.name + '-analysis'), ffmpeg)
            except (ValueError, RuntimeError):
                checks.append(folder.name + ' rejected by ' + operation)
            else:
                raise AssertionError(folder.name + ' was accepted by ' + operation)
    fake_jpeg = root / 'renamed-image.jpg'
    fake_jpeg.write_bytes(image.read_bytes())
    for decode in (lambda: image_pixels(fake_jpeg, ffmpeg), lambda: list(image_sequence([fake_jpeg], ffmpeg))):
        try:
            decode()
        except RuntimeError:
            pass
        else:
            raise AssertionError('Non-JPEG evidence accepted')
    checks.append('Renamed OpenEXR rejected by both JPEG decoding paths')
    (root / 'results.json').write_text(json.dumps({'checks': checks}, indent=2))
    for check in checks:
        print('PASS ' + check, flush=True)


if __name__ == '__main__':
    main()
