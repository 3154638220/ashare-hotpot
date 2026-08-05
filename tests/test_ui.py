from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.models import (
    NewsEvent,
    OfficialPopularitySnapshot,
    ParsedArticle,
    PopularityRankRow,
    RankingRow,
    Snapshot,
    SourceCoverage,
    StockMention,
)
from ashare_hotpot.service import RefreshService
from ashare_hotpot.storage import Storage
from ashare_hotpot.ui import ArticleDetailDialog, MainWindow, RankingTableModel


def test_main_window_displays_snapshot_and_filters(qtbot, tmp_path) -> None:
    settings = AppSettings(app_root=tmp_path)
    storage = Storage(settings.database_path)
    window = MainWindow(settings, storage, RefreshService(settings, storage))
    qtbot.addWidget(window)
    assert window.content_stack.currentWidget() is window.empty_state
    assert window.ths_card.value_label.text() == "—"
    assert window.pop_card.value_label.text() == "—"
    assert window.window_hours_input.value() == 24
    header = window.table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.Interactive
    assert header.sectionResizeMode(1) == QHeaderView.Stretch
    assert header.sectionResizeMode(5) == QHeaderView.Interactive
    window.window_hours_input.setValue(12)
    assert settings.window_hours == 12
    assert "12 小时" in window.subtitle_label.text()
    assert "仅影响同花顺新闻榜" in window.subtitle_label.text()
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    ping_an = StockMention("000001", "平安银行")
    article = ParsedArticle(
        "1",
        "https://example.com/ping-an",
        "平安银行公布半年报",
        "",
        now,
        "companynews",
        "公司资讯",
        "同花顺财经",
        (ping_an,),
    )
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
            RankingRow(1, "000001", "平安银行", 3, 4, now, ("event-1",), ("银行",)),
            RankingRow(2, "600519", "贵州茅台", 2, 2, now - timedelta(hours=1), ("event-2",), ("白酒",)),
        ],
        events=[NewsEvent("event-1", article.title, now, (ping_an,), [article])],
        stats={"list_items": 10, "unique_urls": 9, "filtered": 1, "failed": 0, "unmapped": 1, "events": 5},
        popularity=OfficialPopularitySnapshot(
            available=True,
            is_stale=False,
            success_at=now,
            error=None,
            popularity=[
                PopularityRankRow(1, "000001", "平安银行", None, 11.25, 1.5, "https://guba.eastmoney.com/rank/stock?code=000001"),
                PopularityRankRow(2, "600519", "贵州茅台", None, 1600.0, 2.0, "https://guba.eastmoney.com/rank/stock?code=600519"),
            ],
            surging=[
                PopularityRankRow(3, "600519", "贵州茅台", 5, 1600.0, 2.0, "https://guba.eastmoney.com/rank/stock?code=600519"),
            ],
        ),
    )
    window.set_snapshot(snapshot)
    assert window.table_model.rowCount() == 2
    assert window.content_stack.currentWidget() is window.table
    assert window.ths_card.value_label.text() == "2 只"
    assert window.pop_card.value_label.text() == "2 只"
    assert "有效事件 5" in window.stats_label.text()
    assert window.heat_bar_delegate.maximum == 3

    window.search_input.setText("600519")
    assert window.proxy_model.rowCount() == 1
    assert window.heat_bar_delegate.maximum == 2
    window.search_input.clear()
    assert window.proxy_model.rowCount() == 2
    assert window.heat_bar_delegate.maximum == 3
    assert set(window.industry_filter._checkboxes) == {"白酒", "银行"}
    window.industry_filter._checkboxes["白酒"].setChecked(True)
    assert window.proxy_model.rowCount() == 1
    assert window.proxy_model.index(0, RankingTableModel.STOCK_NAME_COLUMN).data() == "贵州茅台"
    window.industry_filter._checkboxes["银行"].setChecked(True)
    assert window.proxy_model.rowCount() == 2
    assert window.industry_filter.text() == "已选 2 个行业"
    window.industry_filter.set_selected_tags(set())
    assert window.proxy_model.rowCount() == 2

    window.search_input.setText("000001")
    window._select_source("pop")
    assert window.selected_source == "pop"
    assert window.table_model.source_key == "pop"
    assert window.table_model.headerData(3, Qt.Horizontal) == "现价"
    assert window.table_model.headerData(4, Qt.Horizontal) == "涨跌幅"
    assert not window.rank_toggle_pop.isHidden()
    assert window.rank_toggle_pop.isChecked()
    assert window.table_model.rowCount() == 2
    assert window.proxy_model.rowCount() == 1
    assert "官方榜单" in window.stats_label.text()
    assert "数据截至" in window.stats_label.text()
    assert window.heat_bar_delegate.maximum == 1

    window.search_input.clear()
    window._select_source("surge")
    assert window.selected_source == "surge"
    assert window.table_model.headerData(3, Qt.Horizontal) == "较昨日变动"
    assert window.table_model.headerData(4, Qt.Horizontal) == "现价"
    assert window.table_model.headerData(5, Qt.Horizontal) == "涨跌幅"
    assert window.rank_toggle_surge.isChecked()
    assert window.proxy_model.rowCount() == 1
    assert window.table_model.data(window.table_model.index(0, 3), Qt.DisplayRole) == "↑ 5"

    window._select_source("ths")
    assert window.selected_source == "ths"
    assert window.rank_toggle_pop.isHidden()


def test_main_window_restores_controls_after_refresh(qtbot, tmp_path) -> None:
    settings = AppSettings(app_root=tmp_path)
    storage = Storage(settings.database_path)
    window = MainWindow(settings, storage, RefreshService(settings, storage))
    qtbot.addWidget(window)

    window.refresh_button.setEnabled(False)
    window.empty_refresh_button.setEnabled(False)
    window.cancel_button.setEnabled(True)
    window.search_input.setEnabled(False)
    window.industry_filter.setEnabled(False)
    window.window_hours_input.setEnabled(False)
    window.progress_bar.show()
    assert not window.progress_bar.isHidden()

    window._thread_finished()

    assert window.refresh_button.isEnabled()
    assert window.empty_refresh_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.search_input.isEnabled()
    assert window.industry_filter.isEnabled()
    assert window.window_hours_input.isEnabled()
    assert window.progress_bar.isHidden()


def test_stock_name_click_shows_effective_article_titles_and_links(qtbot, tmp_path, monkeypatch) -> None:
    settings = AppSettings(app_root=tmp_path)
    storage = Storage(settings.database_path)
    window = MainWindow(settings, storage, RefreshService(settings, storage))
    qtbot.addWidget(window)
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    stock = StockMention("000001", "平安银行")
    newest_article = ParsedArticle(
        "2",
        "https://example.com/newest",
        "平安银行最新公告",
        "",
        now,
        "companynews",
        "公司资讯",
        "同花顺财经",
        (stock,),
    )
    older_article = ParsedArticle(
        "1",
        "https://example.com/older",
        "平安银行此前公告",
        "",
        now - timedelta(hours=1),
        "companynews",
        "公司资讯",
        "同花顺财经",
        (stock,),
    )
    events = [
        NewsEvent("event-new", newest_article.title, now, (stock,), [newest_article]),
        NewsEvent("event-old", older_article.title, older_article.published_at, (stock,), [older_article]),
    ]
    ranking = RankingRow(1, stock.code, stock.name, 2, 2, now, ("event-new", "event-old"))
    snapshot = Snapshot(
        snapshot_id=1,
        window_start=now - timedelta(hours=24),
        window_end=now,
        created_at=now,
        partial=False,
        coverages=[],
        rankings=[ranking],
        events=events,
        stats={},
    )
    window.set_snapshot(snapshot)

    dialog = ArticleDetailDialog(ranking, events)
    qtbot.addWidget(dialog)
    assert dialog.article_tree.topLevelItemCount() == ranking.event_count
    assert dialog.article_tree.topLevelItem(0).text(0) == newest_article.title
    assert dialog.article_tree.topLevelItem(0).text(1) == newest_article.url
    assert dialog.article_tree.topLevelItem(1).text(0) == older_article.title
    assert dialog.article_tree.topLevelItem(1).text(1) == older_article.url

    shown_rows = []

    class FakeDialog:
        def __init__(self, row, _events, _parent) -> None:
            shown_rows.append(row)

        def exec(self) -> None:
            return None

    monkeypatch.setattr("ashare_hotpot.ui.ArticleDetailDialog", FakeDialog)
    name_index = window.proxy_model.index(0, RankingTableModel.STOCK_NAME_COLUMN)
    other_index = window.proxy_model.index(0, 0)
    window.table.clicked.emit(other_index)
    assert shown_rows == []
    window.table.clicked.emit(name_index)
    assert shown_rows == [ranking]
    assert window.table_model.data(window.table_model.index(0, RankingTableModel.STOCK_NAME_COLUMN), Qt.ForegroundRole)


def test_popularity_stock_name_click_opens_official_page(qtbot, tmp_path, monkeypatch) -> None:
    settings = AppSettings(app_root=tmp_path)
    storage = Storage(settings.database_path)
    window = MainWindow(settings, storage, RefreshService(settings, storage))
    qtbot.addWidget(window)
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    pop_row = PopularityRankRow(
        1,
        "000001",
        "平安银行",
        None,
        11.25,
        1.5,
        "https://guba.eastmoney.com/rank/stock?code=000001",
    )
    snapshot = Snapshot(
        snapshot_id=1,
        window_start=now - timedelta(hours=24),
        window_end=now,
        created_at=now,
        partial=False,
        coverages=[],
        rankings=[],
        events=[],
        stats={},
        popularity=OfficialPopularitySnapshot(
            available=True,
            is_stale=False,
            success_at=now,
            error=None,
            popularity=[pop_row],
            surging=[],
        ),
    )
    window.set_snapshot(snapshot)
    window._select_source("pop")

    opened: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.ui.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    name_index = window.proxy_model.index(0, RankingTableModel.STOCK_NAME_COLUMN)
    other_index = window.proxy_model.index(0, 0)
    window.table.clicked.emit(other_index)
    assert opened == []
    window.table.clicked.emit(name_index)
    assert opened == ["https://guba.eastmoney.com/rank/stock?code=000001"]


def test_popularity_stale_shows_expiry_and_failure_reason(qtbot, tmp_path) -> None:
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
        partial=False,
        coverages=[],
        rankings=[],
        events=[],
        stats={},
        popularity=OfficialPopularitySnapshot(
            available=True,
            is_stale=True,
            success_at=now - timedelta(hours=1),
            error="身份核实页",
            popularity=[
                PopularityRankRow(1, "000001", "平安银行", None, 11.25, 1.5, "https://guba.eastmoney.com/rank/stock?code=000001")
            ],
            surging=[],
        ),
    )
    window.set_snapshot(snapshot)
    window._select_source("pop")

    assert "已过期" in window.ranking_caption.text()
    assert "数据截至" in window.stats_label.text()
    assert "身份核实页" in window.stats_label.text()
    assert window.pop_card.value_label.text() == "1 只"
    assert "已过期" in window.pop_card.detail_label.text()
