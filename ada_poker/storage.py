"""Local SQLite storage for reconstructed hand histories.

save_analysis() replaces a session's generated hands on each rerun without
duplicating rows and without touching other sessions. Human corrections (if
any) live in a separate table and are never overwritten by a rerun; they are
merged back in at load time. No player statistics are fabricated: the
player_sightings table only records where an on-screen username was seen.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    source_dir TEXT,
    summary_json TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS hands (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    start_time REAL,
    end_time REAL,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hands_session ON hands(session_id, start_time);
CREATE TABLE IF NOT EXISTS player_sightings (
    session_id TEXT NOT NULL,
    hand_id TEXT NOT NULL,
    seat INTEGER,
    username TEXT,
    start_time REAL,
    end_time REAL
);
CREATE INDEX IF NOT EXISTS idx_sightings_session ON player_sightings(session_id);
CREATE INDEX IF NOT EXISTS idx_sightings_username ON player_sightings(username, hand_id);
CREATE INDEX IF NOT EXISTS idx_sightings_hand_seat ON player_sightings(hand_id, seat);
CREATE TABLE IF NOT EXISTS corrections (
    hand_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    patch_json TEXT NOT NULL,
    updated_at TEXT
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version == 1:
        with conn:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sightings_username ON player_sightings(username, hand_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sightings_hand_seat ON player_sightings(hand_id, seat)")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        raise ValueError(
            f"Ada Poker database schema version {version} is not supported "
            f"by this build (expected {SCHEMA_VERSION})."
        )


def save_analysis(db_path: Path, session_id: str, source_dir: str,
                   hands: list[dict], summary: dict) -> None:
    """Replace session_id's stored hands with `hands`; other sessions and
    any human corrections are left untouched."""
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        with conn:
            conn.execute(
                "INSERT INTO sessions(session_id, source_dir, summary_json, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "source_dir=excluded.source_dir, summary_json=excluded.summary_json, "
                "updated_at=excluded.updated_at",
                (session_id, str(source_dir), json.dumps(summary),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.execute("DELETE FROM hands WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM player_sightings WHERE session_id = ?", (session_id,))
            for hand in hands:
                conn.execute(
                    "INSERT INTO hands(id, session_id, start_time, end_time, json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (hand["id"], session_id, hand.get("start_time"),
                     hand.get("end_time"), json.dumps(hand)),
                )
                for seat in hand.get("seats") or []:
                    username = seat.get("username")
                    if not username:
                        continue
                    conn.execute(
                        "INSERT INTO player_sightings"
                        "(session_id, hand_id, seat, username, start_time, end_time) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (session_id, hand["id"], seat.get("seat"), username,
                         hand.get("start_time"), hand.get("end_time")),
                    )
    finally:
        conn.close()


def load_hands(db_path: Path, session_id: str) -> list[dict]:
    """Return this session's stored hands, ordered by start_time, with any
    saved human corrections shallow-merged back into the generated JSON."""
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, json FROM hands WHERE session_id = ? ORDER BY start_time",
            (session_id,),
        ).fetchall()
        corrections = dict(conn.execute(
            "SELECT hand_id, patch_json FROM corrections WHERE session_id = ?",
            (session_id,),
        ).fetchall())
        result = []
        for hand_id, hand_json in rows:
            hand = json.loads(hand_json)
            patch = corrections.get(hand_id)
            if patch:
                hand.update(json.loads(patch))
            result.append(hand)
        return result
    finally:
        conn.close()


def _read_connection(db_path: Path) -> sqlite3.Connection:
    """Lookup never creates or migrates a database. V1 is readable as-is."""
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (1, SCHEMA_VERSION):
        conn.close()
        raise ValueError(f"Unsupported player database version: {version}")
    conn.row_factory = sqlite3.Row
    return conn


def _page(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("Use a page size from 1 to 100 and a non-negative offset.")


# Occupancy evidence, not proof of participation. Seat 4 is hero in this layout.
_SHARED_TABLE = """
    p.seat != 4 AND EXISTS (
        SELECT 1 FROM player_sightings hero
        WHERE hero.hand_id = p.hand_id AND hero.session_id = p.session_id
          AND hero.seat = 4 AND hero.username IS NOT NULL AND hero.username != ''
    )
"""


def search_players(db_path: Path, query: str = "", *, limit: int = 50,
                   offset: int = 0) -> list[dict]:
    """Suggest literal substrings, preserving exact, case-sensitive identities.

    Counts are distinct candidate hands with both seats observed, including
    folds and unknown participation. Unknown hero occupancy is excluded.
    """
    _page(limit, offset)
    if not Path(db_path).exists():
        return []
    # Escape SQL LIKE wildcards: underscores are common in real usernames.
    query = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn = _read_connection(db_path)
    try:
        rows = conn.execute(f"""
            SELECT p.username, COUNT(DISTINCT p.hand_id) AS hand_count,
                   COUNT(DISTINCT p.session_id) AS session_count
            FROM player_sightings p
            JOIN hands h ON h.id=p.hand_id AND h.session_id=p.session_id
            WHERE {_SHARED_TABLE} AND p.username LIKE ? ESCAPE '\\'
            GROUP BY p.username COLLATE BINARY
            ORDER BY p.username COLLATE NOCASE, p.username COLLATE BINARY
            LIMIT ? OFFSET ?
        """, (f"%{query}%", limit, offset)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def load_player_hands(db_path: Path, username: str, *, limit: int = 50,
                      offset: int = 0) -> list[dict]:
    """Get one exact username's shared-table hands across sessions, newest first.

    Reuses the stored hand rather than duplicating it per opponent. Generated
    names are indexed by save_analysis after applying source-name corrections.
    The legacy hand patch table is applied to returned details, as in load_hands.
    """
    _page(limit, offset)
    if not Path(db_path).exists():
        return []
    conn = _read_connection(db_path)
    try:
        rows = conn.execute(f"""
            SELECT h.json, s.source_dir, h.session_id, c.patch_json
            FROM hands h JOIN sessions s ON s.session_id=h.session_id
            LEFT JOIN corrections c ON c.hand_id=h.id AND c.session_id=h.session_id
            WHERE h.id IN (
                SELECT p.hand_id FROM player_sightings p
                WHERE p.username = ? COLLATE BINARY AND {_SHARED_TABLE}
            )
            ORDER BY h.session_id DESC, h.start_time DESC, h.id DESC
            LIMIT ? OFFSET ?
        """, (username, limit, offset)).fetchall()
        result = []
        for row in rows:
            hand = json.loads(row['json'])
            if row['patch_json']:
                hand.update(json.loads(row['patch_json']))
            result.append({'hand': hand, 'source_dir': row['source_dir'],
                           'session_id': row['session_id']})
        return result
    finally:
        conn.close()
