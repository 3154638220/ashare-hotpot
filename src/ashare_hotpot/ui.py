from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRect, QSortFilterProxyModel, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
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
    QStackedWidget,
    QStatusBar,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .config import APP_NAME, APP_VERSION, AppSettings
from .models import NewsEvent, PopularityRankRow, RankingRow, Snapshot
from .service import RefreshService
from .storage import Storage
from .worker import RefreshWorker


COLOR_BACKGROUND = "#0d1118"
COLOR_SURFACE = "#151b25"
COLOR_SURFACE_RAISED = "#1b2330"
COLOR_BORDER = "#2a3444"
COLOR_TEXT = "#edf2f8"
COLOR_MUTED = "#96a4b7"
COLOR_LINK = "#86b7ff"
COLOR_HOT = "#ff6677"
COLOR_SUCCESS = "#4ed6a5"
COLOR_WARNING = "#f6c667"


DARK_STYLESHEET = f"""
QMainWindow, QDialog {{
    background: {COLOR_BACKGROUND};
}}
QWidget {{
    color: {COLOR_TEXT};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}}
QFrame#topBar, QFrame#overviewFrame, QFrame#workbenchFrame, QFrame#activityFrame, QFrame#emptyState {{
    background: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 12px;
}}
QFrame#brandMark {{
    background: #f04f67;
    border: none;
    border-radius: 12px;
}}
QLabel#brandGlyph {{
    color: white;
    font-size: 21px;
    font-weight: 800;
}}
QLabel#appTitle {{
    color: {COLOR_TEXT};
    font-size: 25px;
    font-weight: 750;
}}
QLabel#sectionTitle {{
    color: {COLOR_TEXT};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#mutedLabel, QLabel#sectionKicker, QLabel#metricTitle, QLabel#metricDetail, QLabel#windowLabel, QLabel#statsLabel {{
    color: {COLOR_MUTED};
}}
QLabel#sectionKicker {{
    color: #7d8da4;
    font-size: 11px;
    font-weight: 700;
}}
QFrame#metricCard {{
    background: {COLOR_SURFACE_RAISED};
    border: 1px solid #2a3748;
    border-radius: 9px;
}}
QFrame#metricCard[selected="true"] {{ background: #202d3d; border-color: {COLOR_LINK}; }}
QFrame#metricCard[accent="hot"] {{ border-left: 3px solid {COLOR_HOT}; }}
QFrame#metricCard[accent="link"] {{ border-left: 3px solid {COLOR_LINK}; }}
QFrame#metricCard[accent="success"] {{ border-left: 3px solid {COLOR_SUCCESS}; }}
QFrame#metricCard[accent="warning"] {{ border-left: 3px solid {COLOR_WARNING}; }}
QLabel#metricValue {{
    color: {COLOR_TEXT};
    font-size: 22px;
    font-weight: 750;
}}
QLabel#metricValue[status="success"] {{ color: {COLOR_SUCCESS}; }}
QLabel#metricValue[status="warning"] {{ color: {COLOR_WARNING}; }}
QLabel#coverageLabel {{
    background: #1a2330;
    border: 1px solid #2a3748;
    border-radius: 7px;
    color: {COLOR_MUTED};
    padding: 8px 10px;
}}
QLabel#coverageLabel[state="ok"] {{
    color: {COLOR_SUCCESS};
    border-color: #245943;
    background: #13261f;
}}
QLabel#coverageLabel[state="warning"] {{
    color: {COLOR_WARNING};
    border-color: #655228;
    background: #2a2518;
}}
QLabel#coverageLabel[state="empty"] {{ color: {COLOR_MUTED}; }}
QLabel#activityLabel {{ color: {COLOR_MUTED}; }}
QLabel#activityLabel[active="true"] {{ color: {COLOR_TEXT}; }}
QPushButton, QToolButton {{
    min-height: 32px;
    padding: 0 14px;
    background: #202a38;
    border: 1px solid #344154;
    border-radius: 7px;
    color: {COLOR_TEXT};
    font-weight: 600;
}}
QPushButton:hover, QToolButton:hover {{ background: #2a3646; border-color: #52657e; }}
QPushButton:pressed, QToolButton:pressed {{ background: #151c26; }}
QPushButton:disabled, QToolButton:disabled {{ color: #647185; background: #17202c; border-color: #273241; }}
QPushButton#primaryButton {{
    background: #e85368;
    border-color: #e85368;
    color: white;
}}
QPushButton#primaryButton:hover {{ background: #fa6679; border-color: #fa6679; }}
QPushButton#emptyRefreshButton {{ min-width: 140px; }}
QLineEdit, QSpinBox {{
    min-height: 32px;
    padding: 0 10px;
    background: #101722;
    border: 1px solid #344154;
    border-radius: 7px;
    color: {COLOR_TEXT};
    selection-background-color: #34527b;
}}
QLineEdit:hover, QSpinBox:hover {{ border-color: #52657e; }}
QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {COLOR_LINK}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 17px;
    border: none;
    background: #202a38;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: #2b394b; }}
QScrollArea {{ background: #101722; border: 1px solid #344154; border-radius: 6px; }}
QCheckBox {{ padding: 5px 3px; }}
QCheckBox:hover {{ background: #1d2b3b; }}
QTableView, QTreeWidget {{
    background: #111822;
    alternate-background-color: #151e29;
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    color: {COLOR_TEXT};
    gridline-color: #273343;
    selection-background-color: #263c59;
    selection-color: {COLOR_TEXT};
    outline: 0;
}}
QTableView::item, QTreeWidget::item {{ padding: 0 8px; border: 0; }}
QTableView::item:hover, QTreeWidget::item:hover {{ background: #1d2b3b; }}
QTableView::item:selected, QTreeWidget::item:selected {{ background: #263c59; }}
QHeaderView::section {{
    background: #1b2634;
    border: 0;
    border-right: 1px solid #2c394a;
    border-bottom: 1px solid #344154;
    color: #bac7d8;
    padding: 9px 8px;
    font-size: 12px;
    font-weight: 700;
}}
QProgressBar {{
    min-height: 5px;
    border: none;
    border-radius: 3px;
    background: #263343;
}}
QProgressBar::chunk {{ background: {COLOR_HOT}; border-radius: 3px; }}
QStatusBar {{
    background: #111721;
    border-top: 1px solid #202b39;
    color: {COLOR_MUTED};
}}
QStatusBar::item {{ border: none; }}
QMenuBar {{ background: #111721; color: {COLOR_MUTED}; }}
QMenuBar::item {{ padding: 7px 10px; background: transparent; }}
QMenuBar::item:selected {{ color: {COLOR_TEXT}; background: #202a38; }}
QMenu {{ background: #18212d; color: {COLOR_TEXT}; border: 1px solid #334154; padding: 5px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: #293c55; }}
QMenu::separator {{ height: 1px; background: #2e3b4c; margin: 5px 8px; }}
QMenu#industryFilterMenu {{ background: #18212d; border: 1px solid #344154; padding: 0; }}
QWidget#industryFilterPanel {{ background: #18212d; color: {COLOR_TEXT}; }}
QScrollArea#industryFilterScroll {{ background: #101722; border: 1px solid #344154; border-radius: 6px; }}
QWidget#industryFilterOptions {{ background: #101722; color: {COLOR_TEXT}; }}
QCheckBox#industryFilterOption {{ background: #101722; color: {COLOR_TEXT}; padding: 6px 4px; border-radius: 4px; }}
QCheckBox#industryFilterOption:hover {{ background: #1d2b3b; }}
QCheckBox#industryFilterOption::indicator {{ width: 15px; height: 15px; border: 1px solid #607189; border-radius: 3px; background: #111822; }}
QCheckBox#industryFilterOption::indicator:checked {{ border-color: {COLOR_LINK}; background: {COLOR_LINK}; }}
QPushButton#industryFilterControl {{ min-height: 28px; background: #202a38; color: {COLOR_TEXT}; }}
QScrollBar:vertical {{ width: 10px; background: #111822; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 28px; background: #3a4a60; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: #52677f; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 10px; background: #111822; margin: 2px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; background: #3a4a60; border-radius: 5px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


def format_datetime(value: datetime | None, *, seconds: bool = False) -> str:
    if value is None:
        return "—"
    pattern = "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M"
    return value.strftime(pattern)


def format_price(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def format_percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def format_change(value: int | None) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"↑ {value}"
    if value < 0:
        return f"↓ {abs(value)}"
    return "0"


class MetricCard(QFrame):
    """A compact snapshot metric shown in the overview row."""

    clicked = Signal()

    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setProperty("accent", accent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("metricTitle")
        self.value_label = QLabel("—")
        self.value_label.setObjectName("metricValue")
        self.detail_label = QLabel("等待刷新")
        self.detail_label.setObjectName("metricDetail")
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)

    def set_value(self, value: str, detail: str, *, status: str | None = None) -> None:
        self.value_label.setText(value)
        self.detail_label.setText(detail)
        self.value_label.setProperty("status", status or "")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_accent(self, accent: str) -> None:
        self.setProperty("accent", accent)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HeatBarDelegate(QStyledItemDelegate):
    """Adds a subtle intensity indicator below the effective-mention count."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._maximum = 1

    @property
    def maximum(self) -> int:
        return self._maximum

    def set_maximum(self, value: int) -> None:
        self._maximum = max(1, value)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        super().paint(painter, option, index)
        value = index.data(Qt.UserRole)
        if not isinstance(value, int) or value <= 0:
            return
        ratio = min(1.0, value / self._maximum)
        available_width = max(0, option.rect.width() - 20)
        bar_width = max(3, int(available_width * ratio))
        bar_rect = QRect(option.rect.left() + 10, option.rect.bottom() - 6, bar_width, 2)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLOR_HOT))
        painter.drawRoundedRect(bar_rect, 1, 1)
        painter.restore()


class RankingTableModel(QAbstractTableModel):
    NEWS_HEADERS = ("排名", "股票名称", "代码", "所属行业", "有效提及", "原始篇数", "最近提及")
    POP_HEADERS = ("排名", "股票名称", "代码", "现价", "涨跌幅")
    SURGE_HEADERS = ("排名", "股票名称", "代码", "较昨日变动", "现价", "涨跌幅")
    STOCK_NAME_COLUMN = 1
    INDUSTRY_COLUMN = 3
    HEAT_COLUMN = 4

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.rows: list[RankingRow | PopularityRankRow] = []
        self.source_key = "ths"

    @property
    def headers(self) -> tuple[str, ...]:
        if self.source_key == "pop":
            return self.POP_HEADERS
        if self.source_key == "surge":
            return self.SURGE_HEADERS
        return self.NEWS_HEADERS

    def set_rows(self, rows: list[RankingRow] | list[PopularityRankRow], *, source_key: str = "ths") -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.source_key = source_key
        self.endResetModel()
        self.headerDataChanged.emit(Qt.Horizontal, 0, max(0, len(self.headers) - 1))

    def row_at(self, row: int) -> RankingRow | PopularityRankRow | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.headers):
            return self.headers[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        row = self.rows[index.row()]
        if isinstance(row, PopularityRankRow):
            if self.source_key == "surge":
                raw_values = (
                    row.rank,
                    row.name,
                    row.code,
                    row.change,
                    row.current_price,
                    row.change_percent,
                )
                display_values = (
                    str(row.rank),
                    row.name,
                    row.code,
                    format_change(row.change),
                    format_price(row.current_price),
                    format_percent(row.change_percent),
                )
            else:
                raw_values = (
                    row.rank,
                    row.name,
                    row.code,
                    row.current_price,
                    row.change_percent,
                )
                display_values = (
                    str(row.rank),
                    row.name,
                    row.code,
                    format_price(row.current_price),
                    format_percent(row.change_percent),
                )
        else:
            raw_values = (
                row.rank,
                row.name,
                row.code,
                row.industry_tags or ("未标注",),
                row.event_count,
                row.raw_article_count,
                row.latest_mention.timestamp(),
            )
            display_values = (
                str(row.rank),
                row.name,
                row.code,
                "、".join(row.industry_tags) if row.industry_tags else "未标注",
                str(row.event_count),
                str(row.raw_article_count),
                format_datetime(row.latest_mention),
            )
        if role == Qt.DisplayRole:
            return display_values[index.column()]
        if role == Qt.UserRole:
            return raw_values[index.column()]
        if role == Qt.TextAlignmentRole:
            if isinstance(row, PopularityRankRow):
                centered = {0, 2, 3, 4} if self.source_key == "pop" else {0, 2, 3, 4, 5}
            else:
                centered = {0, 2, 4, 5}
            if index.column() in centered:
                return int(Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)
        if role == Qt.ForegroundRole and index.column() == self.STOCK_NAME_COLUMN:
            return QColor(COLOR_LINK)
        if isinstance(row, PopularityRankRow):
            percent_column = 4 if self.source_key == "pop" else 5
            if index.column() == percent_column and row.change_percent is not None:
                if row.change_percent > 0:
                    return QColor(COLOR_HOT)
                if row.change_percent < 0:
                    return QColor(COLOR_SUCCESS)
        if role == Qt.ForegroundRole and index.column() == self.HEAT_COLUMN:
            return QColor(COLOR_HOT)
        if role == Qt.ForegroundRole and index.column() == 0 and row.rank <= 3:
            return QColor(COLOR_WARNING)
        if role == Qt.FontRole and index.column() in {self.STOCK_NAME_COLUMN, self.HEAT_COLUMN}:
            font = QFont()
            font.setBold(True)
            if index.column() == self.STOCK_NAME_COLUMN:
                font.setUnderline(True)
            return font
        return None


class IndustryFilterButton(QToolButton):
    """An Excel-like multi-select menu for the industry filter."""

    selection_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._selected_tags: set[str] = set()
        self._syncing = False

        self.setText("全部行业")
        self.setToolTip("按股票所属行业筛选榜单；可多选")
        self.setPopupMode(QToolButton.InstantPopup)
        self.setFixedWidth(156)

        menu = QMenu(self)
        menu.setObjectName("industryFilterMenu")
        menu.setFixedWidth(264)
        self.setMenu(menu)

        content = QWidget(menu)
        content.setObjectName("industryFilterPanel")
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setFixedWidth(246)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        self.select_all_button = QPushButton("全选")
        self.select_all_button.setObjectName("industryFilterControl")
        self.select_all_button.setFixedHeight(28)
        self.clear_button = QPushButton("清空")
        self.clear_button.setObjectName("industryFilterControl")
        self.clear_button.setFixedHeight(28)
        controls.addWidget(self.select_all_button)
        controls.addWidget(self.clear_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("industryFilterScroll")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setMinimumHeight(112)
        self._scroll_area.setMaximumHeight(248)
        self._options_widget = QWidget()
        self._options_widget.setObjectName("industryFilterOptions")
        self._options_widget.setAttribute(Qt.WA_StyledBackground, True)
        self._options_layout = QVBoxLayout(self._options_widget)
        self._options_layout.setContentsMargins(8, 5, 8, 5)
        self._options_layout.setSpacing(1)
        self._options_layout.addStretch(1)
        self._scroll_area.setWidget(self._options_widget)
        layout.addWidget(self._scroll_area)

        self.done_button = QPushButton("完成")
        self.done_button.setObjectName("industryFilterControl")
        self.done_button.setFixedHeight(28)
        layout.addWidget(self.done_button)

        widget_action = QWidgetAction(menu)
        widget_action.setDefaultWidget(content)
        menu.addAction(widget_action)

        self.select_all_button.clicked.connect(self._select_all)
        self.clear_button.clicked.connect(self._clear_selection)
        self.done_button.clicked.connect(menu.hide)

    @property
    def selected_tags(self) -> frozenset[str]:
        return frozenset(self._selected_tags)

    def set_options(self, tags: set[str]) -> None:
        available_tags = sorted(tags)
        self._selected_tags.intersection_update(available_tags)
        self._syncing = True
        while self._options_layout.count():
            item = self._options_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        self._checkboxes.clear()
        for tag in available_tags:
            checkbox = QCheckBox(tag)
            checkbox.setObjectName("industryFilterOption")
            checkbox.setChecked(tag in self._selected_tags)
            checkbox.toggled.connect(self._on_checkbox_toggled)
            self._options_layout.addWidget(checkbox)
            self._checkboxes[tag] = checkbox
        self._options_layout.addStretch(1)
        self._syncing = False
        self.select_all_button.setEnabled(bool(available_tags))
        self.clear_button.setEnabled(bool(self._selected_tags))
        self._update_button_text()

    def set_selected_tags(self, tags: set[str], *, emit: bool = True) -> None:
        selected_tags = set(tags).intersection(self._checkboxes)
        if selected_tags == self._selected_tags:
            return
        self._selected_tags = selected_tags
        self._syncing = True
        for tag, checkbox in self._checkboxes.items():
            checkbox.setChecked(tag in self._selected_tags)
        self._syncing = False
        self.clear_button.setEnabled(bool(self._selected_tags))
        self._update_button_text()
        if emit:
            self.selection_changed.emit(set(self._selected_tags))

    def _select_all(self) -> None:
        self.set_selected_tags(set(self._checkboxes))

    def _clear_selection(self) -> None:
        self.set_selected_tags(set())

    def _on_checkbox_toggled(self, _checked: bool) -> None:
        if self._syncing:
            return
        self._selected_tags = {
            tag for tag, checkbox in self._checkboxes.items() if checkbox.isChecked()
        }
        self.clear_button.setEnabled(bool(self._selected_tags))
        self._update_button_text()
        self.selection_changed.emit(set(self._selected_tags))

    def _update_button_text(self) -> None:
        if not self._selected_tags:
            self.setText("全部行业")
        elif len(self._selected_tags) == 1:
            self.setText(next(iter(self._selected_tags)))
        else:
            self.setText(f"已选 {len(self._selected_tags)} 个行业")


class RankingProxyModel(QSortFilterProxyModel):
    UNCATEGORIZED_LABEL = "未标注"

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._query = ""
        self._industry_tags: set[str] = set()
        self.setSortRole(Qt.UserRole)
        self.setDynamicSortFilter(True)

    def set_query(self, value: str) -> None:
        self._query = value.strip().lower()
        self.invalidateFilter()

    def set_industry_tags(self, values: set[str]) -> None:
        self._industry_tags = {value.strip() for value in values if value.strip()}
        self.invalidateFilter()

    def maximum_filtered_heat(self) -> int:
        model = self.sourceModel()
        if not isinstance(model, RankingTableModel):
            return 1
        return max((self._heat(row) for row in model.rows if self._matches(row)), default=1)

    @staticmethod
    def _heat(row: RankingRow | PopularityRankRow) -> int:
        if isinstance(row, PopularityRankRow):
            return 0
        return row.event_count

    def _matches(self, row: RankingRow | PopularityRankRow) -> bool:
        if self._query and self._query not in row.name.lower() and self._query not in row.code.lower():
            return False
        if not self._industry_tags:
            return True
        industry_tags = getattr(row, "industry_tags", ()) or (self.UNCATEGORIZED_LABEL,)
        return bool(set(industry_tags).intersection(self._industry_tags))

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if isinstance(model, RankingTableModel):
            row = model.row_at(source_row)
            return bool(row and self._matches(row))
        return True


class ArticleDetailDialog(QDialog):
    def __init__(self, row: RankingRow, events: list[NewsEvent], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{row.name}（{row.code}）有效提及文章")
        self.resize(920, 620)
        self.setStyleSheet(DARK_STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        heading_frame = QFrame()
        heading_frame.setObjectName("topBar")
        heading_layout = QHBoxLayout(heading_frame)
        heading_layout.setContentsMargins(16, 13, 16, 13)
        title_box = QVBoxLayout()
        title = QLabel(f"{row.name} · {row.code}")
        title.setObjectName("sectionTitle")
        hint = QLabel("每行对应一次去重后的有效提及；点击链接即可打开原文")
        hint.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(hint)
        heading_layout.addLayout(title_box)
        heading_layout.addStretch(1)
        count = QLabel(f"{row.event_count} 次有效提及")
        count.setObjectName("coverageLabel")
        heading_layout.addWidget(count)
        layout.addWidget(heading_frame)

        self.article_tree = QTreeWidget()
        self.article_tree.setColumnCount(3)
        self.article_tree.setHeaderLabels(["文章标题", "原文链接", "发布时间"])
        self.article_tree.setAlternatingRowColors(True)
        self.article_tree.setRootIsDecorated(False)
        self.article_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        event_map = {event.event_id: event for event in events}
        for event_id in row.event_ids:
            event = event_map.get(event_id)
            if not event:
                continue
            matching_articles = [
                article
                for article in event.articles
                if row.code in {stock.code for stock in article.stocks}
            ]
            article = matching_articles[0] if matching_articles else None
            if article is None and event.articles:
                article = event.articles[0]
            if article is None:
                continue
            item = QTreeWidgetItem(
                [
                    article.title,
                    article.url,
                    format_datetime(article.published_at, seconds=True),
                ]
            )
            item.setData(1, Qt.UserRole, article.url)
            item.setToolTip(0, article.url)
            item.setToolTip(1, "单击打开原文")
            link_font = item.font(1)
            link_font.setUnderline(True)
            item.setFont(1, link_font)
            item.setForeground(1, QColor(COLOR_LINK))
            self.article_tree.addTopLevelItem(item)
        self.article_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.article_tree.header().setSectionResizeMode(1, QHeaderView.Interactive)
        self.article_tree.header().setSectionResizeMode(2, QHeaderView.Interactive)
        self.article_tree.setColumnWidth(1, 320)
        self.article_tree.setColumnWidth(2, 154)
        self.article_tree.setTextElideMode(Qt.ElideMiddle)
        self.article_tree.itemClicked.connect(self._open_article)
        layout.addWidget(self.article_tree, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    @staticmethod
    def _open_article(item: QTreeWidgetItem, column: int) -> None:
        url = item.data(1, Qt.UserRole)
        if column == 1 and url:
            QDesktopServices.openUrl(QUrl(str(url)))


class MainWindow(QMainWindow):
    snapshot_changed = Signal(object)

    def __init__(self, settings: AppSettings, storage: Storage, service: RefreshService) -> None:
        super().__init__()
        self.settings = settings
        self.storage = storage
        self.service = service
        self.snapshot: Snapshot | None = None
        self.selected_source = "ths"
        self._thread: QThread | None = None
        self._worker: RefreshWorker | None = None

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(980, 680)
        self.resize(1180, 760)
        self._build_ui()
        self._build_menus()
        self._apply_styles()
        self.load_latest_snapshot()

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(14)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_overview())
        outer.addWidget(self._build_workbench(), 1)
        self.setCentralWidget(central)

        status = QStatusBar()
        disclaimer = QLabel("数据来源：同花顺公开新闻页面、东方财富公开官方人气榜 · 热度仅供信息整理")
        disclaimer.setObjectName("mutedLabel")
        status.addWidget(disclaimer, 1)
        status.addPermanentWidget(QLabel(APP_NAME))
        self.setStatusBar(status)

    def _build_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("topBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        mark = QFrame()
        mark.setObjectName("brandMark")
        mark.setFixedSize(48, 48)
        mark_layout = QVBoxLayout(mark)
        mark_layout.setContentsMargins(0, 0, 0, 0)
        glyph = QLabel("A")
        glyph.setObjectName("brandGlyph")
        glyph.setAlignment(Qt.AlignCenter)
        mark_layout.addWidget(glyph)
        layout.addWidget(mark)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("appTitle")
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("mutedLabel")
        self._update_subtitle()
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle_label)
        layout.addLayout(title_box)
        layout.addStretch(1)

        window_hours_label = QLabel("观察窗口")
        window_hours_label.setObjectName("mutedLabel")
        self.window_hours_input = QSpinBox()
        self.window_hours_input.setRange(1, 168)
        self.window_hours_input.setValue(self.settings.window_hours)
        self.window_hours_input.setSuffix(" 小时")
        self.window_hours_input.setFixedWidth(104)
        self.window_hours_input.setToolTip("选择本次刷新向前统计同花顺新闻的时间范围")
        self.window_hours_input.valueChanged.connect(self._set_window_hours)
        layout.addWidget(window_hours_label)
        layout.addWidget(self.window_hours_input)

        self.refresh_button = QPushButton("刷新数据")
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.clicked.connect(self.start_refresh)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_refresh)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.cancel_button)
        return frame

    def _build_overview(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("overviewFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        kicker = QLabel("SNAPSHOT OVERVIEW")
        kicker.setObjectName("sectionKicker")
        heading.addWidget(kicker)
        heading.addStretch(1)
        self.window_label = QLabel("尚无本地快照 · 点击刷新数据开始采集")
        self.window_label.setObjectName("windowLabel")
        heading.addWidget(self.window_label)
        layout.addLayout(heading)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.ths_card = MetricCard("同花顺新闻", "link")
        self.pop_card = MetricCard("东方财富综合人气", "hot")
        self.ths_card.setToolTip("切换到同花顺新闻热度榜")
        self.pop_card.setToolTip("切换到东方财富官方综合人气榜（含飙升榜）")
        self.ths_card.clicked.connect(lambda: self._select_source("ths"))
        self.pop_card.clicked.connect(lambda: self._select_source("pop"))
        for metric in (self.ths_card, self.pop_card):
            metrics.addWidget(metric)
        layout.addLayout(metrics)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("statsLabel")
        layout.addWidget(self.stats_label)

        activity = QFrame()
        activity.setObjectName("activityFrame")
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(10, 8, 10, 8)
        activity_layout.setSpacing(6)
        self.status_message = QLabel("数据终端已就绪")
        self.status_message.setObjectName("activityLabel")
        self.status_message.setProperty("active", "false")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        activity_layout.addWidget(self.status_message)
        activity_layout.addWidget(self.progress_bar)
        layout.addWidget(activity)
        return frame

    def _build_workbench(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("workbenchFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("热度排行榜")
        title.setObjectName("sectionTitle")
        self.ranking_caption = QLabel("按去重后的有效新闻提及次数排序")
        self.ranking_caption.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(self.ranking_caption)
        heading.addLayout(title_box)
        heading.addStretch(1)
        self.result_count_label = QLabel("0 只股票")
        self.result_count_label.setObjectName("mutedLabel")
        heading.addWidget(self.result_count_label)
        self.rank_toggle_pop = QPushButton("人气榜")
        self.rank_toggle_surge = QPushButton("飙升榜")
        self.rank_toggle_pop.setCheckable(True)
        self.rank_toggle_surge.setCheckable(True)
        self.rank_toggle_pop.setFixedWidth(86)
        self.rank_toggle_surge.setFixedWidth(86)
        self.rank_toggle_pop.setToolTip("官方综合人气榜 Top 100")
        self.rank_toggle_surge.setToolTip("官方飙升榜 Top 100（较昨日排名提升最多）")
        self.rank_toggle_pop.clicked.connect(lambda: self._select_source("pop"))
        self.rank_toggle_surge.clicked.connect(lambda: self._select_source("surge"))
        self.rank_toggle_pop.setVisible(False)
        self.rank_toggle_surge.setVisible(False)
        heading.addWidget(self.rank_toggle_pop)
        heading.addWidget(self.rank_toggle_surge)
        self.industry_label = QLabel("行业")
        self.industry_label.setObjectName("mutedLabel")
        heading.addWidget(self.industry_label)
        self.industry_filter = IndustryFilterButton()
        heading.addWidget(self.industry_filter)
        search_label = QLabel("搜索")
        search_label.setObjectName("mutedLabel")
        heading.addWidget(search_label)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入股票名称或代码")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(250)
        heading.addWidget(self.search_input)
        layout.addLayout(heading)

        self.table_model = RankingTableModel(self)
        self.proxy_model = RankingProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)
        self.search_input.textChanged.connect(self.proxy_model.set_query)
        self.industry_filter.selection_changed.connect(self._set_industry_filter)
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
        self.table.setToolTip("单击蓝色股票名称查看明细")
        self.table.setMouseTracking(True)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self._configure_table_columns()
        self.table.clicked.connect(self.show_selected_details)
        self.table.entered.connect(self._update_table_cursor)

        self.empty_state = self._build_empty_state()
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.empty_state)
        self.content_stack.addWidget(self.table)
        self.content_stack.setCurrentWidget(self.empty_state)
        layout.addWidget(self.content_stack, 1)
        return frame

    def _build_empty_state(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("emptyState")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)
        layout.addStretch(1)
        glyph = QLabel("◆")
        glyph.setObjectName("brandGlyph")
        glyph.setAlignment(Qt.AlignCenter)
        layout.addWidget(glyph)
        title = QLabel("尚未生成热度榜单")
        title.setObjectName("sectionTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("刷新数据后，将在这里呈现新闻与官方人气两类独立的 A 股热度排行。")
        hint.setObjectName("mutedLabel")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
        self.empty_refresh_button = QPushButton("开始刷新")
        self.empty_refresh_button.setObjectName("emptyRefreshButton")
        self.empty_refresh_button.clicked.connect(self.start_refresh)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.empty_refresh_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return frame

    def _build_menus(self) -> None:
        data_menu = QMenu("数据", self)
        refresh_action = QAction("刷新数据", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.start_refresh)
        data_menu.addAction(refresh_action)
        clear_action = QAction("清除本地数据…", self)
        clear_action.triggered.connect(self.clear_local_data)
        data_menu.addAction(clear_action)
        data_menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        data_menu.addAction(exit_action)
        self.menuBar().addMenu(data_menu)

        help_menu = QMenu("帮助", self)
        about_action = QAction("关于", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        self.menuBar().addMenu(help_menu)

    def _apply_styles(self) -> None:
        self.setStyleSheet(DARK_STYLESHEET)

    def load_latest_snapshot(self) -> None:
        snapshot = self.storage.load_latest_snapshot()
        if snapshot:
            self.set_snapshot(snapshot)

    def set_snapshot(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.window_label.setText(
            f"统计窗口：{format_datetime(snapshot.window_start, seconds=True)} 至 "
            f"{format_datetime(snapshot.window_end, seconds=True)} · 刷新于 {format_datetime(snapshot.created_at, seconds=True)}"
        )
        news_stats = snapshot.stats
        self.ths_card.set_value(
            f"{len(snapshot.rankings):,} 只",
            f"有效事件 {news_stats.get('events', 0):,} · 点击查看",
        )
        popularity = snapshot.popularity
        if popularity.available:
            pop_detail = f"数据截至 {format_datetime(popularity.success_at) if popularity.success_at else '—'}"
            if popularity.is_stale:
                pop_detail += " · 已过期"
            self.pop_card.set_value(
                f"{len(popularity.popularity):,} 只",
                pop_detail,
                status="warning" if popularity.is_stale else None,
            )
        else:
            pop_detail = "读取失败" if popularity.error else "等待刷新"
            self.pop_card.set_value("—", pop_detail, status="warning" if popularity.error else None)
        if self.selected_source in {"pop", "surge"} and not snapshot.popularity.available:
            self.selected_source = "ths"
        self._render_selected_source(reset_industry=False)
        self.snapshot_changed.emit(snapshot)

    def _selected_rows(self) -> list[RankingRow] | list[PopularityRankRow]:
        if not self.snapshot:
            return []
        if self.selected_source == "pop":
            return self.snapshot.popularity.popularity
        if self.selected_source == "surge":
            return self.snapshot.popularity.surging
        return self.snapshot.rankings

    def _select_source(self, source_key: str) -> None:
        if not self.snapshot:
            return
        if source_key == "ths":
            self.selected_source = "ths"
        elif source_key in {"pop", "surge"}:
            if not self.snapshot.popularity.available:
                return
            self.selected_source = source_key
        else:
            return
        self._render_selected_source(reset_industry=True)

    def _update_rank_toggles(self) -> None:
        is_pop = self.selected_source in {"pop", "surge"}
        self.rank_toggle_pop.setVisible(is_pop)
        self.rank_toggle_surge.setVisible(is_pop)
        if is_pop:
            self.rank_toggle_pop.setChecked(self.selected_source == "pop")
            self.rank_toggle_surge.setChecked(self.selected_source == "surge")

    def _render_selected_source(self, *, reset_industry: bool) -> None:
        if not self.snapshot:
            return
        if reset_industry:
            self.industry_filter.set_selected_tags(set(), emit=False)
        source_key = self.selected_source
        is_news = source_key == "ths"
        is_pop = source_key in {"pop", "surge"}
        rows = self._selected_rows()
        self.ths_card.set_selected(is_news)
        self.pop_card.set_selected(is_pop)
        self._update_rank_toggles()
        self.industry_label.setVisible(is_news)
        self.industry_filter.setVisible(is_news)
        self.table_model.set_rows(rows, source_key=source_key)
        self._configure_table_columns()
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.content_stack.setCurrentWidget(self.table)
        self._set_industry_filter_options(rows)
        if is_news:
            stats = self.snapshot.stats
            self.ranking_caption.setText("按去重后的有效新闻提及次数排序")
            self.table.setToolTip("单击蓝色股票名称查看有效提及文章")
            self.stats_label.setText(
                f"列表 {stats.get('list_items', 0)} 篇 · 去重 URL {stats.get('unique_urls', 0)} · "
                f"模板过滤 {stats.get('filtered', 0)} · 正文失败 {stats.get('failed', 0)} · "
                f"未映射 {stats.get('unmapped', 0)} · 有效事件 {stats.get('events', 0)}"
            )
        else:
            popularity = self.snapshot.popularity
            if source_key == "pop":
                base_caption = "东方财富官方综合人气榜 Top 100 · 按官方关注度排名"
            else:
                base_caption = "东方财富官方飙升榜 Top 100 · 较昨日排名提升最多"
            self.table.setToolTip("单击蓝色股票名称打开官方人气页")
            if popularity.available:
                success_text = (
                    format_datetime(popularity.success_at, seconds=True) if popularity.success_at else "—"
                )
                if popularity.is_stale:
                    self.ranking_caption.setText(f"{base_caption} · ⚠ 数据已过期")
                    self.stats_label.setText(
                        f"数据截至 {success_text}（已过期） · 本次失败原因：{popularity.error or '未知'}"
                    )
                else:
                    self.ranking_caption.setText(base_caption)
                    self.stats_label.setText(f"官方榜单 · 数据截至 {success_text}")
            else:
                self.ranking_caption.setText(f"{base_caption} · 暂无数据")
                self.stats_label.setText(f"本次读取失败：{popularity.error or '未知'}")
        self._update_result_count()

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
            self.table.setColumnWidth(4, 130)
        elif self.selected_source == "surge":
            self.table.setColumnWidth(3, 110)
            self.table.setColumnWidth(4, 96)
            self.table.setColumnWidth(5, 130)
        else:
            self.table.setColumnWidth(RankingTableModel.INDUSTRY_COLUMN, 160)
            self.table.setColumnWidth(RankingTableModel.HEAT_COLUMN, 92)
            self.table.setColumnWidth(5, 92)
            self.table.setColumnWidth(6, 154)

    def _set_industry_filter_options(self, rankings: list[RankingRow] | list[PopularityRankRow]) -> None:
        tags = {
            tag
            for row in rankings
            for tag in (getattr(row, "industry_tags", None) or (RankingProxyModel.UNCATEGORIZED_LABEL,))
        }
        self.industry_filter.set_options(tags)
        self.proxy_model.set_industry_tags(set(self.industry_filter.selected_tags))

    def _set_industry_filter(self, tags: set[str]) -> None:
        self.proxy_model.set_industry_tags(tags)
        self._update_result_count()

    def _update_result_count(self, *_: object) -> None:
        self.result_count_label.setText(f"{self.proxy_model.rowCount()} 只股票")
        self.heat_bar_delegate.set_maximum(self.proxy_model.maximum_filtered_heat())

    def _update_subtitle(self) -> None:
        self.subtitle_label.setText(
            f"观察窗口 {self.settings.window_hours} 小时仅影响同花顺新闻榜；"
            "东方财富综合人气为官方榜单、低频更新"
        )

    def _set_window_hours(self, hours: int) -> None:
        self.settings.window_hours = hours
        self._update_subtitle()

    def _update_table_cursor(self, index: QModelIndex) -> None:
        cursor = Qt.PointingHandCursor if index.column() == RankingTableModel.STOCK_NAME_COLUMN else Qt.ArrowCursor
        self.table.viewport().setCursor(cursor)

    def _set_activity(self, message: str, *, active: bool) -> None:
        self.status_message.setText(message)
        self.status_message.setProperty("active", "true" if active else "false")
        self.status_message.style().unpolish(self.status_message)
        self.status_message.style().polish(self.status_message)

    def start_refresh(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        self.settings.window_hours = self.window_hours_input.value()
        self.refresh_button.setEnabled(False)
        self.empty_refresh_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.search_input.setEnabled(False)
        self.industry_filter.setEnabled(False)
        self.window_hours_input.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self._set_activity("正在启动刷新…", active=True)

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
            self._set_activity("正在取消，请稍候…", active=True)
            self._worker.request_cancel()

    def _on_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(value)
        self._set_activity(message, active=True)

    def _on_completed(self, snapshot: Snapshot) -> None:
        self.set_snapshot(snapshot)
        self._set_activity("刷新完成 · 榜单已更新", active=False)

    def _on_failed(self, message: str) -> None:
        self._set_activity("刷新失败，已保留上次结果", active=False)
        QMessageBox.critical(self, "刷新失败", f"本次刷新未生成新榜单。\n\n{message}")

    def _on_cancelled(self) -> None:
        self._set_activity("刷新已取消，未生成新榜单", active=False)

    def _thread_finished(self) -> None:
        self.refresh_button.setEnabled(True)
        self.empty_refresh_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.search_input.setEnabled(True)
        self.industry_filter.setEnabled(True)
        self.window_hours_input.setEnabled(True)
        self.progress_bar.hide()
        self._thread = None
        self._worker = None

    def show_selected_details(self, index: QModelIndex) -> None:
        if not self.snapshot or index.column() != RankingTableModel.STOCK_NAME_COLUMN:
            return
        source_index = self.proxy_model.mapToSource(index)
        row = self.table_model.row_at(source_index.row())
        if isinstance(row, RankingRow):
            dialog = ArticleDetailDialog(row, self.snapshot.events, self)
            dialog.exec()
        elif isinstance(row, PopularityRankRow) and row.url:
            QDesktopServices.openUrl(QUrl(row.url))

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
        self.table_model.set_rows([])
        self._set_industry_filter_options([])
        self.content_stack.setCurrentWidget(self.empty_state)
        self.window_label.setText("尚无本地快照 · 点击刷新数据开始采集")
        self.stats_label.setText("")
        self.ths_card.set_value("—", "等待刷新")
        self.pop_card.set_value("—", "等待刷新")
        self.ths_card.set_selected(True)
        self.pop_card.set_selected(False)
        self.selected_source = "ths"
        self.industry_label.setVisible(True)
        self.industry_filter.setVisible(True)
        self._update_rank_toggles()
        self._set_activity("本地数据已清除", active=False)

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"关于 {APP_NAME}",
            f"<b>{APP_NAME} {APP_VERSION}</b><br><br>"
            "基于同花顺公开新闻与东方财富官方公开人气榜，分别统计 A 股新闻事件与官方综合人气排名。<br>"
            "仅供信息整理，不构成投资建议。",
        )

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
        event.accept()
