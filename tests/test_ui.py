from __future__ import annotations

from datetime import datetime, timedelta

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.models import RankingRow, Snapshot, SourceCoverage
from ashare_hotpot.service import RefreshService
from ashare_hotpot.storage import Storage
from ashare_hotpot.ui import MainWindow


def test_main_window_displays_snapshot_and_filters(qtbot, tmp_path) -> None:
    settings = AppSettings(app_root=tmp_path)
    storage = Storage(settings.database_path)
    window = MainWindow(settings, storage, RefreshService(settings, storage))
    qtbot.addWidget(window)
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    snapshot = Snapshot(
        snapshot_id=1,
        window_start=now - timedelta(hours=24),
        window_end=now,
        created_at=now,
        partial=True,
        coverages=[
            SourceCoverage(
                "companynews",
                "公司资讯",
                20,
                500,
                now - timedelta(hours=7),
                now,
                False,
            )
        ],
        rankings=[
            RankingRow(1, "000001", "平安银行", 3, 4, now, ("event-1",)),
            RankingRow(2, "600519", "贵州茅台", 2, 2, now - timedelta(hours=1), ("event-2",)),
        ],
        events=[],
        stats={"list_items": 10, "unique_urls": 9, "filtered": 1, "failed": 0, "unmapped": 1, "events": 5},
    )
    window.set_snapshot(snapshot)
    assert window.table_model.rowCount() == 2
    assert "数据不完整" in window.coverage_label.text()
    assert "有效事件 5" in window.stats_label.text()

    window.search_input.setText("600519")
    assert window.proxy_model.rowCount() == 1
    window.search_input.clear()
    assert window.proxy_model.rowCount() == 2
