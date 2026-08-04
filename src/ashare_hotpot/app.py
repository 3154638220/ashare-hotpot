from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QStyleFactory

from .config import APP_NAME, APP_VERSION, AppSettings
from .service import RefreshService
from .storage import Storage
from .ui import MainWindow


def configure_logging(settings: AppSettings) -> None:
    settings.ensure_directories()
    handler = RotatingFileHandler(
        settings.log_dir / "ashare-hotpot.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> int:
    settings = AppSettings()
    configure_logging(settings)
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(APP_VERSION)
    application.setOrganizationName("AshareHotPot")
    application.setStyle(QStyleFactory.create("Fusion"))
    application.setFont(QFont("Microsoft YaHei UI", 10))
    application.setAttribute(Qt.AA_DontShowIconsInMenus, False)

    storage = Storage(settings.database_path)
    service = RefreshService(settings, storage)
    window = MainWindow(settings, storage, service)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
