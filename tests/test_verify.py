import csv
from pathlib import Path
import shutil
import unittest
import uuid

from ada_poker.verify import read_segments


class SegmentListTests(unittest.TestCase):
    def setUp(self):
        # Avoid Windows sandbox ACL behavior of tempfile's private directories.
        self.folder = Path(".dev") / "unit-tests" / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        (self.folder / "part-0000.mkv").write_bytes(b"test fixture")
        (self.folder / "part-0001.mkv").write_bytes(b"test fixture")

    def tearDown(self):
        assert self.folder.resolve().is_relative_to((Path.cwd() / ".dev" / "unit-tests").resolve())
        shutil.rmtree(self.folder)

    def write_list(self, rows):
        with (self.folder / "segments.csv").open("w", newline="") as stream:
            csv.writer(stream).writerows(rows)

    def test_contiguous_parts(self):
        self.write_list([("part-0000.mkv", 0, 4), ("part-0001.mkv", 4, 8)])
        self.assertEqual(len(read_segments(self.folder)), 2)

    def test_gap_is_reported(self):
        self.write_list([("part-0000.mkv", 0, 4), ("part-0001.mkv", 5, 8)])
        with self.assertRaisesRegex(ValueError, "gap or overlap"):
            read_segments(self.folder)

    def test_duplicate_and_path_escape_are_rejected(self):
        for rows in [[("part-0000.mkv", 0, 4), ("part-0000.mkv", 4, 8)],
                     [("../other.mkv", 0, 4)]]:
            with self.subTest(rows=rows):
                self.write_list(rows)
                with self.assertRaises(ValueError):
                    read_segments(self.folder)

    def test_invalid_and_nonfinite_timing(self):
        for start, end in [(1, 0), (0, "nan"), ("-inf", 4)]:
            with self.subTest(start=start, end=end):
                self.write_list([("part-0000.mkv", start, end)])
                with self.assertRaises(ValueError):
                    read_segments(self.folder)


if __name__ == "__main__":
    unittest.main()
