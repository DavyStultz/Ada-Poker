"""Security regressions for runtime integrity and media trust boundaries."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ada_poker import media


class MediaRuntimeTests(unittest.TestCase):
    def test_missing_runtime_does_not_fall_back_to_environment(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(media, 'ROOT', Path(temporary)), \
             patch.object(media, 'build_config', return_value={'version': 'test', 'executable_sha256': 'unused'}), \
             patch.dict('os.environ', {'IMAGEIO_FFMPEG_EXE': 'unverified.exe'}):
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                media.get_ffmpeg()

    def test_tampered_runtime_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(media, 'ROOT', Path(temporary)):
            binary = Path(temporary) / '.local/ffmpeg/test/bin/ffmpeg.exe'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'approved synthetic executable')
            config = {'version': 'test', 'executable_sha256': media.sha256_file(binary)}
            with patch.object(media, 'build_config', return_value=config):
                self.assertEqual(media.get_ffmpeg(), str(binary.resolve()))
                binary.write_bytes(b'tampered')
                with self.assertRaisesRegex(RuntimeError, 'integrity'):
                    media.get_ffmpeg()

    def test_corrupt_archive_is_refused_before_installation(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/setup_ffmpeg.py'
        spec = importlib.util.spec_from_file_location('setup_ffmpeg_test', script)
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'runtime.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('runtime/bin/ffmpeg.exe', b'synthetic executable')
            config = {'version': 'test', 'archive_sha256': '0' * 64, 'executable_sha256': '1' * 64,
                      'members': {'runtime/bin/ffmpeg.exe': 'bin/ffmpeg.exe'}}
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'build_config', return_value=config), \
                 patch.object(setup, 'get_ffmpeg', side_effect=RuntimeError('missing')):
                with self.assertRaisesRegex(RuntimeError, 'archive checksum'):
                    setup.install(archive)
            self.assertFalse((root / '.local/ffmpeg/test/bin/ffmpeg.exe').exists())

    def test_verified_archive_still_checks_executable_and_ignores_extra_members(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/setup_ffmpeg.py'
        spec = importlib.util.spec_from_file_location('setup_ffmpeg_test', script)
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'runtime.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('runtime/bin/ffmpeg.exe', b'synthetic executable')
                package.writestr('../outside.txt', b'must not extract')
            config = {'version': 'test', 'archive_sha256': media.sha256_file(archive),
                      'executable_sha256': '0' * 64,
                      'members': {'runtime/bin/ffmpeg.exe': 'bin/ffmpeg.exe'}}
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'build_config', return_value=config), \
                 patch.object(setup, 'get_ffmpeg', side_effect=RuntimeError('missing')):
                with self.assertRaisesRegex(RuntimeError, 'executable checksum'):
                    setup.install(archive)
            self.assertFalse((root / '.local/ffmpeg/test/bin/ffmpeg.exe').exists())
            self.assertFalse((root / '.local/ffmpeg/outside.txt').exists())


if __name__ == '__main__':
    unittest.main()
