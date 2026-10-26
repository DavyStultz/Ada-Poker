"""Small Win32 adapter for monitor discovery and in-memory preview images.

Recording itself belongs to FFmpeg. No screenshot files are created here.
All coordinates are physical pixels relative to the primary monitor origin.
"""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import asdict, dataclass, replace
from pathlib import PureWindowsPath
import math
import os


@dataclass(frozen=True)
class Monitor:
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool
    work_x: int
    work_y: int

    def matches_capture(self, other: Monitor) -> bool:
        return (self.name, self.x, self.y, self.width, self.height) == (
            other.name, other.x, other.y, other.width, other.height)

    def contains(self, x: int, y: int, width: int, height: int) -> bool:
        return (width > 0 and height > 0 and self.x <= x and self.y <= y
                and x + width <= self.x + self.width
                and y + height <= self.y + self.height)


def selection_pixels(monitor: Monitor, preview_size: tuple[int, int],
                     start: tuple[float, float], end: tuple[float, float]) -> tuple[int, int, int, int]:
    """Map a drag in a scaled preview to a bounded, unscaled desktop rectangle."""
    pw, ph = preview_size
    if pw <= 0 or ph <= 0:
        raise ValueError("Preview dimensions must be positive.")
    left, right = sorted((max(0, min(pw, start[0])), max(0, min(pw, end[0]))))
    top, bottom = sorted((max(0, min(ph, start[1])), max(0, min(ph, end[1]))))
    x1 = math.floor(left * monitor.width / pw)
    y1 = math.floor(top * monitor.height / ph)
    x2 = math.ceil(right * monitor.width / pw)
    y2 = math.ceil(bottom * monitor.height / ph)
    return monitor.x + x1, monitor.y + y1, x2 - x1, y2 - y1


def _api():
    if os.name != "nt":
        raise OSError("Ada Poker screen capture requires Windows.")
    return ct.WinDLL("user32", use_last_error=True), ct.WinDLL("gdi32", use_last_error=True)


def enable_dpi_awareness() -> None:
    user32, _ = _api()
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ct.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
        user32.SetProcessDpiAwarenessContext(ct.c_void_p(-4))
    except AttributeError:
        user32.SetProcessDPIAware()


class _MonitorInfo(ct.Structure):
    _fields_ = [("size", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT),
                ("flags", wt.DWORD), ("device", wt.WCHAR * 32)]


def monitors() -> list[Monitor]:
    user32, _ = _api()
    callback_type = ct.WINFUNCTYPE(wt.BOOL, wt.HANDLE, wt.HDC, ct.POINTER(wt.RECT), wt.LPARAM)
    user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ct.POINTER(_MonitorInfo)]
    user32.GetMonitorInfoW.restype = wt.BOOL
    user32.EnumDisplayMonitors.argtypes = [wt.HDC, ct.POINTER(wt.RECT), callback_type, wt.LPARAM]
    user32.EnumDisplayMonitors.restype = wt.BOOL
    found: list[Monitor] = []
    errors: list[int] = []

    @callback_type
    def visit(handle, _dc, _rect, _data):
        info = _MonitorInfo()
        info.size = ct.sizeof(info)
        if not user32.GetMonitorInfoW(handle, ct.byref(info)):
            errors.append(ct.get_last_error())
            return False
        r = info.monitor
        found.append(Monitor(info.device, r.left, r.top, r.right-r.left, r.bottom-r.top,
                             bool(info.flags & 1), info.work.left, info.work.top))
        return True

    if not user32.EnumDisplayMonitors(None, None, visit, 0) or errors:
        raise ct.WinError(errors[0] if errors else ct.get_last_error())
    if not found:
        raise OSError("No active display was found.")
    return sorted(found, key=lambda m: (not m.primary, m.x, m.y))


class _BitmapHeader(ct.Structure):
    _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG),
                ("planes", wt.WORD), ("bits", wt.WORD), ("compression", wt.DWORD),
                ("image_size", wt.DWORD), ("xppm", wt.LONG), ("yppm", wt.LONG),
                ("used", wt.DWORD), ("important", wt.DWORD)]


def preview_ppm(x: int, y: int, width: int, height: int,
                max_width: int = 760, max_height: int = 400, *, hwnd=None) -> tuple[bytes, int, int]:
    """Capture and downsize a preview with GDI, returning binary PPM for Tk."""
    if min(width, height, max_width, max_height) < 1:
        raise ValueError("Capture dimensions must be positive.")
    ratio = min(1.0, max_width / width, max_height / height)
    pw, ph = max(1, int(width * ratio)), max(1, int(height * ratio))
    user32, gdi32 = _api()
    user32.GetDC.argtypes, user32.GetDC.restype = [wt.HWND], wt.HDC
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wt.HDC], wt.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ct.c_int, ct.c_int]
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wt.HDC, wt.HANDLE], wt.HANDLE
    gdi32.DeleteObject.argtypes = [wt.HANDLE]
    gdi32.DeleteDC.argtypes = [wt.HDC]
    gdi32.SetStretchBltMode.argtypes = [wt.HDC, ct.c_int]
    gdi32.StretchBlt.argtypes = [wt.HDC, ct.c_int, ct.c_int, ct.c_int, ct.c_int,
                                wt.HDC, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.DWORD]
    gdi32.StretchBlt.restype = wt.BOOL
    gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                              ct.c_void_p, ct.c_void_p, wt.UINT]
    gdi32.GetDIBits.restype = ct.c_int
    screen = memory = bitmap = previous = None
    try:
        screen = user32.GetDC(hwnd)
        if not screen:
            raise ct.WinError()
        memory = gdi32.CreateCompatibleDC(screen)
        bitmap = gdi32.CreateCompatibleBitmap(screen, pw, ph)
        if not memory or not bitmap:
            raise ct.WinError()
        previous = gdi32.SelectObject(memory, bitmap)
        if not previous:
            raise ct.WinError()
        gdi32.SetStretchBltMode(memory, 4)  # HALFTONE, preview only
        if not gdi32.StretchBlt(memory, 0, 0, pw, ph, screen, x, y, width, height,
                               0x00CC0020 | 0x40000000):
            raise OSError("Windows did not allow desktop capture. Run the recorder in your normal "
                          "signed-in desktop session and keep the display unlocked.")
        # GetDIBits requires the bitmap not to be selected into a DC.
        gdi32.SelectObject(memory, previous)
        previous = None
        header = _BitmapHeader()
        header.size = ct.sizeof(header)
        header.width, header.height = pw, -ph  # top-down BGRA
        header.planes, header.bits = 1, 32
        pixels = ct.create_string_buffer(pw * ph * 4)
        if gdi32.GetDIBits(screen, bitmap, 0, ph, pixels, ct.byref(header), 0) != ph:
            raise ct.WinError()
        bgra = pixels.raw
        rgb = bytearray(pw * ph * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
        return f"P6\n{pw} {ph}\n255\n".encode("ascii") + rgb, pw, ph
    finally:
        if previous and memory:
            gdi32.SelectObject(memory, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory:
            gdi32.DeleteDC(memory)
        if screen:
            user32.ReleaseDC(hwnd, screen)


@dataclass(frozen=True)
class CaptureWindow:
    hwnd: int
    pid: int
    class_name: str
    title: str
    width: int
    height: int
    minimized: bool = False
    affinity: int | None = None
    application: str = ''

    def to_dict(self):
        return asdict(self)


def window_info(hwnd: int) -> CaptureWindow:
    """Read a window's client size and identity without changing the window."""
    u, _ = _api()
    u.IsWindow.argtypes = [wt.HWND]
    u.IsWindowVisible.argtypes = [wt.HWND]
    u.IsIconic.argtypes = [wt.HWND]
    u.GetWindowThreadProcessId.argtypes = [wt.HWND, ct.POINTER(wt.DWORD)]
    u.GetClientRect.argtypes = [wt.HWND, ct.POINTER(wt.RECT)]
    u.GetWindowTextLengthW.argtypes = [wt.HWND]
    u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ct.c_int]
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ct.c_int]
    u.GetWindowDisplayAffinity.argtypes = [wt.HWND, ct.POINTER(wt.DWORD)]
    if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd):
        raise OSError('The selected window closed or is hidden. Refresh and select it again.')
    title = ct.create_unicode_buffer(u.GetWindowTextLengthW(hwnd)+1)
    name = ct.create_unicode_buffer(256)
    pid, rect, affinity = wt.DWORD(), wt.RECT(), wt.DWORD()
    u.GetWindowTextW(hwnd, title, len(title))
    if not u.GetClassNameW(hwnd, name, len(name)) or not u.GetWindowThreadProcessId(hwnd, ct.byref(pid)):
        raise OSError('The selected window is no longer available.')
    if not u.GetClientRect(hwnd, ct.byref(rect)):
        raise ct.WinError()
    known = u.GetWindowDisplayAffinity(hwnd, ct.byref(affinity))
    return CaptureWindow(int(hwnd), pid.value, name.value, title.value,
                         rect.right-rect.left, rect.bottom-rect.top,
                         bool(u.IsIconic(hwnd)), affinity.value if known else None)


def process_name(pid: int) -> str:
    """Read only the executable name; restricted processes may return no name."""
    k = ct.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    k.CloseHandle.argtypes = [wt.HANDLE]
    handle = k.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        size = wt.DWORD(32768)
        path = ct.create_unicode_buffer(size.value)
        return PureWindowsPath(path.value).stem if k.QueryFullProcessImageNameW(handle, 0, path, ct.byref(size)) else ''
    finally:
        k.CloseHandle(handle)


def window_label(window: CaptureWindow) -> str:
    if window.application.casefold() == 'clubgg':
        return 'ClubGG - Lobby' if window.title.casefold() == 'clubgg' else f'ClubGG - {window.title or "Untitled table"}'
    return window.title or window.application or 'Untitled window'


def capture_windows() -> list[CaptureWindow]:
    """List visible top-level clients. Duplicate titles remain separate handles."""
    u, _ = _api()
    callback_type = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumWindows.argtypes = [callback_type, wt.LPARAM]
    u.EnumWindows.restype = wt.BOOL
    found = []
    applications = {}

    @callback_type
    def visit(hwnd, _data):
        try:
            value = window_info(hwnd)
            if (value.pid != os.getpid() and value.width >= 32 and value.height >= 32
                    and value.class_name not in ('Progman', 'WorkerW', 'Shell_TrayWnd')):
                if value.pid not in applications:
                    applications[value.pid] = process_name(value.pid)
                value = replace(value, application=applications[value.pid])
                if value.title or value.application.casefold() == 'clubgg':
                    found.append(value)
        except OSError:
            pass  # Windows can close during enumeration.
        return True

    if not u.EnumWindows(visit, 0):
        raise ct.WinError()
    return sorted(found, key=lambda w: (w.application.casefold() != 'clubgg',
                                      w.title.casefold() == 'clubgg', window_label(w).casefold(), w.hwnd))


def validate_window(target: CaptureWindow, current: CaptureWindow | None = None, *, allow_resize=False) -> CaptureWindow:
    current = current if current is not None else window_info(target.hwnd)
    if (current.hwnd, current.pid, current.class_name) != (target.hwnd, target.pid, target.class_name):
        raise OSError('The selected window was replaced. Refresh and select it again.')
    if current.minimized:
        raise OSError('The selected window is minimized. Restore it before recording.')
    if current.affinity:
        raise OSError('This application is blocking Windows capture. Its content cannot be recorded by this capture method.')
    if not allow_resize and (current.width, current.height) != (target.width, target.height):
        raise OSError('The window size changed. Reselect it to record at the new size.')
    if min(current.width, current.height) < 32:
        raise OSError('The window content is too small to record.')
    return current


def window_client_bounds(target: CaptureWindow) -> tuple[int, int, int, int]:
    """Locate a validated client on the desktop, including negative monitor origins."""
    current = validate_window(target)
    u, _ = _api()
    u.ClientToScreen.argtypes = [wt.HWND, ct.POINTER(wt.POINT)]
    point = wt.POINT(0, 0)
    if not u.ClientToScreen(current.hwnd, ct.byref(point)):
        raise ct.WinError()
    return point.x, point.y, current.width, current.height


def window_preview_ppm(target: CaptureWindow, max_width=760, max_height=400, *, visible_window=False):
    current = validate_window(target, allow_resize=True)
    if visible_window:
        return preview_ppm(*window_client_bounds(current), max_width, max_height)
    return preview_ppm(0, 0, current.width, current.height, max_width, max_height, hwnd=current.hwnd)


def keep_awake(recording: bool) -> None:
    """Temporary, per-thread sleep prevention; does not change power settings."""
    kernel32 = ct.WinDLL("kernel32", use_last_error=True)
    kernel32.SetThreadExecutionState.argtypes = [wt.DWORD]
    kernel32.SetThreadExecutionState.restype = wt.DWORD
    flags = 0x80000000 | (0x00000001 | 0x00000002 if recording else 0)
    if not kernel32.SetThreadExecutionState(flags):
        raise ct.WinError()
