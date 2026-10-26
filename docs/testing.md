# Testing and validation

Run from the repository root after installing the verified native runtime:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall -q ada_poker tests scripts
.\.venv\Scripts\python.exe scripts/check_media_security.py
.\.venv\Scripts\python.exe scripts/check_report_security.py
.\.venv\Scripts\python.exe scripts/check_search_ui.py
.\.venv\Scripts\python.exe scripts/check_combined_ui.py
```

## Automated coverage

The unit suite covers geometry, recorder failures, service lifecycle ordering,
OCR interpretation, card/name consistency, corrections, history reconstruction,
SQLite updates, search/filter semantics, local API authorization and evidence
path restrictions, runtime integrity and installer checks. Native media checks
accept synthetic H.264/JPEG and reject disguised OpenEXR and unsupported codecs.
A headless Edge check rejects hostile report evidence URLs and HTML injection
while preserving valid local JPEG rendering. Inputs are synthetic or mocked; no player dataset is bundled.

The search browser runner exercises six scenarios in installed headless Edge:
search and pagination, combined filters, hand details, narrow layouts, empty
states, keyboard navigation, evidence navigation, delayed responses and errors.
The combined-interface runner adds two window widths and verifies recording UI
state, window discovery, and search during synthetic recording/analysis.
Artifacts stay under ignored `.dev/` directories.

The prepared snapshot passed 208 unit tests and all eight browser scenarios
locally on Windows. The public CI workflow runs the unit suite, compilation and native media security regressions
on Windows with Python 3.12. [Hosted workflow runs](https://github.com/DavyStultz/Ada-Poker/actions/workflows/tests.yml)
show the results for each published commit.

## Desktop integration checks

These launch synthetic native windows and may capture desktop pixels. Run them
deliberately on a normal signed-in Windows desktop; they are not part of CI:

```powershell
.\.venv\Scripts\python.exe scripts/check_window_capture.py
.\.venv\Scripts\python.exe scripts/check_window_recorder_ui.py
.\.venv\Scripts\python.exe scripts/check_visible_capture.py
.\.venv\Scripts\python.exe scripts/check_combined_capture.py
```

They exercise window identity, duplicate titles, changing pixels, movement or
resize interruption, saved video decoding, and close/save/analysis ordering.
Their generated videos remain local. No actual poker window is needed for their
default synthetic checks. The visible-capture script also has an explicit
`--live-table` option; use it only when intentionally testing a real table.

## What these checks do not establish

Synthetic tests verify software behavior, not general recognition accuracy.
Fresh footage needs independently checked names, cards, active seats, actions,
amounts and hand boundaries. `check_hand_reference.py` compares selected facts
against a separately prepared local reference; no real reference data is bundled.
Calibration and validation footage should be separated before reporting accuracy.

Three-hour recording, complete action/accounting coverage, bomb pots, straddles,
side pots and arbitrary table layouts remain unverified or incomplete. A brief
capture or passing UI check must not be presented as evidence for those claims.
