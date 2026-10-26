"""Install the checksum-pinned Windows FFmpeg runtime using only Python's stdlib."""
from pathlib import Path
import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ada_poker.media import build_config, get_ffmpeg, sha256_file


def install(archive_path=None):
    if sys.platform != 'win32':
        raise RuntimeError('This build of Ada Poker requires Windows.')
    build = build_config()
    # A verified installation is reusable; never download during normal app startup.
    try:
        return get_ffmpeg()
    except RuntimeError:
        pass
    destination = ROOT / '.local' / 'ffmpeg' / build['version']
    destination.mkdir(parents=True, exist_ok=True)
    if not destination.resolve().is_relative_to((ROOT / '.local').resolve()):
        raise RuntimeError('Runtime installation must stay in the local project directory.')
    with tempfile.TemporaryFile() as archive:
        digest = hashlib.sha256()
        total = 0
        source = open(archive_path, 'rb') if archive_path else urlopen(build['archive_url'], timeout=60)
        with source:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > 250 * 1024 * 1024:
                    raise RuntimeError('Runtime archive exceeds the expected download limit.')
                digest.update(chunk)
                archive.write(chunk)
        if digest.hexdigest() != build['archive_sha256']:
            raise RuntimeError('FFmpeg archive checksum mismatch; nothing was installed.')
        archive.seek(0)
        with zipfile.ZipFile(archive) as package:
            # Explicit members only: archive paths never choose destinations.
            for member, relative in build['members'].items():
                info = package.getinfo(member)
                if info.file_size > 250 * 1024 * 1024:
                    raise RuntimeError('Runtime archive member exceeds the expected limit.')
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise RuntimeError('Invalid runtime destination.')
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                        temporary = Path(stream.name)
                        with package.open(member) as data:
                            shutil.copyfileobj(data, stream)
                    if relative == 'bin/ffmpeg.exe' and sha256_file(temporary) != build['executable_sha256']:
                        raise RuntimeError('FFmpeg executable checksum mismatch; it was not installed.')
                    os.replace(temporary, target)
                finally:
                    if temporary and temporary.exists():
                        temporary.unlink()
    return get_ffmpeg()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Use a previously downloaded archive; hashes are still enforced.')
    args = parser.parse_args()
    try:
        executable = install(args.archive)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'Runtime setup failed: {exc}\n')
    print(f"Verified FFmpeg {build_config()['version']} is ready: {executable}")


if __name__ == '__main__':
    main()
