"""Read-only validation of session parts: python -m ada_poker.verify PATH.

Decodes locally with the bundled FFmpeg. Does not assess poker recognition.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import subprocess
from .media import get_ffmpeg, recording_input_options


def read_segments(session_dir: Path) -> list[dict]:
    session_dir = session_dir.resolve()
    records = []
    seen = set()
    with (session_dir / "segments.csv").open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.reader(stream):
            if len(row) != 3:
                raise ValueError("Invalid segment-list row.")
            raw, start, end = row
            path = Path(raw)
            if not path.is_absolute():
                path = session_dir / path
            path = path.resolve()
            if path.parent != session_dir or path.suffix.lower() != ".mkv":
                raise ValueError("Segment path must name a Matroska file inside this session.")
            if path in seen:
                raise ValueError("Duplicate segment in segment list.")
            seen.add(path)
            start, end = float(start), float(end)
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise ValueError("Invalid segment timestamps.")
            if records and abs(start - records[-1]["end"]) > 0.2:
                raise ValueError("Segment timestamps have a gap or overlap larger than 0.2 seconds.")
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"Missing or empty video part: {path.name}")
            records.append({"path": path, "start": start, "end": end})
    if not records:
        raise ValueError("No completed video parts were listed.")
    return records


def verify_session(session_dir: Path, ffmpeg_path: str) -> dict:
    records = read_segments(session_dir)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    total_frames = 0
    for record in records:
        # File data is untrusted input; no shell or external URL protocols.
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-v", "error", "-xerror", *recording_input_options(),
             "-i", str(record["path"]), "-map", "0:v:0", "-progress", "pipe:1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=max(60, (record["end"] - record["start"]) * 2),
            creationflags=flags,
        )
        if result.returncode:
            raise ValueError(f"Decode failed for {record['path'].name}: {result.stderr[-2000:]}")
        frames = [int(line.partition("=")[2]) for line in result.stdout.splitlines() if line.startswith("frame=")]
        if not frames or frames[-1] <= 0:
            raise ValueError(f"No video frames decoded from {record['path'].name}.")
        total_frames += frames[-1]
    return {"parts": len(records), "frames_decoded": total_frames,
            "video_seconds": round(records[-1]["end"] - records[0]["start"], 3),
            "bytes": sum(r["path"].stat().st_size for r in records),
            "decode_errors": 0,
            "note": "Confirms readable video and segment timing, not poker readability or capture completeness."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_session(args.session, get_ffmpeg()), indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Verification failed: {exc}\n")


if __name__ == "__main__":
    main()
