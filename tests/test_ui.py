from __future__ import annotations

import csv
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QHeaderView, QMessageBox

from ashare_hotpot.config import APP_NAME, APP_VERSION, PROJECT_URL, AppSettings, SHANGHAI_TZ, release_url
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
from ashare_hotpot.ui_components import AboutDialog
from ashare_hotpot.updates import ReleaseInfo, UpdateCheckResult


def make_snapshot() -> Snapshot:
    now = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)
    ping_an = StockMention("000001", "平安银行")
    moutai = StockMention("600519", "贵州茅台")
    ping_an_article = ParsedArticle(
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
    moutai_article = ParsedArticle(
        "2",
        "https://example.com/moutai",
        "贵州茅台发布经营数据",
        "",
        now - timedelta(hours=1),
        "companynews",
        "公司资讯",
        "同花顺财经",
        (moutai,),
    )
    return Snapshot(
        snapshot_id=1,
        window_start=now - timedelta(hours=24),
        window_end=now,
        created_at=now,
        partial=True,
        coverages=[
            SourceCoverage("companynews", "公司资讯", 20, 500, now - timedelta(hours=7), now, True)
        ],
        rankings=[
            RankingRow(1, "000001", "平安银行", 3, 4, now, ("event-1",), ("银行",)),
            RankingRow(2, "600519", "贵州茅台", 2, 2, now - timedelta(hours=1), ("event-2",), ("白酒",)),
        ],
        events=[
            NewsEvent("event-1", ping_an_article.title, now, (ping_an,), [ping_an_article]),
            NewsEvent("event-2", moutai_article.title, now - timedelta(hours=1), (moutai,), [moutai_article]),
        ],
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


def make_window(tmp_path, qtbot) -> MainWindow:
    settings = AppSettings(app_root=tmp_path)
    storage = Storage(settings.database_path)
    window = MainWindow(settings, storage, RefreshService(settings, storage))
    qtbot.addWidget(window)
    return window


def test_professional_shell_displays_snapshot_and_filters(qtbot, tmp_path) -> None:
    window = make_window(tmp_path, qtbot)
    assert window.windowTitle() == "A股热度"
    assert window.menuBar().isHidden()
    assert window.command_bar.height() == 52
    assert window.refresh_button.defaultAction() is window.refresh_action
    assert set(window.source_buttons) == {"ths", "pop", "surge"}
    assert window.content_stack.currentWidget() is window.empty_state
    assert window.window_hours_input.value() == 24

    window.window_hours_input.setValue(12)
    assert window.settings.window_hours == 12
    assert window.preferences.window_hours == 12

    snapshot = make_snapshot()
    window.set_snapshot(snapshot)
    assert window.table_model.rowCount() == 2
    assert window.content_stack.currentWidget() is window.table
    assert window.source_buttons["ths"].isChecked()
    assert window.kpi_chips[0].value.text() == "2 只"
    assert window.kpi_chips[1].value.text() == "5"
    assert window.freshness_label.text() == "部分覆盖"
    assert window.heat_bar_delegate.maximum == 3
    assert window.export_action.isEnabled()

    header = window.table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.Interactive
    assert header.sectionResizeMode(1) == QHeaderView.Stretch

    window.search_input.setText("600519")
    assert window.proxy_model.rowCount() == 1
    assert window.heat_bar_delegate.maximum == 2
    window.search_input.clear()
    assert window.proxy_model.rowCount() == 2

    assert set(window.industry_filter._checkboxes) == {"白酒", "银行"}
    window.industry_filter._checkboxes["白酒"].setChecked(True)
    assert window.proxy_model.rowCount() == 1
    window._select_source("pop")
    assert window.selected_source == "pop"
    assert window.industry_filter.isHidden()
    assert window.table_model.headerData(3, Qt.Horizontal) == "现价"
    assert window.proxy_model.rowCount() == 2
    name_alignment = window.table_model.data(
        window.table_model.index(0, RankingTableModel.STOCK_NAME_COLUMN), Qt.TextAlignmentRole
    )
    assert name_alignment == int(Qt.AlignVCenter | Qt.AlignLeft)
    assert window.table.columnWidth(4) == 150  # 涨跌幅
    window._select_source("surge")
    assert window.table_model.headerData(3, Qt.Horizontal) == "较昨日变动"
    assert window.table_model.data(window.table_model.index(0, 3), Qt.DisplayRole) == "↑ 5"
    assert window.table.columnWidth(5) == 150  # 涨跌幅

    window._select_source("ths")
    assert window.industry_filter.selected_tags == frozenset({"白酒"})
    assert window.proxy_model.rowCount() == 1

    name_font = window.table_model.data(
        window.table_model.index(0, RankingTableModel.STOCK_NAME_COLUMN), Qt.FontRole
    )
    assert name_font.bold()
    assert not name_font.underline()


def test_main_window_restores_refresh_controls(qtbot, tmp_path) -> None:
    window = make_window(tmp_path, qtbot)
    window.refresh_action.setEnabled(False)
    window.empty_refresh_button.setEnabled(False)
    window.window_hours_input.setEnabled(False)
    window.cancel_button.setEnabled(True)
    window.cancel_button.show()
    window.progress_bar.show()

    window._thread_finished()

    assert window.refresh_action.isEnabled()
    assert window.empty_refresh_button.isEnabled()
    assert window.window_hours_input.isEnabled()
    assert window.cancel_button.isHidden()
    assert window.progress_bar.isHidden()
    assert window.search_input.isEnabled()


def test_single_click_updates_detail_and_explicit_activation_opens_news(qtbot, tmp_path, monkeypatch) -> None:
    window = make_window(tmp_path, qtbot)
    snapshot = make_snapshot()
    window.set_snapshot(snapshot)

    ranking = snapshot.rankings[0]
    dialog = ArticleDetailDialog(ranking, snapshot.events)
    qtbot.addWidget(dialog)
    assert dialog.article_tree.topLevelItemCount() == 1
    assert dialog.article_tree.topLevelItem(0).text(0) == "平安银行公布半年报"

    opened: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    index = window.proxy_model.index(0, RankingTableModel.STOCK_NAME_COLUMN)
    window.table.clicked.emit(index)
    assert opened == []
    assert window.detail_panel.title_label.text() == "平安银行"
    assert window.detail_panel.article_tree.topLevelItemCount() == 1
    assert window.copy_row_action.isEnabled()

    window.activate_selected()
    assert opened == ["https://example.com/ping-an"]


def test_popularity_single_click_does_not_open_and_activation_does(qtbot, tmp_path, monkeypatch) -> None:
    window = make_window(tmp_path, qtbot)
    window.set_snapshot(make_snapshot())
    window._select_source("pop")

    opened: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    index = window.proxy_model.index(0, RankingTableModel.STOCK_NAME_COLUMN)
    window.table.clicked.emit(index)
    assert opened == []
    assert window.detail_panel.open_button.text() == "打开官方页"
    window.activate_selected()
    assert opened == ["https://guba.eastmoney.com/rank/stock?code=000001"]


def test_stale_popularity_is_explicit_and_keeps_rows(qtbot, tmp_path) -> None:
    window = make_window(tmp_path, qtbot)
    snapshot = make_snapshot()
    snapshot.popularity.is_stale = True
    snapshot.popularity.success_at = snapshot.created_at - timedelta(hours=1)
    snapshot.popularity.error = "身份核实页"
    window.set_snapshot(snapshot)
    window._select_source("pop")

    assert window.table_model.rowCount() == 2
    assert window.freshness_label.text() == "数据已过期"
    assert window.kpi_chips[2].value.text() == "已过期"
    assert "身份核实页" in window.quality_label.text()


def test_csv_export_uses_visible_filtered_order_and_copy_is_tab_separated(
    qtbot, tmp_path, monkeypatch
) -> None:
    window = make_window(tmp_path, qtbot)
    window.set_snapshot(make_snapshot())
    window.search_input.setText("600519")
    target = tmp_path / "filtered.csv"
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "CSV 文件 (*.csv)"),
    )

    window.export_current_results()

    with target.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == ["排名", "股票名称", "代码", "所属行业", "有效提及", "原始篇数", "最近提及"]
    assert len(rows) == 2
    assert rows[1][1:3] == ["贵州茅台", "600519"]

    index = window.proxy_model.index(0, 0)
    window.table.setCurrentIndex(index)
    window.copy_selected_row()
    copied = QApplication.clipboard().text()
    assert copied.split("\t")[1:3] == ["贵州茅台", "600519"]


def test_preferences_restore_source_density_and_table_layout(qtbot, tmp_path) -> None:
    first = make_window(tmp_path, qtbot)
    snapshot = make_snapshot()
    first.set_snapshot(snapshot)
    first._select_source("pop")
    first.preferences.density = "comfortable"
    first._apply_density()
    first.table.setColumnWidth(0, 77)
    first._save_table_state("pop")
    first.preferences.sync()

    second = make_window(tmp_path, qtbot)
    second.set_snapshot(snapshot)
    assert second.selected_source == "pop"
    assert second.table.verticalHeader().defaultSectionSize() == 44
    assert second.table.columnWidth(0) == 77


def test_saved_narrow_percent_column_is_widened_to_minimum(qtbot, tmp_path) -> None:
    first = make_window(tmp_path, qtbot)
    snapshot = make_snapshot()
    first.set_snapshot(snapshot)
    first._select_source("pop")
    first.table.setColumnWidth(4, 90)
    first._save_table_state("pop")
    first.preferences.sync()

    second = make_window(tmp_path, qtbot)
    second.set_snapshot(snapshot)
    assert second.selected_source == "pop"
    assert second.table.columnWidth(4) == 150


def test_about_dialog_shows_support_information_and_opens_links(qtbot, monkeypatch) -> None:
    dialog = AboutDialog(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        project_url=PROJECT_URL,
        release_url=release_url(),
    )
    qtbot.addWidget(dialog)
    assert dialog.objectName() == "aboutDialog"
    assert dialog.title_label.text() == APP_NAME
    assert dialog.version_label.text() == f"版本 {APP_VERSION}"
    assert "公开信息整理" in dialog.subtitle_label.text()
    assert dialog.styleSheet()

    opened: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.ui_components.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    dialog.project_button.click()
    dialog.release_button.click()
    assert opened == [PROJECT_URL, release_url()]


def test_about_dialog_requests_existing_diagnostics_copy(qtbot) -> None:
    dialog = AboutDialog(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        project_url=PROJECT_URL,
        release_url=release_url(),
    )
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.diagnostics_requested):
        dialog.diagnostics_button.click()


def test_about_dialog_check_for_updates_finds_new_version(qtbot, monkeypatch) -> None:
    class FakeWorker(QObject):
        finished = Signal(object)

        def __init__(self, project_url: str, current_version: str, *, timeout: float = 10.0) -> None:
            super().__init__()
            self.project_url = project_url
            self.current_version = current_version
            self.timeout = timeout

        def start(self) -> None:
            self.finished.emit(
                UpdateCheckResult(
                    latest=ReleaseInfo(
                        tag_name="v9.9.9",
                        html_url="https://github.com/3154638220/ashare-hotpot/releases/tag/v9.9.9",
                    )
                )
            )

    monkeypatch.setattr("ashare_hotpot.ui_components.UpdateCheckWorker", FakeWorker)
    opened: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.ui_components.QMessageBox.question",
        staticmethod(lambda *_args, **_kwargs: QMessageBox.Yes),
    )
    monkeypatch.setattr(
        "ashare_hotpot.ui_components.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )

    dialog = AboutDialog(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        project_url=PROJECT_URL,
        release_url=release_url(),
    )
    qtbot.addWidget(dialog)

    dialog.check_update_button.click()

    assert "发现新版本 v9.9.9" in dialog.update_status_label.text()
    assert dialog.check_update_button.isEnabled()
    assert opened == ["https://github.com/3154638220/ashare-hotpot/releases/tag/v9.9.9"]


def test_about_dialog_check_for_updates_already_latest(qtbot, monkeypatch) -> None:
    class FakeWorker(QObject):
        finished = Signal(object)

        def __init__(self, project_url: str, current_version: str, *, timeout: float = 10.0) -> None:
            super().__init__()

        def start(self) -> None:
            self.finished.emit(UpdateCheckResult(latest=None))

    monkeypatch.setattr("ashare_hotpot.ui_components.UpdateCheckWorker", FakeWorker)
    shown: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.ui_components.QMessageBox.information",
        staticmethod(lambda *_args, **_kwargs: shown.append("shown")),
    )

    dialog = AboutDialog(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        project_url=PROJECT_URL,
        release_url=release_url(),
    )
    qtbot.addWidget(dialog)

    dialog.check_update_button.click()

    assert dialog.update_status_label.text() == f"已是最新版本 {APP_VERSION}"
    assert shown == ["shown"]
    assert dialog.check_update_button.isEnabled()


def test_about_dialog_check_for_updates_shows_error_without_popup(qtbot, monkeypatch) -> None:
    class FakeWorker(QObject):
        finished = Signal(object)

        def __init__(self, project_url: str, current_version: str, *, timeout: float = 10.0) -> None:
            super().__init__()

        def start(self) -> None:
            self.finished.emit(UpdateCheckResult(latest=None, error="无法连接 GitHub 服务"))

    monkeypatch.setattr("ashare_hotpot.ui_components.UpdateCheckWorker", FakeWorker)
    shown: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.ui_components.QMessageBox.information",
        staticmethod(lambda *_args, **_kwargs: shown.append("shown")),
    )

    dialog = AboutDialog(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        project_url=PROJECT_URL,
        release_url=release_url(),
    )
    qtbot.addWidget(dialog)

    dialog.check_update_button.click()

    assert "检查更新失败" in dialog.update_status_label.text()
    assert shown == []
    assert dialog.check_update_button.isEnabled()


def test_show_about_connects_diagnostics_to_the_main_window(qtbot, tmp_path, monkeypatch) -> None:
    window = make_window(tmp_path, qtbot)
    dialog = AboutDialog(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        project_url=PROJECT_URL,
        release_url=release_url(),
        parent=window,
    )
    monkeypatch.setattr("ashare_hotpot.professional_window.AboutDialog", lambda **_kwargs: dialog)
    monkeypatch.setattr(dialog, "exec", lambda: dialog.diagnostics_requested.emit())

    window.show_about()

    assert QApplication.clipboard().text() == window.diagnostic_text()
    assert window.status_message.text() == "诊断信息已复制"
