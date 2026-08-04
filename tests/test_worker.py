from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread

from ashare_hotpot.worker import RefreshWorker


class FakeService:
    def refresh(self, *, progress, cancel_event: threading.Event):
        progress(50, "测试进度")
        return {"ok": True}


def test_worker_emits_progress_and_completion(qtbot) -> None:
    thread = QThread()
    worker = RefreshWorker(FakeService())
    progress_values: list[tuple[int, str]] = []
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.completed.connect(thread.quit)
    worker.progress.connect(lambda value, text: progress_values.append((value, text)))
    with qtbot.waitSignal(worker.completed, timeout=2000) as blocker:
        thread.start()
    assert progress_values == [(50, "测试进度")]
    assert blocker.args == [{"ok": True}]
    qtbot.waitUntil(lambda: not thread.isRunning(), timeout=2000)
    worker.deleteLater()
    thread.deleteLater()
