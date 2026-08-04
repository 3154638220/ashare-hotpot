from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import APP_NAME, APP_VERSION, AppSettings
from .models import NewsEvent, RankingRow, Snapshot
from .service import RefreshService
from .storage import Storage
from .worker import RefreshWorker


def format_datetime(value: datetime | None, *, seconds: bool = False) -> str:
    if value is None:
        return "—"
    pattern = "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M"
    return value.strftime(pattern)


class RankingTableModel(QAbstractTableModel):
    HEADERS = ("排名", "股票名称", "代码", "有效提及", "原始篇数", "最近提及")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.rows: list[RankingRow] = []

    def set_rows(self, rows: list[RankingRow]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> RankingRow | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        row = self.rows[index.row()]
        raw_values = (
            row.rank,
            row.name,
            row.code,
            row.event_count,
            row.raw_article_count,
            row.latest_mention.timestamp(),
        )
        display_values = (
            str(row.rank),
            row.name,
            row.code,
            str(row.event_count),
            str(row.raw_article_count),
            format_datetime(row.latest_mention),
        )
        if role == Qt.DisplayRole:
            return display_values[index.column()]
        if role == Qt.UserRole:
            return raw_values[index.column()]
        if role == Qt.TextAlignmentRole:
            if index.column() in {0, 2, 3, 4}:
                return int(Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)
        if role == Qt.ForegroundRole and index.column() == 3:
            return QColor("#d9272e")
        if role == Qt.FontRole and index.column() in {1, 3}:
            font = QFont()
            font.setBold(True)
            return font
        return None


class RankingProxyModel(QSortFilterProxyModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._query = ""
        self.setSortRole(Qt.UserRole)
        self.setDynamicSortFilter(True)

    def set_query(self, value: str) -> None:
        self._query = value.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._query:
            return True
        model = self.sourceModel()
        name = str(model.index(source_row, 1, source_parent).data(Qt.DisplayRole) or "").lower()
        code = str(model.index(source_row, 2, source_parent).data(Qt.DisplayRole) or "").lower()
        return self._query in name or self._query in code


class ArticleDetailDialog(QDialog):
    def __init__(self, row: RankingRow, events: list[NewsEvent], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{row.name}（{row.code}）新闻明细")
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        heading = QLabel(
            f"<b>{row.name}（{row.code}）</b>　有效事件 {row.event_count}　原始稿件 {row.raw_article_count}"
        )
        heading.setTextFormat(Qt.RichText)
        layout.addWidget(heading)
        hint = QLabel("双击原始稿件可使用系统默认浏览器打开同花顺原文。")
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["事件 / 原始稿件", "栏目 / 来源", "发布时间"])
        tree.setAlternatingRowColors(True)
        tree.setRootIsDecorated(True)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        event_map = {event.event_id: event for event in events}
        for event_id in row.event_ids:
            event = event_map.get(event_id)
            if not event:
                continue
            top = QTreeWidgetItem(
                [
                    event.title,
                    f"去重事件 · {len(event.articles)} 篇",
                    format_datetime(event.published_at, seconds=True),
                ]
            )
            top.setFirstColumnSpanned(False)
            top_font = top.font(0)
            top_font.setBold(True)
            top.setFont(0, top_font)
            tree.addTopLevelItem(top)
            for article in event.articles:
                if row.code not in {stock.code for stock in article.stocks}:
                    continue
                child = QTreeWidgetItem(
                    [
                        article.title,
                        f"{article.channel_name} / {article.source_name}",
                        format_datetime(article.published_at, seconds=True),
                    ]
                )
                child.setData(0, Qt.UserRole, article.url)
                child.setToolTip(0, article.url)
                top.addChild(child)
            top.setExpanded(True)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        tree.itemDoubleClicked.connect(self._open_article)
        layout.addWidget(tree, 1)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    @staticmethod
    def _open_article(item: QTreeWidgetItem, _column: int) -> None:
        url = item.data(0, Qt.UserRole)
        if url:
            QDesktopServices.openUrl(QUrl(str(url)))


class MainWindow(QMainWindow):
    snapshot_changed = Signal(object)

    def __init__(self, settings: AppSettings, storage: Storage, service: RefreshService) -> None:
        super().__init__()
        self.settings = settings
        self.storage = storage
        self.service = service
        self.snapshot: Snapshot | None = None
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
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("A股新闻热度")
        title.setObjectName("appTitle")
        subtitle = QLabel("按同花顺最近 24 小时已发现新闻的去重提及次数排名")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        title_row.addLayout(title_box)
        title_row.addStretch(1)
        self.refresh_button = QPushButton("刷新新闻")
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.clicked.connect(self.start_refresh)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_refresh)
        title_row.addWidget(self.refresh_button)
        title_row.addWidget(self.cancel_button)
        outer.addLayout(title_row)

        info_frame = QFrame()
        info_frame.setObjectName("infoFrame")
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(14, 10, 14, 10)
        self.window_label = QLabel("统计窗口：尚无数据")
        self.coverage_label = QLabel("点击“刷新新闻”开始采集。")
        self.coverage_label.setWordWrap(True)
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("mutedLabel")
        info_layout.addWidget(self.window_label)
        info_layout.addWidget(self.coverage_label)
        info_layout.addWidget(self.stats_label)
        outer.addWidget(info_frame)

        controls = QHBoxLayout()
        search_label = QLabel("搜索")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入股票名称或代码")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMaximumWidth(300)
        controls.addWidget(search_label)
        controls.addWidget(self.search_input)
        controls.addStretch(1)
        self.result_count_label = QLabel("0 只股票")
        self.result_count_label.setObjectName("mutedLabel")
        controls.addWidget(self.result_count_label)
        outer.addLayout(controls)

        self.table_model = RankingTableModel(self)
        self.proxy_model = RankingProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)
        self.search_input.textChanged.connect(self.proxy_model.set_query)
        self.proxy_model.rowsInserted.connect(self._update_result_count)
        self.proxy_model.rowsRemoved.connect(self._update_result_count)
        self.proxy_model.modelReset.connect(self._update_result_count)

        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self.show_selected_details)
        outer.addWidget(self.table, 1)

        disclaimer = QLabel("数据来源：同花顺公开新闻页面。新闻提及热度不构成任何投资建议。")
        disclaimer.setObjectName("disclaimerLabel")
        disclaimer.setAlignment(Qt.AlignCenter)
        outer.addWidget(disclaimer)
        self.setCentralWidget(central)

        status = QStatusBar()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.hide()
        self.status_message = QLabel("就绪")
        status.addWidget(self.status_message, 1)
        status.addPermanentWidget(self.progress_bar)
        self.setStatusBar(status)

    def _build_menus(self) -> None:
        data_menu = QMenu("数据", self)
        refresh_action = QAction("刷新新闻", self)
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
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f6f7f9; color: #20242b; font-size: 14px;
                                  font-family: "Microsoft YaHei UI", "Microsoft YaHei"; }
            QLabel#appTitle { font-size: 28px; font-weight: 700; color: #17191d; }
            QLabel#mutedLabel { color: #6d7480; }
            QLabel#disclaimerLabel { color: #8a5b00; background: #fff8df; border: 1px solid #f1dda0;
                                      border-radius: 6px; padding: 7px; }
            QFrame#infoFrame { background: white; border: 1px solid #e2e5e9; border-radius: 8px; }
            QPushButton { min-height: 32px; padding: 0 14px; border: 1px solid #cbd0d7;
                          border-radius: 6px; background: white; }
            QPushButton:hover { border-color: #d9272e; color: #d9272e; }
            QPushButton:disabled { color: #a7abb2; background: #eceef1; border-color: #dfe2e6; }
            QPushButton#primaryButton { color: white; background: #d9272e; border-color: #d9272e; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #ba1f26; }
            QLineEdit { min-height: 32px; padding: 0 9px; background: white; border: 1px solid #cbd0d7;
                        border-radius: 6px; }
            QLineEdit:focus { border-color: #d9272e; }
            QTableView, QTreeWidget { background: white; alternate-background-color: #fafbfc;
                                      border: 1px solid #dfe2e6; gridline-color: #eceef1;
                                      selection-background-color: #fde8e9; selection-color: #20242b; }
            QHeaderView::section { background: #eff1f4; border: 0; border-right: 1px solid #dfe2e6;
                                   border-bottom: 1px solid #d6d9de; padding: 9px; font-weight: 600; }
            QProgressBar { border: 1px solid #d6d9de; border-radius: 4px; background: white; }
            QProgressBar::chunk { background: #d9272e; border-radius: 3px; }
            """
        )

    def load_latest_snapshot(self) -> None:
        snapshot = self.storage.load_latest_snapshot()
        if snapshot:
            self.set_snapshot(snapshot)

    def set_snapshot(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self.table_model.set_rows(snapshot.rankings)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.window_label.setText(
            f"统计窗口：{format_datetime(snapshot.window_start, seconds=True)} 至 "
            f"{format_datetime(snapshot.window_end, seconds=True)}　·　刷新于 {format_datetime(snapshot.created_at, seconds=True)}"
        )
        coverage_parts = []
        for coverage in snapshot.coverages:
            state = "完整到达截止点" if coverage.reached_cutoff and not coverage.error else "覆盖不足"
            if coverage.error:
                state = "抓取失败"
            coverage_parts.append(
                f"{coverage.source_name}：{coverage.pages_scanned}页/{coverage.article_count}篇，"
                f"最早 {format_datetime(coverage.oldest_seen)}（{state}）"
            )
        prefix = "⚠ 24小时数据不完整。" if snapshot.partial else "✓ 已覆盖24小时窗口。"
        self.coverage_label.setText(prefix + "　" + "；".join(coverage_parts))
        self.coverage_label.setStyleSheet("color: #9b5d00;" if snapshot.partial else "color: #167548;")
        stats = snapshot.stats
        self.stats_label.setText(
            f"列表 {stats.get('list_items', 0)} 篇 · 去重URL {stats.get('unique_urls', 0)} · "
            f"模板过滤 {stats.get('filtered', 0)} · 正文失败 {stats.get('failed', 0)} · "
            f"未映射 {stats.get('unmapped', 0)} · 有效事件 {stats.get('events', 0)}"
        )
        self._update_result_count()
        self.snapshot_changed.emit(snapshot)

    def _update_result_count(self, *_: object) -> None:
        self.result_count_label.setText(f"{self.proxy_model.rowCount()} 只股票")

    def start_refresh(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.search_input.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_message.setText("正在启动刷新…")

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
        self.status_message.setText("刷新完成")

    def _on_failed(self, message: str) -> None:
        self.status_message.setText("刷新失败，已保留上次结果")
        QMessageBox.critical(self, "刷新失败", f"本次刷新未生成新榜单。\n\n{message}")

    def _on_cancelled(self) -> None:
        self.status_message.setText("刷新已取消，未生成新榜单")

    def _thread_finished(self) -> None:
        self.refresh_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.search_input.setEnabled(True)
        self.progress_bar.hide()
        self._thread = None
        self._worker = None

    def show_selected_details(self, index: QModelIndex) -> None:
        if not self.snapshot:
            return
        source_index = self.proxy_model.mapToSource(index)
        row = self.table_model.row_at(source_index.row())
        if row:
            dialog = ArticleDetailDialog(row, self.snapshot.events, self)
            dialog.exec()

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
        self.window_label.setText("统计窗口：尚无数据")
        self.coverage_label.setText("本地数据已清除。点击“刷新新闻”重新采集。")
        self.coverage_label.setStyleSheet("")
        self.stats_label.setText("")
        self.status_message.setText("本地数据已清除")

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"关于 {APP_NAME}",
            f"<b>{APP_NAME} {APP_VERSION}</b><br><br>"
            "基于同花顺公开新闻页面统计 A 股被提及的去重事件数。<br>"
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
