from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .icons import app_icon, icon
from .models import NewsEvent, PopularityRankRow, RankingRow
from .theme import COLOR_LINK, DARK_STYLESHEET
from .updates import UpdateCheckResult
from .worker import UpdateCheckWorker


class KpiChip(QFrame):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiChip")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(1)
        self.label = QLabel(label)
        self.label.setObjectName("kpiLabel")
        self.value = QLabel("—")
        self.value.setObjectName("kpiValue")
        layout.addWidget(self.label)
        layout.addWidget(self.value)

    def set_value(self, value: str, tooltip: str = "") -> None:
        self.value.setText(value)
        self.setToolTip(tooltip)


class ErrorBanner(QFrame):
    retry_requested = Signal()
    details_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("errorBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 8, 7)
        self.message_label = QLabel()
        self.message_label.setObjectName("errorText")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label, 1)
        self.details_button = QPushButton("查看详情")
        self.details_button.clicked.connect(self.details_requested)
        layout.addWidget(self.details_button)
        self.retry_button = QPushButton("重试")
        self.retry_button.clicked.connect(self.retry_requested)
        layout.addWidget(self.retry_button)
        self.hide()

    def show_message(self, message: str, *, details: bool = True) -> None:
        self.message_label.setText(message)
        self.details_button.setVisible(details)
        self.show()


class StockDetailPanel(QFrame):
    open_url_requested = Signal(str)
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self.current_row: RankingRow | PopularityRankRow | None = None
        self.current_source = "ths"
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("detailHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 8, 10)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.title_label = QLabel("股票详情")
        self.title_label.setObjectName("viewTitle")
        self.meta_label = QLabel("选择榜单中的一只股票")
        self.meta_label.setObjectName("detailMeta")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.meta_label)
        header_layout.addLayout(title_box, 1)
        close_button = QPushButton()
        close_button.setIcon(icon("close"))
        close_button.setFixedSize(30, 30)
        close_button.setToolTip("收起详情面板")
        close_button.clicked.connect(self.close_requested)
        header_layout.addWidget(close_button)
        layout.addWidget(header)

        self.summary_label = QLabel("单击榜单行可在此查看详情。")
        self.summary_label.setObjectName("mutedLabel")
        self.summary_label.setWordWrap(True)
        self.summary_label.setContentsMargins(14, 12, 14, 10)
        layout.addWidget(self.summary_label)

        self.article_tree = QTreeWidget()
        self.article_tree.setColumnCount(3)
        self.article_tree.setHeaderLabels(["相关文章", "来源", "时间"])
        self.article_tree.setRootIsDecorated(False)
        self.article_tree.setAlternatingRowColors(True)
        self.article_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.article_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.article_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.article_tree.itemDoubleClicked.connect(self._open_article)
        layout.addWidget(self.article_tree, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(12, 10, 12, 0)
        button_row.addStretch(1)
        self.open_button = QPushButton("打开原文")
        self.open_button.setIcon(icon("info"))
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_primary)
        button_row.addWidget(self.open_button)
        layout.addLayout(button_row)

    def clear(self) -> None:
        self.current_row = None
        self.current_source = "ths"
        self.title_label.setText("股票详情")
        self.meta_label.setText("选择榜单中的一只股票")
        self.summary_label.setText("单击榜单行可在此查看详情。")
        self.article_tree.clear()
        self.article_tree.show()
        self.open_button.setEnabled(False)
        self.open_button.setText("打开原文")

    def set_news(self, row: RankingRow, events: list[NewsEvent]) -> None:
        self.current_row = row
        self.current_source = "ths"
        self.title_label.setText(row.name)
        industries = "、".join(row.industry_tags) if row.industry_tags else "未标注行业"
        self.meta_label.setText(f"{row.code} · {industries}")
        self.summary_label.setText(
            f"排名 {row.rank} · {row.event_count} 次有效提及 · {row.raw_article_count} 篇原始文章"
        )
        self.article_tree.clear()
        event_map = {event.event_id: event for event in events}
        for event_id in row.event_ids:
            event = event_map.get(event_id)
            if event is None:
                continue
            matching = [
                article
                for article in event.articles
                if row.code in {stock.code for stock in article.stocks}
            ]
            article = matching[0] if matching else (event.articles[0] if event.articles else None)
            if article is None:
                continue
            item = QTreeWidgetItem(
                [article.title, article.channel_name or article.source_name, article.published_at.strftime("%m-%d %H:%M")]
            )
            item.setData(0, Qt.UserRole, article.url)
            item.setToolTip(0, article.title)
            item.setForeground(0, QColor(COLOR_LINK))
            self.article_tree.addTopLevelItem(item)
        if self.article_tree.topLevelItemCount():
            self.article_tree.setCurrentItem(self.article_tree.topLevelItem(0))
        self.article_tree.show()
        self.open_button.setText("打开原文")
        self.open_button.setEnabled(self.article_tree.topLevelItemCount() > 0)

    def set_popularity(self, row: PopularityRankRow, source_key: str) -> None:
        self.current_row = row
        self.current_source = source_key
        self.title_label.setText(row.name)
        self.meta_label.setText(f"{row.code} · {'综合人气榜' if source_key == 'pop' else '飙升榜'}")
        parts = [f"官方排名 {row.rank}"]
        if row.change is not None:
            parts.append(f"较昨日 {row.change:+d}")
        if row.current_price is not None:
            parts.append(f"现价 {row.current_price:.2f}")
        if row.change_percent is not None:
            parts.append(f"涨跌幅 {row.change_percent:+.2f}%")
        self.summary_label.setText(" · ".join(parts))
        self.article_tree.clear()
        self.article_tree.hide()
        self.open_button.setText("打开官方页")
        self.open_button.setEnabled(bool(row.url))

    def open_primary(self) -> None:
        if isinstance(self.current_row, PopularityRankRow):
            if self.current_row.url:
                self.open_url_requested.emit(self.current_row.url)
            return
        item = self.article_tree.currentItem()
        if item is not None:
            url = item.data(0, Qt.UserRole)
            if url:
                self.open_url_requested.emit(str(url))

    def _open_article(self, item: QTreeWidgetItem, _column: int) -> None:
        url = item.data(0, Qt.UserRole)
        if url:
            self.open_url_requested.emit(str(url))


class SettingsDialog(QDialog):
    clear_data_requested = Signal()

    def __init__(
        self,
        *,
        window_hours: int,
        auto_refresh: bool,
        density: str,
        retention_days: int,
        data_dir: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(500)
        self.setStyleSheet(DARK_STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)

        tabs = QTabWidget()
        general = QWidget()
        form = QFormLayout(general)
        form.setContentsMargins(16, 16, 16, 16)
        form.setVerticalSpacing(14)
        self.window_hours_input = QSpinBox()
        self.window_hours_input.setRange(1, 168)
        self.window_hours_input.setSuffix(" 小时")
        self.window_hours_input.setValue(window_hours)
        form.addRow("默认新闻观察窗口", self.window_hours_input)
        self.auto_refresh_check = QCheckBox("启动后自动刷新全部数据")
        self.auto_refresh_check.setChecked(auto_refresh)
        form.addRow("启动行为", self.auto_refresh_check)
        self.density_combo = QComboBox()
        self.density_combo.addItem("紧凑", "compact")
        self.density_combo.addItem("舒适", "comfortable")
        self.density_combo.setCurrentIndex(0 if density == "compact" else 1)
        form.addRow("表格密度", self.density_combo)
        tabs.addTab(general, "界面与刷新")

        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(16, 16, 16, 16)
        retention_form = QFormLayout()
        self.retention_input = QSpinBox()
        self.retention_input.setRange(1, 30)
        self.retention_input.setSuffix(" 天")
        self.retention_input.setValue(retention_days)
        retention_form.addRow("文章缓存保留", self.retention_input)
        data_layout.addLayout(retention_form)
        path_title = QLabel("应用数据目录")
        path_title.setObjectName("detailMeta")
        data_layout.addWidget(path_title)
        path_label = QLabel(str(data_dir))
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setWordWrap(True)
        data_layout.addWidget(path_label)
        data_layout.addStretch(1)
        clear_button = QPushButton("清除本地数据…")
        clear_button.setObjectName("dangerButton")
        clear_button.setIcon(icon("trash"))
        clear_button.clicked.connect(self.clear_data_requested)
        data_layout.addWidget(clear_button, 0, Qt.AlignLeft)
        tabs.addTab(data_page, "数据")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def values(self) -> dict[str, object]:
        return {
            "window_hours": self.window_hours_input.value(),
            "auto_refresh": self.auto_refresh_check.isChecked(),
            "density": self.density_combo.currentData(),
            "retention_days": self.retention_input.value(),
        }


class HtmlInfoDialog(QDialog):
    def __init__(self, title: str, html: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 480)
        self.setStyleSheet(DARK_STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(html)
        layout.addWidget(browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    """Product identity and support information for the desktop application."""

    diagnostics_requested = Signal()

    def __init__(
        self,
        *,
        app_name: str,
        app_version: str,
        project_url: str,
        release_url: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_version = app_version
        self.project_url = project_url
        self.release_url = release_url
        self._update_worker: UpdateCheckWorker | None = None
        self.setObjectName("aboutDialog")
        self.setWindowTitle(f"关于 {app_name}")
        self.setMinimumWidth(580)
        self.resize(620, 540)
        self.setStyleSheet(DARK_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 14)
        layout.setSpacing(12)

        hero = QFrame()
        hero.setObjectName("aboutHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(18, 16, 18, 16)
        hero_layout.setSpacing(14)
        app_mark = QLabel()
        app_mark.setPixmap(app_icon().pixmap(52, 52))
        app_mark.setFixedSize(52, 52)
        hero_layout.addWidget(app_mark)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(3)
        self.title_label = QLabel(app_name)
        self.title_label.setObjectName("aboutTitle")
        self.subtitle_label = QLabel("A 股公开信息整理与研究支持工具")
        self.subtitle_label.setObjectName("aboutSubtitle")
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.subtitle_label)
        hero_layout.addLayout(title_layout, 1)
        self.version_label = QLabel(f"版本 {app_version}")
        self.version_label.setObjectName("aboutVersion")
        hero_layout.addWidget(self.version_label, 0, Qt.AlignTop)
        layout.addWidget(hero)

        layout.addWidget(
            self._info_card(
                "产品定位",
                "用于汇总和查看 A 股公开信息，帮助研究时快速发现新闻提及与官方人气排名。",
            )
        )
        layout.addWidget(
            self._info_card(
                "数据来源",
                "同花顺公开新闻页面，以及东方财富公开官方人气榜和飙升榜。"
                "榜单按公开口径展示，软件不推导未公开的热度权重。",
            )
        )
        layout.addWidget(
            self._info_card(
                "风险提示",
                "本软件仅提供公开信息整理，不构成投资建议。请结合独立判断与适用规则使用。",
                risk=True,
            )
        )

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.check_update_button = QPushButton("检查更新")
        self.check_update_button.setIcon(icon("refresh"))
        self.check_update_button.clicked.connect(self.check_for_updates)
        actions.addWidget(self.check_update_button)
        self.project_button = QPushButton("项目主页")
        self.project_button.setIcon(icon("github"))
        self.project_button.clicked.connect(lambda: self._open_url(self.project_url))
        actions.addWidget(self.project_button)
        self.release_button = QPushButton("查看发布版本")
        self.release_button.setIcon(icon("export"))
        self.release_button.clicked.connect(lambda: self._open_url(self.release_url))
        actions.addWidget(self.release_button)
        actions.addStretch(1)
        self.diagnostics_button = QPushButton("复制诊断信息")
        self.diagnostics_button.setObjectName("primaryButton")
        self.diagnostics_button.setIcon(icon("database"))
        self.diagnostics_button.clicked.connect(self.diagnostics_requested)
        actions.addWidget(self.diagnostics_button)
        layout.addLayout(actions)

        self.update_status_label = QLabel("")
        self.update_status_label.setObjectName("aboutUpdateStatus")
        self.update_status_label.setWordWrap(True)
        layout.addWidget(self.update_status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def check_for_updates(self) -> None:
        if self._update_worker is not None:
            return
        self.check_update_button.setEnabled(False)
        self.update_status_label.setText("正在检查更新…")
        worker = UpdateCheckWorker(self.project_url, self.app_version)
        worker.finished.connect(self._on_update_checked)
        self._update_worker = worker
        worker.start()

    def _on_update_checked(self, result: UpdateCheckResult) -> None:
        self._update_worker = None
        self.check_update_button.setEnabled(True)
        if result.error:
            self.update_status_label.setText(f"检查更新失败：{result.error}")
            return
        if result.latest is None:
            self.update_status_label.setText(f"已是最新版本 {self.app_version}")
            QMessageBox.information(self, "检查更新", f"当前已是最新版本 {self.app_version}。")
            return
        tag_name = result.latest.tag_name
        self.update_status_label.setText(f"发现新版本 {tag_name}")
        answer = QMessageBox.question(
            self,
            "发现新版本",
            f"当前版本 {self.app_version}，发现新版本 {tag_name}。\n\n是否前往下载页面？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._open_url(result.latest.html_url)

    @staticmethod
    def _info_card(title: str, text: str, *, risk: bool = False) -> QFrame:
        card = QFrame()
        card.setObjectName("aboutInfoCard")
        card.setProperty("risk", risk)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("aboutCardTitle")
        text_label = QLabel(text)
        text_label.setObjectName("aboutCardText")
        text_label.setWordWrap(True)
        card_layout.addWidget(title_label)
        card_layout.addWidget(text_label)
        return card

    @staticmethod
    def _open_url(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))


def open_local_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(path.as_uri())
