from __future__ import annotations

from pathlib import Path

from ashare_hotpot.theme import DARK_STYLESHEET


def test_checkbox_indicator_is_styled() -> None:
    # The check indicator must be clearly visible on the dark theme: a styled
    # box with a border, a filled checked state, and a white checkmark image.
    assert "QCheckBox::indicator {" in DARK_STYLESHEET
    assert "QCheckBox::indicator:checked" in DARK_STYLESHEET
    assert "check.svg" in DARK_STYLESHEET
    check_svg = Path(__file__).resolve().parents[1] / "src" / "ashare_hotpot" / "assets" / "icons" / "check.svg"
    assert check_svg.is_file()
