from __future__ import annotations

import csv
from dataclasses import replace
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QHeaderView, QMessageBox

from ashare_hotpot.config import APP_NAME, APP_VERSION, PROJECT_URL, AppSettings, SHANGHAI_TZ, release_url
from ashare_hotpot.models import (
    DiscoveryCandidate,
    EventCluster,
    EventClaim,
    EventExtraction,
    EventSignal,
    EvidenceRef,
    InteractionCoverage,
    InteractionRankingRow,
    InteractionRecord,
    IndustryHeatRow,
    IndustryHeatSnapshot,
    NewsEvent,
    OfficialPopularitySnapshot,
    ParsedArticle,
    PopularityRankRow,
    RankingRow,
    Snapshot,
    SourceCoverage,
    SourceDocument,
    StockMention,
    SyncCursor,
)
from ashare_hotpot.service import RefreshService
from ashare_hotpot.storage import Storage
from ashare_hotpot.ui import RESEARCH_HEADERS, ArticleDetailDialog, MainWindow, RankingTableModel
from ashare_hotpot.ui_components import AboutDialog, SettingsDialog
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
        "独家公司互动",
        "证券日报网",
        (moutai,),
    )
    interaction_a = InteractionRecord(
        "irm:1001",
        "irm",
        "深交所互动易",
        "000001",
        "平安银行",
        "请问贵行分红政策未来会调整吗？",
        now - timedelta(hours=3),
        "http://irm.cninfo.com.cn/ircs/question/questionDetail?questionId=1001",
        "您好，公司分红政策稳定。",
        now - timedelta(hours=2),
        ("银行",),
    )
    interaction_b = InteractionRecord(
        "sse:2001",
        "sse",
        "上证e互动",
        "600519",
        "贵州茅台",
        "公司直销渠道占比是否会提升？",
        now - timedelta(hours=5),
        "https://sns.sseinfo.com/qadetail.do?weiboId=2001",
        "公司直销与经销体系协同发展，占比保持稳定。",
        now - timedelta(hours=2),
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
            RankingRow(
                1,
                "000001",
                "平安银行",
                3,
                4,
                now,
                ("event-1",),
                ("银行",),
                ("公司资讯",),
                ("同花顺",),
                ("新闻",),
            ),
            RankingRow(
                2,
                "600519",
                "贵州茅台",
                2,
                2,
                now - timedelta(hours=1),
                ("event-2",),
                ("白酒",),
                ("独家公司互动",),
                ("同花顺",),
                ("新闻",),
            ),
        ],
        events=[
            NewsEvent("event-1", ping_an_article.title, now, (ping_an,), [ping_an_article]),
            NewsEvent("event-2", moutai_article.title, now - timedelta(hours=1), (moutai,), [moutai_article]),
        ],
        stats={
            "list_items": 10,
            "unique_urls": 9,
            "filtered": 1,
            "failed": 0,
            "unmapped": 1,
            "events": 5,
            "interaction_records": 2,
            "interaction_filtered": 0,
            "interaction_usable": 2,
            "interaction_unique": 2,
            "interaction_ranked_stocks": 2,
            "interaction_sources_cached": 0,
        },
        interactions=[interaction_a, interaction_b],
        interaction_rankings=[
            InteractionRankingRow(
                1,
                "000001",
                "平安银行",
                1,
                1,
                now - timedelta(hours=3),
                ("irm:1001",),
                ("银行",),
                ("深交所互动易",),
            ),
            InteractionRankingRow(
                2,
                "600519",
                "贵州茅台",
                1,
                1,
                now - timedelta(hours=2),
                ("sse:2001",),
                ("白酒",),
                ("上证e互动",),
            ),
        ],
        interaction_coverages=[
            InteractionCoverage(
                "irm",
                "深交所互动易",
                2,
                1,
                now - timedelta(hours=3),
                now,
                True,
            ),
            InteractionCoverage(
                "sse",
                "上证e互动",
                2,
                1,
                now - timedelta(hours=5),
                now,
                True,
            ),
        ],
        popularity=OfficialPopularitySnapshot(
            available=True,
            is_stale=False,
            success_at=now,
            error=None,
            popularity=[
                PopularityRankRow(1, "000001", "平安银行", None, 11.25, 1.5, "https://guba.eastmoney.com/rank/stock?code=000001", "金融"),
                PopularityRankRow(2, "600519", "贵州茅台", None, 1600.0, 2.0, "https://guba.eastmoney.com/rank/stock?code=600519", "食品饮料"),
            ],
            surging=[
                PopularityRankRow(3, "600519", "贵州茅台", 5, 1600.0, 2.0, "https://guba.eastmoney.com/rank/stock?code=600519", "食品饮料"),
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
    assert window.navigation_bar.height() == 56
    assert window.refresh_button.defaultAction() is window.refresh_action
    assert set(window.source_buttons) == {"news", "interaction", "pop", "surge"}
    assert window.content_stack.currentWidget() is window.empty_state
    assert window.window_hours_input.value() == 24
    assert window.industry_filter.width() == 128
    assert window.search_input.width() == 180
    assert window.search_input.placeholderText() == "名称/代码"

    window.window_hours_input.setValue(12)
    assert window.settings.window_hours == 12
    assert window.preferences.window_hours == 12

    snapshot = make_snapshot()
    window.set_snapshot(snapshot)
    assert window.table_model.rowCount() == 2
    assert window.content_stack.currentWidget() is window.table
    assert window.source_buttons["news"].isChecked()
    assert window.kpi_chips[0].value.text() == "2 只"
    assert window.kpi_chips[1].value.text() == "5"
    assert window.freshness_label.text() == "部分覆盖"
    assert window.heat_bar_delegate.maximum == 3
    assert window.export_action.isEnabled()
    window.show()
    qtbot.waitUntil(lambda: window.search_input.leading_icon.isVisible())
    assert window.search_input.leading_icon.geometry().center().y() == window.search_input.rect().center().y()

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
    assert window.table_model.headerData(2, Qt.Horizontal) == "所属行业"
    assert window.table_model.data(window.table_model.index(0, 2), Qt.DisplayRole) == "金融"
    assert window.table_model.headerData(3, Qt.Horizontal) == "代码"
    assert window.proxy_model.rowCount() == 2
    name_alignment = window.table_model.data(
        window.table_model.index(0, RankingTableModel.STOCK_NAME_COLUMN), Qt.TextAlignmentRole
    )
    assert name_alignment == int(Qt.AlignVCenter | Qt.AlignLeft)
    assert window.table.columnWidth(5) == 150  # 涨跌幅
    window._select_source("surge")
    assert window.table_model.headerData(2, Qt.Horizontal) == "所属行业"
    assert window.table_model.headerData(4, Qt.Horizontal) == "较昨日变动"
    assert window.table_model.data(window.table_model.index(0, 4), Qt.DisplayRole) == "↑ 5"
    assert window.table.columnWidth(6) == 150  # 涨跌幅

    window._select_source("news")
    assert window.industry_filter.selected_tags == frozenset({"白酒"})
    assert window.proxy_model.rowCount() == 1

    assert set(window.content_type_filter._checkboxes) == {"新闻"}
    window.content_type_filter._checkboxes["新闻"].setChecked(True)
    assert window.proxy_model.rowCount() == 1  # 行业“白酒”筛选仍然生效
    window.content_type_filter._checkboxes["新闻"].setChecked(False)
    window._select_source("pop")
    assert window.content_type_filter.isHidden()
    window._select_source("news")
    assert window.content_type_filter.selected_tags == frozenset()
    assert window.proxy_model.rowCount() == 1
    window.clear_filters()
    assert window.proxy_model.rowCount() == 2

    name_font = window.table_model.data(
        window.table_model.index(0, RankingTableModel.STOCK_NAME_COLUMN), Qt.FontRole
    )
    assert name_font.bold()
    assert not name_font.underline()


def test_command_bars_keep_controls_visible_at_scaled_display_widths(qtbot, tmp_path) -> None:
    window = make_window(tmp_path, qtbot)

    # 1024 is the supported minimum. 1220 is the default window width;
    # 1280/1536 are common logical widths for 1920px displays at 150%/125%.
    for logical_width in (1024, 1220, 1280, 1536):
        window.resize(logical_width, 780)
        window.show()
        QApplication.processEvents()

        assert window.refresh_button.isVisible()
        assert window.more_button.isVisible()
        assert window.window_hours_input.isVisible()
        assert window.freshness_label.isVisible()
        assert all(button.isVisible() for button in window.source_buttons.values())
        assert all(button.isVisible() for button in window.research_buttons.values())


def test_navigation_is_grouped_and_vertically_aligned(qtbot, tmp_path) -> None:
    window = make_window(tmp_path, qtbot)
    window.show()
    QApplication.processEvents()

    groups = (
        window.source_navigation_group,
        window.industry_navigation_group,
        window.research_navigation_group,
    )
    assert [group.property("section") for group in groups] == [
        "original",
        "industry",
        "research",
    ]
    assert len({group.height() for group in groups}) == 1

    buttons = (
        *window.source_buttons.values(),
        window.industry_button,
        *window.research_buttons.values(),
    )
    assert len({button.height() for button in buttons}) == 1
    assert len({button.mapTo(window.navigation_bar, button.rect().center()).y() for button in buttons}) == 1

    group_for_button = {
        **{button: window.source_navigation_group for button in window.source_buttons.values()},
        window.industry_button: window.industry_navigation_group,
        **{button: window.research_navigation_group for button in window.research_buttons.values()},
    }
    for button, group in group_for_button.items():
        top_left = button.mapTo(group, button.rect().topLeft())
        bottom_right = button.mapTo(group, button.rect().bottomRight())
        assert top_left.y() >= 5
        assert group.height() - 1 - bottom_right.y() >= 5


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
    assert window.detail_panel.open_button.text() == "打开所选新闻"
    assert not window.detail_panel.official_button.isHidden()
    window.activate_selected()
    assert opened == ["https://example.com/ping-an"]
    window.detail_panel.official_button.click()
    assert opened[-1] == "https://guba.eastmoney.com/rank/stock?code=000001"


def test_popularity_export_and_copy_include_industry(qtbot, tmp_path, monkeypatch) -> None:
    window = make_window(tmp_path, qtbot)
    window.set_snapshot(make_snapshot())
    window._select_source("pop")
    target = tmp_path / "popularity.csv"
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "CSV 文件 (*.csv)"),
    )
    copied_text: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QApplication.clipboard",
        lambda: type("Clip", (), {"setText": lambda _self, value: copied_text.append(value)})(),
    )

    window.export_current_results()
    with target.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == ["排名", "股票名称", "所属行业", "代码", "现价", "涨跌幅"]
    assert rows[1] == ["1", "平安银行", "金融", "000001", "11.25", "+1.50%"]

    window.table.setCurrentIndex(window.proxy_model.index(0, 0))
    window.copy_selected_row()
    assert copied_text == ["\t".join(rows[1])]


def test_old_popularity_snapshot_uses_cached_industry(qtbot, tmp_path) -> None:
    window = make_window(tmp_path, qtbot)
    snapshot = make_snapshot()
    snapshot.popularity.popularity[0] = replace(
        snapshot.popularity.popularity[0], industry=None
    )
    window.storage.upsert_stock_industries({"000001": "金融"}, snapshot.created_at)

    window.set_snapshot(snapshot)
    window._select_source("pop")

    assert window.table_model.data(window.table_model.index(0, 2), Qt.DisplayRole) == "金融"


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


def test_industry_navigation_detail_and_export_share_columns(
    qtbot, tmp_path, monkeypatch
) -> None:
    window = make_window(tmp_path, qtbot)
    snapshot = make_snapshot()
    article = snapshot.events[0].articles[0]
    snapshot.industry_heat = IndustryHeatSnapshot(
        snapshot_at=snapshot.created_at,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        rows=[
            IndustryHeatRow(
                1, "金融", 75.0, 2, 100.0, 1, 50.0,
                mapping_status="complete", source_status="complete",
                article_urls=(article.url,),
                stock_codes=("000001",),
            ),
            IndustryHeatRow(
                2, "电子", 25.0, 1, 50.0, 0, 0.0,
                mapping_status="complete", source_status="complete",
            ),
        ],
        top100_total=3,
        top100_mapped=3,
        mapping_coverage=1.0,
        research_article_total=1,
        research_article_mapped=1,
        mapping_status="complete",
        source_status="complete",
        articles=[article],
    )
    window.set_snapshot(snapshot)
    window._select_source("industry")

    assert window.selected_source == "industry"
    assert [window.industry_table_model.headerData(i, Qt.Horizontal) for i in range(8)] == [
        "排名", "行业", "热度", "A", "A分位", "B", "B分位", "映射/来源状态"
    ]
    index = window.industry_table_model.index(0, 0)
    window.table.clicked.emit(index)
    assert window.industry_detail_panel.title_label.text() == "金融"
    assert window.industry_detail_panel.article_tree.topLevelItemCount() == 2
    stock_item = window.industry_detail_panel.article_tree.topLevelItem(0)
    assert "000001" in stock_item.text(0)
    assert stock_item.childCount() == 1
    assert "50%" in window.industry_detail_panel.summary_label.text()
    detail_layout = window.industry_detail_panel.layout()
    assert not hasattr(window.industry_detail_panel, "trend")
    assert detail_layout.stretch(detail_layout.indexOf(window.industry_detail_panel.article_tree)) == 1

    target = tmp_path / "industry.csv"
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "CSV 文件 (*.csv)"),
    )
    window.export_current_results()
    with target.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == ["排名", "行业", "热度", "A", "A分位", "B", "B分位", "映射/来源状态"]
    assert rows[1] == ["1", "金融", "75.00", "2", "100.00", "1", "50.00", "complete/complete"]
    copied_text: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QApplication.clipboard",
        lambda: type("Clip", (), {"setText": lambda _self, value: copied_text.append(value)})(),
    )
    window.copy_selected_row()
    assert copied_text[0].split("\t") == rows[1]


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
    assert rows[0] == [
        "排名",
        "股票名称",
        "代码",
        "所属行业",
        "有效事件",
        "原始篇数",
        "最近事件",
        "来源",
        "数据源",
        "内容类型",
    ]
    assert len(rows) == 2
    assert rows[1][1:3] == ["贵州茅台", "600519"]
    assert rows[1][8:10] == ["同花顺", "新闻"]

    copied_text: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QApplication.clipboard",
        lambda: type("Clip", (), {"setText": lambda _self, value: copied_text.append(value)})(),
    )
    index = window.proxy_model.index(0, 0)
    window.table.setCurrentIndex(index)
    window.copy_selected_row()
    assert copied_text[0].split("\t")[1:3] == ["贵州茅台", "600519"]


def test_interaction_tab_switching_filters_and_detail(qtbot, tmp_path, monkeypatch) -> None:
    window = make_window(tmp_path, qtbot)
    snapshot = make_snapshot()
    window.set_snapshot(snapshot)

    assert window.source_buttons["interaction"].isEnabled()
    window._select_source("interaction")
    assert window.selected_source == "interaction"
    assert window.table_model.rowCount() == 2
    assert window.table_model.headerData(4, Qt.Horizontal) == "有效提问"
    assert window.table_model.headerData(6, Qt.Horizontal) == "回复率"
    assert window.table_model.headerData(7, Qt.Horizontal) == "最近回复"
    assert window.kpi_chips[1].value.text() == "2"
    assert "官方问答代理指标" in window.view_subtitle.text()
    assert "只统计已回复提问" in window.view_subtitle.text()

    # 平台筛选
    assert set(window.platform_filter._checkboxes) == {"上证e互动", "深交所互动易"}
    assert not window.industry_filter.isHidden()
    window.platform_filter._checkboxes["上证e互动"].setChecked(True)
    assert window.proxy_model.rowCount() == 1
    assert window.table_model.row_at(window.proxy_model.mapToSource(window.proxy_model.index(0, 0)).row()).code == "600519"

    # 详情面板展示问题与回复
    index = window.proxy_model.index(0, RankingTableModel.STOCK_NAME_COLUMN)
    window.table.clicked.emit(index)
    assert window.detail_panel.title_label.text() == "贵州茅台"
    assert "有效提问" in window.detail_panel.summary_label.text()
    assert window.detail_panel.article_tree.topLevelItemCount() == 1
    assert window.detail_panel.article_tree.topLevelItem(0).text(2) == "上证e互动"
    assert "直销与经销" in window.detail_panel.article_tree.topLevelItem(0).text(1)
    assert window.detail_panel.minimumWidth() == 420
    assert window.detail_panel.article_tree.header().sectionResizeMode(1) == QHeaderView.Stretch
    window.resize(1280, 780)
    window.show()
    qtbot.waitUntil(lambda: window.detail_panel.width() >= 420)
    assert not window.detail_panel.article_tree.horizontalScrollBar().isVisible()

    opened: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    window.activate_selected()
    assert opened == ["https://sns.sseinfo.com/qadetail.do?weiboId=2001"]

    window.clear_filters()
    assert window.proxy_model.rowCount() == 2


def test_interaction_csv_export_and_row_copy(qtbot, tmp_path, monkeypatch) -> None:
    window = make_window(tmp_path, qtbot)
    window.set_snapshot(make_snapshot())
    window._select_source("interaction")
    copied_text: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QApplication.clipboard",
        lambda: type("Clip", (), {"setText": lambda _self, value: copied_text.append(value)})(),
    )
    target = tmp_path / "interaction.csv"
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "CSV 文件 (*.csv)"),
    )

    window.export_current_results()

    with target.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == ["排名", "股票名称", "代码", "所属行业", "有效提问", "已回复", "回复率", "最近回复", "平台"]
    assert len(rows) == 3
    assert rows[1][1:3] == ["平安银行", "000001"]
    assert rows[1][4:7] == ["1", "1", "100.0%"]

    index = window.proxy_model.index(0, 0)
    window.table.setCurrentIndex(index)
    window.copy_selected_row()
    assert copied_text[0].split("\t")[1:3] == ["平安银行", "000001"]


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
    first.table.setColumnWidth(5, 90)
    first._save_table_state("pop")
    first.preferences.sync()

    second = make_window(tmp_path, qtbot)
    second.set_snapshot(snapshot)
    assert second.selected_source == "pop"
    assert second.table.columnWidth(5) == 150


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
    copied_text: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QApplication.clipboard",
        lambda: type("Clip", (), {"setText": lambda _self, value: copied_text.append(value)})(),
    )
    monkeypatch.setattr("ashare_hotpot.professional_window.AboutDialog", lambda **_kwargs: dialog)
    monkeypatch.setattr(dialog, "exec", lambda: dialog.diagnostics_requested.emit())

    window.show_about()

    assert copied_text == [window.diagnostic_text()]
    assert window.status_message.text() == "诊断信息已复制"


# ---------------------------------------------------------------------------
# Milestone 5: research views, filters, export, AI settings and diagnostics
# ---------------------------------------------------------------------------


def _seed_article(storage: Storage, code: str = "000001", name: str = "平安银行") -> None:
    now = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)
    storage.upsert_article(
        ParsedArticle(
            f"seq-{code}",
            f"https://example.test/{code}",
            f"{name}公开公告",
            "",
            now,
            "companynews",
            "公司资讯",
            "同花顺",
            (StockMention(code, name),),
        ),
        now,
    )


def _seed_signal(
    storage: Storage,
    *,
    board: str = "confirmed_positive",
    extractor_kind: str = "rules",
    provisional: bool = False,
    event_id: str = "event-1",
    stock_code: str = "000001",
    event_type: str = "major_contract",
    stock_name: str = "平安银行",
) -> None:
    now = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)
    document = SourceDocument(
        f"doc-{event_id}",
        "cninfo",
        "巨潮资讯",
        "announcement",
        f"https://cninfo.example.test/{event_id}",
        None,
        "公司签订重大合同公告",
        now - timedelta(hours=2),
        (stock_code,),
        "公司近日与客户签订重大合同，合同金额1.2亿元，占营业收入的10%。",
        f"hash-{event_id}",
        "parsed",
        None,
        None,
    )
    storage.upsert_source_document(document, now)
    storage.upsert_event_cluster(
        EventCluster(
            event_id,
            (stock_code,),
            "公司签订重大合同公告",
            now - timedelta(hours=3),
            now - timedelta(hours=2),
            f"doc-{event_id}",
            [f"doc-{event_id}"],
            None,
        )
    )
    storage.link_event_document(event_id, f"doc-{event_id}")
    storage.upsert_event_extraction(
        EventExtraction(
            event_id,
            stock_code,
            event_type,
            "positive",
            "新增合同预计增厚营业收入",
            (
                {
                    "name": "合同金额",
                    "value": 1.2,
                    "unit": "亿元",
                    "comparison_basis": "营业收入",
                    "comparison_ratio": 0.1,
                    "evidence_id": f"ev-{event_id}",
                },
            ),
            "signed",
            0.9,
            100.0,
            50.0,
            2,
            (),
            (f"ev-{event_id}",),
            False,
            extractor_kind,
            "rules-v1",
        ),
        now,
    )
    storage.upsert_evidence_ref(
        EvidenceRef(
            f"ev-{event_id}",
            f"doc-{event_id}",
            0,
            40,
            "合同金额1.2亿元，占营业收入的10%。",
            f"https://cninfo.example.test/{event_id}",
        )
    )
    storage.upsert_event_signal(
        EventSignal(
            event_id,
            stock_code,
            board,
            80.0,
            0.9,
            2,
            0.9,
            50.0,
            100.0,
            1.0,
            0.0,
            provisional,
        ),
        created_at=now,
    )


def _make_research_window(tmp_path, qtbot, *, seed: bool = True):
    settings = AppSettings(app_root=tmp_path)
    storage = Storage(settings.database_path)
    storage.initialize()
    if seed:
        _seed_article(storage)
        _seed_signal(storage)
        _seed_article(storage, code="600519", name="贵州茅台")
        _seed_signal(
            storage,
            event_id="event-2",
            stock_code="600519",
            event_type="approval",
            stock_name="贵州茅台",
        )
    window = MainWindow(settings, storage, RefreshService(settings, storage))
    qtbot.addWidget(window)
    return window


def test_research_navigation_two_groups_and_empty_state(qtbot, tmp_path) -> None:
    window = _make_research_window(tmp_path, qtbot, seed=False)
    assert set(window.research_buttons) == {"confirm", "catalyst", "discovery"}
    assert window.research_buttons["confirm"].text() == "确定性利好"
    assert window.research_buttons["catalyst"].text() == "潜在催化"
    # The two visual groups live in the same dedicated navigation row.
    assert window.research_buttons["confirm"] is not window.source_buttons["news"]

    window._select_source("confirm")
    assert window.selected_source == "confirm"
    assert window._table_mode == "research"
    assert window.content_stack.currentWidget() is window.empty_state
    assert "暂无数据" in window.empty_title.text()

    window._select_source("news")
    assert window._table_mode == "legacy"


def test_research_view_renders_seeded_rows_and_filters(qtbot, tmp_path) -> None:
    window = _make_research_window(tmp_path, qtbot)
    window._select_source("confirm")
    assert window.research_proxy.rowCount() == 2
    assert window.research_table_model.data(
        window.research_table_model.index(0, 1)
    ) == "平安银行"
    assert window.research_table_model.data(
        window.research_table_model.index(0, 3)
    ) == "重大订单"
    # Quality column honestly shows cold start when no sync state exists.
    assert window.research_table_model.data(
        window.research_table_model.index(0, 10)
    ) in {"冷启动", "部分覆盖"}

    window.event_type_filter.set_selected_tags({"获批认证"})
    assert window.research_proxy.rowCount() == 1
    window.event_type_filter.set_selected_tags({"重大订单"})
    assert window.research_proxy.rowCount() == 1
    window.search_input.setText("600519")
    assert window.research_proxy.rowCount() == 0
    window.clear_filters()
    assert window.research_proxy.rowCount() == 2


def test_research_search_filters_by_code_and_name(qtbot, tmp_path) -> None:
    window = _make_research_window(tmp_path, qtbot)
    window._select_source("confirm")

    window.search_input.setText("600519")
    assert window.research_proxy.rowCount() == 1
    assert window.research_table_model.row_at(
        window.research_proxy.mapToSource(window.research_proxy.index(0, 0)).row()
    ).stock_name == "贵州茅台"

    window.search_input.setText("平安")
    assert window.research_proxy.rowCount() == 1
    assert window.research_table_model.row_at(
        window.research_proxy.mapToSource(window.research_proxy.index(0, 0)).row()
    ).stock_code == "000001"

    window.search_input.setText("不存在")
    assert window.research_proxy.rowCount() == 0
    window.search_input.clear()
    assert window.research_proxy.rowCount() == 2


def test_research_detail_panel_shows_not_landed_and_evidence(qtbot, tmp_path) -> None:
    window = _make_research_window(tmp_path, qtbot, seed=False)
    _seed_article(window.storage)
    _seed_signal(
        window.storage,
        board="potential_catalyst",
        extractor_kind="rules_fallback",
        provisional=True,
    )
    # v2 里程碑 5：候选事实复核状态随事件明细渲染。
    window.storage.upsert_event_claim(
        EventClaim(
            claim_id="claim:ui",
              document_id="doc-event-1",
            stock_code="000001",
            event_type="major_contract",
            direction="positive",
            positive_mechanism="新增合同预计增厚营业收入",
            metrics=(),
            certainty_stage="framework",
            certainty=0.45,
            materiality_level=1,
            counter_evidence=(),
            evidence_ids=(),
            rejection_reason=None,
            review_status="pending_review",
            gate_trace=(
                {
                    "gate": "ai_review",
                    "passed": False,
                    "reason": "规则与AI分歧：AI建议 event_type=mna",
                },
            ),
            extractor_kind="rules",
            extractor_version="rules-v1",
            created_at=datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ),
        )
    )
    window._select_source("catalyst")
    assert window.research_proxy.rowCount() == 1
    window.table.setCurrentIndex(window.research_proxy.index(0, 0))
    window._selection_changed()
    panel = window.research_detail_panel
    assert window.detail_stack.currentWidget() is panel
    assert not panel.banner.isHidden()
    assert "尚未落地" in panel.banner_label.text()
    assert "规则降级" in panel.meta_label.text()
    tree_text = []

    def collect(item):
        tree_text.append(item.text(0))
        for index in range(item.childCount()):
            collect(item.child(index))

    for index in range(panel.detail_tree.topLevelItemCount()):
        collect(panel.detail_tree.topLevelItem(index))
    joined = "\n".join(tree_text)
    assert "证据摘录" in joined
    assert "1.2亿元" in joined
    assert "候选事实复核状态" in joined
    assert "规则与AI分歧" in joined
    assert "尚未落地" in joined


def test_research_csv_export_and_copy_match_table_columns(qtbot, tmp_path, monkeypatch) -> None:
    window = _make_research_window(tmp_path, qtbot)
    window._select_source("confirm")
    target = tmp_path / "research.csv"
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "CSV 文件 (*.csv)"),
    )
    window.export_current_results()
    with target.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == list(RESEARCH_HEADERS["confirm"])
    assert len(rows) == 3
    assert rows[1][1:3] == ["平安银行", "000001"]
    assert rows[1][3] == "重大订单"
    exported = "\n".join(",".join(row) for row in rows)
    assert "密钥" not in exported and "api_key" not in exported.lower()

    copied_text: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QApplication.clipboard",
        lambda: type("Clip", (), {"setText": lambda _self, value: copied_text.append(value)})(),
    )
    window.table.setCurrentIndex(window.research_proxy.index(0, 0))
    window.copy_selected_row()
    assert copied_text[0].split("\t")[1:3] == ["平安银行", "000001"]


def test_research_quality_panel_shows_cold_start_text(qtbot, tmp_path) -> None:
    window = _make_research_window(tmp_path, qtbot, seed=False)
    window._select_source("confirm")
    assert window.content_stack.currentWidget() is window.empty_state
    assert "暂无数据" in window.empty_title.text()
    assert "冷启动" in window.quality_label.text() or "回填" in window.quality_label.text()
    assert window.freshness_label.text() in {"冷启动", "部分覆盖", "尚无数据"}


def _seed_discovery(
    storage: Storage,
    *,
    document_id: str,
    title: str,
    status: str,
    discovery_type: str = "contract_order",
    trigger_reason: str = "标题含“重大合同”",
    document_url: str = "https://static.cninfo.com.cn/finalpage/x.PDF",
    source_key: str = "cninfo_announcement",
    source_name: str = "巨潮资讯公告",
    code: str = "600390",
) -> None:
    now = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)
    document = SourceDocument(
        document_id,
        "cninfo",
        "巨潮资讯",
        "announcement",
        "https://cninfo.example.test/list",
        document_url,
        title,
        now,
        (code,),
        "",
        f"hash-{document_id}",
        "metadata_only",
        None,
        None,
    )
    candidate = DiscoveryCandidate(
        document_id=document_id,
        source_key=source_key,
        source_name=source_name,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        stock_codes=(code,),
        title=title,
        published_at=now,
        discovery_type=discovery_type,
        trigger_reason=trigger_reason,
        queue_status=status,
        attachment_type="PDF" if document_url else None,
        document_url=document_url,
        enqueued_at=now if status == "pending_attachment" else None,
        updated_at=now,
        signal_priority=True,
    )
    storage.save_research_batch(
        [document],
        [candidate],
        SyncCursor(
            source_key=source_key,
            sync_kind="announcement",
            cursor={"page": 1},
            target_start=now.date(),
            covered_start=now.date(),
            last_success_at=now,
            last_error=None,
            updated_at=now,
        ),
        now,
    )


def test_discovery_view_renders_statuses_and_order(qtbot, tmp_path) -> None:
    window = _make_research_window(tmp_path, qtbot, seed=False)
    _seed_discovery(
        window.storage,
        document_id="doc-pending",
        title="关于拟签订重大合同的公告",
        status="pending_attachment",
    )
    _seed_discovery(
        window.storage,
        document_id="doc-awaiting",
        title="2026年半年度报告摘要",
        status="awaiting_review",
        discovery_type="financial_report",
        trigger_reason="标题含“半年报”",
        document_url=None,
        code="688167",
    )
    _seed_discovery(
        window.storage,
        document_id="doc-failed",
        title="回购报告书",
        status="failed",
        discovery_type="capital_action",
        trigger_reason="标题含“回购”",
        code="300184",
    )

    window._select_source("discovery")

    assert window.selected_source == "discovery"
    assert window._table_mode == "research"
    assert window.research_proxy.rowCount() == 3
    model = window.research_table_model
    # 待解析优先显示，其次待核验，最后解析失败。
    assert model.data(model.index(0, 4)) == "关于拟签订重大合同的公告"
    assert model.data(model.index(0, 6)) == "待解析"
    assert model.data(model.index(1, 6)) == "待核验"
    assert model.data(model.index(2, 6)) == "解析失败"
    assert model.data(model.index(0, 3)) == "合同订单"
    assert model.data(model.index(1, 3)) == "财务报告"
    assert model.data(model.index(2, 3)) == "资本动作"
    # 标题列与触发原因可见。
    assert "半年报" in model.data(model.index(1, 5))
    # 视图明确不是研究结论。
    assert "待核验" in window.view_title.text()
    assert "尚非研究结论" in window.view_subtitle.text()


def test_discovery_detail_panel_shows_not_a_conclusion_and_url(
    qtbot, tmp_path,
) -> None:
    window = _make_research_window(tmp_path, qtbot, seed=False)
    _seed_discovery(
        window.storage,
        document_id="doc-pending",
        title="关于拟签订重大合同的公告",
        status="pending_attachment",
    )
    window._select_source("discovery")
    window.table.setCurrentIndex(window.research_proxy.index(0, 0))
    window._selection_changed()

    panel = window.research_detail_panel
    assert window.detail_stack.currentWidget() is panel
    assert not panel.banner.isHidden()
    assert "尚非研究结论" in panel.banner_label.text()
    assert panel.open_button.isEnabled()
    assert panel.current_urls == ["https://static.cninfo.com.cn/finalpage/x.PDF"]


def test_discovery_csv_export_and_copy_match_table_columns(
    qtbot, tmp_path, monkeypatch,
) -> None:
    window = _make_research_window(tmp_path, qtbot, seed=False)
    _seed_discovery(
        window.storage,
        document_id="doc-awaiting",
        title="2026年半年度报告摘要",
        status="awaiting_review",
        discovery_type="financial_report",
        trigger_reason="标题含“半年报”",
        document_url=None,
        code="688167",
    )
    window._select_source("discovery")
    target = tmp_path / "discovery.csv"
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "CSV 文件 (*.csv)"),
    )
    window.export_current_results()
    with target.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == list(RESEARCH_HEADERS["discovery"])
    assert len(rows) == 2
    assert rows[1][1:3] == ["688167", "688167"]
    assert rows[1][3] == "财务报告"
    assert rows[1][6] == "待核验"

    copied_text: list[str] = []
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.QApplication.clipboard",
        lambda: type("Clip", (), {"setText": lambda _self, value: copied_text.append(value)})(),
    )
    window.table.setCurrentIndex(window.research_proxy.index(0, 0))
    window.copy_selected_row()
    assert copied_text[0].split("\t")[1:3] == ["688167", "688167"]


def test_discovery_quality_panel_shows_per_source_stats(qtbot, tmp_path) -> None:
    window = _make_research_window(tmp_path, qtbot, seed=False)
    _seed_discovery(
        window.storage,
        document_id="doc-pending",
        title="关于拟签订重大合同的公告",
        status="pending_attachment",
    )
    _seed_discovery(
        window.storage,
        document_id="doc-failed",
        title="回购报告书",
        status="failed",
        discovery_type="capital_action",
        trigger_reason="标题含“回购”",
    )
    window._select_source("discovery")

    text = window.quality_label.text()
    assert "已发现 2" in text
    assert "待解析 1" in text
    assert "失败 1" in text
    assert "最早待处理" in text
    assert "覆盖交易日" in text


def test_institution_navigation_is_retired(qtbot, tmp_path) -> None:
    window = _make_research_window(tmp_path, qtbot, seed=False)
    assert set(window.research_buttons) == {"confirm", "catalyst", "discovery"}
    assert window._select_source("z20") is None
    assert window._select_source("persist") is None
    assert window.selected_source == "news"
    assert not hasattr(window, "persist_60_button")
    assert not hasattr(window, "persist_120_button")


def test_settings_dialog_ai_tab_values_and_clear_credential(qtbot, tmp_path, monkeypatch) -> None:
    dialog = SettingsDialog(
        window_hours=24,
        auto_refresh=False,
        density="compact",
        retention_days=7,
        data_dir=tmp_path,
        ai_enabled=True,
        ai_base_url="https://api.example.test/v1",
        ai_model="model-x",
        ai_timeout_seconds=45.0,
        ai_has_credential=True,
    )
    values = dialog.values
    assert values["ai_enabled"] is True
    assert values["ai_base_url"] == "https://api.example.test/v1"
    assert values["ai_model"] == "model-x"
    assert values["ai_timeout_seconds"] == 45.0
    monkeypatch.setattr(
        "ashare_hotpot.ui_components.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    dialog._clear_credential()
    assert dialog.values["ai_credential_cleared"] is True
    assert dialog.clear_credential_button.isEnabled() is False


def test_show_settings_saves_ai_configuration_and_credential(qtbot, tmp_path, monkeypatch) -> None:
    window = make_window(tmp_path, qtbot)

    class FakeCredentialStore:
        def __init__(self, _app_root) -> None:
            self.saved: str | None = None
            self.cleared = False

        def load(self) -> str | None:
            return None

        def save(self, api_key: str) -> None:
            self.saved = api_key

        def clear(self) -> None:
            self.cleared = True

    fake_store = FakeCredentialStore(tmp_path)
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.AiCredentialStore",
        lambda *_args: fake_store,
    )
    dialog = SettingsDialog(
        window_hours=24,
        auto_refresh=False,
        density="compact",
        retention_days=7,
        data_dir=tmp_path,
        ai_enabled=False,
        ai_base_url="",
        ai_model="",
        ai_timeout_seconds=30.0,
        ai_has_credential=False,
    )
    dialog.ai_enabled_check.setChecked(True)
    dialog.ai_base_url_input.setText("https://api.example.test/v1")
    dialog.ai_model_input.setText("model-x")
    dialog.ai_key_input.setText("sk-test-secret")
    monkeypatch.setattr(
        "ashare_hotpot.professional_window.SettingsDialog",
        lambda **_kwargs: dialog,
    )
    monkeypatch.setattr(dialog, "exec", lambda: QDialog.Accepted)

    window.show_settings()

    assert window.preferences.ai_enabled is True
    assert window.preferences.ai_base_url == "https://api.example.test/v1"
    assert window.preferences.ai_model == "model-x"
    assert fake_store.saved == "sk-test-secret"


def test_diagnostic_text_includes_research_and_ai_state_without_keys(qtbot, tmp_path) -> None:
    window = make_window(tmp_path, qtbot)
    text = window.diagnostic_text()
    assert "事件簇" in text
    assert "调研活动" in text
    assert "AI 增强" in text
    assert "sk-" not in text
    assert "Authorization" not in text
