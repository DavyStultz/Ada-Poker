"""Asynchronous FFmpeg-based desktop capture engine for Ada Poker.

Uses gdigrab to capture a fixed screen region into five-minute Matroska
segments produced by a single, long-lived FFmpeg process. No Tk or other UI
code lives here; the UI polls :meth:`Recorder.snapshot` for status.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from .windows import CaptureWindow, validate_window, window_client_bounds

# -- Tunable constants -------------------------------------------------

MIN_FREE_BYTES_TO_START = 1 * 1024 ** 3  # 1 GiB
MIN_FREE_BYTES_RUNNING = 512 * 1024 ** 2  # 512 MiB
STARTUP_STALL_SECONDS = 30.0
STALE_PROGRESS_SECONDS = 30.0
STOP_GRACE_SECONDS = 15.0
METADATA_WRITE_INTERVAL_SECONDS = 5.0
WATCHDOG_POLL_SECONDS = 1.0


class RecorderError(RuntimeError):
    """Raised for recorder setup failures that are detected synchronously."""


# -- Data model ----------------------------------------------------------


@dataclass(frozen=True)
class CaptureRegion:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if any(type(v) is not int for v in (self.x, self.y, self.width, self.height)):
            raise ValueError("Capture coordinates and dimensions must be whole pixels")
        if self.width < 32 or self.height < 32:
            raise ValueError(
                "CaptureRegion width and height must each be at least 32 pixels"
            )

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class RecordingOptions:
    fps: int = 15
    duration_seconds: int = 10800
    segment_seconds: int = 300
    crf: int = 18

    def __post_init__(self) -> None:
        if any(type(v) is not int for v in dataclasses.astuple(self)):
            raise ValueError("Recording options must be whole numbers")
        if not (5 <= self.fps <= 60):
            raise ValueError("fps must be between 5 and 60")
        if not (1 <= self.duration_seconds <= 10800):
            raise ValueError("duration_seconds must be between 1 and 10800")
        if not (1 <= self.segment_seconds <= 3600):
            raise ValueError("segment_seconds must be between 1 and 3600")
        if not (0 <= self.crf <= 35):
            raise ValueError("crf must be between 0 and 35")


def _padded_even(value: int) -> int:
    return value + (value % 2)


def encoded_dimensions(region: CaptureRegion) -> tuple[int, int]:
    return _padded_even(region.width), _padded_even(region.height)


def _escape_percent(path_str: str) -> str:
    """Escape literal '%' in a directory string for FFmpeg output patterns.

    FFmpeg expands '%' sequences (e.g. %04d) in the video output pattern,
    so any stray '%' coming from the session directory name must be doubled.
    """

    return path_str.replace("%", "%%")


def build_command(
    ffmpeg_path: str,
    region: CaptureRegion,
    options: RecordingOptions,
    session_dir: Path,
    window: CaptureWindow | None = None,
    visible_window: bool = False,
) -> list[str]:
    """Build the argv list for the single long-lived FFmpeg process.

    Standalone so it can be unit tested without launching a process.
    """

    session_dir = Path(session_dir)
    escaped_dir = _escape_percent(str(session_dir))
    output_pattern = f"{escaped_dir}{os.sep}part-%04d.mkv"
    segment_list_path = str(session_dir / "segments.csv")
    gop = max(1, options.fps * 2)

    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel", "warning",
        "-nostats",
        "-y",
        "-progress", "pipe:1",
        "-stats_period", "1",
        "-thread_queue_size", "16",
        "-probesize", "32",
        "-analyzeduration", "0",
        "-f", "gdigrab",
        "-framerate", str(options.fps),
        "-draw_mouse", "0",
        "-offset_x", str(0 if window and not visible_window else region.x),
        "-offset_y", str(0 if window and not visible_window else region.y),
        "-video_size", f"{region.width}x{region.height}",
        "-i", f"hwnd={window.hwnd}" if window and not visible_window else "desktop",
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(options.crf),
        "-pix_fmt", "yuv420p",
        "-fps_mode", "passthrough",
        "-bf", "0",
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-force_key_frames", f"expr:gte(t,n_forced*{options.segment_seconds})",
        "-t", str(options.duration_seconds),
        "-f", "segment",
        "-segment_time", str(options.segment_seconds),
        "-reset_timestamps", "1",
        "-segment_list", segment_list_path,
        "-segment_list_type", "csv",
        "-segment_format", "matroska",
        output_pattern,
    ]


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, indent=2, sort_keys=True, default=str)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


class Recorder:
    """Drives one FFmpeg gdigrab recording session in a background thread."""

    def __init__(
        self,
        ffmpeg_path: str,
        region: CaptureRegion,
        output_root: Path,
        options: RecordingOptions,
        window: CaptureWindow | None = None,
        visible_window: bool = False,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.region = region
        self.output_root = Path(output_root)
        self.options = options
        self.window = window
        self.visible_window = visible_window
        if visible_window and window is None:
            raise ValueError('Visible table capture requires a selected window.')
        if window and (region.width, region.height) != (window.width, window.height):
            raise ValueError('Recording dimensions must match the selected window client.')

        self._lock = threading.Lock()
        self._started = False
        self._stop_event = threading.Event()
        self._stop_reason: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._session_dir: Optional[Path] = None
        self._start_monotonic: Optional[float] = None
        self._finish_monotonic: Optional[float] = None
        self._last_progress_monotonic: Optional[float] = None
        self._start_time_iso: Optional[str] = None
        self._failure: Optional[str] = None
        self._finished = threading.Event()

        self._data: dict[str, Any] = {
            "state": "idle",
            "session_dir": None,
            "frames": 0,
            "fps": 0.0,
            "media_seconds": 0.0,
            "speed": 0.0,
            "dropped_frames": 0,
            "duplicated_frames": 0,
            "error": None,
            "stop_reason": None,
        }

    # -- Public API -------------------------------------------------

    def start(self) -> Path:
        with self._lock:
            if self._started:
                raise RecorderError("Recorder.start() has already been called")
            self._started = True
            self._data["state"] = "starting"

        try:
            if self.window:
                validate_window(self.window)
                self._validate_visible_bounds()
            resolved_ffmpeg = self._resolve_ffmpeg(self.ffmpeg_path)
            session_dir = self._create_session_dir()
            self._check_free_space(session_dir, MIN_FREE_BYTES_TO_START)
        except (RecorderError, OSError) as exc:
            with self._lock:
                self._data["state"] = "error"
                self._data["error"] = str(exc)
            raise

        self._session_dir = session_dir
        with self._lock:
            self._data["session_dir"] = str(session_dir)

        self._start_monotonic = time.monotonic()
        self._last_progress_monotonic = self._start_monotonic
        self._start_time_iso = datetime.now(timezone.utc).isoformat()

        self._thread = threading.Thread(
            target=self._run,
            args=(resolved_ffmpeg, session_dir),
            daemon=True,
        )
        self._thread.start()
        return session_dir

    def stop(self, reason: str = "user_requested", error: Optional[str] = None) -> None:
        with self._lock:
            state = self._data["state"]
            if state in ("idle", "complete", "error"):
                return
            if self._stop_event.is_set():
                return

            if error:
                self._failure = error
            self._data["state"] = "stopping"
            self._stop_reason = reason
            self._stop_event.set()

    def snapshot(self) -> dict:
        data = self._snapshot_for_metadata()
        if (data["state"] in ("complete", "error") and self._thread is not None
                and not self._finished.is_set()):
            data["state"] = "stopping"
        return data

    def _snapshot_for_metadata(self) -> dict:
        with self._lock:
            data = dict(self._data)
        if self._start_monotonic is None:
            data["elapsed_seconds"] = 0.0
        else:
            end = (
                self._finish_monotonic
                if self._finish_monotonic is not None
                else time.monotonic()
            )
            data["elapsed_seconds"] = round(end - self._start_monotonic, 3)
        return data

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # -- Setup validation --------------------------------------------

    def _resolve_ffmpeg(self, path: str) -> str:
        resolved = shutil.which(path)
        if resolved is None and Path(path).is_file():
            resolved = str(path)
        if resolved is None:
            raise RecorderError(f"ffmpeg executable not found: {path}")
        return resolved

    def _create_session_dir(self) -> Path:
        self.output_root.mkdir(parents=True, exist_ok=True)
        last_error: Optional[Exception] = None
        for _ in range(5):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            session_id = f"{timestamp}_{uuid.uuid4().hex[:8]}"
            candidate = self.output_root / session_id
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                return candidate
            except FileExistsError as exc:
                last_error = exc
                continue
        raise RecorderError(
            f"failed to create a unique session directory: {last_error}"
        )

    def _check_free_space(self, path: Path, minimum_bytes: int) -> None:
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            raise RecorderError(f"unable to determine free disk space: {exc}") from exc
        if usage.free < minimum_bytes:
            raise RecorderError(
                "insufficient free disk space "
                f"({usage.free} bytes available, {minimum_bytes} required)"
            )

    # -- Worker thread -------------------------------------------------

    def _run(self, ffmpeg_path: str, session_dir: Path) -> None:
        try:
            self._capture(ffmpeg_path, session_dir)
        except Exception as exc:
            # Unexpected I/O or subprocess errors must not strand the UI in Recording.
            self._failure = f"Recording worker failed: {exc}"
            try:
                if self._process is not None and self._process.poll() is None:
                    self._terminate_gracefully(self._process)
            finally:
                self._finish_monotonic = time.monotonic()
                with self._lock:
                    self._data.update(state="error", error=self._failure, stop_reason="worker_error")
                self._safe_write_metadata(session_dir, build_command(ffmpeg_path, self.region, self.options, session_dir, self.window, self.visible_window),
                                          datetime.now(timezone.utc), encoded_dimensions(self.region))
        finally:
            # Publish completion only after metadata and process cleanup finish.
            self._finished.set()

    def _capture(self, ffmpeg_path: str, session_dir: Path) -> None:
        command = build_command(ffmpeg_path, self.region, self.options, session_dir, self.window, self.visible_window)
        creation_dt = datetime.now(timezone.utc)
        encoded_dims = encoded_dimensions(self.region)
        log_path = session_dir / "encoder.log"

        try:
            log_file = open(log_path, "wb")
        except OSError as exc:
            with self._lock:
                self._data["state"] = "error"
                self._data["error"] = f"failed to open encoder log: {exc}"
            self._finish_monotonic = time.monotonic()
            self._safe_write_metadata(session_dir, command, creation_dt, encoded_dims)
            return

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log_file,
                creationflags=creationflags,
            )
        except OSError as exc:
            log_file.close()
            with self._lock:
                self._data["state"] = "error"
                self._data["error"] = f"failed to launch ffmpeg: {exc}"
            self._finish_monotonic = time.monotonic()
            self._safe_write_metadata(session_dir, command, creation_dt, encoded_dims)
            return

        self._process = proc
        self._safe_write_metadata(session_dir, command, creation_dt, encoded_dims)

        watchdog_stop = threading.Event()
        watchdog_result: dict[str, Any] = {"reason": None, "error": None}
        watchdog_thread = threading.Thread(
            target=self._watchdog,
            args=(proc, session_dir, watchdog_stop, watchdog_result),
            daemon=True,
        )
        watchdog_thread.start()

        last_metadata_write = time.monotonic()
        try:
            if proc.stdout is not None:
                for raw_line in proc.stdout:
                    self._handle_progress_line(raw_line)
                    now = time.monotonic()
                    if now - last_metadata_write >= METADATA_WRITE_INTERVAL_SECONDS:
                        ok = self._safe_write_metadata(
                            session_dir, command, creation_dt, encoded_dims
                        )
                        if not ok:
                            self.stop("metadata_write_failed")
                        last_metadata_write = now
        except Exception as exc:
            self._failure = f"Unable to read encoder progress: {exc}"
            self._terminate_gracefully(proc)
        finally:
            watchdog_stop.set()
            watchdog_thread.join(timeout=STOP_GRACE_SECONDS + 10)
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except OSError:
                pass
            try:
                log_file.close()
            except OSError:
                pass
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except OSError:
                pass

        returncode = proc.poll()
        if returncode is None:
            try:
                returncode = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._failure = "Encoder did not exit cleanly; inspect the final video part."
                proc.kill()
                returncode = proc.wait(timeout=5)

        self._finish_monotonic = time.monotonic()
        self._finalize_state(session_dir, returncode, watchdog_result)
        self._safe_write_metadata(session_dir, command, creation_dt, encoded_dims)

    def _handle_progress_line(self, raw_line: bytes) -> None:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or "=" not in line:
            return
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        with self._lock:
            if key == "frame":
                try:
                    frames = int(value)
                    if frames > self._data["frames"]:
                        self._last_progress_monotonic = time.monotonic()
                    self._data["frames"] = frames
                except ValueError:
                    pass
            elif key == "fps":
                try:
                    self._data["fps"] = float(value)
                except ValueError:
                    pass
            elif key == "out_time_us":
                try:
                    self._data["media_seconds"] = max(0, round(int(value) / 1_000_000, 3))
                except ValueError:
                    pass
            elif key == "speed":
                try:
                    self._data["speed"] = float(value.rstrip("x").strip())
                except ValueError:
                    pass
            elif key == "drop_frames":
                try:
                    self._data["dropped_frames"] = int(value)
                except ValueError:
                    pass
            elif key == "dup_frames":
                try:
                    self._data["duplicated_frames"] = int(value)
                except ValueError:
                    pass
            elif key == "progress":
                if self._data["state"] == "starting" and self._data["frames"]:
                    self._data["state"] = "recording"

    def _watchdog(
        self,
        proc: subprocess.Popen,
        session_dir: Path,
        stop_flag: threading.Event,
        result: dict,
    ) -> None:
        launch = time.monotonic()
        while not stop_flag.is_set():
            if proc.poll() is not None:
                return
            now = time.monotonic()
            with self._lock:
                frames_seen = bool(self._data.get("frames"))
                last_progress = self._last_progress_monotonic or launch

            if self._stop_event.is_set():
                result["reason"] = self._stop_reason or "user_requested"
                with self._lock:
                    self._data["state"] = "stopping"
                self._terminate_gracefully(proc)
                return

            if self.window:
                try:
                    validate_window(self.window)
                    self._validate_visible_bounds()
                except OSError as exc:
                    result['reason'] = 'window_unavailable'
                    result['error'] = str(exc)
                    with self._lock:
                        self._data['state'] = 'stopping'
                    self._terminate_gracefully(proc)
                    return

            if not frames_seen and (now - launch) > STARTUP_STALL_SECONDS:
                result["reason"] = "startup_stalled"
                result["error"] = (
                    "ffmpeg produced no frames within "
                    f"{int(STARTUP_STALL_SECONDS)} seconds of startup; see encoder.log"
                )
                with self._lock:
                    self._data["state"] = "stopping"
                self._terminate_gracefully(proc)
                return

            if frames_seen and (now - last_progress) > STALE_PROGRESS_SECONDS:
                result["reason"] = "stale_progress"
                result["error"] = (
                    "ffmpeg stopped reporting progress for over "
                    f"{int(STALE_PROGRESS_SECONDS)} seconds; see encoder.log"
                )
                with self._lock:
                    self._data["state"] = "stopping"
                self._terminate_gracefully(proc)
                return

            try:
                free = shutil.disk_usage(session_dir).free
            except OSError as exc:
                result["reason"] = "disk_access_failed"
                result["error"] = f"Cannot access recording drive: {exc}"
                self._terminate_gracefully(proc)
                return
            if free is not None and free < MIN_FREE_BYTES_RUNNING:
                result["reason"] = "low_disk_space"
                result["error"] = (
                    "available disk space fell below the minimum required to "
                    "continue recording; recording was stopped"
                )
                with self._lock:
                    self._data["state"] = "stopping"
                self._terminate_gracefully(proc)
                return

            stop_flag.wait(WATCHDOG_POLL_SECONDS)

    def _validate_visible_bounds(self):
        if self.visible_window and window_client_bounds(self.window) != (
                self.region.x, self.region.y, self.region.width, self.region.height):
            raise OSError('The table moved or changed size. Recording stopped; position it and start a new recording.')

    def _terminate_gracefully(self, proc: subprocess.Popen) -> None:
        try:
            if proc.stdin is not None:
                proc.stdin.write(b"q")
                proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=STOP_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        self._failure = self._failure or "Encoder did not stop cleanly; inspect the final video part."
        try:
            proc.terminate()
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def _finalize_state(
        self, session_dir: Path, returncode: Optional[int], watchdog_result: dict
    ) -> None:
        with self._lock:
            frames = self._data.get("frames") or 0
        has_output = self._has_valid_output(session_dir)
        watchdog_error = watchdog_result.get("error")
        stop_requested = self._stop_event.is_set()

        if self._failure:
            final_state = "error"
            error_msg = self._failure
        elif watchdog_error:
            final_state = "error"
            error_msg = watchdog_error
        elif returncode not in (0,):
            final_state = "error"
            error_msg = f"ffmpeg exited with code {returncode}; see {session_dir / 'encoder.log'}"
        elif stop_requested:
            if frames > 0 and has_output:
                final_state = "complete"
                error_msg = None
            else:
                final_state = "error"
                error_msg = (
                    "recording was stopped before any frames were captured; "
                    "see encoder.log"
                )
        elif frames <= 0 or not has_output:
            final_state = "error"
            error_msg = (
                "ffmpeg produced no valid recorded segments; see encoder.log"
            )
        else:
            final_state = "complete"
            error_msg = None

        with self._lock:
            self._data["state"] = final_state
            self._data["error"] = error_msg
            self._data["stop_reason"] = (watchdog_result.get("reason") or self._stop_reason
                                         or ("duration_limit" if final_state == "complete" else "encoder_error"))

    def _has_valid_output(self, session_dir: Path) -> bool:
        from .verify import read_segments
        try:
            return bool(read_segments(session_dir))
        except (OSError, ValueError):
            return False

    def _safe_write_metadata(
        self,
        session_dir: Path,
        command: list[str],
        creation_dt: datetime,
        encoded_dims: tuple[int, int],
    ) -> bool:
        try:
            self._write_metadata(session_dir, command, creation_dt, encoded_dims)
            return True
        except OSError as exc:
            with self._lock:
                self._failure = f"failed to write session.json: {exc}"
                self._data["error"] = self._failure
                if self._finish_monotonic is not None:
                    self._data["state"] = "error"
                else:
                    self._stop_reason = "metadata_write_failed"
                    self._stop_event.set()
            return False

    def _write_metadata(
        self,
        session_dir: Path,
        command: list[str],
        creation_dt: datetime,
        encoded_dims: tuple[int, int],
    ) -> None:
        snap = self._snapshot_for_metadata()
        finish_iso = (
            datetime.now(timezone.utc).isoformat()
            if snap["state"] in ("complete", "error")
            else None
        )
        data = {
            "schema_version": 1,
            "session_id": session_dir.name,
            "creation_time_utc": creation_dt.isoformat(),
            "start_time_utc": self._start_time_iso,
            "finish_time_utc": finish_iso,
            "region": self.region.to_dict(),
            "capture_source": {"mode": "visible_window" if self.visible_window else "window_client", "window": self.window.to_dict()}
                              if self.window else {"mode": "desktop_region"},
            "options": dataclasses.asdict(self.options),
            "encoded_width": encoded_dims[0],
            "encoded_height": encoded_dims[1],
            "command": command,
            "state": snap["state"],
            "snapshot": snap,
            "video_time_reference": (
                "start_time_utc is an approximate wall-clock reference for when "
                "capture began; it is not frame-accurate. segments.csv and each "
                "segment's internal timestamps (reset per segment) are the "
                "authoritative source for video-relative timing."
            ),
        }
        _atomic_write_json(session_dir / "session.json", data)
