from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from datetime import datetime

from .clustering import (
    MERGE_WINDOW,
    PersistentEventClusterer,
    _report_period_key,
    provider_priority,
)
from .config import AppSettings
from .ambiguity_review import (
    REVIEW_STATUS_AGREE,
    REVIEW_STATUS_DIVERGE,
    REVIEW_STATUS_REVIEW_FAILED,
    build_ambiguity_reviewer,
    should_review_claim,
)
from .dedupe import normalize_title
from .models import (
    EVENT_CLAIM_REVIEW_VERIFIED,
    EVENT_CLAIM_REVIEW_PENDING,
    EventClaim,
    EventCluster,
    EventExtraction,
    EventSignal,
    SourceDocument,
)
from .storage import Storage


logger = logging.getLogger(__name__)

# Current short-term signals use public announcements and explicit company
# statements.  Legacy institution-activity documents stay in SQLite for
# compatibility but never enter this active signal pipeline.
ACTIVE_SIGNAL_DOCUMENT_KINDS = frozenset({"announcement", "news"})

# plan.md 10.4: source confidence by provider.
SOURCE_CONFIDENCE: dict[str, float] = {
    "cninfo": 1.00,   # 交易所或巨潮正式披露
    "sse": 0.90,      # 交易所官方互动中的公司正式回复/发布
    "irm": 0.90,
    "ths": 0.60,      # 聚合媒体正文，无法定位原始公告
}


def source_confidence(document: SourceDocument) -> float:
    return SOURCE_CONFIDENCE.get(document.provider_key, 0.60)


def materiality_score(level: int) -> float:
    """plan.md 10.5: M 0/1/2/3/4 -> 0/25/50/75/100."""

    return {0: 0.0, 1: 25.0, 2: 50.0, 3: 75.0, 4: 100.0}.get(level, 0.0)


def _counter_kinds(extraction: EventExtraction) -> set[str]:
    return {
        str(item.get("kind"))
        for item in extraction.counter_evidence
        if str(item.get("kind"))
    }


@dataclass(frozen=True, slots=True)
class SignalDecision:
    signal: EventSignal | None
    rejection_reason: str | None = None


class SignalScorer:
    """plan.md 10.5/10.6 transparent scoring and board gates."""

    def decide(
        self,
        extraction: EventExtraction,
        cluster: EventCluster,
        *,
        representative: SourceDocument | None,
        now: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> SignalDecision:
        if extraction.no_valid_signal or not extraction.positive_mechanism:
            return SignalDecision(None, "无有效正向机制")
        kinds = _counter_kinds(extraction)
        if "title_body_conflict" in kinds:
            return SignalDecision(None, "标题与正文结论冲突")

        source_conf = (
            source_confidence(representative) if representative is not None else 0.60
        )
        confidence = min(source_conf, extraction.certainty)
        materiality = materiality_score(extraction.materiality_level)
        timeliness = self._timeliness(
            cluster.last_seen_at, window_start, window_end
        )
        penalty = self._penalty(extraction, cluster)
        score = (
            confidence
            * (
                0.35 * materiality
                + 0.25 * extraction.unexpectedness
                + 0.20 * extraction.novelty
                + 0.20 * timeliness
            )
            - penalty
        )

        confirmed = (
            extraction.materiality_level >= 2
            and extraction.certainty >= 0.70
            and "high_uncertainty" not in kinds
            and score >= 60.0
        )
        catalyst = (
            not confirmed
            and extraction.materiality_level >= 1
            and extraction.certainty >= 0.40
            and score >= 35.0
        )
        if confirmed:
            return SignalDecision(
                EventSignal(
                    event_id=extraction.event_id,
                    stock_code=extraction.stock_code,
                    board="confirmed_positive",
                    score=round(score, 2),
                    source_confidence=round(source_conf, 2),
                    materiality_level=extraction.materiality_level,
                    certainty=extraction.certainty,
                    unexpectedness=extraction.unexpectedness,
                    novelty=extraction.novelty,
                    timeliness=round(timeliness, 2),
                    penalty=round(penalty, 2),
                    provisional=False,
                ),
                None,
            )
        if catalyst:
            return SignalDecision(
                EventSignal(
                    event_id=extraction.event_id,
                    stock_code=extraction.stock_code,
                    board="potential_catalyst",
                    score=round(score, 2),
                    source_confidence=round(source_conf, 2),
                    materiality_level=extraction.materiality_level,
                    certainty=extraction.certainty,
                    unexpectedness=extraction.unexpectedness,
                    novelty=extraction.novelty,
                    timeliness=round(timeliness, 2),
                    penalty=round(penalty, 2),
                    provisional=True,
                ),
                None,
            )
        reasons: list[str] = []
        if extraction.materiality_level < 1:
            reasons.append("重大性不足1级")
        elif extraction.materiality_level < 2:
            reasons.append("重大性不足2级")
        if extraction.certainty < 0.40:
            reasons.append("确定性不足0.40")
        elif extraction.certainty < 0.70:
            reasons.append("确定性不足0.70")
        if score < 35.0:
            reasons.append("得分不足35")
        elif score < 60.0:
            reasons.append("得分不足60")
        if "high_uncertainty" in kinds:
            reasons.append("高不确定性反证")
        if not reasons:
            reasons.append("未达确定性利好/潜在催化门槛")
        return SignalDecision(None, "；".join(reasons))

    @staticmethod
    def _timeliness(
        published_at: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        """Linear decay from 100 at publish time to 0 at the window boundary.

        Future or abnormal timestamps are clamped to 100 (no extra score).
        """

        duration = max(
            (window_end - window_start).total_seconds(),
            1.0,
        )
        remaining = (published_at - window_start).total_seconds()
        if remaining < 0.0:
            return 0.0
        return 100.0 * min(1.0, remaining / duration)

    @staticmethod
    def _penalty(extraction: EventExtraction, cluster: EventCluster) -> float:
        """plan.md 10.5 penalties, additive and capped at 80."""

        kinds = _counter_kinds(extraction)
        total = 0.0
        if "partial" in kinds:
            total += 15.0
        if "high_uncertainty" in kinds:
            total += 35.0
        if extraction.unexpectedness == 25.0:
            total += 20.0  # 此前已预告/正常进展
        if extraction.materiality_level == 0:
            total += 20.0  # 相对规模低于 1 级门槛
        if extraction.novelty == 30.0 and cluster.historical_similar_event_id:
            total += 40.0  # 旧闻或近重复
        return min(total, 80.0)

    @staticmethod
    def explain_rejection(extraction: EventExtraction) -> str:
        """Deterministic rejection explanation derived from persisted fields."""

        if extraction.no_valid_signal or not extraction.positive_mechanism:
            return "无有效正向机制"
        kinds = _counter_kinds(extraction)
        if "title_body_conflict" in kinds:
            return "标题与正文结论冲突"
        reasons: list[str] = []
        if extraction.materiality_level < 1:
            reasons.append("重大性不足1级")
        elif extraction.materiality_level < 2:
            reasons.append("重大性不足2级")
        if extraction.certainty < 0.40:
            reasons.append("确定性不足0.40")
        elif extraction.certainty < 0.70:
            reasons.append("确定性不足0.70")
        if "high_uncertainty" in kinds:
            reasons.append("高不确定性反证")
        if not reasons:
            reasons.append("未达确定性利好/潜在催化门槛")
        return "；".join(reasons)


def sort_signals(
    signals: list[EventSignal],
    clusters_by_event: dict[str, EventCluster],
) -> list[EventSignal]:
    """plan.md 10.6: score desc, M desc, certainty desc, event time desc,
    stock code asc."""

    return sorted(
        signals,
        key=lambda signal: (
            -signal.score,
            -signal.materiality_level,
            -signal.certainty,
            -clusters_by_event[signal.event_id].last_seen_at.timestamp(),
            signal.stock_code,
        ),
    )


@dataclass(frozen=True, slots=True)
class ShortTermRunResult:
    documents_considered: int
    clusters_created: int
    clusters_merged: int
    clusters_processed: int
    extractions_persisted: int
    signals_confirmed: int
    signals_catalyst: int
    rejected: int
    signals: tuple[EventSignal, ...]
    completed: bool
    errors: tuple[str, ...]


class ShortTermBoardService:
    """Persistent clustering -> extraction -> scoring -> short-term boards.

    Runs entirely offline on persisted ``source_documents``; a failing
    extractor only degrades the current event, never the whole refresh.
    """

    def __init__(
        self,
        settings: AppSettings,
        storage: Storage,
        *,
        clusterer: PersistentEventClusterer | None = None,
        extractor: object | None = None,
        scorer: SignalScorer | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.clusterer = clusterer or PersistentEventClusterer(storage)
        self.extractor = extractor
        self.scorer = scorer or SignalScorer()
        self._reviewer = None

    def _default_extractor(self) -> object:
        from .ai_extractor import build_signal_extractor

        return build_signal_extractor(self.settings, self.storage)

    def run(
        self,
        *,
        now: datetime,
        window_start: datetime,
        window_end: datetime,
        publish: bool = True,
    ) -> ShortTermRunResult:
        extractor = self.extractor or self._default_extractor()
        errors: list[str] = []
        try:
            cluster_result = self.clusterer.process_window(
                window_start - MERGE_WINDOW,
                window_end,
                now,
                kinds=tuple(sorted(ACTIVE_SIGNAL_DOCUMENT_KINDS)),
            )
        except Exception as exc:  # noqa: BLE001 - degrade per refresh
            logger.warning("persistent clustering failed: %s", exc)
            return ShortTermRunResult(
                documents_considered=0,
                clusters_created=0,
                clusters_merged=0,
                clusters_processed=0,
                extractions_persisted=0,
                signals_confirmed=0,
                signals_catalyst=0,
                rejected=0,
                signals=(),
                completed=False,
                errors=(str(exc)[:500],),
            )

        clusters = self.storage.get_event_clusters_active(window_start, window_end)
        confirmed = 0
        catalyst = 0
        rejected = 0
        extractions_persisted = 0
        signals: list[EventSignal] = []
        for cluster in clusters:
            documents = self._cluster_documents(cluster)
            if not documents:
                continue
            try:
                extractions = extractor.extract_all(cluster, documents)
            except Exception as exc:  # noqa: BLE001 - one event only
                logger.warning("extraction failed for %s: %s", cluster.event_id, exc)
                errors.append(f"{cluster.event_id}: {str(exc)[:300]}")
                continue
            representative = self._representative(cluster, documents)
            alternate_facts = (
                {
                    extraction.stock_code: extractor.alternate_facts(
                        cluster, documents, extraction.stock_code, extraction.event_type
                    )
                    for extraction in extractions
                }
                if hasattr(extractor, "alternate_facts")
                else {}
            )
            for extraction in extractions:
                decision = self.scorer.decide(
                    extraction,
                    cluster,
                    representative=representative,
                    now=now,
                    window_start=window_start,
                    window_end=window_end,
                )
                self._persist_claim(
                    extraction,
                    decision,
                    cluster,
                    representative,
                    now,
                )
                for alternate in alternate_facts.get(extraction.stock_code, []):
                    self._persist_alternate_claim(
                        extraction,
                        alternate,
                        cluster,
                        representative,
                        now,
                    )
                final = replace(
                    extraction,
                    no_valid_signal=decision.signal is None,
                )
                self.storage.upsert_event_extraction(final, now)
                extractions_persisted += 1
                if decision.signal is None:
                    rejected += 1
                    continue
                signals.append(decision.signal)
                if decision.signal.board == "confirmed_positive":
                    confirmed += 1
                else:
                    catalyst += 1

        if publish:
            # v2 里程碑 3：榜单发布前同股票同事件指纹零重复保护（聚类的最后
            # 一道网；稳定事件 ID 不受影响——只折叠重复行，不合并簇）。
            signals = self._dedupe_board_families(signals, now)
            self.storage.replace_event_signals(signals, created_at=now)

        return ShortTermRunResult(
            documents_considered=cluster_result.documents_considered,
            clusters_created=cluster_result.clusters_created,
            clusters_merged=cluster_result.clusters_merged,
            clusters_processed=len(clusters),
            extractions_persisted=extractions_persisted,
            signals_confirmed=confirmed,
            signals_catalyst=catalyst,
            rejected=rejected,
            signals=tuple(signals),
            completed=True,
            errors=tuple(errors),
        )

    def _dedupe_board_families(
        self,
        signals: list[EventSignal],
        now: datetime,
    ) -> list[EventSignal]:
        """Collapse same-stock same-event-family board rows before publish.

        同股票、同事件类型且满足公告族指纹（同规范化标题、定期报告+摘要报告期
        对、同次回购文件族，或标题相似度 ≥90% 且关键金额兼容）的多行只保留
        分数最高的一行，避免聚类遗漏时同一事件重复入榜；不改变事件簇/事件 ID。
        """

        if len(signals) <= 1:
            return signals
        clusters_by_event: dict[str, EventCluster] = {}
        extractions: dict[tuple[str, str], EventExtraction] = {}
        for signal in signals:
            if signal.event_id not in clusters_by_event:
                cluster = self.storage.get_event_cluster(signal.event_id)
                if cluster is not None:
                    clusters_by_event[signal.event_id] = cluster
            extraction = self.storage.get_event_extraction(
                signal.event_id, signal.stock_code
            )
            if extraction is not None:
                extractions[(signal.event_id, signal.stock_code)] = extraction
        ordered = sorted(
            signals,
            key=lambda signal: (
                -signal.score,
                -signal.materiality_level,
                -signal.certainty,
                signal.stock_code,
            ),
        )
        kept: list[EventSignal] = []
        for signal in ordered:
            cluster = clusters_by_event.get(signal.event_id)
            extraction = extractions.get(
                (signal.event_id, signal.stock_code)
            )
            if cluster is None or extraction is None:
                kept.append(signal)
                continue
            duplicate = False
            for other in kept:
                other_cluster = clusters_by_event.get(other.event_id)
                other_extraction = extractions.get(
                    (other.event_id, other.stock_code)
                )
                if other_cluster is None or other_extraction is None:
                    continue
                if other.stock_code != signal.stock_code:
                    continue
                if other_extraction.event_type != extraction.event_type:
                    continue
                if self._same_board_family(
                    signal,
                    cluster,
                    extraction,
                    other,
                    other_cluster,
                    other_extraction,
                ):
                    duplicate = True
                    break
            if not duplicate:
                kept.append(signal)
        return kept

    def _persist_claim(
        self,
        extraction: EventExtraction,
        decision: SignalDecision,
        cluster: EventCluster,
        representative: SourceDocument | None,
        now: datetime,
    ) -> None:
        """Persist one candidate event fact with its per-gate decision trace.

        v2 多事实管线：每条抽取事实都落 ``event_claims``（含拒绝原因、复核
        状态与逐门控决策轨迹），供待核验明细与人工/AI 复核使用；榜单仍只消费
        ``EventExtraction`` 的最终选中事实。
        """

        document_id = (
            representative.document_id
            if representative is not None
            else cluster.representative_document_id
        )
        kinds = _counter_kinds(extraction)
        trace: list[dict[str, object]] = []
        mechanism_ok = bool(
            extraction.positive_mechanism
        ) and not extraction.no_valid_signal
        trace.append(
            {
                "gate": "mechanism",
                "passed": mechanism_ok,
                "reason": (
                    "正向机制存在" if mechanism_ok else "无有效正向机制"
                ),
            }
        )
        conflict = "title_body_conflict" in kinds
        trace.append(
            {
                "gate": "title_body_conflict",
                "passed": not conflict,
                "reason": "无标题正文冲突" if not conflict else "标题与正文结论冲突",
            }
        )
        materiality_ok = extraction.materiality_level >= 1
        trace.append(
            {
                "gate": "materiality",
                "passed": materiality_ok,
                "reason": f"重大性={extraction.materiality_level}级",
            }
        )
        certainty_ok = extraction.certainty >= 0.40
        trace.append(
            {
                "gate": "certainty",
                "passed": certainty_ok,
                "reason": f"确定性={extraction.certainty}",
            }
        )
        if decision.signal is not None:
            trace.append(
                {
                    "gate": "score",
                    "passed": True,
                    "reason": (
                        f"入榜 {decision.signal.board} "
                        f"S={decision.signal.score}"
                    ),
                }
            )
            rejection_reason = None
        else:
            trace.append(
                {
                    "gate": "score",
                    "passed": False,
                    "reason": decision.rejection_reason or "未达入榜门槛",
                }
            )
            rejection_reason = decision.rejection_reason or "未达入榜门槛"
        claim_id = hashlib.sha1(
            f"{document_id}:{extraction.stock_code}:{extraction.event_type}".encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        try:
            claim = EventClaim(
                claim_id=f"claim:{claim_id}",
                document_id=document_id,
                stock_code=extraction.stock_code,
                event_type=extraction.event_type,
                direction=extraction.direction,
                positive_mechanism=extraction.positive_mechanism,
                metrics=extraction.metrics,
                certainty_stage=extraction.certainty_stage,
                certainty=extraction.certainty,
                materiality_level=extraction.materiality_level,
                counter_evidence=extraction.counter_evidence,
                evidence_ids=extraction.evidence_ids,
                rejection_reason=rejection_reason,
                review_status=EVENT_CLAIM_REVIEW_PENDING,
                gate_trace=tuple(trace),
                extractor_kind=extraction.extractor_kind,
                extractor_version=extraction.extractor_version,
                created_at=now,
            )
            self.storage.upsert_event_claim(claim)
            # v2 里程碑 5：只对歧义项调用 AI 复核；规则结果始终保留。
            if should_review_claim(claim):
                outcome = self._ambiguity_reviewer().review(
                    claim, representative
                )
                updated = self._apply_review_outcome(claim, outcome)
                if updated is not claim:
                    self.storage.upsert_event_claim(updated)
        except Exception as exc:  # noqa: BLE001 - claim must not break refresh
            logger.warning("event claim persist failed: %s", exc)

    def _ambiguity_reviewer(self):
        if self._reviewer is None:
            self._reviewer = build_ambiguity_reviewer(self.settings)
        return self._reviewer

    @staticmethod
    def _apply_review_outcome(claim: EventClaim, outcome: object) -> EventClaim:
        """把复核结果写回候选事实；规则结果与榜单不受影响。"""

        status = outcome.status
        trace = list(claim.gate_trace)
        if status == REVIEW_STATUS_AGREE:
            trace.append(
                {
                    "gate": "ai_review",
                    "passed": True,
                    "reason": "规则与AI一致",
                }
            )
            return replace(
                claim,
                review_status=EVENT_CLAIM_REVIEW_VERIFIED,
                gate_trace=tuple(trace),
            )
        if status == REVIEW_STATUS_DIVERGE:
            trace.append(
                {
                    "gate": "ai_review",
                    "passed": False,
                    "reason": (
                        "规则与AI分歧：AI建议 "
                        f"event_type={outcome.suggested_event_type} "
                        f"direction={outcome.suggested_direction} "
                        f"（{outcome.rationale}）"
                    ),
                }
            )
            # 保留规则结果：review_status 保持 pending_review。
            return replace(claim, gate_trace=tuple(trace))
        if status == REVIEW_STATUS_REVIEW_FAILED:
            trace.append(
                {
                    "gate": "ai_review",
                    "passed": False,
                    "reason": f"复核失败：{outcome.rationale}",
                }
            )
            return replace(claim, gate_trace=tuple(trace))
        return claim  # 未复核：保持 pending_review，不追加轨迹。

    def _persist_alternate_claim(
        self,
        selected: EventExtraction,
        alternate: object,
        cluster: EventCluster,
        representative: SourceDocument | None,
        now: datetime,
    ) -> None:
        """Persist a non-selected candidate fact (v2 多事实留明细)."""

        document_id = (
            representative.document_id
            if representative is not None
            else cluster.representative_document_id
        )
        try:
            claim_id = hashlib.sha1(
                (
                    f"{document_id}:{selected.stock_code}:"
                    f"{alternate.event_type}"
                ).encode("utf-8")
            ).hexdigest()[:16]
            self.storage.upsert_event_claim(
                EventClaim(
                    claim_id=f"claim:{claim_id}",
                    document_id=document_id,
                    stock_code=selected.stock_code,
                    event_type=alternate.event_type,
                    direction=alternate.direction,
                    positive_mechanism=alternate.positive_mechanism,
                    metrics=alternate.metrics,
                    certainty_stage=alternate.certainty_stage,
                    certainty=alternate.certainty,
                    materiality_level=alternate.materiality_level,
                    counter_evidence=alternate.counter_evidence,
                    evidence_ids=alternate.evidence_ids,
                    rejection_reason=(
                        "同文档门控更高事实已入榜（留明细）"
                        if alternate.direction == "positive"
                        else "方向非正向"
                    ),
                    review_status=EVENT_CLAIM_REVIEW_PENDING,
                    gate_trace=(
                        {
                            "gate": "board_selection",
                            "passed": False,
                            "reason": (
                                f"同文档 {selected.event_type} 门控更高，"
                                "本事实保留在明细"
                            ),
                        },
                    ),
                    extractor_kind="rules",
                    extractor_version=selected.extractor_version,
                    created_at=now,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("alternate claim persist failed: %s", exc)

    @staticmethod
    def _same_board_family(
        signal: EventSignal,
        cluster: EventCluster,
        extraction: EventExtraction,
        other: EventSignal,
        other_cluster: EventCluster,
        other_extraction: EventExtraction,
    ) -> bool:
        left_title = normalize_title(cluster.canonical_title)
        right_title = normalize_title(other_cluster.canonical_title)
        left_period = _report_period_key(cluster.canonical_title)
        right_period = _report_period_key(other_cluster.canonical_title)
        # 定期报告+摘要（同一报告期）与同次回购文件族：金额差异不阻断折叠。
        if (
            left_period
            and left_period == right_period
            and "报告" in cluster.canonical_title
            and "报告" in other_cluster.canonical_title
            and (
                "摘要" in cluster.canonical_title
                or "摘要" in other_cluster.canonical_title
            )
        ):
            return True
        if (
            extraction.event_type == "buyback_or_increase"
            and other_extraction.event_type == "buyback_or_increase"
        ):
            return True
        if left_title and right_title and left_title == right_title:
            # 同标题：同药品批文族容忍金额差异；其余事件类型必须关键金额兼容
            # （金额冲突视为不同事件，plan.md 9.2）。
            if extraction.event_type == "approval" and (
                other_extraction.event_type == "approval"
            ):
                return True
            return not _amounts_conflict(extraction, other_extraction)
        if left_title and right_title:
            from rapidfuzz.fuzz import ratio as similarity_ratio

            if similarity_ratio(left_title, right_title) >= 90.0:
                # 相似（非相同）标题只折叠关键金额兼容的行。
                return not _amounts_conflict(extraction, other_extraction)
        return False

    def _cluster_documents(self, cluster: EventCluster) -> tuple[SourceDocument, ...]:
        documents = self.storage.get_source_documents_between(
            cluster.first_seen_at - MERGE_WINDOW,
            cluster.last_seen_at + MERGE_WINDOW,
        )
        ids = set(cluster.document_ids)
        return tuple(
            doc
            for doc in documents
            if doc.document_id in ids and doc.kind in ACTIVE_SIGNAL_DOCUMENT_KINDS
        )

    @staticmethod
    def _representative(
        cluster: EventCluster, documents: tuple[SourceDocument, ...]
    ) -> SourceDocument | None:
        for document in documents:
            if document.document_id == cluster.representative_document_id:
                return document
        if not documents:
            return None
        return min(documents, key=provider_priority)


def _amounts_conflict(
    left: EventExtraction, right: EventExtraction
) -> bool:
    """True when two extractions carry conflicting key amounts.

    同标题/相似标题的榜单行只有在关键金额兼容时才折叠（plan.md 9.2：关键金额
    冲突视为不同事件）；金额、利润类指标逐项按 (name, value) 比较。
    """

    def amounts(extraction: EventExtraction) -> set[str]:
        result: set[str] = set()
        for metric in extraction.metrics:
            name = str(metric.get("name") or "")
            value = metric.get("value")
            if value is not None and (
                "金额" in name or "利润" in name or "净利润" in name
            ):
                result.add(f"{name}:{value}")
        return result

    left_values = amounts(left)
    right_values = amounts(right)
    if not left_values or not right_values:
        return False
    return left_values != right_values
