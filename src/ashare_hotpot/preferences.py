from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings


class UiPreferences:
    """Small, typed wrapper around the app-local Qt settings file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.settings = QSettings(str(path), QSettings.IniFormat)

    def sync(self) -> None:
        self.settings.sync()

    def window_geometry(self) -> QByteArray:
        value = self.settings.value("window/geometry", QByteArray())
        return value if isinstance(value, QByteArray) else QByteArray()

    def set_window_geometry(self, value: QByteArray) -> None:
        self.settings.setValue("window/geometry", value)

    def splitter_state(self) -> QByteArray:
        value = self.settings.value("window/detail_splitter", QByteArray())
        return value if isinstance(value, QByteArray) else QByteArray()

    def set_splitter_state(self, value: QByteArray) -> None:
        self.settings.setValue("window/detail_splitter", value)

    def header_state(self, source_key: str) -> QByteArray:
        value = self.settings.value(f"table/{source_key}/header", QByteArray())
        return value if isinstance(value, QByteArray) else QByteArray()

    def set_header_state(self, source_key: str, value: QByteArray) -> None:
        self.settings.setValue(f"table/{source_key}/header", value)

    def sort(self, source_key: str) -> tuple[int, int]:
        column = int(self.settings.value(f"table/{source_key}/sort_column", 0))
        order = int(self.settings.value(f"table/{source_key}/sort_order", 0))
        return column, order

    def set_sort(self, source_key: str, column: int, order: int) -> None:
        self.settings.setValue(f"table/{source_key}/sort_column", column)
        self.settings.setValue(f"table/{source_key}/sort_order", order)

    @property
    def last_source(self) -> str:
        value = str(self.settings.value("view/last_source", "ths"))
        return value if value in {"ths", "pop", "surge"} else "ths"

    @last_source.setter
    def last_source(self, value: str) -> None:
        if value in {"ths", "pop", "surge"}:
            self.settings.setValue("view/last_source", value)

    @property
    def detail_visible(self) -> bool:
        return self._bool("view/detail_visible", True)

    @detail_visible.setter
    def detail_visible(self, value: bool) -> None:
        self.settings.setValue("view/detail_visible", value)

    @property
    def density(self) -> str:
        value = str(self.settings.value("view/density", "compact"))
        return value if value in {"compact", "comfortable"} else "compact"

    @density.setter
    def density(self, value: str) -> None:
        if value in {"compact", "comfortable"}:
            self.settings.setValue("view/density", value)

    @property
    def window_hours(self) -> int:
        return max(1, min(168, int(self.settings.value("refresh/window_hours", 24))))

    @window_hours.setter
    def window_hours(self, value: int) -> None:
        self.settings.setValue("refresh/window_hours", max(1, min(168, value)))

    @property
    def auto_refresh(self) -> bool:
        return self._bool("refresh/auto_on_start", False)

    @auto_refresh.setter
    def auto_refresh(self, value: bool) -> None:
        self.settings.setValue("refresh/auto_on_start", value)

    @property
    def retention_days(self) -> int:
        return max(1, min(30, int(self.settings.value("data/retention_days", 7))))

    @retention_days.setter
    def retention_days(self, value: int) -> None:
        self.settings.setValue("data/retention_days", max(1, min(30, value)))

    def reset_table_layouts(self) -> None:
        self.settings.remove("table")

    def _bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
