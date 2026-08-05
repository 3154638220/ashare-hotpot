from __future__ import annotations

from PySide6.QtCore import QByteArray

from ashare_hotpot.preferences import UiPreferences


def test_ui_preferences_are_typed_clamped_and_persistent(tmp_path) -> None:
    prefs = UiPreferences(tmp_path / "settings.ini")
    prefs.window_hours = 999
    prefs.retention_days = 0
    prefs.auto_refresh = True
    prefs.density = "comfortable"
    prefs.last_source = "surge"
    prefs.detail_visible = False
    prefs.set_header_state("ths", QByteArray(b"header-state"))
    prefs.set_sort("ths", 4, 1)
    prefs.sync()

    restored = UiPreferences(tmp_path / "settings.ini")
    assert restored.window_hours == 168
    assert restored.retention_days == 1
    assert restored.auto_refresh is True
    assert restored.density == "comfortable"
    assert restored.last_source == "surge"
    assert restored.detail_visible is False
    assert bytes(restored.header_state("ths")) == b"header-state"
    assert restored.sort("ths") == (4, 1)

    restored.reset_table_layouts()
    assert restored.header_state("ths").isEmpty()
    assert restored.sort("ths") == (0, 0)
