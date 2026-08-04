from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot

from .service import RefreshService
from .sources import RefreshCancelled


class RefreshWorker(QObject):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, service: RefreshService) -> None:
        super().__init__()
        self._service = service
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            snapshot = self._service.refresh(
                progress=lambda value, message: self.progress.emit(value, message),
                cancel_event=self._cancel_event,
            )
        except RefreshCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(snapshot)

