r"""Capture a short, local test and decode its video parts. No uploads.

Run from the project root: .venv\Scripts\python.exe scripts\smoke_capture.py
The normal desktop session is required; the Codex sandbox may block capture.
"""

from pathlib import Path
import argparse
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ada_poker.media import get_ffmpeg
from ada_poker import windows
from ada_poker.recorder import CaptureRegion, Recorder, RecordingOptions
from ada_poker.verify import verify_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=12)
    parser.add_argument("--stop-after", type=float, help="Exercise the manual Stop path after this many seconds")
    parser.add_argument("--output", type=Path, default=Path(".dev") / "capture-tests")
    args = parser.parse_args()
    windows.enable_dpi_awareness()
    displays = windows.monitors()
    monitor = next((m for m in displays if not m.primary), displays[0])
    # Odd dimensions exercise lossless one-pixel padding.
    region = CaptureRegion(monitor.x, monitor.y, monitor.width-1, monitor.height-1)
    encoder = get_ffmpeg()
    recorder = Recorder(encoder, region, args.output.resolve(),
                        RecordingOptions(duration_seconds=args.seconds, segment_seconds=4))
    session = recorder.start()
    print(f"Session: {session}", flush=True)
    started = time.monotonic()
    try:
        while recorder.snapshot()["state"] in {"starting", "recording", "stopping"}:
            if args.stop_after is not None and time.monotonic() - started >= args.stop_after:
                recorder.stop()
            if time.monotonic() - started > args.seconds + 50:
                raise TimeoutError("Capture did not finish within the smoke-test deadline.")
            recorder.join(0.5)
        recorder.join(5)
        result = recorder.snapshot()
        print(json.dumps(result, indent=2), flush=True)
        if result["state"] != "complete":
            raise RuntimeError(result.get("error") or "Recording failed.")
        verification = verify_session(session, encoder)
        print(json.dumps(verification, indent=2), flush=True)
        if args.stop_after is None and abs(verification["video_seconds"] - args.seconds) > 0.3:
            raise RuntimeError("Recorded duration differs from requested duration.")
    finally:
        recorder.stop()
        recorder.join(25)


if __name__ == "__main__":
    main()
