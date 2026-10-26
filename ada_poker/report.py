"""Generate a self-contained, local browser review page; no server or CDN."""
import json
from pathlib import Path


def write_report(output: Path, summary, observations, hands):
    payload = json.dumps({"summary":summary,"observations":observations,"hands":hands},ensure_ascii=False).replace("<","\\u003c")
    template = Path(__file__).with_name("report.html").read_text(encoding="utf-8")
    path = output/"review.html"
    path.write_text(template.replace("__ADA_DATA__",payload),encoding="utf-8")
    return path
