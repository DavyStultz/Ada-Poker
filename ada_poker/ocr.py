"""Local Windows OCR. No cloud calls or third-party OCR dependency."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import tempfile
import threading


def recognize_images(paths, on_progress=None):
    paths = [Path(p).resolve() for p in paths]
    script = Path(__file__).with_name("ocr_bridge.ps1").read_text(encoding="utf-8-sig")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # EncodedCommand executes this bundled code without changing script policy.
    # Filenames are JSON on stdin, never interpolated into executable shell text.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        def feed():
            try:
                for path in paths:
                    process.stdin.write(json.dumps(str(path)) + "\n")
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        results = []
        try:
            for line in process.stdout:
                result = json.loads(line)
                if "error" in result:
                    raise RuntimeError(f"OCR failed for {result.get('path')}: {result['error']}")
                results.append(result)
                if on_progress:
                    on_progress(len(results), len(paths))
            code = process.wait(timeout=30)
            if code or len(results) != len(paths):
                errors.seek(0)
                raise RuntimeError("Windows OCR did not finish: " + errors.read().decode("utf-8", errors="replace")[-2000:])
            return results
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            process.stdout.close()
            writer.join(timeout=5)
