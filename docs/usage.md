# Usage guide

## Setup

Use Windows with Python 3.12 (including Tkinter), Microsoft Edge, and the English
Windows OCR language pack for recognition. Install from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe scripts/setup_ffmpeg.py
```

The commands do not require activating the environment or changing PowerShell
execution policy. Setup downloads a pinned FFmpeg archive and verifies its SHA-256
and executable hash before use. The runtime and its third-party license stay in
`.local/ffmpeg/`; no system-wide installation is needed. Subsequent recording and
analysis work offline. Only recorder-compatible Matroska/H.264 video and generated
JPEG evidence are supported. `setup.ps1` and `start-recorder.ps1` are optional convenience
scripts on systems that permit PowerShell scripts.

## Synthetic demonstration

```powershell
.\.venv\Scripts\python.exe scripts/demo.py
```

Search for `Mira11`, submit the search, and open the first hand. Use Filters to
combine positions, streets, actions and amounts. Names and hands are fictional;
the second hand illustrates uncertainty with a generated placeholder image.
The demo is explicitly labeled as example data and has recording disabled.
Its files stay in `.dev/portfolio-demo`, separate from the player database.

## Recording

```powershell
.\.venv\Scripts\python.exe -m ada_poker
```

1. Open the table window and arrange it before starting.
2. Expand **Record a session**, choose the table window and check the preview.
3. Use **Visible table** for a capture of its visible desktop area. Keep the
   window uncovered, unmoved and at the same size. Overlapping windows appear
   in the recording. **Direct window** may produce stale frames on some clients.
4. Start with a **1 minute (test)** capture. Stop and inspect the resulting video
   for changing, readable cards, names and amounts.
5. **Stop & save** finalizes the video and automatically starts local analysis.
   Search and review remain available during recording and processing.

Closing or minimizing the table, geometry changes, and detected capture exclusion
can stop recording. Inspect partial output after an interruption. The program
does not bypass capture protection. A playable short recording does not prove
complete action coverage or long-session reliability.

## Search and review

Typing shows username suggestions. Press Enter, click Search, or choose a
suggestion to load hands. Cards revealed at showdown receive priority.
Filters combine position, street, action, raise order and size in BB; they must
match the same action. Raise size means the total raised to, not the increment.

Open a hand for the table and street-by-street actions. **Back to hands** restores
the search state. **View screenshot** on an uncertain later street opens source
evidence. All generated histories remain drafts until independently checked.

## Files and maintenance

Defaults are `Videos\Ada Poker` and `Videos\Ada Poker Analysis` under the current
Windows user's home directory. Recordings contain segmented MKV files, timing
metadata and an encoder log. Analysis produces JSON, evidence frames, HTML
reports, caches and `players.sqlite3`. Runtime settings live in `.local/` in the
checkout. None of these data files belongs in version control. Choose a local,
unsynced output directory if recordings must remain only on this computer.

```powershell
.\.venv\Scripts\python.exe -m ada_poker.review --maintenance
.\.venv\Scripts\python.exe -m ada_poker.analyze 'C:\path\to\session'
.\.venv\Scripts\python.exe -m ada_poker.verify 'C:\path\to\session'
```

The maintenance window supports analysis and exact-name corrections. Corrections
persist across reanalysis and retain the raw OCR. Verification decodes video and
checks segment timing; it does not verify the poker interpretation.
