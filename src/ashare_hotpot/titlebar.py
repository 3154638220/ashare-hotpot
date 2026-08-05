"""Native Windows title-bar theming (dark caption matching the app theme).

The Qt stylesheet cannot paint the OS-owned caption bar, so on Windows we use
the Desktop Window Manager (DWM) attributes directly: immersive dark mode is
enabled (Windows 10 1809+), and on Windows 11 the caption is additionally
tinted to the app's surface color so the bar blends seamlessly with the
toolbar underneath -- the same trick professional dark apps rely on.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget

from .theme import COLOR_BORDER, COLOR_SURFACE

_IS_WINDOWS = sys.platform == "win32"

# DWM window attributes (dwmapi.h)
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20  # Windows 10 2004 / 11
_DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19  # Windows 10 1809-1903
_DWMWA_BORDER_COLOR = 34  # Windows 11+
_DWMWA_CAPTION_COLOR = 35  # Windows 11+
_DWMWA_TEXT_COLOR = 36  # Windows 11+

_dwmapi = None
if _IS_WINDOWS:
    try:
        _dwmapi = ctypes.WinDLL("dwmapi")
        _dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        _dwmapi.DwmSetWindowAttribute.restype = ctypes.HRESULT
    except (OSError, AttributeError):  # pragma: no cover - unusual Windows setup
        _dwmapi = None


def _colorref_from_hex(hex_color: str) -> int:
    """Convert an '#RRGGBB' color to a Windows COLORREF (0x00BBGGRR)."""
    value = hex_color.lstrip("#")
    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    return (blue << 16) | (green << 8) | red


def _dwm_set_attribute(hwnd: int, attribute: int, value: int) -> bool:
    if _dwmapi is None:
        return False
    try:
        result = _dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            attribute,
            ctypes.cast(ctypes.byref(ctypes.c_int(value)), ctypes.c_void_p),
            ctypes.sizeof(ctypes.c_int),
        )
    except (OSError, ValueError, ctypes.ArgumentError):  # pragma: no cover
        return False
    return result == 0


def apply_dark_title_bar(window: QWidget) -> None:
    """Tint the native Windows title bar to match the dark app theme.

    Immersive dark mode turns the caption (including the minimize/maximize/
    close buttons) dark; on Windows 11 the caption is additionally tinted to
    the app's surface color and the outer window border to the app's border
    color, so the title bar and frame look continuous with the UI. Unsupported
    attributes and non-Windows platforms are silently ignored.
    """
    if not _IS_WINDOWS or not window.isWindow():
        return
    try:
        hwnd = int(window.winId())
    except (TypeError, ValueError):  # pragma: no cover
        return
    if not _dwm_set_attribute(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1):
        _dwm_set_attribute(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY, 1)
    _dwm_set_attribute(hwnd, _DWMWA_BORDER_COLOR, _colorref_from_hex(COLOR_BORDER))
    _dwm_set_attribute(hwnd, _DWMWA_CAPTION_COLOR, _colorref_from_hex(COLOR_SURFACE))
    _dwm_set_attribute(hwnd, _DWMWA_TEXT_COLOR, _colorref_from_hex("#FFFFFF"))


class TitleBarWindowFilter(QObject):
    """Apply the themed title bar to every native top-level window.

    Installed on QApplication so the main window and every dialog (settings,
    message boxes, info dialogs, ...) get the same dark, app-matching caption.
    """

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            if isinstance(watched, QWidget) and watched.isWindow():
                apply_dark_title_bar(watched)
        return super().eventFilter(watched, event)
