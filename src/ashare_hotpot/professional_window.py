from __future__ import annotations

import html
import platform
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import APP_NAME, APP_VERSION, PROJECT_URL, AppSettings, SHANGHAI_TZ, release_url
from .exporting import SOURCE_LABELS, default_export_name, export_csv, tab_separated_row
from .icons import app_icon, icon
from .ai_extractor import AiCredentialStore
from .models import (
    DiscoveryViewRow,
    InteractionRankingRow,
    PopularityRankRow,
    IndustryHeatRow,
    IndustryHeatSnapshot,
    RankingRow,
    ShortTermViewRow,
    Snapshot,
    ParsedArticle,
)
from .preferences import UiPreferences
from .popularity import restore_popularity_names
from .research_views import (
    COVERAGE_STATE_LABELS,
    build_discovery_quality,
    coverage_state as research_coverage_state,
    load_discovery_rows,
    load_event_detail,
    load_short_term_rows,
    research_coverage,
)
from .service import RefreshService
from .storage import Storage
from .theme import DARK_STYLESHEET
from .ui_components import (
    AboutDialog,
    ErrorBanner,
    HtmlInfoDialog,
    KpiChip,
    ResearchDetailPanel,
    SettingsDialog,
    StockDetailPanel,
    open_local_directory,
)
from .worker import RefreshWorker

# These mature widgets and models remain import-compatible through ashare_hotpot.ui.
from .ui import (
    ContentTypeFilterButton,
    HeatBarDelegate,
    IndustryFilterButton,
    PlatformFilterButton,
    QUALITY_STATE_BY_LABEL,
    QualityFilterButton,
    RankingProxyModel,
    RankingTableModel,
    ResearchProxyModel,
    ResearchTableModel,
    TopicFilterButton,
    format_datetime,
)


# 涨跌幅列的最小宽度（综合人气榜第 4 列、飙升榜第 5 列）。
PERCENT_COLUMN_WIDTH = 150
INDUSTRY_SOURCE_KEY = "industry"


class IndustryTableModel(QAbstractTableModel):
    HEADERS = ("排名", "行业", "热度", "A", "A分位", "B", "B分位", "映射/来源状态")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[IndustryHeatRow] = []

    def set_rows(self, rows: list[IndustryHeatRow]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> IndustryHeatRow | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        row = self.rows[index.row()]
        values = (
            row.rank, row.industry, f"{row.heat:.2f}", row.a,
            f"{row.a_percentile:.2f}", row.b, f"{row.b_percentile:.2f}",
            f"{row.mapping_status}/{row.source_status}",
        )
        if role == Qt.DisplayRole:
            return str(values[index.column()])
        if role == Qt.UserRole:
            return values[index.column()]
        if role == Qt.TextAlignmentRole and index.column() in {0, 2, 3, 4, 5, 6}:
            return int(Qt.AlignVCenter | Qt.AlignRight)
        if role == Qt.FontRole and index.column() in {1, 2}:
            from PySide6.QtGui import QFont
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ForegroundRole and index.column() == 2:
            return QColor("#f2b84b")
        return None

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:  # noqa: N802
        if not 0 <= column < len(self.HEADERS):
            return
        numeric_columns = {0, 2, 3, 4, 5, 6}

        def key(row: IndustryHeatRow):
            values = (
                row.rank,
                row.industry,
                row.heat,
                row.a,
                row.a_percentile,
                row.b,
                row.b_percentile,
                f"{row.mapping_status}/{row.source_status}",
            )
            return values[column] if column in numeric_columns else str(values[column])

        self.layoutAboutToBeChanged.emit()
        self.rows.sort(key=key, reverse=order == Qt.DescendingOrder)
        self.layoutChanged.emit()


class IndustryDetailPanel(QFrame):
    open_url_requested = Signal(str)
    close_requested = Signal()

    def __init__(self, storage: Storage, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.current_row: IndustryHeatRow | None = None
        self.current_snapshot: IndustryHeatSnapshot | None = None
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        header = QFrame()
        header_layout = QHBoxLayout(header)
        title_box = QVBoxLayout()
        self.title_label = QLabel("行业热度详情")
        self.title_label.setObjectName("viewTitle")
        self.meta_label = QLabel("选择行业查看明细")
        self.meta_label.setObjectName("detailMeta")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.meta_label)
        header_layout.addLayout(title_box, 1)
        close = QPushButton()
        close.setIcon(icon("close"))
        close.clicked.connect(self.close_requested)
        header_layout.addWidget(close)
        layout.addWidget(header)
        self.summary_label = QLabel("行业热度 = 50% × A分位 + 50% × B分位；B 使用 log(1+B) 计算分位。")
        self.summary_label.setWordWrap(True)
        self.summary_label.setObjectName("mutedLabel")
        layout.addWidget(self.summary_label)
        self.article_tree = QTreeWidget()
        self.article_tree.setHeaderLabels(["行业成分股 / 资讯", "类型/来源", "发布时间"])
        self.article_tree.setRootIsDecorated(True)
        self.article_tree.itemDoubleClicked.connect(self._open_article)
        layout.addWidget(self.article_tree, 1)
        self.open_button = QPushButton("打开所选文章")
        self.open_button.clicked.connect(self._open_selected)
        self.open_button.setEnabled(False)
        layout.addWidget(self.open_button, 0, Qt.AlignRight)

    def clear(self) -> None:
        self.current_row = None
        self.current_snapshot = None
        self.title_label.setText("行业热度详情")
        self.meta_label.setText("选择行业查看明细")
        self.article_tree.clear()
        self.open_button.setEnabled(False)

    def set_row(self, row: IndustryHeatRow, snapshot: IndustryHeatSnapshot) -> None:
        self.current_row = row
        self.current_snapshot = snapshot
        self.title_label.setText(row.industry)
        self.meta_label.setText(f"排名 {row.rank} · 当前榜 {row.source_status} · 映射 {row.mapping_status}")
        self.summary_label.setText(
            f"A={row.a}（A分位 {row.a_percentile:.2f}） · B={row.b}（B分位 {row.b_percentile:.2f}） · "
            f"行业热度 {row.heat:.2f} = 50% × {row.a_percentile:.2f} + 50% × {row.b_percentile:.2f}"
        )
        self.article_tree.clear()
        stock_names = self.storage.get_stock_names(set(row.stock_codes))
        for code in row.stock_codes:
            stock_item = QTreeWidgetItem(
                [f"{stock_names.get(code, code)}（{code}）", "A 成分股", ""]
            )
            stock_item.setData(0, Qt.UserRole + 1, code)
            related = [
                article
                for article in snapshot.articles
                if code in {stock.code for stock in article.stocks}
                and article.channel_key != "industry_research"
            ]
            for article in sorted(
                related, key=lambda item: item.published_at, reverse=True
            ):
                child = QTreeWidgetItem(
                    [
                        article.title,
                        article.provider_name or article.source_name,
                        article.published_at.strftime("%m-%d %H:%M"),
                    ]
                )
                child.setData(0, Qt.UserRole, article.url)
                stock_item.addChild(child)
            self.article_tree.addTopLevelItem(stock_item)
            stock_item.setExpanded(True)
        allowed = set(row.article_urls)
        for article in sorted(snapshot.articles, key=lambda item: item.published_at, reverse=True):
            if (article.url or article.seq) not in allowed:
                continue
            item = QTreeWidgetItem([
                article.title,
                article.provider_name or article.source_name,
                article.published_at.strftime("%m-%d %H:%M"),
            ])
            item.setData(0, Qt.UserRole, article.url)
            self.article_tree.addTopLevelItem(item)
        if self.article_tree.topLevelItemCount():
            self.article_tree.setCurrentItem(self.article_tree.topLevelItem(0))
        def has_url(item: QTreeWidgetItem) -> bool:
            return bool(item.data(0, Qt.UserRole)) or any(
                has_url(item.child(index)) for index in range(item.childCount())
            )

        self.open_button.setEnabled(any(
            has_url(self.article_tree.topLevelItem(index))
            for index in range(self.article_tree.topLevelItemCount())
        ))

    def _open_selected(self) -> None:
        item = self.article_tree.currentItem()
        if item and item.data(0, Qt.UserRole):
            self.open_url_requested.emit(str(item.data(0, Qt.UserRole)))

    def _open_article(self, item, _column) -> None:
        if item.data(0, Qt.UserRole):
            self.open_url_requested.emit(str(item.data(0, Qt.UserRole)))

RESEARCH_SOURCE_KEYS = frozenset({"confirm", "catalyst", "discovery"})

RESEARCH_VIEW_META: dict[str, tuple[str, str]] = {
    "confirm": (
        "确定性利好",
        "已确认、有明确正向机制、重大性达标且无高度反证的事件",
    ),
    "catalyst": (
        "潜在催化",
        "中标待签、审批中、框架协议或筹划阶段的潜在事件",
    ),
    "discovery": (
        "待核验",
        "公开资料发现层：待解析、待核验与解析失败的披露，尚非研究结论",
    ),
}


class SearchLineEdit(QLineEdit):
    """A compact search field with a vertically centered leading icon."""

    ICON_SIZE = QSize(16, 16)
    ICON_LEFT = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.leading_icon = QLabel(self)
        self.leading_icon.setPixmap(icon("search").pixmap(self.ICON_SIZE))
        self.leading_icon.setFixedSize(self.ICON_SIZE)
        self.leading_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setTextMargins(self.ICON_LEFT + self.ICON_SIZE.width() + 8, 0, 0, 0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.leading_icon.move(
            self.ICON_LEFT,
            (self.height() - self.leading_icon.height()) // 2,
        )


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
        if self.selected_source in {"z20", "persist", "persist60", "persist120"}:
            self.selected_source = "news"
        self._news_industry_tags: set[str] = set()
        self._news_content_types: set[str] = set()
        self._interaction_industry_tags: set[str] = set()
        self._interaction_platform_tags: set[str] = set()
        self._thread = None
        self._worker: RefreshWorker | None = None
        self._last_error_details = ""
        self._restoring_table = False
        self._table_mode = "legacy"  # "legacy" | "research"
        self._research_coverage = None
        self._research_event_types: set[str] = set()
        self._research_topics: set[str] = set()
        self._research_industries: set[str] = set()
        self._research_quality_states: set[str] = set()
        self._persist_window = self.preferences.persist_window

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

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self.freshness_label = QLabel("尚无数据")
        self.freshness_label.setObjectName("freshnessLabel")
        toolbar.addWidget(self.freshness_label)

        self.window_hours_input = QSpinBox()
        self.window_hours_input.setRange(1, 168)
        self.window_hours_input.setPrefix("消息窗口 ")
        self.window_hours_input.setSuffix("h")
        self.window_hours_input.setValue(self.settings.window_hours)
        self.window_hours_input.setFixedWidth(132)
        self.window_hours_input.setToolTip("仅影响基本面消息榜的统计范围")
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

        self.addToolBarBreak(Qt.TopToolBarArea)
        navigation = QToolBar()
        navigation.setObjectName("navigationBar")
        navigation.setMovable(False)
        navigation.setFloatable(False)
        navigation.setFixedHeight(56)
        self.addToolBar(Qt.TopToolBarArea, navigation)
        navigation.layout().setContentsMargins(5, 12, 5, 12)
        self.navigation_bar = navigation

        self.source_button_group = QButtonGroup(self)
        self.source_button_group.setExclusive(True)
        self.source_buttons: dict[str, QPushButton] = {}
        source_group, source_group_layout = self._navigation_group("原始榜单", "original")
        self.source_navigation_group = source_group
        for key, label in (
            ("news", "基本面消息"),
            ("interaction", "基本面互动"),
            ("pop", "综合人气"),
            ("surge", "飙升榜"),
        ):
            button = QPushButton(label)
            button.setObjectName("sourceTab")
            button.setCheckable(True)
            button.setFixedHeight(30)
            button.setMinimumWidth(84)
            button.clicked.connect(lambda _checked=False, source=key: self._select_source(source))
            self.source_buttons[key] = button
            self.source_button_group.addButton(button)
            source_group_layout.addWidget(button)
        navigation.addWidget(source_group)

        industry_group, industry_group_layout = self._navigation_group("行业观察", "industry")
        self.industry_navigation_group = industry_group
        self.industry_button = QPushButton("行业热度")
        self.industry_button.setObjectName("sourceTab")
        self.industry_button.setCheckable(True)
        self.industry_button.setFixedHeight(30)
        self.industry_button.setMinimumWidth(92)
        self.industry_button.clicked.connect(lambda: self._select_source(INDUSTRY_SOURCE_KEY))
        self.source_button_group.addButton(self.industry_button)
        industry_group_layout.addWidget(self.industry_button)
        navigation.addWidget(industry_group)

        research_group, research_group_layout = self._navigation_group("研究信号", "research")
        self.research_navigation_group = research_group
        self.research_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("confirm", "确定性利好"),
            ("catalyst", "潜在催化"),
            ("discovery", "待核验"),
        ):
            button = QPushButton(label)
            button.setObjectName("sourceTab")
            button.setCheckable(True)
            button.setFixedHeight(30)
            button.setMinimumWidth(84)
            button.clicked.connect(lambda _checked=False, source=key: self._select_source(source))
            self.research_buttons[key] = button
            self.source_button_group.addButton(button)
            research_group_layout.addWidget(button)
        navigation.addWidget(research_group)

        navigation_spacer = QWidget()
        navigation_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        navigation.addWidget(navigation_spacer)

    @staticmethod
    def _navigation_group(title: str, section: str) -> tuple[QFrame, QHBoxLayout]:
        group = QFrame()
        group.setObjectName("navigationGroup")
        group.setProperty("section", section)
        group.setFixedHeight(42)
        layout = QHBoxLayout(group)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(2)

        label = QLabel(title)
        label.setObjectName("navigationGroupLabel")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        return group, layout

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
        self.view_title = QLabel("基本面消息")
        self.view_title.setObjectName("viewTitle")
        self.view_subtitle = QLabel("按去重后的有效消息事件排序")
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

        self.research_table_model = ResearchTableModel(self)
        self.research_proxy = ResearchProxyModel(self)
        self.research_proxy.setSourceModel(self.research_table_model)
        self.research_proxy.rowsInserted.connect(self._update_result_count)
        self.research_proxy.rowsRemoved.connect(self._update_result_count)
        self.research_proxy.modelReset.connect(self._update_result_count)

        self.industry_table_model = IndustryTableModel(self)

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
        self.research_detail_panel = ResearchDetailPanel()
        self.research_detail_panel.close_requested.connect(
            lambda: self._set_detail_visible(False)
        )
        self.research_detail_panel.open_url_requested.connect(self._open_url)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(self.detail_panel)
        self.detail_stack.addWidget(self.research_detail_panel)

        self.industry_detail_panel = IndustryDetailPanel(self.storage)
        self.industry_detail_panel.close_requested.connect(
            lambda: self._set_detail_visible(False)
        )
        self.industry_detail_panel.open_url_requested.connect(self._open_url)
        self.detail_stack.addWidget(self.industry_detail_panel)

        self.splitter.addWidget(table_panel)
        self.splitter.addWidget(self.detail_stack)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([820, 420])
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

        self.content_type_label = QLabel("类型")
        self.content_type_label.setObjectName("toolLabel")
        layout.addWidget(self.content_type_label)
        self.content_type_filter = ContentTypeFilterButton()
        self.content_type_filter.selection_changed.connect(self._set_content_type_filter)
        layout.addWidget(self.content_type_filter)

        self.platform_label = QLabel("平台")
        self.platform_label.setObjectName("toolLabel")
        layout.addWidget(self.platform_label)
        self.platform_filter = PlatformFilterButton()
        self.platform_filter.selection_changed.connect(self._set_platform_filter)
        layout.addWidget(self.platform_filter)

        self.event_type_filter = ContentTypeFilterButton()
        self.event_type_filter.selection_changed.connect(self._set_event_type_filter)
        layout.addWidget(self.event_type_filter)

        self.topic_filter = TopicFilterButton()
        self.topic_filter.selection_changed.connect(self._set_topic_filter)
        layout.addWidget(self.topic_filter)

        self.quality_filter = QualityFilterButton()
        self.quality_filter.selection_changed.connect(self._set_quality_filter)
        layout.addWidget(self.quality_filter)

        self.event_type_filter.hide()
        self.topic_filter.hide()
        self.quality_filter.hide()

        layout.addStretch(1)
        self.search_input = SearchLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("名称/代码")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(180)
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
        self.empty_hint = QLabel("刷新数据后，将在这里显示基本面消息、基本面互动与官方人气榜单。")
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
        popularity_rows = (*snapshot.popularity.popularity, *snapshot.popularity.surging)
        missing_names = {row.code for row in popularity_rows if "股票名称" in row.missing_quote_fields}
        if missing_names:
            restore_popularity_names(snapshot.popularity, self.storage.get_stock_names(missing_names))
        missing_codes = {row.code for row in popularity_rows if not row.industry}
        if missing_codes:
            cached_industries = self.storage.get_stock_industries(missing_codes)
            snapshot.popularity.popularity = [
                replace(row, industry=cached_industries.get(row.code) or row.industry)
                for row in snapshot.popularity.popularity
            ]
            snapshot.popularity.surging = [
                replace(row, industry=cached_industries.get(row.code) or row.industry)
                for row in snapshot.popularity.surging
            ]
        if snapshot.industry_heat.rows:
            cached = self.storage.get_articles_between(
                snapshot.industry_heat.window_start or snapshot.window_start,
                snapshot.industry_heat.window_end or snapshot.window_end,
            )
            by_url = {
                article.url or article.seq: article
                for article in (*snapshot.industry_heat.articles, *cached)
            }
            # Industry details need both the B research articles and ordinary
            # stock news for the industry -> stock -> news drill-down.
            snapshot.industry_heat.articles = list(by_url.values())
        self.industry_button.setEnabled(True)
        for key in ("pop", "surge"):
            self.source_buttons[key].setEnabled(snapshot.popularity.available)
        self.source_buttons["interaction"].setEnabled(
            bool(snapshot.interaction_coverages or snapshot.interaction_rankings)
        )
        if self.selected_source in {"pop", "surge"} and not snapshot.popularity.available:
            self.selected_source = "news"
        if self.selected_source == "interaction" and not (
            snapshot.interaction_coverages or snapshot.interaction_rankings
        ):
            self.selected_source = "news"
        self._render_selected_source()
        self.snapshot_changed.emit(snapshot)

    def _select_source(self, source_key: str) -> None:
        if source_key == "persist":
            source_key = self._persist_window
        if source_key not in {"news", "interaction", "pop", "surge", INDUSTRY_SOURCE_KEY} | set(RESEARCH_SOURCE_KEYS):
            return
        if source_key in {"pop", "surge"} and (not self.snapshot or not self.snapshot.popularity.available):
            return
        if (
            source_key == "interaction"
            and self.snapshot
            and not (self.snapshot.interaction_coverages or self.snapshot.interaction_rankings)
        ):
            return
        if source_key == self.selected_source:
            self._set_source_button_checked(source_key)
            return
        if self.snapshot or self.selected_source in RESEARCH_SOURCE_KEYS:
            self._save_table_state(self.selected_source)
        self.selected_source = source_key
        self.preferences.last_source = source_key
        if self.selected_source in RESEARCH_SOURCE_KEYS or self.snapshot:
            self._render_selected_source()
        else:
            self._render_empty_shell()

    def _set_source_button_checked(self, source_key: str) -> None:
        if source_key in self.source_buttons:
            self.source_buttons[source_key].setChecked(True)
        elif source_key in self.research_buttons:
            self.research_buttons[source_key].setChecked(True)
        elif source_key == INDUSTRY_SOURCE_KEY:
            self.industry_button.setChecked(True)
        else:
            self.source_buttons["news"].setChecked(True)

    def _selected_rows(
        self,
    ) -> list[RankingRow] | list[PopularityRankRow] | list[InteractionRankingRow]:
        if not self.snapshot:
            return []
        if self.selected_source == "pop":
            return self.snapshot.popularity.popularity
        if self.selected_source == "surge":
            return self.snapshot.popularity.surging
        if self.selected_source == "interaction":
            return self.snapshot.interaction_rankings
        if self.selected_source == INDUSTRY_SOURCE_KEY:
            return self.snapshot.industry_heat.rows
        return self.snapshot.rankings

    def _render_selected_source(self) -> None:
        source_key = self.selected_source
        if source_key == INDUSTRY_SOURCE_KEY:
            self._render_industry_source()
            return
        if source_key in RESEARCH_SOURCE_KEYS:
            self._render_research_source()
            return
        if not self.snapshot:
            self._render_empty_shell()
            return
        self.source_buttons[source_key].setChecked(True)
        if self._table_mode != "legacy":
            self.table.setModel(self.proxy_model)
            self.table.setItemDelegateForColumn(
                RankingTableModel.HEAT_COLUMN, self.heat_bar_delegate
            )
            self._table_mode = "legacy"
        rows = self._selected_rows()
        self.table_model.set_rows(rows, source_key=source_key)
        self._configure_table_columns()
        self._restore_table_state(source_key)

        is_news = source_key == "news"
        is_interaction = source_key == "interaction"
        self.industry_label.setVisible(is_news or is_interaction)
        self.industry_filter.setVisible(is_news or is_interaction)
        self.content_type_label.setVisible(is_news)
        self.content_type_filter.setVisible(is_news)
        self.platform_label.setVisible(is_interaction)
        self.platform_filter.setVisible(is_interaction)
        if is_news:
            tags = {
                tag
                for row in rows
                for tag in (getattr(row, "industry_tags", None) or (RankingProxyModel.UNCATEGORIZED_LABEL,))
            }
            self.industry_filter.set_options(tags)
            self.industry_filter.set_selected_tags(self._news_industry_tags, emit=False)
            self.proxy_model.set_industry_tags(self._news_industry_tags)
            content_types = {
                content_type
                for row in rows
                for content_type in (getattr(row, "content_types", None) or ())
            }
            self.content_type_filter.set_options(content_types)
            self.content_type_filter.set_selected_tags(self._news_content_types, emit=False)
            self.proxy_model.set_content_types(self._news_content_types)
            self.proxy_model.set_platforms(set())
        elif is_interaction:
            tags = {
                tag
                for row in rows
                for tag in (getattr(row, "industry_tags", None) or (RankingProxyModel.UNCATEGORIZED_LABEL,))
            }
            self.industry_filter.set_options(tags)
            self.industry_filter.set_selected_tags(self._interaction_industry_tags, emit=False)
            self.proxy_model.set_industry_tags(self._interaction_industry_tags)
            platforms = {
                platform
                for row in rows
                for platform in (getattr(row, "platforms", None) or ())
            }
            self.platform_filter.set_options(platforms)
            self.platform_filter.set_selected_tags(self._interaction_platform_tags, emit=False)
            self.proxy_model.set_platforms(self._interaction_platform_tags)
            self.proxy_model.set_channels(set())
            self.proxy_model.set_content_types(set())
        else:
            self.proxy_model.set_industry_tags(set())
            self.proxy_model.set_channels(set())
            self.proxy_model.set_content_types(set())
            self.proxy_model.set_platforms(set())

        self._render_header()
        self.content_stack.setCurrentWidget(self.table if rows else self.empty_state)
        if not rows:
            self.empty_title.setText(f"{SOURCE_LABELS[source_key]}暂无数据")
            self.empty_hint.setText("刷新后仍会保留上次成功结果；可在数据质量中查看当前状态。")
        self.detail_stack.setCurrentWidget(self.detail_panel)
        self.detail_panel.clear()
        self.snapshot_time_label.setText(f"快照 {format_datetime(self.snapshot.created_at)}")
        self._update_result_count()
        self._update_action_states()

    def _render_industry_source(self) -> None:
        if not self.snapshot:
            self._render_empty_shell()
            return
        self._set_source_button_checked(INDUSTRY_SOURCE_KEY)
        self._table_mode = "industry"
        self.table.setModel(self.industry_table_model)
        self.industry_table_model.set_rows(self.snapshot.industry_heat.rows)
        self._configure_table_columns()
        self._restore_table_state(INDUSTRY_SOURCE_KEY)
        self.industry_label.setVisible(False)
        self.industry_filter.setVisible(False)
        self.content_type_label.setVisible(False)
        self.content_type_filter.setVisible(False)
        self.platform_label.setVisible(False)
        self.platform_filter.setVisible(False)
        self.event_type_filter.setVisible(False)
        self.topic_filter.setVisible(False)
        self.quality_filter.setVisible(False)
        self._render_header()
        rows = self.snapshot.industry_heat.rows
        self.content_stack.setCurrentWidget(self.table if rows else self.empty_state)
        if not rows:
            self.empty_title.setText("行业热度暂无数据")
            self.empty_hint.setText("完成东方财富 Top100 与同花顺行业研究 24 小时覆盖后生成。")
        self.detail_stack.setCurrentWidget(self.industry_detail_panel)
        self.industry_detail_panel.clear()
        self.snapshot_time_label.setText(
            f"行业快照 {format_datetime(self.snapshot.industry_heat.snapshot_at)}"
            if self.snapshot.industry_heat.snapshot_at else "行业快照 暂无"
        )
        self._update_result_count()
        self._update_action_states()

    def _render_research_source(self) -> None:
        source_key = self.selected_source
        self._set_source_button_checked(source_key)
        coverage = research_coverage(self.settings, self.storage)
        self._research_coverage = coverage
        if source_key in {"confirm", "catalyst"}:
            board = (
                "confirmed_positive"
                if source_key == "confirm"
                else "potential_catalyst"
            )
            rows = load_short_term_rows(
                self.storage, board, coverage=coverage
            )
        elif source_key == "discovery":
            rows = load_discovery_rows(self.storage, coverage=coverage)
        self.table.setModel(self.research_proxy)
        self.table.setItemDelegateForColumn(4, None)
        self._table_mode = "research"
        self.research_table_model.set_rows(rows, source_key=source_key)
        self._configure_table_columns()
        self._restore_table_state(source_key)
        self._configure_research_filters(rows)
        self._render_research_header(rows, coverage)
        self.content_stack.setCurrentWidget(self.table if rows else self.empty_state)
        if not rows:
            self.empty_title.setText(f"{RESEARCH_VIEW_META[source_key][0]}暂无数据")
            self.empty_hint.setText(
                "刷新与历史回填完成后生成研究信号；冷启动、部分覆盖与日历降级状态见数据质量。"
            )
        self.detail_stack.setCurrentWidget(self.research_detail_panel)
        self.research_detail_panel.clear()
        self.snapshot_time_label.setText(
            f"研究数据 {format_datetime(coverage.last_success_at)}"
            if coverage.last_success_at
            else "研究数据 暂无同步"
        )
        self._update_result_count()
        self._update_action_states()

    def _configure_research_filters(self, rows) -> None:
        source_key = self.selected_source
        is_short = source_key in {"confirm", "catalyst"}
        is_discovery = source_key == "discovery"
        self.industry_label.setVisible(False)
        self.industry_filter.setVisible(False)
        self.content_type_label.setVisible(False)
        self.content_type_filter.setVisible(False)
        self.platform_label.setVisible(False)
        self.platform_filter.setVisible(False)
        self.event_type_filter.setVisible(is_short)
        self.topic_filter.setVisible(False)
        self.quality_filter.setVisible(not is_discovery)

        if is_short:
            options = sorted({row.event_type for row in rows if row.event_type})
            self.event_type_filter.set_options(options)
            self.event_type_filter.set_selected_tags(
                self._research_event_types, emit=False
            )
            self.research_proxy.set_event_types(self._research_event_types)
        else:
            self.research_proxy.set_event_types(set())
        self.research_proxy.set_topics(set())
        self.research_proxy.set_industries(set())
        self.quality_filter.set_options(set(QUALITY_STATE_BY_LABEL))
        selected_quality = {
            label
            for state, label in QUALITY_STATE_BY_LABEL.items()
            if state in self._research_quality_states
        }
        self.quality_filter.set_selected_tags(selected_quality, emit=False)
        self.research_proxy.set_quality_states(self._research_quality_states)

    def _render_research_header(self, rows, coverage) -> None:
        source_key = self.selected_source
        title, subtitle = RESEARCH_VIEW_META[source_key]
        self.view_title.setText(title)
        self.view_subtitle.setText(subtitle)
        state = research_coverage_state(coverage, has_rows=bool(rows))
        state_label = COVERAGE_STATE_LABELS.get(state, state)
        if source_key in {"confirm", "catalyst"}:
            extractors = sorted(
                {row.extractor_label for row in rows if row.extractor_label}
            )
            values = (
                ("结果", f"{len(rows)} 只"),
                ("抽取方式", "、".join(extractors) if extractors else "—"),
                ("质量状态", state_label),
                (
                    "同步时间",
                    coverage.last_success_at.strftime("%m-%d %H:%M")
                    if coverage.last_success_at
                    else "—",
                ),
            )
        elif source_key == "discovery":
            pending = sum(1 for row in rows if row.parse_status == "pending_attachment")
            awaiting = sum(
                1 for row in rows if row.parse_status == "awaiting_review"
            )
            failed = sum(
                1
                for row in rows
                if row.parse_status in ("empty_text", "failed")
            )
            values = (
                ("结果", f"{len(rows)} 条"),
                ("待解析", f"{pending} 条"),
                ("待核验", f"{awaiting} 条"),
                ("解析失败", f"{failed} 条"),
            )
        else:
            values = (
                ("结果", f"{len(rows)} 只"),
                ("质量状态", state_label),
                (
                    "同步时间",
                    coverage.last_success_at.strftime("%m-%d %H:%M")
                    if coverage.last_success_at
                    else "—",
                ),
            )
        for chip, (label, value) in zip(self.kpi_chips, values):
            chip.label.setText(label)
            chip.set_value(value)

        if source_key == "discovery":
            self.quality_label.setText(
                build_discovery_quality(self.settings, self.storage, coverage=coverage)
            )
        else:
            coverage_text = (
                f"请求窗口 {coverage.requested_start} 起 · 实际覆盖 "
                f"{coverage.covered_start or '—'} ~ {coverage.covered_end or '—'} · "
                f"覆盖交易日 {coverage.trading_days_covered} · "
                f"已扫描来源 {coverage.sources_scanned}/{coverage.sources_total}"
            )
            if coverage.calendar_fallback:
                coverage_text += " · 日历降级（周一至周五）"
            if not coverage.reached_cutoff:
                coverage_text += " · 未到达时间边界"
            if coverage.provisional:
                coverage_text += " · 冷启动/暂定"
            if coverage.error:
                coverage_text += f" · ⚠ {coverage.error}"
            self.quality_label.setText(coverage_text)
        if not coverage.last_success_at or coverage.provisional or not coverage.reached_cutoff:
            self._set_freshness("冷启动" if not coverage.last_success_at else "部分覆盖", "stale")
        else:
            self._set_freshness(
                f"更新于 {coverage.last_success_at.strftime('%H:%M')}", "fresh"
            )

    def _render_empty_shell(self) -> None:
        self._set_source_button_checked(self.selected_source)
        for key in ("pop", "surge"):
            self.source_buttons[key].setEnabled(False)
        self.source_buttons["interaction"].setEnabled(False)
        self.content_stack.setCurrentWidget(self.empty_state)
        if self._table_mode != "legacy":
            self.table.setModel(self.proxy_model)
            self.table.setItemDelegateForColumn(
                RankingTableModel.HEAT_COLUMN, self.heat_bar_delegate
            )
            self._table_mode = "legacy"
        self.industry_table_model.set_rows([])
        self.table_model.set_rows([], source_key="news")
        self.view_title.setText(SOURCE_LABELS.get(self.selected_source, "基本面消息"))
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
        if source_key == INDUSTRY_SOURCE_KEY:
            heat = self.snapshot.industry_heat
            self.view_title.setText("行业热度")
            self.view_subtitle.setText(
                "东方财富综合人气 Top100 行业覆盖 A + 同花顺行业研究 24 小时文章 B · 不使用飙升榜"
            )
            state = heat.source_status if heat.mapping_status == "complete" else "暂定/部分映射"
            values = (
                ("结果", f"{len(rows)} 个行业"),
                ("A覆盖", f"{heat.top100_mapped}/{heat.top100_total}"),
                ("B文章", str(heat.research_article_mapped)),
                ("状态", state),
            )
            for chip, (label, value) in zip(self.kpi_chips, values):
                chip.label.setText(label)
                chip.set_value(value)
            self.quality_label.setText(
                f"A映射覆盖率 {heat.mapping_coverage * 100:.2f}% · "
                f"B归因：显式标签 {heat.explicit_article_count} / 概念规则 {heat.concept_article_count} / "
                f"股票回退 {heat.stock_fallback_article_count} · 未映射 {heat.unmapped_article_count}"
                f"（未知标签 {heat.unknown_label_article_count}、无行业证据 {heat.no_evidence_article_count}、"
                f"未知概念 {heat.unknown_concept_article_count}、"
                f"股票行业不兼容 {heat.stock_industry_unmapped_article_count}） · "
                f"来源状态 {heat.source_status}{(' · ' + heat.source_error) if heat.source_error else ''}"
            )
            self._set_freshness(
                "完整结果" if heat.source_status == "complete" and heat.mapping_status == "complete" else "暂定/部分覆盖",
                "fresh" if heat.source_status == "complete" and heat.mapping_status == "complete" else "stale",
            )
            return
        self.view_title.setText(SOURCE_LABELS[source_key])
        if source_key == "news":
            stats = self.snapshot.stats
            total_coverage = len(self.snapshot.coverages)
            good_coverage = sum(
                1 for coverage in self.snapshot.coverages if coverage.reached_cutoff and not coverage.error
            )
            self.view_subtitle.setText(
                "按去重后的有效消息事件排序 · 同花顺公开新闻"
            )
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
                f"未映射 {stats.get('unmapped', 0)} · 有效事件 {stats.get('events', 0)} · "
                f"缓存来源 {stats.get('news_sources_cached', 0)}"
            )
            if self.snapshot.partial:
                self._set_freshness("部分覆盖", "stale")
            else:
                self._set_freshness(f"更新于 {self.snapshot.created_at.strftime('%H:%M')}", "fresh")
        elif source_key == "interaction":
            stats = self.snapshot.stats
            total_coverage = len(self.snapshot.interaction_coverages)
            good_coverage = sum(
                1
                for coverage in self.snapshot.interaction_coverages
                if coverage.reached_cutoff and not coverage.error
            )
            self.view_subtitle.setText(
                "官方问答代理指标 · 深交所互动易 + 上证e互动（仅沪深市场）· "
                "只统计已回复提问，按回复时间计窗口"
            )
            values = (
                ("结果", f"{len(rows)} 只"),
                ("有效提问", str(stats.get("interaction_unique", 0))),
                ("来源覆盖", f"{good_coverage}/{total_coverage}" if total_coverage else "—"),
                ("观察窗口", f"{self.settings.window_hours}h"),
            )
            for chip, (label, value) in zip(self.kpi_chips, values):
                chip.label.setText(label)
                chip.set_value(value)
            platform_errors = [
                coverage
                for coverage in self.snapshot.interaction_coverages
                if coverage.error
            ]
            cached_count = stats.get("interaction_sources_cached", 0)
            self.quality_label.setText(
                f"原始问答 {stats.get('interaction_records', 0)} · 过滤 {stats.get('interaction_filtered', 0)} · "
                f"去重后 {stats.get('interaction_unique', 0)} · 覆盖股票 {stats.get('interaction_ranked_stocks', 0)} · "
                f"缓存来源 {cached_count}"
            )
            if platform_errors:
                details = "；".join(
                    f"{item.source_name}：{item.error}" for item in platform_errors
                )
                self.quality_label.setText(self.quality_label.text() + f" · ⚠ {details}")
            if self.snapshot.partial:
                self._set_freshness("部分覆盖", "stale")
            else:
                self._set_freshness(f"更新于 {self.snapshot.created_at.strftime('%H:%M')}", "fresh")
        else:
            popularity = self.snapshot.popularity
            board_label = "官方关注度排名" if source_key == "pop" else "较昨日排名提升"
            self.view_subtitle.setText(f"东方财富官方{SOURCE_LABELS[source_key]} · {board_label}")
            success_text = popularity.success_at.strftime("%m-%d %H:%M") if popularity.success_at else "—"
            quote_rows = popularity.popularity if source_key == "pop" else popularity.surging
            incomplete = sum(row.quote_incomplete for row in quote_rows)
            state = "已过期" if popularity.is_stale else (
                ("部分数据" if incomplete else "正常") if popularity.available else "不可用"
            )
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
                if incomplete:
                    missing = sum(bool(row.missing_quote_fields) for row in quote_rows)
                    cached_names = sum(row.name_from_cache for row in quote_rows)
                    self.quality_label.setText(
                        f"官方排名可用；{missing} 只缺少名称或行情字段，{cached_names} 只名称使用本地缓存。"
                        "缺失行情显示 —，不使用旧价格填充；再次刷新可限频补取。"
                    )
                    self._set_freshness("行情待补全", "stale")
                else:
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
        self.research_proxy.set_query(value)
        self._update_result_count()

    def _set_industry_filter(self, tags: set[str]) -> None:
        if self.selected_source == "news":
            self._news_industry_tags = set(tags)
            self.proxy_model.set_industry_tags(tags)
        elif self.selected_source == "interaction":
            self._interaction_industry_tags = set(tags)
            self.proxy_model.set_industry_tags(tags)
        self._update_result_count()

    def _set_content_type_filter(self, content_types: set[str]) -> None:
        self._news_content_types = set(content_types)
        if self.selected_source == "news":
            self.proxy_model.set_content_types(content_types)
        self._update_result_count()

    def _set_event_type_filter(self, event_types: set[str]) -> None:
        self._research_event_types = set(event_types)
        if self.selected_source in {"confirm", "catalyst"}:
            self.research_proxy.set_event_types(event_types)
        self._update_result_count()

    def _set_topic_filter(self, topics: set[str]) -> None:
        self._research_topics = set(topics)
        self._update_result_count()

    def _set_quality_filter(self, labels: set[str]) -> None:
        states = {
            QUALITY_STATE_BY_LABEL[label]
            for label in labels
            if label in QUALITY_STATE_BY_LABEL
        }
        self._research_quality_states = states
        if self.selected_source in RESEARCH_SOURCE_KEYS:
            self.research_proxy.set_quality_states(states)
        self._update_result_count()

    def _set_platform_filter(self, platforms: set[str]) -> None:
        self._interaction_platform_tags = set(platforms)
        if self.selected_source == "interaction":
            self.proxy_model.set_platforms(platforms)
        self._update_result_count()

    def clear_filters(self) -> None:
        self.search_input.clear()
        if self.selected_source == "news":
            self._news_industry_tags.clear()
            self.industry_filter.set_selected_tags(set())
            self._news_content_types.clear()
            self.content_type_filter.set_selected_tags(set())
        elif self.selected_source == "interaction":
            self._interaction_industry_tags.clear()
            self.industry_filter.set_selected_tags(set())
            self._interaction_platform_tags.clear()
            self.platform_filter.set_selected_tags(set())
        elif self.selected_source in RESEARCH_SOURCE_KEYS:
            self._research_event_types.clear()
            self.event_type_filter.set_selected_tags(set())
            self._research_topics.clear()
            self.topic_filter.set_selected_tags(set())
            self._research_industries.clear()
            self.industry_filter.set_selected_tags(set())
            self._research_quality_states.clear()
            self.quality_filter.set_selected_tags(set())
            self.research_proxy.set_event_types(set())
            self.research_proxy.set_topics(set())
            self.research_proxy.set_industries(set())
            self.research_proxy.set_quality_states(set())
        self._update_result_count()

    def _update_result_count(self, *_: object) -> None:
        if getattr(self, "_table_mode", "legacy") == "industry":
            model = self.industry_table_model
        else:
            model = (
                self.research_proxy
                if getattr(self, "_table_mode", "legacy") == "research"
                else (self.proxy_model if hasattr(self, "proxy_model") else None)
            )
        count = model.rowCount() if model is not None else 0
        self.result_count_label.setText(f"{count} 个行业" if self._table_mode == "industry" else f"{count} 只股票")
        if hasattr(self, "heat_bar_delegate") and self._table_mode == "legacy":
            self.heat_bar_delegate.set_maximum(self.proxy_model.maximum_filtered_heat())
        if hasattr(self, "export_action"):
            self._update_action_states()

    def _configure_table_columns(self) -> None:
        if self.selected_source == INDUSTRY_SOURCE_KEY:
            header = self.table.horizontalHeader()
            for column in range(self.industry_table_model.columnCount()):
                header.setSectionResizeMode(column, QHeaderView.Interactive)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            for column, width in ((0, 58), (2, 82), (3, 72), (4, 84), (5, 72), (6, 84), (7, 150)):
                self.table.setColumnWidth(column, width)
            self._apply_density()
            return
        header = self.table.horizontalHeader()
        model = self.table.model()
        column_count = model.columnCount() if model is not None else self.table_model.columnCount()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in range(2, column_count):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 58)
        self.table.setColumnWidth(2, 82)
        if self.selected_source in {"confirm", "catalyst"}:
            self.table.setColumnWidth(3, 100)
            self.table.setColumnWidth(4, 220)
        elif self.selected_source == "discovery":
            self.table.setColumnWidth(3, 100)
            self.table.setColumnWidth(4, 260)
            self.table.setColumnWidth(5, 150)
            self.table.setColumnWidth(9, 110)
            self.table.setColumnWidth(10, 90)
        elif self.selected_source == "pop":
            self.table.setColumnWidth(RankingTableModel.POPULARITY_INDUSTRY_COLUMN, 120)
            self.table.setColumnWidth(RankingTableModel.POPULARITY_CODE_COLUMN, 82)
            self.table.setColumnWidth(4, 96)
            self.table.setColumnWidth(5, PERCENT_COLUMN_WIDTH)
        elif self.selected_source == "surge":
            self.table.setColumnWidth(RankingTableModel.POPULARITY_INDUSTRY_COLUMN, 120)
            self.table.setColumnWidth(RankingTableModel.POPULARITY_CODE_COLUMN, 82)
            self.table.setColumnWidth(4, 110)
            self.table.setColumnWidth(5, 96)
            self.table.setColumnWidth(6, PERCENT_COLUMN_WIDTH)
        elif self.selected_source == "interaction":
            self.table.setColumnWidth(RankingTableModel.INDUSTRY_COLUMN, 145)
            self.table.setColumnWidth(RankingTableModel.HEAT_COLUMN, 88)
            self.table.setColumnWidth(5, 88)
            self.table.setColumnWidth(6, 92)
            self.table.setColumnWidth(7, 145)
        else:
            self.table.setColumnWidth(RankingTableModel.INDUSTRY_COLUMN, 145)
            self.table.setColumnWidth(RankingTableModel.HEAT_COLUMN, 88)
            self.table.setColumnWidth(5, 88)
            self.table.setColumnWidth(6, 145)
        self._apply_density()

    def _apply_density(self) -> None:
        self.table.verticalHeader().setDefaultSectionSize(36 if self.preferences.density == "compact" else 44)

    @staticmethod
    def _table_pref_key(source_key: str) -> str:
        # The internal news view key moved from "ths" to "news"; reuse the old
        # saved column layout so existing users keep their customizations.
        return "ths" if source_key == "news" else source_key

    def _save_table_state(self, source_key: str) -> None:
        if self._restoring_table or not hasattr(self, "table"):
            return
        header = self.table.horizontalHeader()
        pref_key = self._table_pref_key(source_key)
        self.preferences.set_header_state(pref_key, header.saveState())
        self.preferences.set_sort(
            pref_key,
            header.sortIndicatorSection(),
            header.sortIndicatorOrder().value,
        )

    def _restore_table_state(self, source_key: str) -> None:
        self._restoring_table = True
        header = self.table.horizontalHeader()
        state = self.preferences.header_state(self._table_pref_key(source_key))
        if not state.isEmpty():
            header.restoreState(state)
            percent_column = 5 if source_key == "pop" else (6 if source_key == "surge" else None)
            if percent_column is not None and header.sectionSize(percent_column) < PERCENT_COLUMN_WIDTH:
                header.resizeSection(percent_column, PERCENT_COLUMN_WIDTH)
        column, order = self.preferences.sort(self._table_pref_key(source_key))
        model = self.table.model()
        column_count = model.columnCount() if model is not None else self.table_model.columnCount()
        if not 0 <= column < column_count:
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
        if isinstance(row, IndustryHeatRow) and self.snapshot:
            self.detail_stack.setCurrentWidget(self.industry_detail_panel)
            self.industry_detail_panel.set_row(row, self.snapshot.industry_heat)
        elif isinstance(row, RankingRow) and self.snapshot:
            self.detail_stack.setCurrentWidget(self.detail_panel)
            self.detail_panel.set_news(row, self.snapshot.events)
        elif isinstance(row, InteractionRankingRow) and self.snapshot:
            self.detail_stack.setCurrentWidget(self.detail_panel)
            self.detail_panel.set_interaction(row, self.snapshot.interactions)
        elif isinstance(row, PopularityRankRow):
            self.detail_stack.setCurrentWidget(self.detail_panel)
            articles = self.snapshot.events if self.snapshot else []
            flat_articles = [article for event in articles for article in event.articles]
            if self.snapshot:
                flat_articles.extend(
                    article for article in self.storage.get_articles_between(
                        self.snapshot.window_start, self.snapshot.window_end
                    )
                    if article.channel_key != "industry_research"
                )
            unique = {article.url: article for article in flat_articles if article.url}
            self.detail_panel.set_popularity_articles(row, self.selected_source, list(unique.values()))
        elif isinstance(row, ShortTermViewRow):
            self.detail_stack.setCurrentWidget(self.research_detail_panel)
            detail = load_event_detail(self.storage, row.event_id, row.stock_code)
            if detail is not None:
                self.research_detail_panel.set_short_term(
                    detail,
                    COVERAGE_STATE_LABELS.get(row.quality_state, row.quality_state),
                )
            else:
                self.research_detail_panel.clear()
        elif isinstance(row, DiscoveryViewRow):
            self.detail_stack.setCurrentWidget(self.research_detail_panel)
            self.research_detail_panel.set_discovery(
                row,
                COVERAGE_STATE_LABELS.get(row.quality_state, row.quality_state),
            )
        else:
            self.detail_stack.setCurrentWidget(self.detail_panel)
            self.detail_panel.clear()
        self._update_action_states()

    def _current_row(
        self,
    ) -> (
        RankingRow
        | PopularityRankRow
        | InteractionRankingRow
        | IndustryHeatRow
        | ShortTermViewRow
        | DiscoveryViewRow
        | None
    ):
        index = self.table.currentIndex()
        if not index.isValid():
            return None
        if self._table_mode == "industry":
            return self.industry_table_model.row_at(index.row())
        if self._table_mode == "research":
            source_index = self.research_proxy.mapToSource(index)
            return self.research_table_model.row_at(source_index.row())
        source_index = self.proxy_model.mapToSource(index)
        return self.table_model.row_at(source_index.row())

    def activate_selected(self) -> None:
        if self._current_row() is None:
            return
        if not self.detail_stack.isVisible():
            self._set_detail_visible(True)
            self._selection_changed()
        panel = self._active_detail_panel()
        if isinstance(panel, IndustryDetailPanel):
            panel._open_selected()
        else:
            panel.open_primary()

    def _active_detail_panel(self):
        if self.detail_stack.currentWidget() is self.industry_detail_panel:
            return self.industry_detail_panel
        return (
            self.research_detail_panel
            if self.detail_stack.currentWidget() is self.research_detail_panel
            else self.detail_panel
        )

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
        primary = menu.addAction("查看详情")
        if isinstance(row, PopularityRankRow):
            primary.setText("打开东方财富官方页")
            primary.triggered.connect(self.detail_panel.open_official)
        else:
            primary.triggered.connect(lambda: self._set_detail_visible(True))
        copy_identity = menu.addAction(icon("copy"), "复制股票名称和代码")
        name, code = self._stock_identity(row)
        copy_identity.triggered.connect(
            lambda: self._copy_text(f"{name}\t{code}", "股票信息已复制")
        )
        menu.addAction(self.copy_row_action)
        menu.addSeparator()
        menu.addAction(self.export_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    @staticmethod
    def _stock_identity(row) -> tuple[str, str]:
        if isinstance(row, IndustryHeatRow):
            return row.industry, ""
        if isinstance(row, ShortTermViewRow):
            return row.stock_name, row.stock_code
        return row.name, row.code

    def _set_detail_visible(self, visible: bool) -> None:
        self.detail_stack.setVisible(visible)
        self.toggle_detail_action.setChecked(visible)
        self.preferences.detail_visible = visible
        if visible and self._current_row() is not None:
            self._selection_changed()

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    # ---- export and clipboard ----------------------------------------

    def _visible_rows(self) -> list:
        rows: list = []
        if self._table_mode == "industry":
            return list(self.industry_table_model.rows)
        if self._table_mode == "research":
            proxy = self.research_proxy
            source_model = self.research_table_model
        else:
            proxy = self.proxy_model
            source_model = self.table_model
        for proxy_row in range(proxy.rowCount()):
            source_index = proxy.mapToSource(proxy.index(proxy_row, 0))
            row = source_model.row_at(source_index.row())
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
        credential_store = AiCredentialStore(self.settings.app_root)
        ai_has_credential = bool(credential_store.load())
        dialog = SettingsDialog(
            window_hours=self.settings.window_hours,
            auto_refresh=self.preferences.auto_refresh,
            density=self.preferences.density,
            retention_days=self.settings.retention_days,
            data_dir=self.settings.app_root,
            ai_enabled=self.preferences.ai_enabled,
            ai_base_url=self.preferences.ai_base_url,
            ai_model=self.preferences.ai_model,
            ai_timeout_seconds=self.preferences.ai_timeout_seconds,
            ai_has_credential=ai_has_credential,
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
        self.settings.ai_enabled = bool(values["ai_enabled"])
        self.settings.ai_base_url = str(values["ai_base_url"])
        self.settings.ai_model = str(values["ai_model"])
        self.settings.ai_timeout_seconds = float(values["ai_timeout_seconds"])
        self.preferences.ai_enabled = self.settings.ai_enabled
        self.preferences.ai_base_url = self.settings.ai_base_url
        self.preferences.ai_model = self.settings.ai_model
        self.preferences.ai_timeout_seconds = self.settings.ai_timeout_seconds
        if bool(values.get("ai_credential_cleared", False)):
            credential_store.clear()
        elif str(values.get("ai_api_key") or "").strip():
            credential_store.save(str(values["ai_api_key"]))
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
        try:
            coverage = research_coverage(self.settings, self.storage)
            coverage_text = (
                f"覆盖交易日 {coverage.trading_days_covered} · "
                f"来源 {coverage.sources_scanned}/{coverage.sources_total} · "
                f"到达边界 {coverage.reached_cutoff} · "
                f"日历降级 {coverage.calendar_fallback} · "
                f"暂定 {coverage.provisional}"
            )
        except Exception:  # noqa: BLE001 - diagnostics must never crash
            coverage_text = "覆盖状态不可用"
        ai_text = (
            f"AI 增强：{'开启' if self.preferences.ai_enabled else '关闭'} · "
            f"模型 {self.preferences.ai_model or '未配置'}"
        )
        return "\n".join(
            (
                f"{APP_NAME} {APP_VERSION}",
                f"系统：{platform.platform()}",
                f"数据目录：{self.settings.app_root}",
                f"数据库：{self.storage.database_path} ({stats.database_bytes / 1024:.1f} KiB)",
                f"缓存文章：{stats.article_count}",
                f"历史快照：{stats.snapshot_count}",
                f"研究文档：{stats.source_document_count}",
                f"事件簇：{stats.event_cluster_count}",
                f"机构实体：{stats.institution_count}",
                f"调研活动：{stats.research_activity_count}",
                f"待核验候选：{stats.discovery_candidate_count}",
                f"交易日历：{stats.trading_day_count}",
                f"覆盖 manifest：{stats.source_manifest_count}",
                f"政策文档：{stats.policy_document_count}",
                f"OCR 页：{stats.ocr_page_count}",
                f"覆盖快照：{stats.coverage_snapshot_count}",
                f"事件候选事实：{stats.event_claim_count}",
                f"参与者原始提及：{stats.participant_mention_count}",
                f"披露总数记录：{stats.reported_participant_count_count}",
                f"活动发生日：{stats.activity_occurrence_count}",
                f"参与者发生日：{stats.participant_occurrence_count}",
                f"逐来源窗口覆盖：{stats.source_window_coverage_count}",
                f"研究覆盖：{coverage_text}",
                ai_text,
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
            <h3>基本面消息榜</h3>
            <p>来自<b>同花顺</b>公开新闻栏目。按观察窗口收集，过滤资金流、融资余额等固定模板（每条过滤均保留原因）；同一股票、6 小时内且规范化标题相似度不低于 90% 的报道合并为同一事件。排序：有效事件数降序 → 最近事件时间降序 → 股票代码升序。原始篇数和来源数仅用于解释，不参与加权。</p>
            <h3>基本面互动榜（官方问答代理指标）</h3>
            <p>来自<b>深交所互动易</b>与<b>上证 e 互动</b>的全市场最新问答流（仅覆盖沪深市场，北交所未纳入本版所选官方问答平台）。<b>只有公司已回复的提问才计为有效提问，且提问的统计时间定义为回复时间</b>；未回复提问不计入榜单。按平台问答 ID 去重，同一股票 24 小时内（按回复时间）完全相同的规范化问题再次去重；仅过滤空内容、非 A 股、明显垃圾内容以及“纯走势/庄家/盘口”且没有经营、财务、治理或重大事项语义的问题。排序：有效提问数降序 → 最近回复时间降序 → 股票代码升序。该榜是官方公司问答的代理指标，不声称覆盖普通社区的全部基本面讨论。</p>
            <h3>综合人气榜与飙升榜</h3>
            <p>来自<b>东方财富</b>公开官方榜单，保留官方口径独立展示；该榜综合访问、关注和社区互动，本身不等于基本面讨论。产品只展示官方排名、排名变化和公开行情字段，不推导未公开的热度分值或权重，也不合成主观总分。</p>
            <h3>确定性利好与潜在催化（研究信号）</h3>
            <p>来自<b>巨潮资讯、上交所与北交所</b>公开公司公告。先对公开文档做持久化事件聚类（同一股票、72 小时内的高置信相似内容合并，金额/客户/日期冲突时宁拆不并），再按十六类固定事件类型做规则抽取，输出正向机制、量化字段、确定性、意外性、新颖性与反证；确定性利好要求重大性 L≥2、确定性≥0.70、得分≥60 且无高度反证；潜在催化要求 L≥1、确定性≥0.40、得分≥35，并醒目标注“尚未落地”。`no_valid_signal` 是正常且高频的结果，产品不强迫每条信息生成利好。</p>
            <p><b>待核验</b>：所有公开列表项先进入发现层（财务报告、合同订单、审批客户、资本动作、产能项目、政策补贴、其他需核验披露），附件按“新调研资料 → 高优先级待核验事件 → 最旧普通待解析资料”循环下载；额度不足只标记延后。候选不计分、不称为利好，正文证据决定是否进入确定性利好或潜在催化榜。</p>
            <h3>行业热度</h3>
            <p>A 是东方财富综合人气 Top100 的一级行业覆盖，B 是同花顺“行业研究”最近 24 小时可解析文章数；两项分别使用并列平均秩分位，行业热度 = 50%×A分位 + 50%×B分位。飙升榜不参与 A。文章依次采用正文明确行业标签、同花顺结构化概念链接/固定高置信概念规则、正文明确关联股票的一级行业；泛化词“AI”“新能源”本身不触发行业。显式标签、概念规则、股票回退及各类未映射原因分别显示，不能可靠归集的样本保持为空。</p>
            <p>只有上海时间 18:00 后、东方财富 Top100 新鲜且行业研究 24 小时覆盖完整时，才写入当天首份不可变日快照。失败、部分覆盖或仅缓存结果仍可查看，但不进入历史趋势。行业热度只用于公开信息整理，不预测股价或收益。</p>
            <h3>可选 AI 增强</h3>
            <p>默认关闭；开启后仅向模型发送规则初筛后的事件代表文本，输出经同一数据契约严格校验，模型故障只降级当前事件（显示“规则降级”），不影响整榜。密钥使用 Windows DPAPI 加密保存在独立文件，不写入数据库、日志、导出或剪贴板。</p>
            <p>公告来源采用低频渐进回填并受页面与附件配额约束；机构活动来源和机构指标不再进入主动刷新。部分覆盖、来源失败、映射不足和附件失败都会在当前视图明确标注。</p>
            <p>官方榜单与新增来源低频读取并使用短期缓存；任何来源读取失败、沿用本地缓存或未到达窗口边界都会在界面“数据质量”中明确标注部分覆盖与过期状态。</p>
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
        if self.snapshot and self.selected_source in {"news", "interaction"}:
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
        self.selected_source = "news"
        self.preferences.last_source = "news"
        self._news_industry_tags.clear()
        self._news_content_types.clear()
        self._interaction_industry_tags.clear()
        self._interaction_platform_tags.clear()
        self._research_event_types.clear()
        self._research_topics.clear()
        self._research_industries.clear()
        self._research_quality_states.clear()
        self._research_coverage = None
        self.industry_filter.set_options(set())
        self.content_type_filter.set_options(set())
        self.platform_filter.set_options(set())
        self.event_type_filter.set_options(set())
        self.topic_filter.set_options(set())
        self.quality_filter.set_options(set())
        self.search_input.clear()
        self.detail_panel.clear()
        self.research_detail_panel.clear()
        self.research_table_model.set_rows([], source_key="confirm")
        self.research_proxy.set_event_types(set())
        self.research_proxy.set_topics(set())
        self.research_proxy.set_industries(set())
        self.research_proxy.set_quality_states(set())
        self._render_empty_shell()
        self.status_message.setText("本地数据已清除")

    def _update_action_states(self) -> None:
        model = self.table.model() if hasattr(self, "table") else None
        has_rows = model is not None and model.rowCount() > 0
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
        self.preferences.persist_window = self._persist_window
        self.preferences.set_window_geometry(self.saveGeometry())
        self.preferences.set_splitter_state(self.splitter.saveState())
        self.preferences.sync()
        event.accept()
