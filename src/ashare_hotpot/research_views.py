"""UI-facing research board loaders (plan.md milestone 5).

This module composes persisted research models (EventSignal / EventExtraction /
EventCluster / Z20Row-style metric snapshots / ResearchActivity) into stable
display rows.  It never touches the network, never reads API keys and never
infers investment opinions; every value shown stays traceable to the persisted
evidence or to the fixed enums below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .config import SHANGHAI_TZ
from .discovery import (
    QUEUE_STATUS_AWAITING_REVIEW,
    QUEUE_STATUS_EMPTY_TEXT,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_PENDING_ATTACHMENT,
    discovery_type_label,
    queue_status_label,
)
from .institution_metrics import build_research_coverage
from .models import (
    DiscoveryCandidate,
    DiscoveryViewRow,
    EventCluster,
    EventClaim,
    EventExtraction,
    EventSignal,
    EvidenceRef,
    Institution,
    InstitutionZ20ViewRow,
    PersistenceViewRow,
    ResearchActivity,
    ResearchCoverage,
    ResearchParticipant,
    ReportedParticipantCount,
    ShortTermViewRow,
    SourceDocument,
)
from .storage import Storage


# Fixed enum labels per plan.md 10.1 / 12.3 / 12.1.  The UI only maps these
# persisted enum values to Chinese display text; it never creates new enums.
EVENT_TYPE_LABELS: dict[str, str] = {
    "earnings_upgrade": "业绩上修",
    "major_contract": "重大订单",
    "price_increase": "产品涨价",
    "approval": "获批认证",
    "buyback_or_increase": "回购增持",
    "mna": "并购重组",
    "capacity_launch": "产能投产",
    "direct_policy_benefit": "直接政策受益",
    "customer_breakthrough": "重要客户突破",
      "subsidy_or_compensation": "补贴赔偿",
      "shareholder_return": "股东回报",
      "rd_milestone": "研发里程碑",
      "risk_resolution": "风险解除",
      "equity_incentive": "股权激励",
      "financing_completion": "融资完成",
      "asset_disposal": "资产处置",
      "unsupported_event_type": "未支持类型",
  }

TOPIC_LABELS: dict[str, str] = {
    "growth": "增长",
    "profitability": "盈利",
    "orders": "订单",
    "capacity": "产能",
    "products": "产品",
    "customers": "客户",
    "risks": "风险",
    "governance": "治理",
    "other": "其他",
}

INSTITUTION_TYPE_LABELS: dict[str, str] = {
    "brokerage": "券商",
    "public_fund": "公募基金",
    "private_fund": "私募基金",
    "insurance": "保险",
    "asset_management": "资管",
    "foreign_institution": "外资机构",
    "other": "其他",
}

ACTIVITY_TYPE_LABELS: dict[str, str] = {
    "research": "调研",
    "briefing": "说明会",
    "roadshow": "路演",
}

DISCOVERY_STATUS_LABELS: dict[str, str] = {
    QUEUE_STATUS_PENDING_ATTACHMENT: "待解析",
    QUEUE_STATUS_AWAITING_REVIEW: "待核验",
    QUEUE_STATUS_EMPTY_TEXT: "空文本",
    QUEUE_STATUS_FAILED: "解析失败",
}

# Coverage states used by table rows and the view header.  They map to visible
# Chinese text so cold start / partial coverage / calendar fallback never hide
# behind an opaque quality dialog.
COVERAGE_STATE_LABELS: dict[str, str] = {
    "ok": "正常",
    "partial": "部分覆盖",
    "cold_start": "冷启动",
    "provisional": "暂定",
    "error": "来源失败",
}

EXTRACTOR_LABELS: dict[str, str] = {
    "rules": "规则",
    "llm": "AI 增强",
    "rules_fallback": "规则降级",
}


def extractor_label(kind: str) -> str:
    return EXTRACTOR_LABELS.get(kind, kind or "规则")


def event_type_label(event_type: str) -> str:
    return EVENT_TYPE_LABELS.get(event_type, event_type or "—")


def topic_label(topic: str) -> str:
    return TOPIC_LABELS.get(topic, topic)


def institution_type_label(kind: str) -> str:
    return INSTITUTION_TYPE_LABELS.get(kind, kind or "其他")


def coverage_state(
    coverage: ResearchCoverage | None,
    *,
    has_rows: bool,
    provisional_row: bool = False,
) -> str:
    """One visible quality state for a research view or a single row."""

    if provisional_row:
        return "provisional"
    if coverage is None or coverage.error:
        return "error" if coverage is not None else "cold_start"
    if not has_rows and (coverage.covered_start is None or not coverage.sources_scanned):
        return "cold_start"
    if (
        coverage.provisional
        or coverage.calendar_fallback
        or not coverage.reached_cutoff
        or coverage.trading_days_covered == 0
    ):
        return "partial"
    return "ok"


def format_key_metric(extraction: EventExtraction | None) -> str:
    """Render the first quantitative metric of an extraction, or an empty dash."""

    if extraction is None or not extraction.metrics:
        return "—"
    metric = extraction.metrics[0]
    name = str(metric.get("name") or "")
    value = metric.get("value")
    unit = str(metric.get("unit") or "")
    parts = [part for part in (name, _format_metric_value(value), unit) if part]
    return " ".join(parts) if parts else "—"


def _format_metric_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def format_counter_evidence(extraction: EventExtraction | None) -> str:
    """Summarize counter-evidence items for a table cell (short excerpt)."""

    if extraction is None or not extraction.counter_evidence:
        return ""
    labels: list[str] = []
    for item in extraction.counter_evidence:
        kind = str(item.get("kind") or "")
        summary = str(item.get("summary") or item.get("text") or "")
        labels.append(summary or kind)
    return "；".join(labels)[:240]


def load_short_term_rows(
    storage: Storage,
    board: str,
    *,
    coverage: ResearchCoverage | None = None,
) -> list[ShortTermViewRow]:
    """Load one short-term board (confirmed_positive / potential_catalyst)."""

    signals = storage.get_event_signals(board)
    if not signals:
        return []
    codes = {signal.stock_code for signal in signals}
    names = storage.get_stock_names(codes)
    industries = storage.get_stock_industries(codes)
    rows: list[ShortTermViewRow] = []
    for rank, signal in enumerate(signals, start=1):
        extraction = storage.get_event_extraction(signal.event_id, signal.stock_code)
        cluster = storage.get_event_cluster(signal.event_id)
        rows.append(
            ShortTermViewRow(
                rank=rank,
                stock_code=signal.stock_code,
                stock_name=names.get(signal.stock_code, signal.stock_code),
                industry=industries.get(signal.stock_code),
                event_type=event_type_label(
                    extraction.event_type if extraction else "unsupported_event_type"
                ),
                positive_mechanism=extraction.positive_mechanism if extraction else None,
                materiality_level=signal.materiality_level,
                key_metric=format_key_metric(extraction),
                certainty=signal.certainty,
                counter_evidence=format_counter_evidence(extraction),
                event_time=cluster.last_seen_at if cluster else None,
                quality_state=coverage_state(
                    coverage,
                    has_rows=True,
                    provisional_row=signal.provisional,
                ),
                extractor_label=extractor_label(
                    extraction.extractor_kind if extraction else "rules"
                ),
                provisional=signal.provisional,
                event_id=signal.event_id,
                board=signal.board,
                score=signal.score,
            )
        )
    return rows


def load_z20_rows(
    storage: Storage,
    *,
    coverage: ResearchCoverage | None = None,
) -> list[InstitutionZ20ViewRow]:
    """Load the 20-trading-day institution warming board from metric snapshots."""

    snapshots = storage.get_latest_institution_metric_snapshots("z20")
    if not snapshots:
        return []
    names = storage.get_stock_names(set(snapshots))
    industries = storage.get_stock_industries(set(snapshots))
    rows: list[InstitutionZ20ViewRow] = []
    for stock_code, (_snapshot_at, metrics) in snapshots.items():
        rows.append(
            InstitutionZ20ViewRow(
                rank=0,
                stock_code=stock_code,
                stock_name=names.get(stock_code, stock_code),
                industry=industries.get(stock_code),
                z20=_as_float(metrics.get("z20")),
                current_unique_groups=int(metrics.get("current_unique_groups", 0)),
                new_groups=int(metrics.get("new_groups", 0)),
                analyst_count=int(metrics.get("analyst_count", 0)),
                high_depth_ratio=float(metrics.get("high_depth_ratio", 0.0)),
                question_count=int(metrics.get("question_count", 0)),
                recent_activity=_as_date(metrics.get("recent_activity")),
                industry_percentile=_as_float(metrics.get("industry_percentile")),
                industry_sample_size=int(metrics.get("industry_sample_size", 0)),
                provisional=bool(metrics.get("provisional", False)),
                coverage_state=coverage_state(
                    coverage,
                    has_rows=True,
                    provisional_row=bool(metrics.get("provisional", False)),
                ),
            )
        )
    return _sort_z20(rows)


def load_persistence_rows(
    storage: Storage,
    window_kind: str,
    *,
    coverage: ResearchCoverage | None = None,
) -> list[PersistenceViewRow]:
    """Load the 60/120-trading-day persistence board from metric snapshots."""

    snapshots = storage.get_latest_institution_metric_snapshots(window_kind)
    if not snapshots:
        return []
    names = storage.get_stock_names(set(snapshots))
    rows: list[PersistenceViewRow] = []
    for stock_code, (_snapshot_at, metrics) in snapshots.items():
        provisional = bool(metrics.get("provisional", False))
        rows.append(
            PersistenceViewRow(
                rank=0,
                stock_code=stock_code,
                stock_name=names.get(stock_code, stock_code),
                window_kind=window_kind,
                persistence_score=float(metrics.get("persistence_score", 0.0)),
                active_weeks=int(metrics.get("active_weeks", 0)),
                active_week_ratio=float(metrics.get("active_week_ratio", 0.0)),
                unique_groups=int(metrics.get("unique_groups", 0)),
                repeat_followup_ratio=float(metrics.get("repeat_followup_ratio", 0.0)),
                depth_score=float(metrics.get("depth_score", 0.0)),
                single_day_concentration=float(
                    metrics.get("single_day_concentration", 0.0)
                ),
                topics={
                    str(key): int(value)
                    for key, value in (metrics.get("topics") or {}).items()
                },
                recent_activity=_as_date(metrics.get("recent_activity")),
                covered_trading_days=int(metrics.get("covered_trading_days", 0)),
                coverage_state=coverage_state(
                    coverage,
                    has_rows=True,
                    provisional_row=provisional,
                ),
                provisional=provisional,
            )
        )
    return _sort_persistence(rows)


def _discovery_status_rank(status: str) -> int:
    order = {
        QUEUE_STATUS_PENDING_ATTACHMENT: 0,
        QUEUE_STATUS_AWAITING_REVIEW: 1,
        QUEUE_STATUS_EMPTY_TEXT: 2,
        QUEUE_STATUS_FAILED: 3,
    }
    return order.get(status, 9)


def load_discovery_rows(
    storage: Storage,
    *,
    coverage: ResearchCoverage | None = None,
) -> list[DiscoveryViewRow]:
    """Load the 待核验 discovery view (后 1.1.0 可靠性里程碑).

    Candidates already promoted to a strict board are excluded (they are
    visible in 确定性利好/潜在催化); everything else stays visible by
    待解析 → 待核验 → 空文本/解析失败, newest published first.  Rows never
    pretend to be research conclusions.
    """

    candidates = storage.get_discovery_candidates()
    if not candidates:
        return []
    promoted = storage.get_promoted_document_ids()
    codes = {
        candidate.stock_codes[0]
        for candidate in candidates
        if candidate.stock_codes
    }
    names = storage.get_stock_names(codes)
    rows: list[DiscoveryViewRow] = []
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            _discovery_status_rank(candidate.queue_status),
            -int(candidate.published_at.timestamp()),
            candidate.document_id,
        ),
    )
    for rank, candidate in enumerate(ordered, start=1):
        if candidate.document_id in promoted:
            continue
        stock_code = candidate.stock_codes[0] if candidate.stock_codes else ""
        rows.append(
            DiscoveryViewRow(
                rank=rank,
                stock_code=stock_code,
                stock_name=(
                    names.get(stock_code, stock_code) if stock_code else "—"
                ),
                discovery_type=candidate.discovery_type,
                discovery_type_label=discovery_type_label(candidate.discovery_type),
                title=candidate.title,
                trigger_reason=candidate.trigger_reason,
                parse_status=candidate.queue_status,
                parse_status_label=queue_status_label(candidate.queue_status),
                published_at=candidate.published_at,
                source_name=candidate.source_name,
                document_id=candidate.document_id,
                document_url=candidate.document_url,
                quality_state=coverage_state(coverage, has_rows=True),
            )
        )
    return rows


def build_discovery_quality(
    settings,
    storage: Storage,
    *,
    coverage: ResearchCoverage | None = None,
) -> str:
    """数据质量文本：每个来源的已发现/待解析/已解析/空文本/失败/最早待处理。"""

    stats = storage.get_discovery_stats()
    lines: list[str] = []
    for stat in stats:
        source_name = str(stat["source_name"] or stat["source_key"])
        earliest = stat["earliest_pending_ts"]
        line = (
            f"{source_name}：已发现 {stat['discovered']} · "
            f"待解析 {stat['pending']} · 已解析/待核验 {stat['awaiting']} · "
            f"空文本 {stat['empty_text']} · 失败 {stat['failed']}"
        )
        if earliest is not None:
            earliest_at = datetime.fromtimestamp(
                int(earliest), tz=SHANGHAI_TZ
            )
            line += f" · 最早待处理 {earliest_at.strftime('%m-%d %H:%M')}"
        lines.append(line)
    if not lines:
        lines.append("尚未发现公开列表项；刷新与回填后生成待核验候选。")
    if coverage is not None:
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
        lines.append(coverage_text)
    if getattr(settings, "research_pipeline_version", "v2") == "v1":
        lines.append(
            "机构解析使用 v1 兼容口径（回退模式，发布前整篇正文行级提取）"
        )
    policy_sources = tuple(getattr(settings, "policy_sources", ()) or ())
    if policy_sources:
        policy_lines: list[str] = []
        for config in policy_sources:
            manifests = storage.get_source_manifests(config.key)
            latest = manifests[0] if manifests else None
            if latest is None:
                policy_lines.append(f"{config.name}：未同步")
            elif (
                latest.failure_intervals
                and latest.failure_intervals[-1].ended_at is None
            ):
                policy_lines.append(
                    f"{config.name}：失败（"
                    f"{latest.failure_intervals[-1].reason[:24]}…）"
                )
            else:
                policy_lines.append(
                    f"{config.name}：{latest.total_count} 条"
                )
        lines.append("政策源：" + " · ".join(policy_lines))
    return "\n".join(lines)


def _sort_z20(rows: list[InstitutionZ20ViewRow]) -> list[InstitutionZ20ViewRow]:
    """plan.md 13.2 ordering: full z20 first, then cold-start raw metrics."""

    full = [row for row in rows if row.z20 is not None]
    cold = [row for row in rows if row.z20 is None]
    full.sort(
        key=lambda row: (
            -row.z20,
            -row.new_groups,
            -row.high_depth_ratio,
            _date_sort_key(row.recent_activity),
            row.stock_code,
        )
    )
    cold.sort(
        key=lambda row: (
            -row.current_unique_groups,
            -row.new_groups,
            _date_sort_key(row.recent_activity),
            row.stock_code,
        )
    )
    return [
        _replace_rank(row, rank)
        for rank, row in enumerate(full + cold, start=1)
    ]


def _sort_persistence(rows: list[PersistenceViewRow]) -> list[PersistenceViewRow]:
    rows = sorted(
        rows,
        key=lambda row: (
            -row.persistence_score,
            -row.active_weeks,
            -row.unique_groups,
            _date_sort_key(row.recent_activity),
            row.stock_code,
        ),
    )
    return [_replace_rank(row, rank) for rank, row in enumerate(rows, start=1)]


def _replace_rank(row, rank: int):
    from dataclasses import replace

    return replace(row, rank=rank)


def _date_sort_key(value: date | datetime | None) -> tuple[int, int]:
    """Descending-oriented date key: newer dates sort first, None last."""

    if value is None:
        return (0, 0)
    return (-value.year, -(value.month * 100 + value.day))


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


@dataclass(slots=True)
class EventDetail:
    """Everything the short-term detail panel needs for one signal row."""

    signal: EventSignal
    extraction: EventExtraction
    cluster: EventCluster
    stock_name: str = ""
    documents: tuple[SourceDocument, ...] = ()
    evidence_by_id: dict[str, EvidenceRef] = field(default_factory=dict)
    claims: tuple[EventClaim, ...] = ()


@dataclass(slots=True)
class InstitutionDetail:
    """Everything the institution detail panel needs for one board row."""

    stock_code: str
    stock_name: str
    window_kind: str
    coverage: ResearchCoverage | None
    metrics: dict[str, object] = field(default_factory=dict)
    comparison_metrics: dict[str, object] = field(default_factory=dict)
    activities: tuple[ResearchActivity, ...] = ()
    participants_by_activity: dict[str, list[ResearchParticipant]] = field(
        default_factory=dict
    )
    institutions_by_id: dict[str, Institution] = field(default_factory=dict)
    documents_by_id: dict[str, SourceDocument] = field(default_factory=dict)
    evidence_by_id: dict[str, EvidenceRef] = field(default_factory=dict)
    reported_counts_by_activity: dict[str, ReportedParticipantCount] = field(
        default_factory=dict
    )


def load_event_detail(
    storage: Storage,
    event_id: str,
    stock_code: str,
) -> EventDetail | None:
    signal = next(
        (
            item
            for item in storage.get_event_signals()
            if item.event_id == event_id and item.stock_code == stock_code
        ),
        None,
    )
    extraction = storage.get_event_extraction(event_id, stock_code)
    cluster = storage.get_event_cluster(event_id)
    if signal is None or extraction is None or cluster is None:
        return None
    stock_name = storage.get_stock_names({stock_code}).get(stock_code, stock_code)
    documents = storage.get_source_documents_by_ids(cluster.document_ids)
    evidence_by_id: dict[str, EvidenceRef] = {}
    for document in documents:
        for ref in storage.get_evidence_refs_for_document(document.document_id):
            evidence_by_id.setdefault(ref.evidence_id, ref)
    return EventDetail(
        signal=signal,
        extraction=extraction,
        cluster=cluster,
        stock_name=stock_name,
        documents=documents,
        evidence_by_id=evidence_by_id,
        claims=tuple(
            claim
            for claim in storage.get_event_claims_by_stock(stock_code)
            if claim.document_id in {doc.document_id for doc in documents}
            and claim.event_type == extraction.event_type
        ),
    )


def load_institution_detail(
    storage: Storage,
    stock_code: str,
    stock_name: str,
    window_kind: str,
    *,
    start_date: date,
    end_date: date,
    coverage: ResearchCoverage | None,
) -> InstitutionDetail:
    metrics: dict[str, object] = {}
    latest = storage.get_latest_institution_metric_snapshots(window_kind)
    if stock_code in latest:
        metrics = dict(latest[stock_code][1])
    comparison_metrics: dict[str, object] = {}
    if window_kind == "persistence_120":
        comparison = storage.get_latest_institution_metric_snapshots(
            "persistence_120_detail"
        )
        if stock_code in comparison:
            comparison_metrics = dict(comparison[stock_code][1])
    activities = storage.get_research_activities_between(
        start_date, end_date, stock_code=stock_code
    )
    participants_by_activity: dict[str, list[ResearchParticipant]] = {}
    institutions_by_id: dict[str, Institution] = {}
    documents_by_id: dict[str, SourceDocument] = {}
    evidence_by_id: dict[str, EvidenceRef] = {}
    reported_counts_by_activity: dict[str, ReportedParticipantCount] = {}
    for activity in activities:
        participants = storage.get_research_participants(activity.activity_id)
        participants_by_activity[activity.activity_id] = participants
        for participant in participants:
            if participant.institution_id not in institutions_by_id:
                institution = storage.get_institution(participant.institution_id)
                if institution is not None:
                    institutions_by_id[participant.institution_id] = institution
        if activity.source_document_id not in documents_by_id:
            document = storage.get_source_document(activity.source_document_id)
            if document is not None:
                documents_by_id[activity.source_document_id] = document
                for ref in storage.get_evidence_refs_for_document(
                    activity.source_document_id
                ):
                    evidence_by_id.setdefault(ref.evidence_id, ref)
        reported = storage.get_reported_participant_count(activity.activity_id)
        if reported is not None:
            reported_counts_by_activity[activity.activity_id] = reported
    return InstitutionDetail(
        stock_code=stock_code,
        stock_name=stock_name,
        window_kind=window_kind,
        coverage=coverage,
        metrics=metrics,
        comparison_metrics=comparison_metrics,
        activities=tuple(activities),
        participants_by_activity=participants_by_activity,
        institutions_by_id=institutions_by_id,
        documents_by_id=documents_by_id,
        evidence_by_id=evidence_by_id,
        reported_counts_by_activity=reported_counts_by_activity,
    )


def research_coverage(
    settings,
    storage: Storage,
    *,
    now: datetime | None = None,
) -> ResearchCoverage:
    """Public shortcut used by the window and tests."""

    return build_research_coverage(settings, storage, now=now)
