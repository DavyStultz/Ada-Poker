"""Pinned local FFmpeg runtime and narrow input options for recorder media."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
BUILD_PATH = Path(__file__).with_name('ffmpeg-build.json')


def build_config():
    return json.loads(BUILD_PATH.read_text(encoding='utf-8'))


def sha256_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def get_ffmpeg():
    """Never silently fall back to an older wheel, PATH entry, or environment override."""
    build = build_config()
    path = ROOT / '.local' / 'ffmpeg' / build['version'] / 'bin' / 'ffmpeg.exe'
    if not path.is_file():
        raise RuntimeError('The verified video runtime is missing. Run python scripts/setup_ffmpeg.py.')
    if sha256_file(path) != build['executable_sha256']:
        raise RuntimeError('The video runtime failed its integrity check. Run python scripts/setup_ffmpeg.py.')
    return str(path.resolve())


def recording_input_options():
    """Recorder output is local Matroska containing H.264 video, with no audio."""
    return ['-protocol_whitelist', 'file,pipe', '-f', 'matroska', '-codec_whitelist', 'h264']


def jpeg_input_options():
    """Recognition evidence is a local JPEG; never auto-probe another image codec."""
    return ['-protocol_whitelist', 'file,pipe', '-f', 'image2', '-pattern_type', 'none',
            '-codec_whitelist', 'mjpeg', '-c:v', 'mjpeg']
