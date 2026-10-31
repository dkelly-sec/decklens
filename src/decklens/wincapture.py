"""Capture one window's picture on Windows, even when other windows cover it.

Uses PrintWindow with PW_RENDERFULLCONTENT (Windows 8.1+), which asks the
window to draw itself into our bitmap, like the Snipping Tool's window mode.
It can't capture a minimised window (Windows stops drawing those), and a few
GPU-drawn windows come back black; callers fall back to reading the screen.

Every Windows handle type is declared explicitly: on 64-bit Python, leaving
ctypes to guess truncates handles to 32 bits and crashes at random.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Phone Link's programs (it used to be called "Your Phone"). Its mirror window
# is titled after your phone (e.g. "David's A53") and, as seen on a real PC,
# belongs to YourPhoneAppProxyHost.exe, so the program is what identifies it.
PHONE_LINK_PROCESSES = ("yourphoneappproxyhost.exe", "phoneexperiencehost.exe", "yourphone.exe")
PHONE_LINK_MAIN_TITLES = ("phone link", "link to windows")


@dataclass(frozen=True)
class Window:
    handle: int
    title: str
    process: str = ""  # program file name, lower case, e.g. "phoneexperiencehost.exe"


def _api() -> tuple[Any, Any]:
    if sys.platform != "win32":
        raise OSError("window capture needs Windows")
    user32, gdi32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    H = wintypes.HANDLE
    sigs = {
        (user32, "EnumWindows"): ([ctypes.c_void_p, wintypes.LPARAM], wintypes.BOOL),
        (user32, "IsWindowVisible"): ([wintypes.HWND], wintypes.BOOL),
        (user32, "IsIconic"): ([wintypes.HWND], wintypes.BOOL),
        (user32, "GetWindowTextLengthW"): ([wintypes.HWND], ctypes.c_int),
        (user32, "GetWindowTextW"): ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32, "GetClientRect"): ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user32, "ClientToScreen"): ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        (user32, "GetDC"): ([wintypes.HWND], wintypes.HDC),
        (user32, "ReleaseDC"): ([wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (user32, "PrintWindow"): ([wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
        (gdi32, "CreateCompatibleDC"): ([wintypes.HDC], wintypes.HDC),
        (gdi32, "CreateCompatibleBitmap"): ([wintypes.HDC, ctypes.c_int, ctypes.c_int], H),
        (gdi32, "SelectObject"): ([wintypes.HDC, H], H),
        (gdi32, "DeleteObject"): ([H], wintypes.BOOL),
        (gdi32, "DeleteDC"): ([wintypes.HDC], wintypes.BOOL),
        (gdi32, "GetDIBits"): ([wintypes.HDC, H, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                               ctypes.POINTER(BITMAPINFO), wintypes.UINT], ctypes.c_int),
        (user32, "GetWindowThreadProcessId"): ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                                               wintypes.DWORD),
        (kernel32, "OpenProcess"): ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], H),
        (kernel32, "QueryFullProcessImageNameW"): ([H, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        (kernel32, "CloseHandle"): ([H], wintypes.BOOL),
    }
    global _kernel32
    _kernel32 = kernel32
    for (dll, name), (args, result) in sigs.items():
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, result
    return user32, gdi32


_kernel32: Any = None


def _process_name(user32: Any, hwnd: Any) -> str:
    """File name of the program that owns a window ("" if Windows won't say)."""
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(handle)


def list_windows() -> list[Window]:
    """Visible top-level windows that have a title, with the program they belong to."""
    user32, _ = _api()
    found: list[Window] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd: Any, _: Any) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                found.append(Window(int(hwnd), buf.value, _process_name(user32, hwnd)))
        return True

    callback = proc_type(each)  # keep a reference while Windows calls it
    user32.EnumWindows(ctypes.cast(callback, ctypes.c_void_p), 0)
    return found


def choose_window(windows: list[Window], titles: list[str]) -> Window | None:
    """The mirror window. Titles you named win (exact, case-insensitive, so a
    browser tab mentioning them is never picked); otherwise a Phone Link window
    that isn't Phone Link's main window: the mirror, titled after your phone."""
    for wanted in titles:
        for w in windows:
            if w.title.strip().lower() == wanted.strip().lower():
                return w
    phone_link = [w for w in windows if w.process in PHONE_LINK_PROCESSES]
    mirrors = [w for w in phone_link if w.title.strip().lower() not in PHONE_LINK_MAIN_TITLES]
    return (mirrors or [None])[0]


def find_window(titles: list[str]) -> Window | None:
    return choose_window(list_windows(), titles)


def client_box(window: Window) -> tuple[int, int, int, int] | None:
    """(left, top, width, height) of the window's content on screen, or None
    if it's minimised."""
    user32, _ = _api()
    if user32.IsIconic(window.handle):
        return None
    rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
    if not user32.GetClientRect(window.handle, ctypes.byref(rect)):
        return None
    user32.ClientToScreen(window.handle, ctypes.byref(origin))
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


def capture(window: Window) -> Any | None:
    """The window's content as an RGB numpy array, or None if Windows won't
    draw it for us (minimised, closed, or a black picture)."""
    import numpy as np

    box = client_box(window)
    if box is None or box[2] < 50 or box[3] < 50:
        return None
    width, height = box[2], box[3]
    user32, gdi32 = _api()
    window_dc = user32.GetDC(window.handle)
    if not window_dc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(window.handle, memory_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth, info.bmiHeader.biHeight = width, -height  # top-down rows
        info.bmiHeader.biPlanes, info.bmiHeader.biBitCount = 1, 32
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(info), 0) != height:
            return None
        image = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)[:, :, 2::-1].copy()
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(window.handle, window_dc)
    return image if image.max() > 16 else None  # all black: Windows couldn't draw it
