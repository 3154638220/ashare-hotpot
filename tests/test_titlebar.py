from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ashare_hotpot.titlebar import _colorref_from_hex, apply_dark_title_bar


def test_colorref_from_hex() -> None:
    # Windows COLORREF packs as 0x00BBGGRR.
    assert _colorref_from_hex("#111821") == 0x00211811
    assert _colorref_from_hex("#FFFFFF") == 0x00FFFFFF
    assert _colorref_from_hex("#FF5D68") == 0x00685DFF
    assert _colorref_from_hex("#0B1017") == 0x0017100B


def test_apply_dark_title_bar_smoke(qtbot) -> None:
    # Must never raise, even offscreen / on non-Windows platforms.
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    apply_dark_title_bar(window)
