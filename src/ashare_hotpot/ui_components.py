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
    QLineEdit,
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
from .models import (
    DiscoveryViewRow,
    InteractionRankingRow,
    InteractionRecord,
    NewsEvent,
    PopularityRankRow,
    RankingRow,
)
from .research_views import (
    ACTIVITY_TYPE_LABELS,
    COVERAGE_STATE_LABELS,
    EventDetail,
    INSTITUTION_TYPE_LABELS,
    InstitutionDetail,
    TOPIC_LABELS,
    event_type_label,
    extractor_label,
)
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
        # Keep enough room for the article metadata columns.  The splitter can
        # still grow this pane, but it must not shrink it into a horizontal
        # scrollbar by default.
        self.setMinimumWidth(420)

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

    def set_interaction(self, row: InteractionRankingRow, records: list[InteractionRecord]) -> None:
        self.current_row = row
        self.current_source = "interaction"
        self.title_label.setText(row.name)
        industries = "、".join(row.industry_tags) if row.industry_tags else "未标注行业"
        self.meta_label.setText(f"{row.code} · {industries} · 官方问答代理指标（仅统计已回复提问）")
        self.summary_label.setText(
            f"排名 {row.rank} · {row.question_count} 次有效提问（已回复）· "
            f"最近回复 {row.latest_reply.strftime('%m-%d %H:%M')}"
        )
        self.article_tree.clear()
        self.article_tree.setColumnCount(4)
        self.article_tree.setHeaderLabels(["问题", "公司回复", "平台", "时间"])
        # Both text-heavy columns share the available space.  A fixed 220 px
        # reply column, combined with the platform and time columns, was wider
        # than the detail pane and hid content behind a horizontal scrollbar.
        self.article_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.article_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.article_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.article_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        record_map = {record.record_id: record for record in records}
        for record_id in row.record_ids:
            record = record_map.get(record_id)
            if record is None:
                continue
            reply = record.reply or "（未回复）"
            item = QTreeWidgetItem(
                [
                    record.question,
                    reply,
                    record.platform_name,
                    record.reply_time.strftime("%m-%d %H:%M") if record.reply_time else "—",
                ]
            )
            item.setData(0, Qt.UserRole, record.question_url)
            item.setToolTip(0, record.question)
            item.setToolTip(1, reply)
            item.setToolTip(
                3,
                f"提问于 {record.question_time.strftime('%Y-%m-%d %H:%M')}"
                + (
                    f" · 回复于 {record.reply_time.strftime('%Y-%m-%d %H:%M')}"
                    if record.reply_time
                    else ""
                ),
            )
            item.setForeground(0, QColor(COLOR_LINK))
            self.article_tree.addTopLevelItem(item)
        self.article_tree.setTextElideMode(Qt.ElideMiddle)
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


class ResearchDetailPanel(QFrame):
    """Detail panel for the four research views (milestone 5).

    Short-term views show event summary, mechanism, quantified fields,
    certainty, novelty, counter-evidence, cluster sources and evidence
    excerpts plus the extractor label; institution views show the activity
    timeline, participating institutions, group merges, Q&A depth, topics and
    window metric composition.  Coverage insufficiency and the "尚未落地"
    marker stay visible here as well as in the table.
    """

    open_url_requested = Signal(str)
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self.current_urls: list[str] = []
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("detailHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 8, 10)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.title_label = QLabel("研究详情")
        self.title_label.setObjectName("viewTitle")
        self.meta_label = QLabel("选择研究榜中的一只股票")
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

        self.banner = QFrame()
        self.banner.setObjectName("researchBanner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 7, 12, 7)
        self.banner_label = QLabel("尚未落地")
        self.banner_label.setObjectName("researchBannerText")
        banner_layout.addWidget(self.banner_label)
        self.banner.hide()
        layout.addWidget(self.banner)

        self.summary_label = QLabel("单击研究榜行可在此查看详情。")
        self.summary_label.setObjectName("mutedLabel")
        self.summary_label.setWordWrap(True)
        self.summary_label.setContentsMargins(14, 12, 14, 10)
        layout.addWidget(self.summary_label)

        self.detail_tree = QTreeWidget()
        self.detail_tree.setRootIsDecorated(True)
        self.detail_tree.setAlternatingRowColors(True)
        self.detail_tree.itemDoubleClicked.connect(self._open_item_url)
        layout.addWidget(self.detail_tree, 1)

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
        self.current_urls = []
        self.title_label.setText("研究详情")
        self.meta_label.setText("选择研究榜中的一只股票")
        self.summary_label.setText("单击研究榜行可在此查看详情。")
        self.banner.hide()
        self.detail_tree.clear()
        self.detail_tree.setColumnCount(1)
        self.detail_tree.setHeaderHidden(True)
        self.open_button.setEnabled(False)
        self.open_button.setText("打开原文")

    def set_short_term(self, detail: EventDetail, quality_label: str) -> None:
        self.current_urls = []
        extraction = detail.extraction
        cluster = detail.cluster
        signal = detail.signal
        self.title_label.setText(detail.stock_name)
        self.meta_label.setText(
            f"{detail.signal.stock_code} · {event_type_label(extraction.event_type)} · "
            f"{extractor_label(extraction.extractor_kind)} · "
            f"质量状态 {quality_label}"
        )
        if signal.board == "potential_catalyst":
            self.banner_label.setText("尚未落地：仍处于中标待签、审批中、框架协议或筹划阶段")
            self.banner.show()
        else:
            self.banner.hide()
        parts = [
            f"得分 {signal.score:.1f}",
            f"重大性 L{signal.materiality_level}",
            f"确定性 {signal.certainty * 100:.0f}%",
            f"意外性 {signal.unexpectedness:.0f}",
            f"新颖性 {signal.novelty:.0f}",
            f"时效性 {signal.timeliness:.2f}",
        ]
        self.summary_label.setText(" · ".join(parts))

        self.detail_tree.clear()
        self.detail_tree.setColumnCount(1)
        self.detail_tree.setHeaderHidden(True)

        mechanism_item = QTreeWidgetItem(["正向机制"])
        mechanism_item.setFirstColumnSpanned(True)
        mechanism_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(mechanism_item)
        mechanism_item.addChild(
            QTreeWidgetItem([extraction.positive_mechanism or "（无明确机制）"])
        )

        metrics_item = QTreeWidgetItem(["量化字段"])
        metrics_item.setFirstColumnSpanned(True)
        metrics_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(metrics_item)
        if extraction.metrics:
            for metric in extraction.metrics:
                name = str(metric.get("name") or "")
                value = metric.get("value")
                unit = str(metric.get("unit") or "")
                basis = str(metric.get("comparison_basis") or "")
                ratio = metric.get("comparison_ratio")
                text = f"{name} {value if value is not None else ''} {unit}".strip()
                if basis:
                    text += f" · 对比 {basis}"
                if ratio is not None:
                    text += f" · 比率 {ratio}"
                metrics_item.addChild(QTreeWidgetItem([text]))
        else:
            metrics_item.addChild(QTreeWidgetItem(["（无量化字段）"]))

        counter_item = QTreeWidgetItem(["主要反证 / 落地风险"])
        counter_item.setFirstColumnSpanned(True)
        counter_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(counter_item)
        if extraction.counter_evidence:
            for item in extraction.counter_evidence:
                summary = str(item.get("summary") or item.get("text") or item.get("kind") or "")
                counter_item.addChild(QTreeWidgetItem([summary]))
        elif signal.board == "potential_catalyst":
            counter_item.addChild(QTreeWidgetItem(["尚未落地：存在审批、签约或执行的不确定性"]))
        else:
            counter_item.addChild(QTreeWidgetItem(["（无高度反证）"]))

        evidence_item = QTreeWidgetItem(["证据摘录"])
        evidence_item.setFirstColumnSpanned(True)
        evidence_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(evidence_item)
        for evidence_id in extraction.evidence_ids:
            ref = detail.evidence_by_id.get(evidence_id)
            if ref is None:
                continue
            child = QTreeWidgetItem([ref.excerpt])
            child.setData(0, Qt.UserRole, ref.source_url)
            child.setToolTip(0, "双击打开证据原文")
            evidence_item.addChild(child)
            self.current_urls.append(ref.source_url)
        if not self.current_urls:
            evidence_item.addChild(QTreeWidgetItem(["（证据摘录不可用，可查看来源文档）"]))

        source_item = QTreeWidgetItem(["事件簇来源"])
        source_item.setFirstColumnSpanned(True)
        source_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(source_item)
        for document in detail.documents:
            child = QTreeWidgetItem(
                [
                    f"{document.provider_name} · {document.title} · "
                    f"{document.published_at.strftime('%m-%d %H:%M')}"
                ]
            )
            child.setData(0, Qt.UserRole, document.source_url or document.document_url or "")
            child.setToolTip(0, "双击打开来源文档")
            source_item.addChild(child)
            if document.source_url or document.document_url:
                self.current_urls.append(document.source_url or document.document_url or "")
        source_item.addChild(
            QTreeWidgetItem([f"事件 ID {cluster.event_id} · 首次 {cluster.first_seen_at.strftime('%m-%d %H:%M')} · 最近 {cluster.last_seen_at.strftime('%m-%d %H:%M')}"])
        )
        if detail.claims:
            review_item = QTreeWidgetItem(["候选事实复核状态"])
            review_item.setFirstColumnSpanned(True)
            review_item.setForeground(0, QColor(COLOR_LINK))
            self.detail_tree.addTopLevelItem(review_item)
            for line in _claim_review_lines(detail.claims[0]):
                review_item.addChild(QTreeWidgetItem([line]))
        self.detail_tree.expandAll()
        self.open_button.setText("打开原文")
        self.open_button.setEnabled(bool(self.current_urls))


    def set_discovery(
        self,
        row: DiscoveryViewRow,
        quality_label: str,
        *,
        document: object | None = None,
    ) -> None:
        """待核验视图详情（plan.md 里程碑 7）：候选尚非研究结论。

        只展示公开列表项的元数据与正文状态，不显示分数、机制或任何投资
        结论；“尚非研究结论”横幅始终可见，原文可一键打开。
        """

        self.current_urls = []
        if row.document_url:
            self.current_urls.append(row.document_url)
        self.title_label.setText(row.stock_name)
        self.meta_label.setText(
            f"{row.stock_code} · {row.discovery_type_label} · "
            f"{row.source_name} · 质量状态 {quality_label}"
        )
        self.banner_label.setText("尚非研究结论：待核验候选，需正文证据才能进入确定性利好或潜在催化榜")
        self.banner.show()
        parts = [
            f"正文状态 {row.parse_status_label}",
            row.published_at.strftime("%Y-%m-%d %H:%M")
            if row.published_at
            else "发布时间 —",
        ]
        self.summary_label.setText(" · ".join(parts))

        self.detail_tree.clear()
        self.detail_tree.setColumnCount(1)
        self.detail_tree.setHeaderHidden(True)

        title_item = QTreeWidgetItem(["原始标题"])
        title_item.setFirstColumnSpanned(True)
        title_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(title_item)
        title_item.addChild(QTreeWidgetItem([row.title or "—"]))

        trigger_item = QTreeWidgetItem(["触发原因"])
        trigger_item.setFirstColumnSpanned(True)
        trigger_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(trigger_item)
        trigger_item.addChild(QTreeWidgetItem([row.trigger_reason or "—"]))

        status_item = QTreeWidgetItem(["正文状态"])
        status_item.setFirstColumnSpanned(True)
        status_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(status_item)
        status_item.addChild(QTreeWidgetItem([row.parse_status_label]))

        meta_item = QTreeWidgetItem(["元数据"])
        meta_item.setFirstColumnSpanned(True)
        meta_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(meta_item)
        if row.published_at:
            meta_item.addChild(
                QTreeWidgetItem(
                    [f"发布时间 {row.published_at.strftime('%Y-%m-%d %H:%M:%S')}"]
                )
            )
        meta_item.addChild(QTreeWidgetItem([f"来源 {row.source_name}"]))
        meta_item.addChild(QTreeWidgetItem([f"文档 ID {row.document_id}"]))
        if row.document_url:
            meta_item.addChild(QTreeWidgetItem([f"原文 {row.document_url}"]))

        self.detail_tree.expandAll()
        self.open_button.setText("打开原文")
        self.open_button.setEnabled(bool(self.current_urls))

    def set_institution(self, detail: InstitutionDetail, quality_label: str) -> None:
        self.current_urls = []
        window_label = "60 日" if detail.window_kind == "persistence_60" else (
            "120 日" if detail.window_kind == "persistence_120" else "20 日"
        )
        self.title_label.setText(detail.stock_name)
        self.meta_label.setText(
            f"{detail.stock_code} · {window_label}机构关注 · 质量状态 {quality_label}"
        )
        self.banner.hide()
        parts = self._metric_parts(detail)
        self.summary_label.setText(" · ".join(parts) if parts else "（暂无窗口指标）")

        self.detail_tree.clear()
        self.detail_tree.setColumnCount(1)
        self.detail_tree.setHeaderHidden(True)

        metrics_item = QTreeWidgetItem(["窗口指标组成"])
        metrics_item.setFirstColumnSpanned(True)
        metrics_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(metrics_item)
        for part in self._metric_lines(detail):
            metrics_item.addChild(QTreeWidgetItem([part]))
        if detail.window_kind == "persistence_120" and detail.comparison_metrics:
            comparison_item = QTreeWidgetItem(["120 日结构比较（最近 60 日 vs 此前 60 日）"])
            comparison_item.setFirstColumnSpanned(True)
            comparison_item.setForeground(0, QColor(COLOR_LINK))
            self.detail_tree.addTopLevelItem(comparison_item)
            comparison_item.addChild(QTreeWidgetItem([f"新增机构集团：{'、'.join(detail.comparison_metrics.get('new_groups', [])) or '无' }"]))
            comparison_item.addChild(QTreeWidgetItem([f"流失机构集团：{'、'.join(detail.comparison_metrics.get('lost_groups', [])) or '无' }"]))
            for kind, change in (detail.comparison_metrics.get("type_share_changes") or {}).items():
                label = INSTITUTION_TYPE_LABELS.get(str(kind), str(kind))
                comparison_item.addChild(
                    QTreeWidgetItem([f"{label}占比变化 {float(change):+.1%}"])
                )
            high = detail.comparison_metrics.get("high_depth_ratio_change")
            if high is not None:
                comparison_item.addChild(QTreeWidgetItem([f"高深度问题占比变化 {float(high):+.1%}"]))

        activity_item = QTreeWidgetItem(["活动时间线"])
        activity_item.setFirstColumnSpanned(True)
        activity_item.setForeground(0, QColor(COLOR_LINK))
        self.detail_tree.addTopLevelItem(activity_item)
        if not detail.activities:
            activity_item.addChild(QTreeWidgetItem(["（窗口内暂无已披露活动）"]))
        for activity in detail.activities:
            dates = "、".join(value.isoformat() for value in activity.activity_dates)
            date_note = "（披露日回退）" if activity.date_precision == "disclosure_end" else ""
            participants = detail.participants_by_activity.get(activity.activity_id, [])
            group_names = sorted(
                {
                    detail.institutions_by_id[item.institution_id].group_id
                    for item in participants
                    if item.institution_id in detail.institutions_by_id
                }
            )
            text = (
                f"{dates}{date_note} · {ACTIVITY_TYPE_LABELS.get(activity.activity_type, activity.activity_type)} · "
                f"问答 {activity.question_count}（高深度 {activity.high_depth_question_count}）· "
                f"机构集团 {len(group_names)} · 披露机构数 {activity.reported_participant_count or activity.named_participant_count}"
            )
            child = QTreeWidgetItem([text])
            document = detail.documents_by_id.get(activity.source_document_id)
            if document is not None and (document.source_url or document.document_url):
                child.setData(0, Qt.UserRole, document.source_url or document.document_url or "")
                child.setToolTip(0, "双击打开披露原文")
                self.current_urls.append(document.source_url or document.document_url or "")
            activity_item.addChild(child)
            for participant in participants:
                institution = detail.institutions_by_id.get(participant.institution_id)
                if institution is None:
                    continue
                name = institution.canonical_name
                if institution.group_id != institution.institution_id:
                    name += f"（集团 {institution.group_id}）"
                type_label = INSTITUTION_TYPE_LABELS.get(institution.institution_type, institution.institution_type)
                status_label = {
                    "verified": "已核验",
                    "normalized": "规则归一",
                    "needs_review": "待核",
                }.get(institution.verification_status, institution.verification_status)
                analyst = f" · 分析师 {participant.analyst_name}" if participant.analyst_name else ""
                activity_item.addChild(
                    QTreeWidgetItem([f"  参与机构：{name} · {type_label} · {status_label}{analyst}"])
                )
            if activity.topic_counts:
                topics = "、".join(
                    f"{TOPIC_LABELS.get(topic, topic)} {count}"
                    for topic, count in sorted(activity.topic_counts.items(), key=lambda item: (-item[1], item[0]))
                )
                activity_item.addChild(QTreeWidgetItem([f"  关注主题：{topics}"]))
        if detail.reported_counts_by_activity:
            counts_item = QTreeWidgetItem(["披露总数（分列，不虚构实体）"])
            counts_item.setFirstColumnSpanned(True)
            counts_item.setForeground(0, QColor(COLOR_LINK))
            self.detail_tree.addTopLevelItem(counts_item)
            for activity in detail.activities:
                reported = detail.reported_counts_by_activity.get(
                    activity.activity_id
                )
                if reported is None:
                    continue
                counts_item.addChild(
                    QTreeWidgetItem(
                        [f"明确列名研究机构 {reported.named_research_count} 家"]
                    )
                )
                counts_item.addChild(
                    QTreeWidgetItem(
                        [f"全部列名组织 {reported.all_named_org_count} 家"]
                    )
                )
                counts_item.addChild(
                    QTreeWidgetItem(
                        [
                            (
                                f"披露机构总数 {reported.reported_institution_count} 家"
                                if reported.reported_institution_count is not None
                                else "披露机构总数 —（未披露）"
                            )
                        ]
                    )
                )
                if reported.reported_institution_count is not None:
                    unnamed = max(
                        0,
                        reported.reported_institution_count
                        - reported.named_research_count,
                    )
                    counts_item.addChild(
                        QTreeWidgetItem(
                            [
                                f"未列名参与者约 {unnamed} 家"
                                "（据披露总数推算，不生成实体）"
                            ]
                        )
                    )
        self.detail_tree.expandAll()
        self.open_button.setText("打开原文")
        self.open_button.setEnabled(bool(self.current_urls))

    def _metric_parts(self, detail: InstitutionDetail) -> list[str]:
        metrics = detail.metrics
        if not metrics:
            return []
        if detail.window_kind == "z20":
            z20 = metrics.get("z20")
            parts = [f"z20 {float(z20):.2f}" if z20 is not None else "z20 冷启动"]
            parts.append(f"机构集团 {metrics.get('current_unique_groups', 0)}")
            parts.append(f"新增 {metrics.get('new_groups', 0)}")
            return parts
        parts = [f"持续关注分 {float(metrics.get('persistence_score', 0.0)):.1f}"]
        parts.append(f"活跃周 {metrics.get('active_weeks', 0)}")
        parts.append(f"机构集团 {metrics.get('unique_groups', 0)}")
        return parts

    def _metric_lines(self, detail: InstitutionDetail) -> list[str]:
        metrics = detail.metrics
        if not metrics:
            return ["（暂无指标快照，刷新后生成）"]
        if detail.window_kind == "z20":
            z20 = metrics.get("z20")
            percentile = metrics.get("industry_percentile")
            lines = [
                f"z20 = {float(z20):.2f}" if z20 is not None else "z20 = 冷启动（交易日覆盖不足 120 天）",
                f"当前独立机构集团数 {metrics.get('current_unique_groups', 0)}",
                f"新增机构集团数 {metrics.get('new_groups', 0)}",
                f"明确披露分析师数 {metrics.get('analyst_count', 0)}",
                f"高深度问题占比 {float(metrics.get('high_depth_ratio', 0.0)) * 100:.1f}%",
                f"行业关注分位 {float(percentile) * 100:.1f}%" if percentile is not None else "行业关注分位 样本不足",
            ]
            return lines
        lines = [
            f"持续关注分 {float(metrics.get('persistence_score', 0.0)):.1f}",
            f"活跃周数 {metrics.get('active_weeks', 0)} / 比例 {float(metrics.get('active_week_ratio', 0.0)) * 100:.1f}%",
            f"独立机构集团数 {metrics.get('unique_groups', 0)}",
            f"重复跟进比例 {float(metrics.get('repeat_followup_ratio', 0.0)) * 100:.1f}%",
            f"研究深度 {float(metrics.get('depth_score', 0.0)) * 100:.1f}%",
            f"单日集中度 {float(metrics.get('single_day_concentration', 0.0)) * 100:.1f}%",
            f"覆盖交易日 {metrics.get('covered_trading_days', 0)}",
        ]
        return lines

    def open_primary(self) -> None:
        if self.current_urls:
            self.open_url_requested.emit(self.current_urls[0])

    def _open_item_url(self, item: QTreeWidgetItem, _column: int) -> None:
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
        ai_enabled: bool = False,
        ai_base_url: str = "",
        ai_model: str = "",
        ai_timeout_seconds: float = 30.0,
        ai_has_credential: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        self.setStyleSheet(DARK_STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        self.ai_credential_changed = False
        self.ai_credential_cleared = False

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

        ai_page = QWidget()
        ai_layout = QVBoxLayout(ai_page)
        ai_layout.setContentsMargins(16, 16, 16, 16)
        ai_layout.setSpacing(10)
        self.ai_enabled_check = QCheckBox("启用 AI 抽取增强（默认关闭；关闭时仅使用规则管线）")
        self.ai_enabled_check.setChecked(ai_enabled)
        ai_layout.addWidget(self.ai_enabled_check)

        ai_form = QFormLayout()
        ai_form.setVerticalSpacing(10)
        self.ai_base_url_input = QLineEdit(ai_base_url)
        self.ai_base_url_input.setPlaceholderText("https://api.openai.com/v1")
        self.ai_base_url_input.setClearButtonEnabled(True)
        ai_form.addRow("接口地址", self.ai_base_url_input)
        self.ai_model_input = QLineEdit(ai_model)
        self.ai_model_input.setPlaceholderText("gpt-4o-mini")
        self.ai_model_input.setClearButtonEnabled(True)
        ai_form.addRow("模型名称", self.ai_model_input)
        self.ai_timeout_input = QSpinBox()
        self.ai_timeout_input.setRange(10, 300)
        self.ai_timeout_input.setSuffix(" 秒")
        self.ai_timeout_input.setValue(int(ai_timeout_seconds))
        ai_form.addRow("请求超时", self.ai_timeout_input)
        self.ai_key_input = QLineEdit()
        self.ai_key_input.setEchoMode(QLineEdit.Password)
        self.ai_key_input.setPlaceholderText("输入新密钥后保存（DPAPI 加密，不入数据库）")
        self.ai_key_input.setClearButtonEnabled(True)
        ai_form.addRow("API 密钥", self.ai_key_input)
        ai_layout.addLayout(ai_form)

        credential_row = QHBoxLayout()
        self.credential_status_label = QLabel(
            "密钥已配置" if ai_has_credential else "密钥未配置"
        )
        self.credential_status_label.setObjectName("detailMeta")
        credential_row.addWidget(self.credential_status_label)
        credential_row.addStretch(1)
        self.clear_credential_button = QPushButton("清除 AI 凭据…")
        self.clear_credential_button.setObjectName("dangerButton")
        self.clear_credential_button.setIcon(icon("trash"))
        self.clear_credential_button.setEnabled(ai_has_credential)
        self.clear_credential_button.clicked.connect(self._clear_credential)
        credential_row.addWidget(self.clear_credential_button)
        ai_layout.addLayout(credential_row)

        ai_note = QLabel(
            "启用后仅向模型发送规则初筛后的事件代表文本；密钥使用 Windows DPAPI 加密保存在独立文件中，"
            "不会写入数据库、日志、导出或剪贴板。模型故障只降级当前事件，不影响整榜。"
        )
        ai_note.setObjectName("mutedLabel")
        ai_note.setWordWrap(True)
        ai_layout.addWidget(ai_note)
        ai_layout.addStretch(1)
        tabs.addTab(ai_page, "AI 增强")
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
            "ai_enabled": self.ai_enabled_check.isChecked(),
            "ai_base_url": self.ai_base_url_input.text().strip(),
            "ai_model": self.ai_model_input.text().strip(),
            "ai_timeout_seconds": float(self.ai_timeout_input.value()),
            "ai_api_key": self.ai_key_input.text(),
            "ai_credential_cleared": self.ai_credential_cleared,
        }

    def _clear_credential(self) -> None:
        answer = QMessageBox.question(
            self,
            "清除 AI 凭据",
            "这会删除本机保存的 API 密钥（DPAPI 加密文件），无法恢复。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.ai_credential_cleared = True
        self.ai_credential_changed = False
        self.ai_key_input.clear()
        self.credential_status_label.setText("密钥未配置（已清除）")
        self.clear_credential_button.setEnabled(False)


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
                "原始榜：同花顺公开新闻、深交所互动易与上证e互动问答、东方财富官方人气榜和飙升榜。"
                "研究榜：巨潮资讯公告与调研栏目、上证e互动上市公司发布，覆盖事件聚类、短期催化与机构关注指标。"
                "榜单按公开口径展示，软件不推导未公开的热度权重，也不预测股价。",
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


def _claim_review_lines(claim: object) -> list[str]:
    """v2 里程碑 5：候选事实复核状态（未复核/复核失败/规则与AI一致/分歧）。"""

    ai_gate = next(
        (
            gate
            for gate in claim.gate_trace
            if gate.get("gate") == "ai_review"
        ),
        None,
    )
    if claim.review_status == "verified":
        return ["规则与AI一致（已复核）"]
    if ai_gate is not None:
        reason = str(ai_gate.get("reason") or "")
        if ai_gate.get("passed"):
            return ["规则与AI一致（已复核）"]
        if "分歧" in reason:
            return [f"规则与AI分歧：{reason}"]
        return [f"复核失败：{reason}"]
    return ["未复核（规则结果，AI 未启用或无需复核）"]
