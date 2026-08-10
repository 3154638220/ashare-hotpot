from __future__ import annotations

import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from rapidfuzz.fuzz import ratio as similarity_ratio

from .dedupe import normalize_title
from .extraction import (
    canonical_amounts,
    canonical_dates,
    canonical_targets,
    event_type_hint,
)
from .models import EventCluster, SourceDocument
from .storage import Storage


logger = logging.getLogger(__name__)

MERGE_WINDOW = timedelta(hours=72)
HISTORY_WINDOW = timedelta(days=180)
DEFAULT_TITLE_SIMILARITY = 90.0
DEFAULT_TRIGRAM_SIMILARITY = 0.82
_EXCERPT_CHARS = 120

# v2 优化计划（plan.md 第三部分 里程碑 1/3）：年报/摘要合并的报告期键。
_REPORT_PERIOD_RE = re.compile(
    r"(20\d{2})年(半年度|年度|一季度|二季度|三季度|四季度|半年报|一季报|中报|三季报|年报|季报)"
)
_REPORT_PERIOD_ALIASES = {
    "半年报": "半年度",
    "中报": "半年度",
    "一季报": "一季度",
    "二季报": "二季度",
    "三季报": "三季度",
    "四季度报告": "四季度",
    "年报": "年度",
}


def _report_period_key(title: str) -> str | None:
    """Normalized report period key (e.g. ``2026-半年度``) or ``None``."""

    match = _REPORT_PERIOD_RE.search(title or "")
    if not match:
        return None
    year, period = match.group(1), match.group(2)
    return f"{year}-{_REPORT_PERIOD_ALIASES.get(period, period)}"


def provider_priority(document: SourceDocument) -> int:
    """Representative-document priority; lower is more authoritative."""

    if document.provider_key == "cninfo":
        return 0
    if document.provider_key in {"sse", "irm"}:
        return 1
    if document.provider_key == "ths":
        return 3
    return 2


def title_plus_excerpt(document: SourceDocument) -> str:
    """Normalized title + leading body text used by trigram similarity."""

    body = re.sub(r"\s+", "", document.body_text or "")[:_EXCERPT_CHARS]
    return f"{normalize_title(document.title)}\n{body}"


def _trigrams(text: str) -> set[str]:
    if not text:
        return set()
    return {text[index : index + 3] for index in range(len(text) - 2)}


def trigram_cosine(left: SourceDocument, right: SourceDocument) -> float:
    """Character-trigram cosine similarity over title + excerpt."""

    left_set = _trigrams(title_plus_excerpt(left))
    right_set = _trigrams(title_plus_excerpt(right))
    if not left_set or not right_set:
        return 0.0
    intersection = len(left_set & right_set)
    return intersection / math.sqrt(len(left_set) * len(right_set))


def structured_fingerprint(
    document: SourceDocument,
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """(event_type, amounts, targets, dates) fingerprint of one document."""

    text = f"{document.title}\n{document.body_text or ''}"
    return (
        event_type_hint(text),
        canonical_amounts(text),
        canonical_targets(text),
        canonical_dates(text),
    )


def _aggregate_fingerprint(
    documents: list[SourceDocument],
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    fingerprints = [structured_fingerprint(doc) for doc in documents]
    if not fingerprints:
        return ("", (), (), ())
    hints = [item[0] for item in fingerprints if item[0]]
    return (
        hints[0] if hints else "",
        tuple(dict.fromkeys(amount for _item in fingerprints for amount in _item[1])),
        tuple(dict.fromkeys(target for _item in fingerprints for target in _item[2])),
        tuple(dict.fromkeys(value for _item in fingerprints for value in _item[3])),
    )


def fingerprints_match(
    left: tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    right: tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
) -> bool:
    """High-confidence structured match: same event type and no conflicting
    key amount/target/date (missing fields on either side are tolerated)."""

    if not left[0] or left[0] != right[0]:
        return False
    pairs = ((left[1], right[1]), (left[2], right[2]), (left[3], right[3]))
    for left_values, right_values in pairs:
        if left_values and right_values and set(left_values) != set(right_values):
            return False
    return True


def fingerprints_conflict(
    left: tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    right: tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
) -> bool:
    """Any differing non-empty key amount/target/date blocks a merge."""

    pairs = ((left[1], right[1]), (left[2], right[2]), (left[3], right[3]))
    for left_values, right_values in pairs:
        if left_values and right_values and set(left_values) != set(right_values):
            return True
    return False


@dataclass(frozen=True, slots=True)
class ClusterRunResult:
    documents_considered: int
    clusters_created: int
    clusters_merged: int
    documents_linked: int
    historical_linked: int


class PersistentEventClusterer:
    """Persistent event clustering over ``source_documents``.

    Event ids are stable UUIDs created once; new documents merge into the
    existing cluster instead of creating a new id.  Merging only happens for
    candidates sharing at least one stock and whose last activity is within
    ``merge_window`` of the new document, using the high-confidence conditions
    of plan.md section 9.2.
    """

    def __init__(
        self,
        storage: Storage,
        *,
        title_similarity_threshold: float = DEFAULT_TITLE_SIMILARITY,
        trigram_similarity_threshold: float = DEFAULT_TRIGRAM_SIMILARITY,
        merge_window: timedelta = MERGE_WINDOW,
        history_window: timedelta = HISTORY_WINDOW,
    ) -> None:
        self.storage = storage
        self.title_similarity_threshold = title_similarity_threshold
        self.trigram_similarity_threshold = trigram_similarity_threshold
        self.merge_window = merge_window
        self.history_window = history_window

    def process_window(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> ClusterRunResult:
        documents = self.storage.get_source_documents_between(start, end)
        return self.process_documents(documents, now)

    def process_documents(
        self,
        documents: list[SourceDocument],
        now: datetime,
    ) -> ClusterRunResult:
        created = 0
        merged = 0
        linked = 0
        historical = 0
        considered = 0
        for document in sorted(
            (doc for doc in documents if doc.stock_codes and doc.title.strip()),
            key=lambda doc: doc.published_at,
        ):
            considered += 1
            candidate = self._find_merge_candidate(document)
            if candidate is None:
                if self.storage.get_event_clusters_by_document(
                    document.document_id
                ):
                    # The document is already clustered and no high-confidence
                    # merge candidate exists (e.g. fingerprint conflict with a
                    # different event).  Never create a duplicate cluster for
                    # an already-clustered document: this keeps event ids
                    # stable across refreshes (plan.md 9.3).
                    continue
                historical_id = self._find_historical_link(document)
                if historical_id is not None:
                    historical += 1
                cluster = EventCluster(
                    event_id=uuid.uuid4().hex,
                    stock_codes=tuple(sorted(set(document.stock_codes))),
                    canonical_title=document.title,
                    first_seen_at=document.published_at,
                    last_seen_at=document.published_at,
                    representative_document_id=document.document_id,
                    document_ids=[document.document_id],
                    historical_similar_event_id=historical_id,
                )
                self.storage.upsert_event_cluster(cluster)
                created += 1
                continue
            merged += 1
            linked += 1
            # The same document may have been cross-linked into several
            # clusters; consolidate every one of them so one event never has
            # multiple clusters for the same content.
            for existing in self.storage.get_event_clusters_by_document(
                document.document_id
            ):
                if existing.event_id == candidate.event_id:
                    continue
                keep, drop = _pick_keep(existing, candidate)
                self._merge_clusters(keep, drop)
                candidate = keep
            self._merge_document(candidate, document)
        return ClusterRunResult(
            documents_considered=considered,
            clusters_created=created,
            clusters_merged=merged,
            documents_linked=linked,
            historical_linked=historical,
        )

    def _find_merge_candidate(self, document: SourceDocument) -> EventCluster | None:
        own_ids = {
            cluster.event_id
            for cluster in self.storage.get_event_clusters_by_document(
                document.document_id
            )
        }
        candidates = self.storage.find_event_cluster_candidates(
            set(document.stock_codes),
            document.published_at - self.merge_window,
        )
        for candidate in candidates:
            if candidate.event_id in own_ids:
                # 文档已归入该簇：只有当存在另一个高置信合并簇时才有合并动作，
                # 避免自匹配挡住同股票同事件的跨簇合并（v2 里程碑 1 回归）。
                continue
            if self._merges(candidate, document):
                return candidate
        return None

    def _find_historical_link(self, document: SourceDocument) -> str | None:
        """Find a >72h but <=180d highly similar event for novelty checks."""

        candidates = self.storage.find_event_cluster_candidates(
            set(document.stock_codes),
            document.published_at - self.history_window,
            document.published_at - self.merge_window,
        )
        for candidate in candidates:
            if self._historical_similar(candidate, document):
                return candidate.event_id
        return None

    def _historical_similar(
        self, cluster: EventCluster, document: SourceDocument
    ) -> bool:
        cluster_documents = self._cluster_documents(cluster)
        fingerprint = structured_fingerprint(document)
        aggregate = _aggregate_fingerprint(cluster_documents)
        if fingerprints_conflict(fingerprint, aggregate):
            return False
        for existing in cluster_documents:
            left = normalize_title(document.title)
            right = normalize_title(existing.title)
            if left and right and similarity_ratio(left, right) >= self.title_similarity_threshold:
                return True
            if trigram_cosine(document, existing) >= self.trigram_similarity_threshold:
                return True
        return fingerprints_match(fingerprint, aggregate)

    def _merges(self, cluster: EventCluster, document: SourceDocument) -> bool:
        cluster_documents = self._cluster_documents(cluster)
        fingerprint = structured_fingerprint(document)
        aggregate = _aggregate_fingerprint(cluster_documents)
        # v2 同股票同日公告族（同标题、年报/摘要、同次回购文件）先于金额冲突
        # 检查合并：半年报与摘要、同药品多份批文、回购方案与核查意见属同一事件，
        # 正文金额差异不应拆成重复事件（688381/001389/600196/600581/603001 回归）。
        if any(
            self._same_day_family(document, existing)
            for existing in cluster_documents
        ):
            return True
        if fingerprints_conflict(fingerprint, aggregate):
            return False
        for existing in cluster_documents:
            if (
                existing.document_url
                and document.document_url
                and existing.document_url == document.document_url
            ):
                return True
            if existing.content_hash and document.content_hash and existing.content_hash == document.content_hash:
                return True
            left = normalize_title(document.title)
            right = normalize_title(existing.title)
            if left and right and similarity_ratio(left, right) >= self.title_similarity_threshold:
                return True
            if trigram_cosine(document, existing) >= self.trigram_similarity_threshold:
                return True
        return fingerprints_match(fingerprint, aggregate)

    def _same_day_family(
        self, document: SourceDocument, existing: SourceDocument
    ) -> bool:
        """High-confidence same-day announcement family match.

        Only applies to documents published on the same date: identical
        approval-family titles (同药品多份批文), 定期报告+摘要 pairs with the
        same report period, and same-day buyback documents
        (方案/核查意见/实施结果) are treated as one event even when body
        amounts differ.  Contract documents keep the plan 9.2 amount-conflict
        guard: two different contracts never merge.
        """

        if document.published_at.date() != existing.published_at.date():
            return False
        left = normalize_title(document.title)
        right = normalize_title(existing.title)
        left_hint = event_type_hint(
            f"{document.title}\n{document.body_text or ''}"
        )
        right_hint = event_type_hint(
            f"{existing.title}\n{existing.body_text or ''}"
        )
        if (
            left
            and right
            and left == right
            and left_hint == "approval"
            and right_hint == "approval"
        ):
            return True
        left_period = _report_period_key(document.title)
        right_period = _report_period_key(existing.title)
        if (
            left_period
            and left_period == right_period
            and "报告" in (document.title or "")
            and "报告" in (existing.title or "")
            and ("摘要" in (document.title or "") or "摘要" in (existing.title or ""))
        ):
            return True
        if left_hint == "buyback_or_increase" and right_hint == "buyback_or_increase":
            return True
        return False

    def _cluster_documents(self, cluster: EventCluster) -> list[SourceDocument]:
        documents = self.storage.get_source_documents_between(
            cluster.first_seen_at - timedelta(hours=1),
            cluster.last_seen_at + timedelta(hours=1),
        )
        ids = set(cluster.document_ids)
        return [doc for doc in documents if doc.document_id in ids]

    def _merge_document(self, cluster: EventCluster, document: SourceDocument) -> None:
        representative_id = cluster.representative_document_id
        canonical_title = cluster.canonical_title
        current_representative = self.storage.get_source_document(
            cluster.representative_document_id
        )
        if current_representative is None or provider_priority(
            document
        ) < provider_priority(current_representative):
            representative_id = document.document_id
            canonical_title = document.title
        updated = EventCluster(
            event_id=cluster.event_id,
            stock_codes=tuple(
                sorted(set((*cluster.stock_codes, *document.stock_codes)))
            ),
            canonical_title=canonical_title,
            first_seen_at=min(cluster.first_seen_at, document.published_at),
            last_seen_at=max(cluster.last_seen_at, document.published_at),
            representative_document_id=representative_id,
            document_ids=(
                [*cluster.document_ids, document.document_id]
                if document.document_id not in cluster.document_ids
                else list(cluster.document_ids)
            ),
            historical_similar_event_id=cluster.historical_similar_event_id,
        )
        self.storage.upsert_event_cluster(updated)

    def _merge_clusters(
        self, target: EventCluster, source: EventCluster
    ) -> None:
        """Consolidate ``source`` into ``target`` and delete ``source``.

        Keeps the target event id (stable across re-runs); unions documents
        and stocks, widens the time range, and keeps the higher-priority
        representative document.
        """

        if source.event_id == target.event_id:
            return
        documents = list(dict.fromkeys([*target.document_ids, *source.document_ids]))
        stock_codes = tuple(sorted(set((*target.stock_codes, *source.stock_codes))))
        representative_id = target.representative_document_id
        canonical_title = target.canonical_title
        target_rep = self.storage.get_source_document(
            target.representative_document_id
        )
        source_rep = self.storage.get_source_document(
            source.representative_document_id
        )
        if target_rep is None or (
            source_rep is not None
            and provider_priority(source_rep) < provider_priority(target_rep)
        ):
            representative_id = source.representative_document_id
            canonical_title = source.canonical_title
        historical = (
            target.historical_similar_event_id
            or source.historical_similar_event_id
        )
        merged = EventCluster(
            event_id=target.event_id,
            stock_codes=stock_codes,
            canonical_title=canonical_title,
            first_seen_at=min(target.first_seen_at, source.first_seen_at),
            last_seen_at=max(target.last_seen_at, source.last_seen_at),
            representative_document_id=representative_id,
            document_ids=documents,
            historical_similar_event_id=historical,
        )
        self.storage.upsert_event_cluster(merged)
        self.storage.delete_event_cluster(source.event_id)


def _pick_keep(left: EventCluster, right: EventCluster) -> tuple[EventCluster, EventCluster]:
    """Deterministic survivor choice when consolidating duplicate clusters.

    Keeps the richer cluster (more documents), then the earlier one, then the
    lexicographically smaller event id for stable re-runs.
    """

    if len(left.document_ids) != len(right.document_ids):
        return (left, right) if len(left.document_ids) > len(right.document_ids) else (right, left)
    if left.first_seen_at != right.first_seen_at:
        return (left, right) if left.first_seen_at < right.first_seen_at else (right, left)
    return (left, right) if left.event_id < right.event_id else (right, left)
