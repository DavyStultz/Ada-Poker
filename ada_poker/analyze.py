"""Offline analysis: python -m ada_poker.analyze SESSION --output DIRECTORY."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .ocr import recognize_images
from .reading import read_frame, stabilize_names
from .verify import read_segments
from .media import get_ffmpeg, recording_input_options

ANALYSIS_VERSION = "0.4"
OCR_CACHE_VERSION = "0.1"


def fingerprint(session):
    digest = hashlib.sha256()
    for record in read_segments(session):
        with record["path"].open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):
                digest.update(chunk)
    return digest.hexdigest()


def extract_frames(session, output, ffmpeg):
    frames_dir = output / "frames"
    frames_dir.mkdir(parents=True,exist_ok=True)
    frames = []
    for part_index,record in enumerate(read_segments(session)):
        pattern = frames_dir / f"p{part_index:03d}-%06d.jpg"
        result = subprocess.run([ffmpeg,"-hide_banner","-nostdin","-loglevel","info",*recording_input_options(),
            "-i",str(record["path"]),"-vf","select='isnan(prev_selected_t)+gte(t-prev_selected_t,0.18)',showinfo",
            "-fps_mode","passthrough","-an","-q:v","2","-y",str(pattern)],
            capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=max(120,(record["end"]-record["start"])*3))
        if result.returncode:
            raise RuntimeError("Frame extraction failed: "+result.stderr[-2000:])
        times = [float(t) for t in re.findall(r"pts_time:\s*([\d.]+)",result.stderr)]
        if not times:
            raise RuntimeError("No timestamped frames extracted.")
        for number,offset in enumerate(times,1):
            image = frames_dir / f"p{part_index:03d}-{number:06d}.jpg"
            if not image.is_file():
                raise RuntimeError("An extracted frame is missing.")
            frames.append({"time":round(record["start"]+offset,6),"evidence":{
                "part":record["path"].name,"offset":offset,"image":image.relative_to(output).as_posix()}})
    return frames


def write_json(path, value):
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    temporary.replace(path)


def analyze(session, output, progress=print):
    from .history import reconstruct
    from .storage import save_analysis
    from .corrections import load_name_corrections, apply_name_corrections
    session,output = Path(session).resolve(),Path(output).resolve()
    if output == session or session in output.parents:
        raise ValueError("Keep analysis output separate from the source recording.")
    ffmpeg = get_ffmpeg()
    output.mkdir(parents=True,exist_ok=True)
    source_hash = fingerprint(session)
    corrections = load_name_corrections(output,source_hash)
    cache_path = output/"ocr-cache.json"
    cache = None
    if cache_path.exists():
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        if loaded.get("source_hash")==source_hash and loaded.get("version")==OCR_CACHE_VERSION:
            cache = loaded
    if cache is None:
        progress("Extracting frames with original video timestamps…")
        frames = extract_frames(session,output,ffmpeg)
        progress(f"Reading {len(frames)} frames locally with Windows OCR…")
        def report(done,total):
            if done % 50 == 0 or done==total:
                progress(f"Read {done}/{total} frames")
        raw = recognize_images([output/f["evidence"]["image"] for f in frames], report)
        cache = {"version":OCR_CACHE_VERSION,"source_hash":source_hash,"frames":frames,"ocr":raw}
        write_json(cache_path,cache)
    else:
        progress("Reusing local OCR cache for this exact source video.")
    observations = stabilize_names([read_frame(raw,frame["time"],frame["evidence"]) for frame,raw in zip(cache["frames"],cache["ocr"])])
    from .names import add_name_readings
    observations = add_name_readings(observations,output,ffmpeg,source_hash,progress)
    observations = apply_name_corrections(observations,corrections)
    from .cards import add_card_readings
    observations = add_card_readings(observations,output,ffmpeg,source_hash,progress)
    hands = reconstruct(session.name,observations)
    summary = {"version":ANALYSIS_VERSION,"session_id":session.name,"source_dir":str(session),"source_hash":source_hash,
               "frames_analyzed":len(observations),"table_frames":sum(o["table_visible"] for o in observations),
               "hands":len(hands),"status":"needs_review",
               "limitations":["Calibrated ClubGG seven-seat layouts only; OCR spellings and amounts require review.",
                              "Join Waiting spectator layout has no hero; bottom-seat hole cards remain uncalibrated.",
                              "Sampled at approximately 5 fps; brief events may be missed.",
                              "Card templates cover only observed glyphs; weak or occluded cards stay unknown.",
                              "Full action completeness and chip accounting are not yet verified."],
               "name_corrections":len(corrections),
               "frames_with_hero_cards":sum(bool(o.get('hero_cards')) for o in observations),
               "frames_with_opponent_card_parts":sum(any(s['seat']!=4 for s in o.get('visible_hole_cards',[])) for o in observations),
               "frames_with_recovered_names":sum(any(s.get('username_source')=='ocr_crop' for s in o['seats']) for o in observations),
               "frames_with_name_crop_disagreement":sum(any(s.get('crop_username') and s.get('raw_username') and
                    s['crop_username']!=s['raw_username'] for s in o['seats']) for o in observations),
               "frames_with_board":sum(bool(o.get('board')) for o in observations)}
    write_json(output/"observations.json",observations)
    write_json(output/"hands.json",hands)
    write_json(output/"summary.json",summary)
    save_analysis(output.parent/"players.sqlite3",session.name,str(session),hands,summary)
    from .report import write_report
    write_report(output,summary,observations,hands)
    progress(f"Saved {len(hands)} candidate hands to {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session",type=Path)
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    output = args.output or Path.home()/"Videos"/"Ada Poker Analysis"/args.session.name
    try:
        analyze(args.session,output)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        parser.exit(1,f"Analysis failed: {exc}\n")


if __name__ == "__main__":
    main()

