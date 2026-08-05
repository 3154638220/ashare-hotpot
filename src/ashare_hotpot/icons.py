from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon


ASSET_DIR = Path(__file__).resolve().parent / "assets" / "icons"


def icon(name: str) -> QIcon:
    return QIcon(str(ASSET_DIR / f"{name}.svg"))


def app_icon() -> QIcon:
    ico_path = ASSET_DIR / "app.ico"
    return QIcon(str(ico_path if ico_path.exists() else ASSET_DIR / "app.svg"))
