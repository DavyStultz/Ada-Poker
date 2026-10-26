import json
import shutil
import unittest
import uuid
from pathlib import Path

from ada_poker.corrections import (
    apply_name_corrections,
    load_name_corrections,
    save_name_correction,
)


def make_observation(seat, raw_username, username=None, time=0.0):
    return {
        "time": time,
        "evidence": {"image": "frames/p000-000001.jpg", "part": "part0.mkv", "offset": time},
        "table_visible": True,
        "seats": [{
            "seat": seat,
            "username": username,
            "raw_username": raw_username,
            "username_candidates": [raw_username] if raw_username else [],
            "stack_bb": 100.0,
        }],
        "issues": ["ocr_values_require_review"],
    }


class TestCorrections(unittest.TestCase):
    def setUp(self):
        self._test_dir = (Path(".dev") / "unit-tests" / uuid.uuid4().hex).resolve()
        self._test_dir.mkdir(parents=True)
        self.output = self._test_dir / "session-output"
        self.output.mkdir()
        self.source_hash = "abc123"

    def tearDown(self):
        assert self._test_dir.is_relative_to((Path.cwd() / ".dev" / "unit-tests").resolve())
        shutil.rmtree(self._test_dir)

    # -- load/save persistence ---------------------------------------------

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_name_corrections(self.output, self.source_hash), {})

    def test_duplicate_mappings_cannot_be_loaded_or_overwritten(self):
        path=self.output/'name-corrections.json'
        entry={'seat':0,'raw_name':'A','corrected_name':'Alice'}
        path.write_text(json.dumps({'version':1,'source_hash':self.source_hash,'names':[entry,entry]}),encoding='utf-8')
        original=path.read_bytes()
        with self.assertRaises(ValueError):load_name_corrections(self.output,self.source_hash)
        with self.assertRaises(ValueError):save_name_correction(self.output,self.source_hash,1,'B','Bob')
        self.assertEqual(path.read_bytes(),original)

    def test_control_characters_are_not_silently_trimmed(self):
        with self.assertRaises(ValueError):
            save_name_correction(self.output,self.source_hash,0,'A','Alice\n')

    def test_save_then_load_round_trip(self):
        save_name_correction(self.output, self.source_hash, 3, "Al1ce", "Alice")
        loaded = load_name_corrections(self.output, self.source_hash)
        self.assertEqual(loaded, {(3, "Al1ce"): "Alice"})

    def test_repeated_save_replaces_matching_mapping(self):
        save_name_correction(self.output, self.source_hash, 3, "Al1ce", "Alice")
        save_name_correction(self.output, self.source_hash, 3, "Al1ce", "Alicia")
        loaded = load_name_corrections(self.output, self.source_hash)
        self.assertEqual(loaded, {(3, "Al1ce"): "Alicia"})

    def test_same_raw_name_different_seat_unchanged(self):
        save_name_correction(self.output, self.source_hash, 3, "Bob", "Robert")
        save_name_correction(self.output, self.source_hash, 5, "Bob", "Bobby")
        loaded = load_name_corrections(self.output, self.source_hash)
        self.assertEqual(loaded, {(3, "Bob"): "Robert", (5, "Bob"): "Bobby"})

    def test_multiple_mappings_survive_independent_saves(self):
        save_name_correction(self.output, self.source_hash, 0, "X1", "Xavier")
        save_name_correction(self.output, self.source_hash, 1, "Y1", "Yara")
        loaded = load_name_corrections(self.output, self.source_hash)
        self.assertEqual(loaded, {(0, "X1"): "Xavier", (1, "Y1"): "Yara"})

    def test_corrected_name_equal_raw_name_is_valid_confirmation(self):
        save_name_correction(self.output, self.source_hash, 2, "Steve", "Steve")
        loaded = load_name_corrections(self.output, self.source_hash)
        self.assertEqual(loaded, {(2, "Steve"): "Steve"})

    def test_source_mismatch_raises(self):
        save_name_correction(self.output, self.source_hash, 0, "A", "Andy")
        with self.assertRaises(ValueError):
            load_name_corrections(self.output, "different-hash")

    def test_save_with_mismatched_source_hash_raises_and_does_not_overwrite(self):
        save_name_correction(self.output, self.source_hash, 0, "A", "Andy")
        with self.assertRaises(ValueError):
            save_name_correction(self.output, "different-hash", 1, "B", "Bea")
        loaded = load_name_corrections(self.output, self.source_hash)
        self.assertEqual(loaded, {(0, "A"): "Andy"})

    def test_duplicate_invalid_schema_version_raises(self):
        path = self.output / "name-corrections.json"
        path.write_text(json.dumps({"version": 999, "source_hash": self.source_hash, "names": []}),
                         encoding="utf-8")
        with self.assertRaises(ValueError):
            load_name_corrections(self.output, self.source_hash)

    def test_invalid_seat_rejected(self):
        with self.assertRaises(ValueError):
            save_name_correction(self.output, self.source_hash, 7, "A", "Andy")
        with self.assertRaises(ValueError):
            save_name_correction(self.output, self.source_hash, -1, "A", "Andy")
        with self.assertRaises(ValueError):
            save_name_correction(self.output, self.source_hash, "3", "A", "Andy")

    def test_blank_name_rejected(self):
        with self.assertRaises(ValueError):
            save_name_correction(self.output, self.source_hash, 0, "   ", "Andy")
        with self.assertRaises(ValueError):
            save_name_correction(self.output, self.source_hash, 0, "A", "   ")

    def test_control_character_name_rejected(self):
        with self.assertRaises(ValueError):
            save_name_correction(self.output, self.source_hash, 0, "A\x07", "Andy")
        with self.assertRaises(ValueError):
            save_name_correction(self.output, self.source_hash, 0, "A", "Andy\x00")

    def test_overlong_name_rejected(self):
        with self.assertRaises(ValueError):
            save_name_correction(self.output, self.source_hash, 0, "A" * 101, "Andy")

    def test_names_are_trimmed(self):
        save_name_correction(self.output, self.source_hash, 0, "  Al1ce  ", "  Alice  ")
        loaded = load_name_corrections(self.output, self.source_hash)
        self.assertEqual(loaded, {(0, "Al1ce"): "Alice"})

    def test_saved_corrections_survive_independent_output_rewrite(self):
        save_name_correction(self.output, self.source_hash, 0, "A", "Andy")
        # Simulate analyze() rewriting other generated output files, which must
        # not touch the separate name-corrections.json file.
        (self.output / "observations.json").write_text("[]", encoding="utf-8")
        (self.output / "summary.json").write_text(
            json.dumps({"source_hash": self.source_hash}), encoding="utf-8")
        loaded = load_name_corrections(self.output, self.source_hash)
        self.assertEqual(loaded, {(0, "A"): "Andy"})

    # -- apply_name_corrections ---------------------------------------------

    def test_apply_corrects_matching_seat_and_raw_name(self):
        observations = [make_observation(2, "Al1ce")]
        corrections = {(2, "Al1ce"): "Alice"}
        result = apply_name_corrections(observations, corrections)
        seat = result[0]["seats"][0]
        self.assertEqual(seat["username"], "Alice")
        self.assertEqual(seat["username_source"], "user")
        self.assertEqual(seat["username_correction"],
                          {"raw_name": "Al1ce", "corrected_name": "Alice", "scope": "session_seat"})

    def test_apply_corrects_even_when_username_was_none(self):
        observations = [make_observation(2, "Al1ce", username=None)]
        corrections = {(2, "Al1ce"): "Alice"}
        result = apply_name_corrections(observations, corrections)
        self.assertEqual(result[0]["seats"][0]["username"], "Alice")

    def test_apply_preserves_raw_username_and_candidates(self):
        observations = [make_observation(2, "Al1ce")]
        observations[0]["seats"][0]["username_candidates"] = ["Al1ce", "Alicee"]
        corrections = {(2, "Al1ce"): "Alice"}
        result = apply_name_corrections(observations, corrections)
        seat = result[0]["seats"][0]
        self.assertEqual(seat["raw_username"], "Al1ce")
        self.assertEqual(seat["username_candidates"], ["Al1ce", "Alicee"])

    def test_apply_preserves_existing_ocr_issues(self):
        observations = [make_observation(2, "Al1ce")]
        corrections = {(2, "Al1ce"): "Alice"}
        result = apply_name_corrections(observations, corrections)
        self.assertIn("ocr_values_require_review", result[0]["issues"])

    def test_apply_does_not_mutate_input(self):
        observations = [make_observation(2, "Al1ce")]
        corrections = {(2, "Al1ce"): "Alice"}
        apply_name_corrections(observations, corrections)
        self.assertIsNone(observations[0]["seats"][0]["username"])

    def test_apply_scoped_to_exact_seat_other_seat_unchanged(self):
        observations = [make_observation(2, "Bob"), make_observation(5, "Bob")]
        corrections = {(2, "Bob"): "Robert"}
        result = apply_name_corrections(observations, corrections)
        self.assertEqual(result[0]["seats"][0]["username"], "Robert")
        self.assertIsNone(result[1]["seats"][0]["username"])
        self.assertNotIn("username_source", result[1]["seats"][0])

    def test_apply_no_chained_mapping(self):
        # A->B and B->C must each apply once to their own raw name, never chain.
        observations = [make_observation(0, "A"), make_observation(0, "B")]
        corrections = {(0, "A"): "B", (0, "B"): "C"}
        result = apply_name_corrections(observations, corrections)
        self.assertEqual(result[0]["seats"][0]["username"], "B")
        self.assertEqual(result[1]["seats"][0]["username"], "C")

    def test_apply_explicit_corrected_singleton_survives(self):
        observations = [make_observation(0, "OnlySeen")]
        corrections = {(0, "OnlySeen"): "ConfirmedName"}
        result = apply_name_corrections(observations, corrections)
        self.assertEqual(result[0]["seats"][0]["username"], "ConfirmedName")

    def test_apply_leaves_unmatched_seat_untouched(self):
        observations = [make_observation(0, "NoCorrection")]
        result = apply_name_corrections(observations, {})
        seat = result[0]["seats"][0]
        self.assertIsNone(seat["username"])
        self.assertNotIn("username_source", seat)

    def test_apply_handles_seat_without_raw_username(self):
        observation = make_observation(0, None)
        result = apply_name_corrections([observation], {(0, None): "Should not match"})
        self.assertIsNone(result[0]["seats"][0]["username"])

    def test_apply_on_empty_observations_no_crash(self):
        self.assertEqual(apply_name_corrections([], {}), [])


if __name__ == "__main__":
    unittest.main()
