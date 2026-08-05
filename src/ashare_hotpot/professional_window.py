from __future__ import annotations

import html
import platform
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .config import APP_NAME, APP_VERSION, PROJECT_URL, AppSettings, release_url
from .exporting import SOURCE_LABELS, default_export_name, export_csv, tab_separated_row
from .icons import app_icon, icon
from .models import PopularityRankRow, RankingRow, Snapshot
from .preferences import UiPreferences
from .service import RefreshService
from .storage import Storage
from .theme import DARK_STYLESHEET
from .ui_components import AboutDialog, ErrorBanner, HtmlInfoDialog, KpiChip, SettingsDialog, StockDetailPanel, open_local_directory
from .worker import RefreshWorker

# These mature widgets and models remain import-compatible through ashare_hotpot.ui.
from .ui import HeatBarDelegate, IndustryFilterButton, RankingProxyModel, RankingTableModel, format_datetime


# 涨跌幅列的最小宽度（综合人气榜第 4 列、飙升榜第 5 列）。
PERCENT_COLUMN_WIDTH = 150


class ProfessionalMainWindow(QMainWindow):
    snapshot_changed = Signal(object)

    def __init__(self, settings: AppSettings, storage: Storage, service: RefreshService) -> None:
        super().__init__()
        self.settings = settings
        self.storage = storage
        self.service = service
        self.preferences = UiPreferences(settings.app_root / "settings.ini")
        self.settings.window_hours = self.preferences.window_hours
        self.settings.retention_days = self.preferences.retention_days

        self.snapshot: Snapshot | None = None
        self.selected_source = self.preferences.last_source
        self._news_industry_tags: set[str] = set()
        self._thread = None
        self._worker: RefreshWorker | None = None
        self._last_error_details = ""
        self._restoring_table = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1024, 680)
        self.resize(1220, 780)
        self.setStyleSheet(DARK_STYLESHEET)

        self._build_actions()
        self._build_command_bar()
        self._build_ui()
        self._restore_preferences()
        self.load_latest_snapshot()
        self._update_action_states()

        if self.preferences.auto_refresh:
            QTimer.singleShot(0, self.start_refresh)

    # ---- construction -------------------------------------------------

    def _build_actions(self) -> None:
        self.refresh_action = QAction(icon("refresh"), "刷新", self)
        self.refresh_action.setShortcut("F5")
        self.refresh_action.setToolTip("刷新全部数据（F5）")
        self.refresh_action.triggered.connect(self.start_refresh)

        self.export_action = QAction(icon("export"), "导出当前结果…", self)
        self.export_action.setShortcut("Ctrl+E")
        self.export_action.triggered.connect(self.export_current_results)

        self.copy_row_action = QAction(icon("copy"), "复制选中行", self)
        self.copy_row_action.setShortcut("Ctrl+C")
        self.copy_row_action.triggered.connect(self.copy_selected_row)

        self.settings_action = QAction(icon("settings"), "设置…", self)
        self.settings_action.setShortcut("Ctrl+,")
        self.settings_action.triggered.connect(self.show_settings)

        self.reset_layout_action = QAction(icon("reset"), "重置表格布局", self)
        self.reset_layout_action.triggered.connect(self.reset_table_layout)

        self.toggle_detail_action = QAction(icon("panel"), "显示详情面板", self)
        self.toggle_detail_action.setCheckable(True)
        self.toggle_detail_action.setChecked(self.preferences.detail_visible)
        self.toggle_detail_action.triggered.connect(self._set_detail_visible)

        self.open_data_action = QAction(icon("folder"), "打开数据目录", self)
        self.open_data_action.triggered.connect(lambda: open_local_directory(self.settings.data_dir))
        self.open_logs_action = QAction(icon("folder"), "打开日志目录", self)
        self.open_logs_action.triggered.connect(lambda: open_local_directory(self.settings.log_dir))

        self.copy_diagnostics_action = QAction(icon("database"), "复制诊断信息", self)
        self.copy_diagnostics_action.triggered.connect(self.copy_diagnostics)

        self.clear_action = QAction(icon("trash"), "清除本地数据…", self)
        self.clear_action.triggered.connect(self.clear_local_data)

        self.methodology_action = QAction(icon("info"), "方法说明与数据来源", self)
        self.methodology_action.triggered.connect(self.show_methodology)
        self.shortcuts_action = QAction(icon("keyboard"), "快捷键", self)
        self.shortcuts_action.triggered.connect(self.show_shortcuts)
        self.about_action = QAction(icon("info"), f"关于 {APP_NAME}", self)
        self.about_action.triggered.connect(self.show_about)
        self.exit_action = QAction("退出", self)
        self.exit_action.triggered.connect(self.close)

        for action in (
            self.refresh_action,
            self.export_action,
            self.copy_row_action,
            self.settings_action,
            self.reset_layout_action,
            self.toggle_detail_action,
            self.open_data_action,
            self.open_logs_action,
            self.copy_diagnostics_action,
            self.clear_action,
            self.methodology_action,
            self.shortcuts_action,
            self.about_action,
            self.exit_action,
        ):
            self.addAction(action)

    def _build_command_bar(self) -> None:
        self.menuBar().hide()
        toolbar = QToolBar()
        toolbar.setObjectName("commandBar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setFixedHeight(52)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        self.command_bar = toolbar

        brand_icon = QLabel()
        brand_icon.setPixmap(app_icon().pixmap(28, 28))
        brand_icon.setFixedSize(30, 30)
        toolbar.addWidget(brand_icon)
        brand_title = QLabel(APP_NAME)
        brand_title.setObjectName("brandTitle")
        toolbar.addWidget(brand_title)
        toolbar.addSeparator()

        self.source_button_group = QButtonGroup(self)
        self.source_button_group.setExclusive(True)
        self.source_buttons: dict[str, QPushButton] = {}
        for key, label in (("ths", "新闻热度"), ("pop", "综合人气"), ("surge", "飙升榜")):
            button = QPushButton(label)
            button.setObjectName("sourceTab")
            button.setCheckable(True)
            button.setMinimumWidth(82)
            button.clicked.connect(lambda _checked=False, source=key: self._select_source(source))
            self.source_buttons[key] = button
            self.source_button_group.addButton(button)
            toolbar.addWidget(button)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self.freshness_label = QLabel("尚无数据")
        self.freshness_label.setObjectName("freshnessLabel")
        toolbar.addWidget(self.freshness_label)

        self.window_hours_input = QSpinBox()
        self.window_hours_input.setRange(1, 168)
        self.window_hours_input.setPrefix("新闻窗口 ")
        self.window_hours_input.setSuffix("h")
        self.window_hours_input.setValue(self.settings.window_hours)
        self.window_hours_input.setFixedWidth(132)
        self.window_hours_input.setToolTip("仅影响同花顺新闻榜的统计范围")
        self.window_hours_input.valueChanged.connect(self._set_window_hours)
        toolbar.addWidget(self.window_hours_input)

        self.refresh_button = QToolButton()
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.setDefaultAction(self.refresh_action)
        self.refresh_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addWidget(self.refresh_button)

        self.more_button = QToolButton()
        self.more_button.setObjectName("moreButton")
        self.more_button.setIcon(icon("more"))
        self.more_button.setIconSize(QSize(20, 20))
        self.more_button.setToolTip("更多命令")
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        self.more_button.setMenu(self._build_more_menu())
        toolbar.addWidget(self.more_button)

    def _build_more_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(self.export_action)
        menu.addAction(self.copy_row_action)
        menu.addSeparator()
        menu.addAction(self.settings_action)
        menu.addAction(self.toggle_detail_action)
        menu.addAction(self.reset_layout_action)

        data_menu = menu.addMenu(icon("database"), "数据与诊断")
        data_menu.addAction(self.open_data_action)
        data_menu.addAction(self.open_logs_action)
        data_menu.addAction(self.copy_diagnostics_action)
        data_menu.addSeparator()
        data_menu.addAction(self.clear_action)

        menu.addSeparator()
        menu.addAction(self.methodology_action)
        menu.addAction(self.shortcuts_action)
        menu.addAction(self.about_action)
        menu.addSeparator()
        menu.addAction(self.exit_action)
        return menu

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        self.error_banner = ErrorBanner()
        self.error_banner.retry_requested.connect(self.start_refresh)
        self.error_banner.details_requested.connect(self.show_error_details)
        outer.addWidget(self.error_banner)

        outer.addWidget(self._build_view_header())
        outer.addWidget(self._build_workbench(), 1)
        self.setCentralWidget(central)

        status = QStatusBar()
        self.disclaimer_label = QLabel("仅供信息整理，不构成投资建议")
        self.disclaimer_label.setObjectName("mutedLabel")
        status.addWidget(self.disclaimer_label, 1)
        self.status_message = QLabel("数据终端已就绪")
        self.status_message.setObjectName("mutedLabel")
        status.addPermanentWidget(self.status_message)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(130)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        status.addPermanentWidget(self.progress_bar)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setFixedHeight(26)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_refresh)
        self.cancel_button.hide()
        status.addPermanentWidget(self.cancel_button)
        self.snapshot_time_label = QLabel("暂无快照")
        self.snapshot_time_label.setObjectName("mutedLabel")
        status.addPermanentWidget(self.snapshot_time_label)
        self.setStatusBar(status)

    def _build_view_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("viewHeader")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 9)
        layout.setSpacing(7)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.view_title = QLabel("新闻热度")
        self.view_title.setObjectName("viewTitle")
        self.view_subtitle = QLabel("按去重后的有效新闻事件排序")
        self.view_subtitle.setObjectName("viewSubtitle")
        title_box.addWidget(self.view_title)
        title_box.addWidget(self.view_subtitle)
        top.addLayout(title_box)
        top.addStretch(1)

        self.kpi_chips = [KpiChip(label) for label in ("结果", "有效事件", "来源覆盖", "观察窗口")]
        for chip in self.kpi_chips:
            chip.setMinimumWidth(104)
            top.addWidget(chip)
        layout.addLayout(top)

        quality_row = QHBoxLayout()
        self.quality_toggle = QToolButton()
        self.quality_toggle.setObjectName("qualityToggle")
        self.quality_toggle.setCheckable(True)
        self.quality_toggle.setText("数据质量  ›")
        self.quality_toggle.clicked.connect(self._toggle_quality)
        quality_row.addWidget(self.quality_toggle)
        quality_row.addStretch(1)
        layout.addLayout(quality_row)

        self.quality_panel = QFrame()
        self.quality_panel.setObjectName("qualityPanel")
        quality_layout = QHBoxLayout(self.quality_panel)
        quality_layout.setContentsMargins(10, 7, 10, 7)
        self.quality_label = QLabel("等待首次刷新")
        self.quality_label.setObjectName("mutedLabel")
        self.quality_label.setWordWrap(True)
        quality_layout.addWidget(self.quality_label)
        self.quality_panel.hide()
        layout.addWidget(self.quality_panel)
        return frame

    def _build_workbench(self) -> QSplitter:
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        table_panel = QFrame()
        table_panel.setObjectName("tablePanel")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)
        table_layout.addWidget(self._build_table_tools())

        self.table_model = RankingTableModel(self)
        self.proxy_model = RankingProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)
        self.proxy_model.rowsInserted.connect(self._update_result_count)
        self.proxy_model.rowsRemoved.connect(self._update_result_count)
        self.proxy_model.modelReset.connect(self._update_result_count)

        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.heat_bar_delegate = HeatBarDelegate(self.table)
        self.table.setItemDelegateForColumn(RankingTableModel.HEAT_COLUMN, self.heat_bar_delegate)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setMouseTracking(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.clicked.connect(self.show_selected_details)
        self.table.doubleClicked.connect(lambda _index: self.activate_selected())
        self.table.activated.connect(lambda _index: self.activate_selected())
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())

        self.empty_state = self._build_empty_state()
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.empty_state)
        self.content_stack.addWidget(self.table)
        table_layout.addWidget(self.content_stack, 1)

        self.detail_panel = StockDetailPanel()
        self.detail_panel.close_requested.connect(lambda: self._set_detail_visible(False))
        self.detail_panel.open_url_requested.connect(self._open_url)

        self.splitter.addWidget(table_panel)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([820, 340])
        return self.splitter

    def _build_table_tools(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("tableTools")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(8)

        self.result_count_label = QLabel("0 只股票")
        self.result_count_label.setObjectName("toolLabel")
        layout.addWidget(self.result_count_label)

        self.industry_label = QLabel("行业")
        self.industry_label.setObjectName("toolLabel")
        layout.addWidget(self.industry_label)
        self.industry_filter = IndustryFilterButton()
        self.industry_filter.selection_changed.connect(self._set_industry_filter)
        layout.addWidget(self.industry_filter)

        layout.addStretch(1)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("搜索股票名称或代码  Ctrl+F")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(250)
        self.search_input.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search_input.textChanged.connect(self.proxy_model_set_query)
        layout.addWidget(self.search_input)

        self.clear_filters_button = QPushButton("清除筛选")
        self.clear_filters_button.clicked.connect(self.clear_filters)
        layout.addWidget(self.clear_filters_button)

        export_button = QToolButton()
        export_button.setDefaultAction(self.export_action)
        export_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        layout.addWidget(export_button)

        detail_button = QToolButton()
        detail_button.setDefaultAction(self.toggle_detail_action)
        detail_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        detail_button.setToolTip("显示或隐藏详情面板")
        layout.addWidget(detail_button)
        return frame

    def _build_empty_state(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("emptyState")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)
        layout.addStretch(1)
        icon_label = QLabel()
        icon_label.setPixmap(app_icon().pixmap(48, 48))
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)
        self.empty_title = QLabel("尚未生成热度榜单")
        self.empty_title.setObjectName("viewTitle")
        self.empty_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_title)
        self.empty_hint = QLabel("刷新数据后，将在这里显示新闻热度与官方人气榜单。")
        self.empty_hint.setObjectName("mutedLabel")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_hint)
        self.empty_refresh_button = QPushButton("开始刷新")
        self.empty_refresh_button.setObjectName("primaryButton")
        self.empty_refresh_button.setIcon(icon("refresh"))
        self.empty_refresh_button.clicked.connect(self.refresh_action.trigger)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.empty_refresh_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return frame

    # ---- snapshot and source rendering --------------------------------

    def load_latest_snapshot(self) -> None:
        snapshot = self.storage.load_latest_snapshot()
        if snapshot:
            self.set_snapshot(snapshot)
        else:
            self._render_empty_shell()

    def set_snapshot(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        for key in ("pop", "surge"):
            self.source_buttons[key].setEnabled(snapshot.popularity.available)
        if self.selected_source in {"pop", "surge"} and not snapshot.popularity.available:
            self.selected_source = "ths"
        self._render_selected_source()
        self.snapshot_changed.emit(snapshot)

    def _select_source(self, source_key: str) -> None:
        if source_key not in {"ths", "pop", "surge"}:
            return
        if source_key in {"pop", "surge"} and (not self.snapshot or not self.snapshot.popularity.available):
            return
        if source_key == self.selected_source and self.snapshot:
            self.source_buttons[source_key].setChecked(True)
            return
        if self.snapshot:
            self._save_table_state(self.selected_source)
        self.selected_source = source_key
        self.preferences.last_source = source_key
        if self.snapshot:
            self._render_selected_source()
        else:
            self._render_empty_shell()

    def _selected_rows(self) -> list[RankingRow] | list[PopularityRankRow]:
        if not self.snapshot:
            return []
        if self.selected_source == "pop":
            return self.snapshot.popularity.popularity
        if self.selected_source == "surge":
            return self.snapshot.popularity.surging
        return self.snapshot.rankings

    def _render_selected_source(self) -> None:
        if not self.snapshot:
            self._render_empty_shell()
            return
        source_key = self.selected_source
        self.source_buttons[source_key].setChecked(True)
        rows = self._selected_rows()
        self.table_model.set_rows(rows, source_key=source_key)
        self._configure_table_columns()
        self._restore_table_state(source_key)

        is_news = source_key == "ths"
        self.industry_label.setVisible(is_news)
        self.industry_filter.setVisible(is_news)
        if is_news:
            tags = {
                tag
                for row in rows
                for tag in (getattr(row, "industry_tags", None) or (RankingProxyModel.UNCATEGORIZED_LABEL,))
            }
            self.industry_filter.set_options(tags)
            self.industry_filter.set_selected_tags(self._news_industry_tags, emit=False)
            self.proxy_model.set_industry_tags(self._news_industry_tags)
        else:
            self.proxy_model.set_industry_tags(set())

        self._render_header()
        self.content_stack.setCurrentWidget(self.table if rows else self.empty_state)
        if not rows:
            self.empty_title.setText(f"{SOURCE_LABELS[source_key]}暂无数据")
            self.empty_hint.setText("刷新后仍会保留上次成功结果；可在数据质量中查看当前状态。")
        self.detail_panel.clear()
        self.snapshot_time_label.setText(f"快照 {format_datetime(self.snapshot.created_at)}")
        self._update_result_count()
        self._update_action_states()

    def _render_empty_shell(self) -> None:
        self.source_buttons[self.selected_source].setChecked(True)
        for key in ("pop", "surge"):
            self.source_buttons[key].setEnabled(False)
        self.content_stack.setCurrentWidget(self.empty_state)
        self.table_model.set_rows([], source_key="ths")
        self.view_title.setText(SOURCE_LABELS.get(self.selected_source, "新闻热度"))
        self.view_subtitle.setText("等待首次刷新")
        for chip in self.kpi_chips:
            chip.set_value("—")
        self.quality_label.setText("尚无本地快照，点击刷新后开始采集。")
        self._set_freshness("尚无数据", "")
        self._update_result_count()
        self._update_action_states()

    def _render_header(self) -> None:
        assert self.snapshot is not None
        source_key = self.selected_source
        rows = self._selected_rows()
        self.view_title.setText(SOURCE_LABELS[source_key])
        if source_key == "ths":
            stats = self.snapshot.stats
            total_coverage = len(self.snapshot.coverages)
            good_coverage = sum(
                1 for coverage in self.snapshot.coverages if coverage.reached_cutoff and not coverage.error
            )
            self.view_subtitle.setText("按去重后的有效新闻事件排序 · 同花顺公开新闻")
            values = (
                ("结果", f"{len(rows)} 只"),
                ("有效事件", str(stats.get("events", 0))),
                ("来源覆盖", f"{good_coverage}/{total_coverage}" if total_coverage else "—"),
                ("观察窗口", f"{self.settings.window_hours}h"),
            )
            for chip, (label, value) in zip(self.kpi_chips, values):
                chip.label.setText(label)
                chip.set_value(value)
            self.quality_label.setText(
                f"列表 {stats.get('list_items', 0)} 篇 · 去重 URL {stats.get('unique_urls', 0)} · "
                f"模板过滤 {stats.get('filtered', 0)} · 正文失败 {stats.get('failed', 0)} · "
                f"未映射 {stats.get('unmapped', 0)} · 有效事件 {stats.get('events', 0)}"
            )
            if self.snapshot.partial:
                self._set_freshness("部分覆盖", "stale")
            else:
                self._set_freshness(f"更新于 {self.snapshot.created_at.strftime('%H:%M')}", "fresh")
        else:
            popularity = self.snapshot.popularity
            board_label = "官方关注度排名" if source_key == "pop" else "较昨日排名提升"
            self.view_subtitle.setText(f"东方财富官方{SOURCE_LABELS[source_key]} · {board_label}")
            success_text = popularity.success_at.strftime("%m-%d %H:%M") if popularity.success_at else "—"
            state = "已过期" if popularity.is_stale else ("正常" if popularity.available else "不可用")
            values = (
                ("结果", f"{len(rows)} 只"),
                ("数据截至", success_text),
                ("状态", state),
                ("来源", "东方财富"),
            )
            for chip, (label, value) in zip(self.kpi_chips, values):
                chip.label.setText(label)
                chip.set_value(value)
            if popularity.is_stale:
                self.quality_label.setText(f"沿用上次成功榜单 · 本次失败：{popularity.error or '未知原因'}")
                self._set_freshness("数据已过期", "stale")
            elif popularity.available:
                self.quality_label.setText("官方榜单完整可用；产品不推导未公开的热度分值或权重。")
                self._set_freshness(f"更新于 {success_text}", "fresh")
            else:
                self.quality_label.setText(f"本次读取失败：{popularity.error or '未知原因'}")
                self._set_freshness("读取失败", "error")

    def _set_freshness(self, text: str, state: str) -> None:
        self.freshness_label.setText(text)
        self.freshness_label.setProperty("state", state)
        self.freshness_label.style().unpolish(self.freshness_label)
        self.freshness_label.style().polish(self.freshness_label)

    # ---- table, filters and details -----------------------------------

    def proxy_model_set_query(self, value: str) -> None:
        self.proxy_model.set_query(value)
        self._update_result_count()

    def _set_industry_filter(self, tags: set[str]) -> None:
        self._news_industry_tags = set(tags)
        if self.selected_source == "ths":
            self.proxy_model.set_industry_tags(tags)
        self._update_result_count()

    def clear_filters(self) -> None:
        self.search_input.clear()
        if self.selected_source == "ths":
            self._news_industry_tags.clear()
            self.industry_filter.set_selected_tags(set())
        self._update_result_count()

    def _update_result_count(self, *_: object) -> None:
        count = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else 0
        self.result_count_label.setText(f"{count} 只股票")
        if hasattr(self, "heat_bar_delegate"):
            self.heat_bar_delegate.set_maximum(self.proxy_model.maximum_filtered_heat())
        if hasattr(self, "export_action"):
            self._update_action_states()

    def _configure_table_columns(self) -> None:
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in range(2, self.table_model.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 58)
        self.table.setColumnWidth(2, 82)
        if self.selected_source == "pop":
            self.table.setColumnWidth(3, 96)
            self.table.setColumnWidth(4, PERCENT_COLUMN_WIDTH)
        elif self.selected_source == "surge":
            self.table.setColumnWidth(3, 110)
            self.table.setColumnWidth(4, 96)
            self.table.setColumnWidth(5, PERCENT_COLUMN_WIDTH)
        else:
            self.table.setColumnWidth(RankingTableModel.INDUSTRY_COLUMN, 145)
            self.table.setColumnWidth(RankingTableModel.HEAT_COLUMN, 88)
            self.table.setColumnWidth(5, 88)
            self.table.setColumnWidth(6, 145)
        self._apply_density()

    def _apply_density(self) -> None:
        self.table.verticalHeader().setDefaultSectionSize(36 if self.preferences.density == "compact" else 44)

    def _save_table_state(self, source_key: str) -> None:
        if self._restoring_table or not hasattr(self, "table"):
            return
        header = self.table.horizontalHeader()
        self.preferences.set_header_state(source_key, header.saveState())
        self.preferences.set_sort(
            source_key,
            header.sortIndicatorSection(),
            header.sortIndicatorOrder().value,
        )

    def _restore_table_state(self, source_key: str) -> None:
        self._restoring_table = True
        header = self.table.horizontalHeader()
        state = self.preferences.header_state(source_key)
        if not state.isEmpty():
            header.restoreState(state)
            percent_column = 4 if source_key == "pop" else (5 if source_key == "surge" else None)
            if percent_column is not None and header.sectionSize(percent_column) < PERCENT_COLUMN_WIDTH:
                header.resizeSection(percent_column, PERCENT_COLUMN_WIDTH)
        column, order = self.preferences.sort(source_key)
        if not 0 <= column < self.table_model.columnCount():
            column = 0
        sort_order = Qt.DescendingOrder if order == Qt.DescendingOrder.value else Qt.AscendingOrder
        self.table.sortByColumn(column, sort_order)
        self._restoring_table = False

    def reset_table_layout(self) -> None:
        self.preferences.reset_table_layouts()
        self._configure_table_columns()
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.status_message.setText("表格布局已重置")

    def show_selected_details(self, index) -> None:
        if index.isValid():
            self.table.setCurrentIndex(index)
            self._selection_changed()

    def _selection_changed(self) -> None:
        row = self._current_row()
        if isinstance(row, RankingRow) and self.snapshot:
            self.detail_panel.set_news(row, self.snapshot.events)
        elif isinstance(row, PopularityRankRow):
            self.detail_panel.set_popularity(row, self.selected_source)
        else:
            self.detail_panel.clear()
        self._update_action_states()

    def _current_row(self) -> RankingRow | PopularityRankRow | None:
        index = self.table.currentIndex()
        if not index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(index)
        return self.table_model.row_at(source_index.row())

    def activate_selected(self) -> None:
        if self._current_row() is None:
            return
        if not self.detail_panel.isVisible():
            self._set_detail_visible(True)
            self._selection_changed()
        self.detail_panel.open_primary()

    def _show_table_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        self.table.setCurrentIndex(index)
        self._selection_changed()
        row = self._current_row()
        if row is None:
            return
        menu = QMenu(self)
        primary = menu.addAction("查看详情" if isinstance(row, RankingRow) else "打开官方页")
        if isinstance(row, RankingRow):
            primary.triggered.connect(lambda: self._set_detail_visible(True))
        else:
            primary.triggered.connect(self.activate_selected)
        copy_identity = menu.addAction(icon("copy"), "复制股票名称和代码")
        copy_identity.triggered.connect(lambda: self._copy_text(f"{row.name}\t{row.code}", "股票信息已复制"))
        menu.addAction(self.copy_row_action)
        menu.addSeparator()
        menu.addAction(self.export_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _set_detail_visible(self, visible: bool) -> None:
        self.detail_panel.setVisible(visible)
        self.toggle_detail_action.setChecked(visible)
        self.preferences.detail_visible = visible
        if visible and self._current_row() is not None:
            self._selection_changed()

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    # ---- export and clipboard ----------------------------------------

    def _visible_rows(self) -> list[RankingRow | PopularityRankRow]:
        rows: list[RankingRow | PopularityRankRow] = []
        for proxy_row in range(self.proxy_model.rowCount()):
            source_index = self.proxy_model.mapToSource(self.proxy_model.index(proxy_row, 0))
            row = self.table_model.row_at(source_index.row())
            if row is not None:
                rows.append(row)
        return rows

    def export_current_results(self) -> None:
        rows = self._visible_rows()
        if not rows:
            return
        created_at = self.snapshot.created_at if self.snapshot else None
        default_path = self.settings.app_root / default_export_name(self.selected_source, created_at)
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "导出当前结果",
            str(default_path),
            "CSV 文件 (*.csv)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        try:
            count = export_csv(path, self.selected_source, rows)
        except OSError as exc:
            self._last_error_details = str(exc)
            self.error_banner.show_message("导出失败，请检查文件是否被占用或目录是否可写。")
            return
        self.status_message.setText(f"已导出 {count} 行 · {path.name}")

    def copy_selected_row(self) -> None:
        row = self._current_row()
        if row is None:
            return
        self._copy_text(tab_separated_row(self.selected_source, row), "选中行已复制")

    def _copy_text(self, text: str, message: str) -> None:
        QApplication.clipboard().setText(text)
        self.status_message.setText(message)

    # ---- settings, diagnostics and help -------------------------------

    def show_settings(self) -> None:
        dialog = SettingsDialog(
            window_hours=self.settings.window_hours,
            auto_refresh=self.preferences.auto_refresh,
            density=self.preferences.density,
            retention_days=self.settings.retention_days,
            data_dir=self.settings.app_root,
            parent=self,
        )
        dialog.clear_data_requested.connect(self.clear_local_data)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values
        self.settings.window_hours = int(values["window_hours"])
        self.settings.retention_days = int(values["retention_days"])
        self.preferences.window_hours = self.settings.window_hours
        self.preferences.retention_days = self.settings.retention_days
        self.preferences.auto_refresh = bool(values["auto_refresh"])
        self.preferences.density = str(values["density"])
        self.window_hours_input.setValue(self.settings.window_hours)
        self._apply_density()
        if self.snapshot:
            self._render_header()
        self.preferences.sync()
        self.status_message.setText("设置已保存")

    def diagnostic_text(self) -> str:
        stats = self.storage.get_storage_stats()
        latest_snapshot = format_datetime(self.snapshot.created_at, seconds=True) if self.snapshot else "无"
        latest_run = stats.latest_run
        run_text = "无"
        if latest_run:
            run_text = f"{latest_run.status} · {format_datetime(latest_run.finished_at or latest_run.started_at, seconds=True)}"
            if latest_run.message:
                run_text += f" · {latest_run.message}"
        return "\n".join(
            (
                f"{APP_NAME} {APP_VERSION}",
                f"系统：{platform.platform()}",
                f"数据目录：{self.settings.app_root}",
                f"数据库：{self.storage.database_path} ({stats.database_bytes / 1024:.1f} KiB)",
                f"缓存文章：{stats.article_count}",
                f"历史快照：{stats.snapshot_count}",
                f"最新快照：{latest_snapshot}",
                f"最近刷新：{run_text}",
                f"日志目录：{self.settings.log_dir}",
            )
        )

    def copy_diagnostics(self) -> None:
        self._copy_text(self.diagnostic_text(), "诊断信息已复制")

    def show_methodology(self) -> None:
        HtmlInfoDialog(
            "方法说明与数据来源",
            """
            <h2>数据来源与计算口径</h2>
            <p><b>新闻热度</b>来自同花顺公开新闻页面，按观察窗口收集文章，过滤固定模板并对重复 URL 和相似事件去重，再统计股票的有效提及次数。</p>
            <p><b>综合人气榜与飙升榜</b>来自东方财富公开官方榜单，产品只展示官方排名、排名变化和公开行情字段，不推导未公开的热度分值或权重。</p>
            <p>官方榜单低频读取并使用短期缓存；读取失败时保留上次完整榜单并明确标注过期。</p>
            <p><b>风险声明：</b>所有结果仅供公开信息整理，不构成投资建议。</p>
            """,
            self,
        ).exec()

    def show_shortcuts(self) -> None:
        HtmlInfoDialog(
            "快捷键",
            """
            <h2>快捷键</h2>
            <table cellspacing="8">
              <tr><td><b>F5</b></td><td>刷新全部数据</td></tr>
              <tr><td><b>Ctrl+F</b></td><td>聚焦股票搜索框</td></tr>
              <tr><td><b>Ctrl+E</b></td><td>导出当前筛选结果</td></tr>
              <tr><td><b>Ctrl+C</b></td><td>复制选中榜单行</td></tr>
              <tr><td><b>Ctrl+,</b></td><td>打开设置</td></tr>
              <tr><td><b>Enter</b></td><td>打开当前股票的主操作</td></tr>
            </table>
            """,
            self,
        ).exec()

    def show_about(self) -> None:
        dialog = AboutDialog(
            app_name=APP_NAME,
            app_version=APP_VERSION,
            project_url=PROJECT_URL,
            release_url=release_url(),
            parent=self,
        )
        dialog.diagnostics_requested.connect(self.copy_diagnostics)
        dialog.exec()

    def show_error_details(self) -> None:
        text = html.escape(self._last_error_details or "没有更多错误信息。")
        HtmlInfoDialog("错误详情", f"<h2>最近一次错误</h2><pre>{text}</pre>", self).exec()

    # ---- refresh lifecycle -------------------------------------------

    def _set_window_hours(self, hours: int) -> None:
        self.settings.window_hours = hours
        self.preferences.window_hours = hours
        if self.snapshot and self.selected_source == "ths":
            self._render_header()

    def start_refresh(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        self.settings.window_hours = self.window_hours_input.value()
        self.refresh_action.setEnabled(False)
        self.empty_refresh_button.setEnabled(False)
        self.window_hours_input.setEnabled(False)
        self.clear_action.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_message.setText("正在启动刷新…")
        self.error_banner.hide()

        from PySide6.QtCore import QThread

        self._thread = QThread(self)
        self._worker = RefreshWorker(self.service)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.completed.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def cancel_refresh(self) -> None:
        if self._worker:
            self.cancel_button.setEnabled(False)
            self.status_message.setText("正在取消，请稍候…")
            self._worker.request_cancel()

    def _on_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(value)
        self.status_message.setText(message)

    def _on_completed(self, snapshot: Snapshot) -> None:
        self.set_snapshot(snapshot)
        self.status_message.setText("刷新完成 · 榜单已更新")
        self.error_banner.hide()

    def _on_failed(self, message: str) -> None:
        self._last_error_details = message
        self.status_message.setText("刷新失败 · 已保留上次结果")
        self.error_banner.show_message("刷新失败，已保留上次成功结果。可重试或查看错误详情。")

    def _on_cancelled(self) -> None:
        self.status_message.setText("刷新已取消 · 未生成新榜单")

    def _thread_finished(self) -> None:
        self.refresh_action.setEnabled(True)
        self.empty_refresh_button.setEnabled(True)
        self.window_hours_input.setEnabled(True)
        self.clear_action.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.progress_bar.hide()
        self._thread = None
        self._worker = None
        self._update_action_states()

    # ---- destructive action and lifecycle ----------------------------

    def clear_local_data(self) -> None:
        if self._thread and self._thread.isRunning():
            QMessageBox.information(self, "正在刷新", "请先取消或等待当前刷新完成。")
            return
        answer = QMessageBox.question(
            self,
            "清除本地数据",
            "这会删除缓存文章和所有历史榜单，且无法恢复。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.storage.clear_all()
        self.snapshot = None
        self.selected_source = "ths"
        self.preferences.last_source = "ths"
        self._news_industry_tags.clear()
        self.industry_filter.set_options(set())
        self.search_input.clear()
        self.detail_panel.clear()
        self._render_empty_shell()
        self.status_message.setText("本地数据已清除")

    def _update_action_states(self) -> None:
        has_rows = hasattr(self, "proxy_model") and self.proxy_model.rowCount() > 0
        has_selection = hasattr(self, "table") and self.table.currentIndex().isValid()
        running = bool(self._thread and self._thread.isRunning())
        self.export_action.setEnabled(has_rows)
        self.copy_row_action.setEnabled(has_selection)
        self.clear_action.setEnabled(not running)
        self.refresh_action.setEnabled(not running)

    def _toggle_quality(self, checked: bool) -> None:
        self.quality_panel.setVisible(checked)
        self.quality_toggle.setText("数据质量  ⌄" if checked else "数据质量  ›")

    def _restore_preferences(self) -> None:
        geometry = self.preferences.window_geometry()
        if not geometry.isEmpty():
            self.restoreGeometry(geometry)
        splitter = self.preferences.splitter_state()
        if not splitter.isEmpty():
            self.splitter.restoreState(splitter)
        self._set_detail_visible(self.preferences.detail_visible)
        self._apply_density()
        focus_search = QAction(self)
        focus_search.setShortcut("Ctrl+F")
        focus_search.triggered.connect(self._focus_search)
        self.addAction(focus_search)
        self.focus_search_action = focus_search

    def _focus_search(self) -> None:
        self.search_input.setFocus(Qt.ShortcutFocusReason)
        self.search_input.selectAll()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._thread and self._thread.isRunning():
            answer = QMessageBox.question(
                self,
                "刷新正在进行",
                "退出会取消本次刷新。是否退出？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            if self._worker:
                self._worker.request_cancel()
            if not self._thread.wait(5000):
                QMessageBox.information(self, "正在取消", "网络请求仍在结束，请稍后再次退出。")
                event.ignore()
                return
        self._save_table_state(self.selected_source)
        self.preferences.set_window_geometry(self.saveGeometry())
        self.preferences.set_splitter_state(self.splitter.saveState())
        self.preferences.sync()
        event.accept()
