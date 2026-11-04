"""Windows-only DWM tweaks (title-bar colours, corner/border chrome).

Everything here is a silent no-op off Windows and swallows every failure -
these are cosmetic touches, never something that should be able to stop a
window from opening. Attribute numbers are from dwmapi.h:

    20  DWMWA_USE_IMMERSIVE_DARK_MODE  (19 on Windows 10 before 20H1)
    33  DWMWA_WINDOW_CORNER_PREFERENCE (Windows 11)
    34  DWMWA_BORDER_COLOR             (Windows 11)
    35  DWMWA_CAPTION_COLOR            (Windows 11)
    36  DWMWA_TEXT_COLOR               (Windows 11)

On Windows 10 only the dark/light title-bar switch takes effect; the colour
attributes are simply ignored there.
"""
from __future__ import annotations

import re
import sys
import weakref

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QDialog

from hdr2sdr.runtime.conversion import is_valid_qobject
from hdr2sdr.theme.palettes import THEMES, _theme_is_dark
from hdr2sdr.theme.theme_tokens import build_tokens, window_tokens

try:
    from shiboken6 import isValid as _shiboken_is_valid
except ImportError:  # pragma: no cover
    _shiboken_is_valid = None


def _is_live_qobject(obj) -> bool:
    if obj is None:
        return False
    if _shiboken_is_valid is not None:
        return bool(_shiboken_is_valid(obj))
    return is_valid_qobject(obj)

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWCP_DONOTROUND = 1
DWMWA_COLOR_NONE = 0xFFFFFFFE

_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")


def _is_windows() -> bool:
    return sys.platform == "win32"


def colorref(hex_color: str):
    """'#RRGGBB' -> Windows COLORREF (0x00BBGGRR), or None if not plain hex."""
    m = _HEX.match((hex_color or "").strip())
    if not m:
        return None
    n = int(m.group(1), 16)
    r, g, b = (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF
    return r | (g << 8) | (b << 16)


def _set_attr(hwnd: int, attr: int, value: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
        v = ctypes.c_uint(value & 0xFFFFFFFF)
        fn = ctypes.windll.dwmapi.DwmSetWindowAttribute
        fn.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        fn.restype = ctypes.c_long
        return fn(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0
    except Exception:
        return False


def _hwnd(widget):
    try:
        return int(widget.winId())
    except Exception:
        return 0


def theme_titlebar(widget, theme_name: str | dict) -> None:
    """Colour a normal framed top-level window's native title bar to match
    the app theme (dark/light switch + caption/text/border colours)."""
    if not _is_windows():
        return
    hwnd = _hwnd(widget)
    if not hwnd:
        return
    t = theme_name if isinstance(theme_name, dict) else build_tokens(theme_name=str(theme_name or "light"))
    dark = 1 if _theme_is_dark(t) else 0
    if not _set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, dark):
        _set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, dark)
    for attr, key in ((DWMWA_CAPTION_COLOR, "bg_card"), (DWMWA_TEXT_COLOR, "text"),
                      (DWMWA_BORDER_COLOR, "border")):
        ref = colorref(t.get(key, ""))
        if ref is not None:
            _set_attr(hwnd, attr, ref)


def no_native_chrome(widget) -> None:
    """For frameless, self-painted top-level windows (our dropdown popups):
    stop Windows 11 adding its own rounded-corner mask and 1px border on
    top of the shape the widget paints itself - the two outlines don't
    line up, which reads as a window drawn inside a window."""
    if not _is_windows():
        return
    hwnd = _hwnd(widget)
    if not hwnd:
        return
    _set_attr(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)
    _set_attr(hwnd, DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE)


class NativeChromeFilter(QObject):
    """App-wide event filter: whenever a framed QDialog/QMessageBox window is
    shown, theme its native title bar. Covers every stock dialog (including
    the static QMessageBox.information(...) calls) without touching each
    call site."""

    def __init__(self, win):
        super().__init__(win)
        self._win_ref = weakref.ref(win) if win is not None else None

    @property
    def _win(self):
        return self._win_ref() if self._win_ref is not None else None

    def eventFilter(self, obj, event):
        if not _is_live_qobject(self) or not _is_live_qobject(obj) or event is None:
            return False
        win = self._win
        if not _is_live_qobject(win):
            return False
        if (event.type() == QEvent.Type.Show and _is_windows()
                and isinstance(obj, QDialog) and obj.isWindow()
                and not (obj.windowFlags() & Qt.WindowType.FramelessWindowHint)):
            theme_titlebar(obj, window_tokens(win))
        return False
