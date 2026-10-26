"""Unit tests for ada_poker.recorder.

Uses fake subprocess.Popen objects; never launches a real ffmpeg process or
records anything.
"""

import subprocess
import sys
import tempfile
import json
import shutil
import uuid
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ada_poker.recorder import (  # noqa: E402
    CaptureRegion,
    RecorderError,
    RecordingOptions,
    Recorder,
    build_command,
)


class _FakeStdin:
    def __init__(self, quit_event: threading.Event):
        self.quit_event = quit_event
        self.closed = False

    def write(self, data):
        if data == b"q":
            self.quit_event.set()

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeStdout:
    def __init__(self, proc: "FakeProcess"):
        self.proc = proc
        self._idx = 0

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            if self.proc.returncode is not None:
                raise StopIteration
            if self._idx < len(self.proc.lines):
                line = self.proc.lines[self._idx]
                self._idx += 1
                time.sleep(self.proc.line_delay)
                return line
            if self.proc.wait_for_quit:
                if self.proc.stdin.quit_event.is_set():
                    self.proc._set_returncode(self.proc.exit_returncode)
                    raise StopIteration
                time.sleep(0.01)
                continue
            self.proc._set_returncode(self.proc.exit_returncode)
            raise StopIteration

    def close(self):
        pass


class FakeProcess:
    """Stand-in for subprocess.Popen used by Recorder's worker thread."""

    def __init__(self, lines=None, exit_returncode=0, wait_for_quit=False,
                 line_delay=0.02):
        self.lines = list(lines or [])
        self.exit_returncode = exit_returncode
        self.wait_for_quit = wait_for_quit
        self.line_delay = line_delay
        self.returncode = None
        self._lock = threading.Lock()
        self.stdin = _FakeStdin(threading.Event())
        self.stdout = _FakeStdout(self)

    def _set_returncode(self, code):
        with self._lock:
            if self.returncode is None:
                self.returncode = code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.returncode is None:
            if timeout is not None and time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            time.sleep(0.01)
        return self.returncode

    def terminate(self):
        self._set_returncode(-15)

    def kill(self):
        self._set_returncode(-9)


def _progress_lines(frame_counts):
    lines = []
    for i, frame in enumerate(frame_counts):
        block = (
            f"frame={frame}\n"
            "fps=15.0\n"
            f"out_time_us={i * 1_000_000}\n"
            "dup_frames=0\n"
            "drop_frames=0\n"
            "speed=1.0x\n"
            "progress=continue\n"
        )
        lines.extend(line.encode("utf-8") for line in block.splitlines(True))
    return lines


class CaptureRegionTests(unittest.TestCase):
    def test_valid_region(self):
        region = CaptureRegion(x=-10, y=-20, width=800, height=600)
        self.assertEqual(region.to_dict(), {"x": -10, "y": -20, "width": 800, "height": 600})

    def test_negative_offsets_allowed(self):
        region = CaptureRegion(x=-100, y=-5, width=64, height=64)
        self.assertEqual(region.x, -100)
        self.assertEqual(region.y, -5)

    def test_width_too_small_rejected(self):
        with self.assertRaises(ValueError):
            CaptureRegion(x=0, y=0, width=31, height=100)

    def test_height_too_small_rejected(self):
        with self.assertRaises(ValueError):
            CaptureRegion(x=0, y=0, width=100, height=0)

    def test_frozen(self):
        region = CaptureRegion(x=0, y=0, width=100, height=100)
        with self.assertRaises(Exception):
            region.x = 5  # type: ignore[misc]


class RecordingOptionsTests(unittest.TestCase):
    def test_defaults_valid(self):
        options = RecordingOptions()
        self.assertEqual(options.fps, 15)
        self.assertEqual(options.duration_seconds, 10800)

    def test_fps_out_of_range(self):
        with self.assertRaises(ValueError):
            RecordingOptions(fps=4)
        with self.assertRaises(ValueError):
            RecordingOptions(fps=61)

    def test_duration_out_of_range(self):
        with self.assertRaises(ValueError):
            RecordingOptions(duration_seconds=0)
        with self.assertRaises(ValueError):
            RecordingOptions(duration_seconds=10801)

    def test_segment_seconds_out_of_range(self):
        with self.assertRaises(ValueError):
            RecordingOptions(segment_seconds=0)
        with self.assertRaises(ValueError):
            RecordingOptions(segment_seconds=3601)

    def test_crf_out_of_range(self):
        with self.assertRaises(ValueError):
            RecordingOptions(crf=-1)
        with self.assertRaises(ValueError):
            RecordingOptions(crf=36)


class BuildCommandTests(unittest.TestCase):
    def test_basic_structure(self):
        region = CaptureRegion(x=-50, y=10, width=1280, height=720)
        options = RecordingOptions(fps=15, duration_seconds=600, segment_seconds=300, crf=18)
        session_dir = Path(r"C:\recordings\session_1")
        command = build_command("ffmpeg.exe", region, options, session_dir)

        self.assertIsInstance(command, list)
        self.assertTrue(all(isinstance(part, str) for part in command))
        self.assertEqual(command[0], "ffmpeg.exe")
        self.assertIn("-offset_x", command)
        self.assertEqual(command[command.index("-offset_x") + 1], "-50")
        self.assertEqual(command[command.index("-offset_y") + 1], "10")
        self.assertEqual(command[command.index("-video_size") + 1], "1280x720")
        self.assertIn("-an", command)
        self.assertEqual(command[command.index("-t") + 1], "600")
        self.assertEqual(command[command.index("-segment_time") + 1], "300")
        self.assertIn("gdigrab", command)
        self.assertIn("-segment_list_type", command)
        self.assertEqual(command[command.index("-segment_list_type") + 1], "csv")

    def test_output_pattern_and_segment_list_present(self):
        region = CaptureRegion(x=0, y=0, width=640, height=480)
        options = RecordingOptions()
        session_dir = Path("/tmp/session_2")
        command = build_command("ffmpeg", region, options, session_dir)
        output_pattern = command[-1]
        self.assertTrue(output_pattern.endswith("part-%04d.mkv"))
        segment_list = command[command.index("-segment_list") + 1]
        self.assertTrue(segment_list.endswith("segments.csv"))

    def test_percent_and_space_in_session_dir_escaped(self):
        region = CaptureRegion(x=0, y=0, width=64, height=64)
        options = RecordingOptions()
        session_dir = Path("C:/data/100% done folder/session_3")
        command = build_command("ffmpeg", region, options, session_dir)
        output_pattern = command[-1]
        # The literal '%' from the directory must be doubled so ffmpeg does
        # not treat it as a format specifier; only the final %04d we added
        # remains a single-percent sequence.
        self.assertIn("100%% done folder", output_pattern)
        self.assertIn("100% done folder", command[command.index("-segment_list") + 1])
        self.assertNotIn("100%%", command[command.index("-segment_list") + 1])
        self.assertTrue(output_pattern.endswith("part-%04d.mkv"))
        self.assertIn(" ", output_pattern)

    def test_no_shell_metacharacter_risk_in_argv(self):
        region = CaptureRegion(x=0, y=0, width=64, height=64)
        options = RecordingOptions()
        session_dir = Path("C:/data/weird & dir/session")
        command = build_command("ffmpeg", region, options, session_dir)
        # Each argument stays a distinct list element; nothing is concatenated
        # into a shell string, so embedded '&' etc. cannot break out.
        self.assertTrue(any("weird & dir" in part for part in command))

    def test_odd_dimensions_padded_not_scaled(self):
        region = CaptureRegion(x=0, y=0, width=641, height=481)
        options = RecordingOptions()
        command = build_command("ffmpeg", region, options, Path("session"))
        self.assertEqual(command[command.index("-video_size") + 1], "641x481")
        vf = command[command.index("-vf") + 1]
        self.assertIn("pad=", vf)
        self.assertNotIn("scale=", vf)


class RecorderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.output_root = (Path(".dev") / "unit-tests" / uuid.uuid4().hex).resolve()
        self.output_root.mkdir(parents=True)
        self.ffmpeg_file = self.output_root / "fake_ffmpeg.exe"
        self.ffmpeg_file.write_bytes(b"")

    def tearDown(self):
        assert self.output_root.is_relative_to((Path.cwd() / ".dev" / "unit-tests").resolve())
        shutil.rmtree(self.output_root)

    def _make_recorder(self, **overrides):
        region = CaptureRegion(x=-10, y=0, width=640, height=480)
        options = RecordingOptions(fps=15, duration_seconds=60, segment_seconds=30)
        return Recorder(str(self.ffmpeg_file), region, self.output_root / "sessions", options)

    def test_unique_session_directories(self):
        with mock.patch("ada_poker.recorder.subprocess.Popen") as popen:
            popen.side_effect = lambda *a, **k: FakeProcess(lines=[], exit_returncode=0)
            recorder_a = self._make_recorder()
            recorder_b = self._make_recorder()
            dir_a = recorder_a.start()
            dir_b = recorder_b.start()
            recorder_a.join(timeout=5)
            recorder_b.join(timeout=5)

        self.assertNotEqual(dir_a, dir_b)
        self.assertTrue(dir_a.is_dir())
        self.assertTrue(dir_b.is_dir())

    def test_stop_immediately_after_start_is_safe_and_idempotent(self):
        with mock.patch("ada_poker.recorder.subprocess.Popen") as popen:
            popen.side_effect = lambda *a, **k: FakeProcess(
                lines=[], wait_for_quit=True
            )
            recorder = self._make_recorder()
            session_dir = recorder.start()
            recorder.stop()
            recorder.stop()  # must not raise or hang
            recorder.join(timeout=10)

        snap = recorder.snapshot()
        self.assertEqual(snap["state"], "error")
        self.assertEqual(snap["stop_reason"], "user_requested")
        self.assertIsNotNone(snap["error"])
        self.assertTrue(session_dir.is_dir())

        # elapsed_seconds must be frozen once finished.
        first = recorder.snapshot()["elapsed_seconds"]
        time.sleep(0.05)
        second = recorder.snapshot()["elapsed_seconds"]
        self.assertEqual(first, second)

    def test_normal_completion_with_output(self):
        lines = _progress_lines([0, 10, 25])

        def fake_popen(*args, **kwargs):
            return FakeProcess(lines=lines, exit_returncode=0, wait_for_quit=False)

        with mock.patch("ada_poker.recorder.subprocess.Popen", side_effect=fake_popen):
            recorder = self._make_recorder()
            session_dir = recorder.start()
            (session_dir / "part-0000.mkv").write_bytes(b"fake-video-bytes")
            (session_dir / "segments.csv").write_text(
                "part-0000.mkv,0.000000,30.000000\n", encoding="utf-8"
            )
            recorder.join(timeout=10)

        snap = recorder.snapshot()
        self.assertEqual(snap["state"], "complete")
        self.assertIsNone(snap["error"])
        self.assertGreater(snap["frames"], 0)
        self.assertTrue((session_dir / "session.json").exists())
        self.assertTrue((session_dir / "encoder.log").exists())

    def test_unexpected_encoder_exit_reports_error(self):
        def fake_popen(*args, **kwargs):
            return FakeProcess(lines=_progress_lines([0, 5]), exit_returncode=1)

        with mock.patch("ada_poker.recorder.subprocess.Popen", side_effect=fake_popen):
            recorder = self._make_recorder()
            session_dir = recorder.start()
            recorder.join(timeout=10)

        snap = recorder.snapshot()
        self.assertEqual(snap["state"], "error")
        self.assertIn("encoder.log", snap["error"])

    def test_ffmpeg_launch_failure_reports_error(self):
        with mock.patch(
            "ada_poker.recorder.subprocess.Popen",
            side_effect=OSError("no such file"),
        ):
            recorder = self._make_recorder()
            recorder.start()
            recorder.join(timeout=10)

        snap = recorder.snapshot()
        self.assertEqual(snap["state"], "error")
        self.assertIn("failed to launch ffmpeg", snap["error"])

    def test_start_validates_ffmpeg_path(self):
        region = CaptureRegion(x=0, y=0, width=64, height=64)
        options = RecordingOptions()
        recorder = Recorder(
            str(self.output_root / "does_not_exist.exe"),
            region,
            self.output_root / "sessions2",
            options,
        )
        with self.assertRaises(RecorderError):
            recorder.start()
        self.assertEqual(recorder.snapshot()["state"], "error")

    def test_double_start_rejected(self):
        with mock.patch("ada_poker.recorder.subprocess.Popen") as popen:
            popen.side_effect = lambda *a, **k: FakeProcess(lines=[], exit_returncode=0)
            recorder = self._make_recorder()
            recorder.start()
            with self.assertRaises(RecorderError):
                recorder.start()
            recorder.join(timeout=5)

    def _write_output(self, session):
        (session / "part-0000.mkv").write_bytes(b"fake-video-bytes")
        (session / "segments.csv").write_text("part-0000.mkv,0,4\n", encoding="utf-8")

    def test_manual_stop_does_not_hide_failed_encoder_exit(self):
        proc = FakeProcess(lines=_progress_lines([15]), exit_returncode=1, wait_for_quit=True)
        with mock.patch("ada_poker.recorder.subprocess.Popen", return_value=proc), \
             mock.patch("ada_poker.recorder.WATCHDOG_POLL_SECONDS", 0.01):
            recorder = self._make_recorder()
            session = recorder.start()
            self._write_output(session)
            time.sleep(0.2)
            recorder.stop()
            recorder.join(3)
        self.assertEqual(recorder.snapshot()["state"], "error")
        self.assertIn("code 1", recorder.snapshot()["error"])

    def test_forced_shutdown_is_error_even_with_video(self):
        proc = FakeProcess(lines=_progress_lines([15]), wait_for_quit=True)
        proc.stdin.write = lambda data: None  # Encoder ignores the graceful quit.
        with mock.patch("ada_poker.recorder.subprocess.Popen", return_value=proc), \
             mock.patch("ada_poker.recorder.WATCHDOG_POLL_SECONDS", 0.01), \
             mock.patch("ada_poker.recorder.STOP_GRACE_SECONDS", 0.05):
            recorder = self._make_recorder()
            session = recorder.start()
            self._write_output(session)
            time.sleep(0.2)
            recorder.stop()
            recorder.join(3)
        self.assertEqual(recorder.snapshot()["state"], "error")
        self.assertIn("did not stop cleanly", recorder.snapshot()["error"])
        self.assertTrue(proc.stdin.closed)

    def test_metadata_failure_stops_capture_and_remains_an_error(self):
        proc = FakeProcess(lines=_progress_lines([15]), wait_for_quit=True)
        with mock.patch("ada_poker.recorder.subprocess.Popen", return_value=proc), \
             mock.patch("ada_poker.recorder.WATCHDOG_POLL_SECONDS", 0.01), \
             mock.patch("ada_poker.recorder._atomic_write_json", side_effect=OSError("disk write failed")):
            recorder = self._make_recorder()
            session = recorder.start()
            self._write_output(session)
            recorder.join(3)
        self.assertEqual(recorder.snapshot()["state"], "error")
        self.assertIn("session.json", recorder.snapshot()["error"])
        self.assertIsNotNone(proc.poll())

    def test_missing_list_is_not_success(self):
        with mock.patch("ada_poker.recorder.subprocess.Popen",
                        return_value=FakeProcess(lines=_progress_lines([15]))):
            recorder = self._make_recorder()
            session = recorder.start()
            (session / "part-0000.mkv").write_bytes(b"possibly incomplete video")
            recorder.join(3)
        self.assertEqual(recorder.snapshot()["state"], "error")

    def test_startup_stall_stops_without_waiting_forever(self):
        proc = FakeProcess(wait_for_quit=True)
        with mock.patch("ada_poker.recorder.subprocess.Popen", return_value=proc), \
             mock.patch("ada_poker.recorder.STARTUP_STALL_SECONDS", 0.05), \
             mock.patch("ada_poker.recorder.WATCHDOG_POLL_SECONDS", 0.01):
            recorder = self._make_recorder()
            recorder.start()
            recorder.join(3)
        self.assertEqual(recorder.snapshot()["state"], "error")
        self.assertEqual(recorder.snapshot()["stop_reason"], "startup_stalled")

    def test_success_manifest_matches_final_state(self):
        with mock.patch("ada_poker.recorder.subprocess.Popen",
                        return_value=FakeProcess(lines=_progress_lines([15]))):
            recorder = self._make_recorder()
            session = recorder.start()
            self._write_output(session)
            recorder.join(3)
        manifest = json.loads((session / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["state"], "complete")
        self.assertEqual(manifest["snapshot"]["stop_reason"], "duration_limit")

    def test_complete_not_published_until_final_metadata_is_saved(self):
        from ada_poker.recorder import _atomic_write_json
        saving = threading.Event()
        allow_save = threading.Event()
        def delayed_save(path, data):
            if data["state"] == "complete":
                saving.set()
                if not allow_save.wait(3):
                    raise OSError("Test did not release the final write")
            _atomic_write_json(path, data)
        with mock.patch("ada_poker.recorder.subprocess.Popen",
                        return_value=FakeProcess(lines=_progress_lines([15]))), \
             mock.patch("ada_poker.recorder._atomic_write_json", side_effect=delayed_save):
            recorder = self._make_recorder()
            session = recorder.start()
            self._write_output(session)
            try:
                self.assertTrue(saving.wait(3))
                self.assertEqual(recorder.snapshot()["state"], "stopping")
            finally:
                allow_save.set()
                recorder.join(3)
        self.assertEqual(recorder.snapshot()["state"], "complete")
        self.assertEqual(json.loads((session / "session.json").read_text())["state"], "complete")

    def test_display_interruption_is_persisted_as_error(self):
        with mock.patch("ada_poker.recorder.subprocess.Popen",
                        return_value=FakeProcess(lines=_progress_lines([15]), wait_for_quit=True)), \
             mock.patch("ada_poker.recorder.WATCHDOG_POLL_SECONDS", 0.01):
            recorder = self._make_recorder()
            session = recorder.start()
            self._write_output(session)
            recorder.stop(reason="display_changed", error="Monitor layout changed")
            recorder.join(3)
        manifest = json.loads((session / "session.json").read_text())
        self.assertEqual(manifest["state"], "error")
        self.assertEqual(manifest["snapshot"]["error"], "Monitor layout changed")


if __name__ == "__main__":
    unittest.main()
