import sqlite3
import tempfile
import shutil
import uuid
import unittest
from pathlib import Path

from ada_poker.storage import save_analysis, load_hands


def make_hand(session_id, index, username="hero", start=0.0, end=1.0):
    return {
        "id": f"{session_id}:{index:04d}:{int(start * 1000)}",
        "session_id": session_id,
        "start_time": start,
        "end_time": end,
        "start_known": True,
        "end_known": True,
        "boundary_reason": "dealer_change",
        "dealer_seat": 0,
        "seats": [{"seat": 4, "username": username, "position": "BTN",
                    "stack_bb": 100.0, "participant": True, "issues": []}],
        "events": [],
        "board": None,
        "hero_cards": None,
        "evidence": [],
        "needs_review": True,
        "issues": [],
    }


class TestStorage(unittest.TestCase):
    def setUp(self):
        self._test_dir = (Path('.dev') / 'unit-tests' / uuid.uuid4().hex).resolve()
        self._test_dir.mkdir(parents=True)
        self.db_path = self._test_dir / "ada_poker.sqlite3"

    def tearDown(self):
        assert self._test_dir.is_relative_to((Path.cwd()/'.dev'/'unit-tests').resolve())
        shutil.rmtree(self._test_dir)

    def test_save_and_load_round_trip(self):
        hands = [make_hand("sess-1", 0)]
        save_analysis(self.db_path, "sess-1", "C:/videos/sess-1", hands, {"note": "ok"})
        loaded = load_hands(self.db_path, "sess-1")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], hands[0]["id"])
        self.assertEqual(loaded[0]["seats"][0]["username"], "hero")

    def test_rerun_replaces_hands_without_duplicating(self):
        save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0)], {})
        save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0)], {})
        loaded = load_hands(self.db_path, "sess-1")
        self.assertEqual(len(loaded), 1)

    def test_rerun_can_shrink_hand_count(self):
        two_hands = [make_hand("sess-1", 0, start=0.0, end=1.0),
                     make_hand("sess-1", 1, start=1.0, end=2.0)]
        save_analysis(self.db_path, "sess-1", "dir", two_hands, {})
        save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0)], {})
        loaded = load_hands(self.db_path, "sess-1")
        self.assertEqual(len(loaded), 1)

    def test_other_sessions_unaffected(self):
        save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0)], {})
        save_analysis(self.db_path, "sess-2", "dir2", [make_hand("sess-2", 0)], {})
        save_analysis(self.db_path, "sess-1", "dir", [], {})
        self.assertEqual(load_hands(self.db_path, "sess-1"), [])
        self.assertEqual(len(load_hands(self.db_path, "sess-2")), 1)

    def test_missing_session_returns_empty_list(self):
        self.assertEqual(load_hands(self.db_path, "nonexistent"), [])

    def test_corrections_survive_rerun(self):
        save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0)], {})
        hand_id = make_hand("sess-1", 0)["id"]
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO corrections(hand_id, session_id, patch_json, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (hand_id, "sess-1", '{"needs_review": false}', "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        # Rerunning analysis must not wipe the human correction.
        save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0)], {})
        loaded = load_hands(self.db_path, "sess-1")
        self.assertEqual(len(loaded), 1)
        self.assertFalse(loaded[0]["needs_review"])

    def test_schema_version_recorded(self):
        save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0)], {})
        conn = sqlite3.connect(str(self.db_path))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(version, 1)

    def test_unsupported_future_schema_version_rejected(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA user_version = 999")
        conn.close()
        with self.assertRaises(ValueError):
            save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0)], {})

    def test_player_sightings_recorded_without_fabricated_stats(self):
        save_analysis(self.db_path, "sess-1", "dir", [make_hand("sess-1", 0, username="hero")], {})
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute(
            "SELECT username, seat FROM player_sightings WHERE session_id = ?", ("sess-1",)
        ).fetchall()
        conn.close()
        self.assertEqual(rows, [("hero", 4)])


if __name__ == "__main__":
    unittest.main()
