"""Exact, session/seat-scoped username corrections.

Corrections only ever replace a specific (seat, raw_username) pairing observed
within one source video, identified by its content hash. They never fuzzy-merge
spellings and never chain: correcting A -> B does not also turn B into C.
Existing user corrections are never silently discarded; a mismatched source
hash raises instead of overwriting a different session's saved work.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
_MAX_NAME_LENGTH = 100
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _validate_seat(seat):
    if not isinstance(seat, int) or isinstance(seat, bool) or not (0 <= seat <= 6):
        raise ValueError(f"Seat must be an int from 0 to 6, got {seat!r}.")


def _validate_name(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    if _CONTROL_CHARS.search(value):
        raise ValueError(f"{label} must not contain control characters.")
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{label} must not be blank.")
    if len(trimmed) > _MAX_NAME_LENGTH:
        raise ValueError(f"{label} must be at most {_MAX_NAME_LENGTH} characters.")
    if _CONTROL_CHARS.search(trimmed):
        raise ValueError(f"{label} must not contain control characters.")
    return trimmed


def _corrections_path(output: Path) -> Path:
    return Path(output) / "name-corrections.json"


def load_name_corrections(output: Path, source_hash: str) -> dict:
    """Return {(seat, raw_name): corrected_name} for this exact source video.

    Returns an empty dict if no correction file exists yet. Raises ValueError
    if a correction file exists but was written for a different source video,
    has an unsupported schema version, or is otherwise malformed -- corrections
    for a different recording must never be silently discarded or reused.
    """
    path = _corrections_path(output)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read name corrections at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Name corrections file {path} is not a JSON object.")
    if data.get("version") != SCHEMA_VERSION:
        raise ValueError(
            f"Name corrections file {path} has unsupported schema version "
            f"{data.get('version')!r} (expected {SCHEMA_VERSION})."
        )
    if data.get("source_hash") != source_hash:
        raise ValueError(
            f"Name corrections file {path} was saved for a different source "
            "video; refusing to apply or overwrite it."
        )
    names = data.get("names")
    if not isinstance(names, list):
        raise ValueError(f"Name corrections file {path} is missing a names list.")
    result = {}
    for entry in names:
        if not isinstance(entry, dict):
            raise ValueError(f"Name corrections file {path} has a malformed entry.")
        seat = entry.get("seat")
        raw_name = entry.get("raw_name")
        corrected_name = entry.get("corrected_name")
        _validate_seat(seat)
        raw_name = _validate_name(raw_name, "raw_name")
        corrected_name = _validate_name(corrected_name, "corrected_name")
        if (seat,raw_name) in result:
            raise ValueError("Duplicate seat/raw-name entry in name corrections.")
        result[(seat, raw_name)] = corrected_name
    return result


def save_name_correction(output: Path, source_hash: str, seat: int, raw_name: str,
                          corrected_name: str) -> None:
    """Add or replace the (seat, raw_name) -> corrected_name mapping for this
    exact source video, atomically rewriting name-corrections.json.

    A repeat save for the same (seat, raw_name) replaces that one mapping and
    leaves every other saved mapping untouched. corrected_name equal to
    raw_name is a valid, explicit user confirmation.
    """
    output = Path(output)
    _validate_seat(seat)
    raw_name = _validate_name(raw_name, "raw_name")
    corrected_name = _validate_name(corrected_name, "corrected_name")

    path = _corrections_path(output)
    entries = []
    if path.exists():
        # Validate every existing mapping, including duplicates and invalid
        # unrelated entries, before rewriting any user-owned correction file.
        load_name_corrections(output, source_hash)
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read existing name corrections at {path}: {exc}") from exc
        if not isinstance(existing, dict):
            raise ValueError(f"Name corrections file {path} is not a JSON object.")
        if existing.get("version") != SCHEMA_VERSION:
            raise ValueError(
                f"Name corrections file {path} has unsupported schema version "
                f"{existing.get('version')!r} (expected {SCHEMA_VERSION})."
            )
        if existing.get("source_hash") != source_hash:
            raise ValueError(
                f"Name corrections file {path} was saved for a different source "
                "video; refusing to overwrite it with corrections for this one."
            )
        existing_names = existing.get("names")
        if not isinstance(existing_names, list):
            raise ValueError(f"Name corrections file {path} is missing a names list.")
        entries = existing_names

    updated_at = datetime.now(timezone.utc).isoformat()
    new_entries = []
    replaced = False
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Name corrections file {path} has a malformed entry.")
        if entry.get("seat") == seat and entry.get("raw_name") == raw_name:
            new_entries.append({"seat": seat, "raw_name": raw_name,
                                 "corrected_name": corrected_name, "updated_at": updated_at})
            replaced = True
        else:
            new_entries.append(entry)
    if not replaced:
        new_entries.append({"seat": seat, "raw_name": raw_name,
                             "corrected_name": corrected_name, "updated_at": updated_at})

    payload = {"version": SCHEMA_VERSION, "source_hash": source_hash, "names": new_entries}
    output.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def apply_name_corrections(observations: list, corrections: dict) -> list:
    """Return a deep copy of observations with exact (seat, raw_username)
    corrections applied. Matching is scoped to this source's seat/raw-name
    pair only; there is no chaining and no fuzzy/global identity merging.

    A corrected seat gains username_source='user' and a username_correction
    evidence dict, but its existing OCR issues are preserved -- a name
    correction is provenance, not a claim that the whole hand is verified.
    """
    result = copy.deepcopy(observations)
    for observation in result:
        for seat in observation.get("seats") or []:
            raw_name = seat.get("raw_username") or seat.get("crop_username")
            if raw_name is None:
                continue
            key = (seat.get("seat"), raw_name)
            corrected_name = corrections.get(key)
            if corrected_name is None:
                continue
            seat["username"] = corrected_name
            seat["username_source"] = "user"
            seat['username_status'] = 'user_corrected'
            seat["username_correction"] = {
                "raw_name": raw_name,
                "corrected_name": corrected_name,
                "scope": "session_seat",
            }
    return result
