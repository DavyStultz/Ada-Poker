# Architecture

Ada Poker separates capture from analysis so that evidence can be saved first
and processed again as recognition improves. Everything runs on one Windows PC.

## Capture and task lifecycle

`windows.py` discovers window clients, reads their geometry and capture status,
and supplies native preview operations. `recorder.py` manages FFmpeg, segmented
video, session metadata, and capture watchdogs. `recording_service.py` coordinates
the application state and queues local analysis after the recording has saved.

Visible-table capture derives a desktop rectangle from the chosen window. It
requires that window to stay visible, uncovered, and in place. Direct-window
capture is also available, but some applications return stale pixels. Detected
capture exclusion, minimization, or geometry changes stop capture and preserve
available output; the program does not bypass application capture restrictions.

Closing the app saves an active recording and allows pending analysis to finish
in the background. Capture and OCR work run outside the HTTP request thread.

`media.py` resolves only the checksum-verified local FFmpeg runtime. Decode paths
accept recorder-compatible Matroska/H.264 and generated JPEG evidence. Report
images are restricted to generated local JPEG paths. Setup is the only step
that downloads the runtime; see [security](../SECURITY.md).

## Recognition and reconstruction

`analyze.py` decodes recorded segments and samples approximately five frames per
second. `ocr.py` and `ocr_bridge.ps1` call the installed Windows OCR engine.
`reading.py` interprets layout regions; `names.py` adds cropped name checks;
`cards.py` matches rank and suit silhouettes from local JSON templates.

Recognition caches depend on the source and relevant reader/template versions.
Repeated observations help reject transient readings; matching scores are not
calibrated probabilities. Conflicting cards and missing information remain
uncertain. The templates are small glyph silhouettes, not stored table images.

`history.py` groups observations into candidate hands and events. An observed
stack change is retained separately from an inferred poker action. Occupied seats
do not establish participation, and incomplete recordings do not become complete
hands by assumption. Full action and chip-accounting reconstruction is unfinished.

## Storage, corrections, and review

`storage.py` stores sessions, hands, and username sightings in SQLite. Reprocessing
a session replaces its generated records rather than appending duplicates.
Per-session JSON and evidence images retain links to the original video offsets.
`corrections.py` applies exact-name corrections scoped to their source and seat,
while retaining raw OCR separately.

`ui_data.py` adapts stored records into search/filter results. It groups only the
specified 1/i/l look-alike name variants and keeps original spellings. Explicit
identity or preflop uncertainty excludes a hand; later-street uncertainty permits
evidence review. Missing certainty metadata is not a guarantee of verified input.

`ui.py` serves assets and APIs through a Python standard-library loopback server.
Each launch gets a fresh token. API requests check the local host, token and
origin; evidence access checks membership and restricts paths to session frames.
The frontend in `ada_poker/web/` uses plain JavaScript and CSS without a build step.

## Deliberate constraints

- One table at a time; seven-seat Hold'em layouts with amounts in big blinds.
- Windows-specific capture and OCR; no cross-platform runtime claim.
- Post-session review; no gameplay automation or live recommendation engine.
- Small calibrated template library; no broad recognition-accuracy claim.
- A local single-user service; not designed as an internet-hosted application.

## Source map

| Area | Main files |
| --- | --- |
| Windows integration | `windows.py`, `window_app.py` |
| Capture | `recorder.py`, `recording_service.py` |
| Analysis | `analyze.py`, `reading.py`, `ocr.py`, `names.py`, `cards.py` |
| Histories and persistence | `history.py`, `storage.py`, `corrections.py` |
| Review UI | `ui.py`, `ui_data.py`, `web/` |
| Maintenance tools | `review.py`, `name_editor.py`, `report.py`, `verify.py` |
| Synthetic verification | `tests/`, `scripts/check_*.py` |
