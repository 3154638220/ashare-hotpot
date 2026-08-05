from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot

from .service import RefreshService
from .sources import RefreshCancelled
from .updates import UpdateCheckResult, check_for_updates


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


class UpdateCheckWorker(QObject):
    """Runs the GitHub release check on a plain daemon thread.

    The signal is emitted from a non-Qt thread; Qt delivers it to the main
    thread with a queued connection, so no UI thread is blocked.
    """

    finished = Signal(object)

    def __init__(self, project_url: str, current_version: str, *, timeout: float = 10.0) -> None:
        super().__init__()
        self._project_url = project_url
        self._current_version = current_version
        self._timeout = timeout

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        result = check_for_updates(
            self._project_url,
            self._current_version,
            timeout=self._timeout,
        )
        self.finished.emit(result)
