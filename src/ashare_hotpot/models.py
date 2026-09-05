from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _to_date(value: object) -> date | None:
    if value is None or value == "" or value == "-":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _to_int(value: object) -> int | None:
    try:
        if value is None or value == "" or value == "-":
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    try:
        if value is None or value == "" or value == "-":
            return None
        return float(str(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class StockMention:
    code: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "name": self.name}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StockMention":
        return cls(code=str(data["code"]), name=str(data.get("name") or data["code"]))


@dataclass(frozen=True, slots=True)
class ArticleCandidate:
    seq: str
    url: str
    title: str
    summary: str
    published_at: datetime
    channel_key: str
    channel_name: str
    provider_key: str = "ths"
    provider_name: str = "同花顺"
    content_type: str = "新闻"
    stocks: tuple[StockMention, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    """One list-page item from a research/announcement source.

    Adapter-level candidate only; persistence happens through
    :class:`SourceDocument`.  ``attachment_type`` mirrors the source's own
    label (``PDF``, ``DOC``, ``DOCX`` ...) and decides which attachment
    extraction path is used; unsupported attachment kinds stay metadata-only.
    """

    document_id: str
    provider_key: str
    provider_name: str
    kind: str  # announcement | research_activity
    source_url: str
    document_url: str | None
    title: str
    published_at: datetime
    stock_codes: tuple[str, ...]
    attachment_type: str | None
    description: str = ""
    stock_names: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    """One persisted discovery-layer row (后 1.1.0 可靠性里程碑).

    Every public list item becomes a discovery candidate before its body is
    decided; attachment downloads are driven by the recoverable work queue
    (``queue_status``).  Candidates never carry a score, never claim a
    positive mechanism and never infer amounts, customers or investment
    opinions — they are the anti-leak layer feeding the strict boards.

    ``queue_status`` is one of the fixed discovery states:
    ``pending_attachment`` (待解析, attachment queued/deferred),
    ``awaiting_review`` (待核验, metadata-only or body extracted),
    ``empty_text`` (空文本) or ``failed`` (解析失败).
    """

    document_id: str
    source_key: str
    source_name: str
    provider_key: str
    provider_name: str
    kind: str  # announcement | research_activity
    stock_codes: tuple[str, ...]
    title: str
    published_at: datetime
    discovery_type: str  # fixed enum from discovery.DISCOVERY_TYPES
    trigger_reason: str
    queue_status: str  # pending_attachment | awaiting_review | empty_text | failed
    attachment_type: str | None
    document_url: str | None
    enqueued_at: datetime | None
    updated_at: datetime
    signal_priority: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_key": self.source_key,
            "source_name": self.source_name,
            "provider_key": self.provider_key,
            "provider_name": self.provider_name,
            "kind": self.kind,
            "stock_codes": list(self.stock_codes),
            "title": self.title,
            "published_at": self.published_at.isoformat(),
            "discovery_type": self.discovery_type,
            "trigger_reason": self.trigger_reason,
            "queue_status": self.queue_status,
            "attachment_type": self.attachment_type,
            "document_url": self.document_url,
            "enqueued_at": self.enqueued_at.isoformat() if self.enqueued_at else None,
            "updated_at": self.updated_at.isoformat(),
            "signal_priority": self.signal_priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryCandidate":
        return cls(
            document_id=str(data["document_id"]),
            source_key=str(data.get("source_key") or ""),
            source_name=str(data.get("source_name") or ""),
            provider_key=str(data.get("provider_key") or ""),
            provider_name=str(data.get("provider_name") or ""),
            kind=str(data.get("kind") or "announcement"),
            stock_codes=tuple(str(item) for item in data.get("stock_codes", [])),
            title=str(data.get("title") or ""),
            published_at=datetime.fromisoformat(data["published_at"]),
            discovery_type=str(data.get("discovery_type") or "other_disclosure"),
            trigger_reason=str(data.get("trigger_reason") or ""),
            queue_status=str(data.get("queue_status") or "awaiting_review"),
            attachment_type=data.get("attachment_type"),
            document_url=data.get("document_url"),
            enqueued_at=_dt(data.get("enqueued_at")),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            signal_priority=bool(data.get("signal_priority", False)),
        )


@dataclass(frozen=True, slots=True)
class ParsedArticle:
    seq: str
    url: str
    title: str
    summary: str
    published_at: datetime
    channel_key: str
    channel_name: str
    source_name: str
    stocks: tuple[StockMention, ...] = ()
    industry_tags: tuple[str, ...] = ()
    provider_key: str = "ths"
    provider_name: str = "同花顺"
    content_type: str = "新闻"
    filtered_reason: str | None = None
    fetch_error: str | None = None
    # Narrow concepts explicitly linked by the source or matched by the fixed
    # high-confidence taxonomy.  Kept at the end to preserve the positional
    # constructor used by legacy integrations.
    industry_concepts: tuple[str, ...] = ()
    industry_parse_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "url": self.url,
            "title": self.title,
            "summary": self.summary,
            "published_at": self.published_at.isoformat(),
            "channel_key": self.channel_key,
            "channel_name": self.channel_name,
            "source_name": self.source_name,
            "provider_key": self.provider_key,
            "provider_name": self.provider_name,
            "content_type": self.content_type,
            "stocks": [stock.to_dict() for stock in self.stocks],
            "industry_tags": list(self.industry_tags),
            "industry_concepts": list(self.industry_concepts),
            "industry_parse_version": self.industry_parse_version,
            "filtered_reason": self.filtered_reason,
            "fetch_error": self.fetch_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParsedArticle":
        return cls(
            seq=str(data.get("seq", "")),
            url=str(data["url"]),
            title=str(data["title"]),
            summary=str(data.get("summary", "")),
            published_at=datetime.fromisoformat(data["published_at"]),
            channel_key=str(data.get("channel_key", "")),
            channel_name=str(data.get("channel_name", "")),
            source_name=str(data.get("source_name", "")),
            provider_key=str(data.get("provider_key") or data.get("channel_key") or "ths"),
            provider_name=str(data.get("provider_name") or data.get("channel_name") or "同花顺"),
            content_type=str(data.get("content_type") or "新闻"),
            stocks=tuple(StockMention.from_dict(item) for item in data.get("stocks", [])),
            industry_tags=tuple(str(item) for item in data.get("industry_tags", []) if str(item).strip()),
            industry_concepts=tuple(
                str(item) for item in data.get("industry_concepts", []) if str(item).strip()
            ),
            industry_parse_version=int(data.get("industry_parse_version", 0)),
            filtered_reason=data.get("filtered_reason"),
            fetch_error=data.get("fetch_error"),
        )


@dataclass(slots=True)
class NewsEvent:
    event_id: str
    title: str
    published_at: datetime
    stocks: tuple[StockMention, ...]
    articles: list[ParsedArticle] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "published_at": self.published_at.isoformat(),
            "stocks": [stock.to_dict() for stock in self.stocks],
            "articles": [article.to_dict() for article in self.articles],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsEvent":
        return cls(
            event_id=str(data["event_id"]),
            title=str(data["title"]),
            published_at=datetime.fromisoformat(data["published_at"]),
            stocks=tuple(StockMention.from_dict(item) for item in data.get("stocks", [])),
            articles=[ParsedArticle.from_dict(item) for item in data.get("articles", [])],
        )


@dataclass(frozen=True, slots=True)
class RankingRow:
    rank: int
    code: str
    name: str
    event_count: int
    raw_article_count: int
    latest_mention: datetime
    event_ids: tuple[str, ...]
    industry_tags: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    content_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "code": self.code,
            "name": self.name,
            "event_count": self.event_count,
            "raw_article_count": self.raw_article_count,
            "latest_mention": self.latest_mention.isoformat(),
            "event_ids": list(self.event_ids),
            "industry_tags": list(self.industry_tags),
            "channels": list(self.channels),
            "sources": list(self.sources),
            "content_types": list(self.content_types),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RankingRow":
        return cls(
            rank=int(data["rank"]),
            code=str(data["code"]),
            name=str(data["name"]),
            event_count=int(data["event_count"]),
            raw_article_count=int(data["raw_article_count"]),
            latest_mention=datetime.fromisoformat(data["latest_mention"]),
            event_ids=tuple(str(item) for item in data.get("event_ids", [])),
            industry_tags=tuple(str(item) for item in data.get("industry_tags", []) if str(item).strip()),
            channels=tuple(str(item) for item in data.get("channels", []) if str(item).strip()),
            sources=tuple(str(item) for item in data.get("sources", []) if str(item).strip()),
            content_types=tuple(str(item) for item in data.get("content_types", []) if str(item).strip()),
        )


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    source_key: str
    source_name: str
    pages_scanned: int
    article_count: int
    oldest_seen: datetime | None
    newest_seen: datetime | None
    reached_cutoff: bool
    error: str | None = None
    provider_key: str = "ths"
    provider_name: str = "同花顺"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "source_name": self.source_name,
            "pages_scanned": self.pages_scanned,
            "article_count": self.article_count,
            "oldest_seen": self.oldest_seen.isoformat() if self.oldest_seen else None,
            "newest_seen": self.newest_seen.isoformat() if self.newest_seen else None,
            "reached_cutoff": self.reached_cutoff,
            "error": self.error,
            "provider_key": self.provider_key,
            "provider_name": self.provider_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceCoverage":
        return cls(
            source_key=str(data["source_key"]),
            source_name=str(data["source_name"]),
            pages_scanned=int(data.get("pages_scanned", 0)),
            article_count=int(data.get("article_count", 0)),
            oldest_seen=_dt(data.get("oldest_seen")),
            newest_seen=_dt(data.get("newest_seen")),
            reached_cutoff=bool(data.get("reached_cutoff", False)),
            error=data.get("error"),
            provider_key=str(data.get("provider_key") or "ths"),
            provider_name=str(data.get("provider_name") or "同花顺"),
        )


@dataclass(frozen=True, slots=True)
class InteractionCoverage:
    """Coverage state of one official Q&A platform during one refresh."""

    source_key: str
    source_name: str
    pages_scanned: int
    record_count: int
    oldest_seen: datetime | None
    newest_seen: datetime | None
    reached_cutoff: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "source_name": self.source_name,
            "pages_scanned": self.pages_scanned,
            "record_count": self.record_count,
            "oldest_seen": self.oldest_seen.isoformat() if self.oldest_seen else None,
            "newest_seen": self.newest_seen.isoformat() if self.newest_seen else None,
            "reached_cutoff": self.reached_cutoff,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InteractionCoverage":
        return cls(
            source_key=str(data["source_key"]),
            source_name=str(data["source_name"]),
            pages_scanned=int(data.get("pages_scanned", 0)),
            record_count=int(data.get("record_count", 0)),
            oldest_seen=_dt(data.get("oldest_seen")),
            newest_seen=_dt(data.get("newest_seen")),
            reached_cutoff=bool(data.get("reached_cutoff", False)),
            error=data.get("error"),
        )


@dataclass(frozen=True, slots=True)
class InteractionRecord:
    """One investor question from an official Q&A platform."""

    record_id: str
    platform_key: str
    platform_name: str
    code: str
    stock_name: str
    question: str
    question_time: datetime
    question_url: str
    reply: str | None = None
    reply_time: datetime | None = None
    industry_tags: tuple[str, ...] = ()
    filtered_reason: str | None = None

    @property
    def replied(self) -> bool:
        return bool(self.reply and self.reply.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "platform_key": self.platform_key,
            "platform_name": self.platform_name,
            "code": self.code,
            "stock_name": self.stock_name,
            "question": self.question,
            "question_time": self.question_time.isoformat(),
            "question_url": self.question_url,
            "reply": self.reply,
            "reply_time": self.reply_time.isoformat() if self.reply_time else None,
            "industry_tags": list(self.industry_tags),
            "filtered_reason": self.filtered_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InteractionRecord":
        return cls(
            record_id=str(data["record_id"]),
            platform_key=str(data["platform_key"]),
            platform_name=str(data["platform_name"]),
            code=str(data["code"]),
            stock_name=str(data.get("stock_name") or data["code"]),
            question=str(data["question"]),
            question_time=datetime.fromisoformat(data["question_time"]),
            question_url=str(data.get("question_url") or ""),
            reply=data.get("reply"),
            reply_time=_dt(data.get("reply_time")),
            industry_tags=tuple(str(item) for item in data.get("industry_tags", []) if str(item).strip()),
            filtered_reason=data.get("filtered_reason"),
        )


@dataclass(frozen=True, slots=True)
class InteractionRankingRow:
    """One row of the official Q&A proxy ranking.

    口径（v2）：只有公司已回复的提问才计为有效提问，且问题的统计时间
    定义为该问题的回复时间（``latest_reply``）。因此榜内每一条记录都是
    已回复的，``replied_count == question_count``。
    """

    rank: int
    code: str
    name: str
    question_count: int
    replied_count: int
    latest_reply: datetime
    record_ids: tuple[str, ...]
    industry_tags: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()

    @property
    def reply_rate(self) -> float:
        if self.question_count <= 0:
            return 0.0
        return self.replied_count / self.question_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "code": self.code,
            "name": self.name,
            "question_count": self.question_count,
            "replied_count": self.replied_count,
            "reply_rate": self.reply_rate,
            "latest_reply": self.latest_reply.isoformat(),
            "record_ids": list(self.record_ids),
            "industry_tags": list(self.industry_tags),
            "platforms": list(self.platforms),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InteractionRankingRow":
        return cls(
            rank=int(data["rank"]),
            code=str(data["code"]),
            name=str(data.get("name") or data["code"]),
            question_count=int(data.get("question_count", 0)),
            replied_count=int(data.get("replied_count", 0)),
            latest_reply=datetime.fromisoformat(
                data.get("latest_reply") or data["latest_question"]
            ),
            record_ids=tuple(str(item) for item in data.get("record_ids", [])),
            industry_tags=tuple(str(item) for item in data.get("industry_tags", []) if str(item).strip()),
            platforms=tuple(str(item) for item in data.get("platforms", []) if str(item).strip()),
        )


@dataclass(frozen=True, slots=True)
class PopularityRankRow:
    """One row of the official Eastmoney popularity board.

    The official board only exposes ranks and rank changes; the underlying
    attention score is not public and its weighting is not disclosed.
    """

    rank: int
    code: str
    name: str
    change: int | None
    current_price: float | None
    change_percent: float | None
    url: str
    industry: str | None = None
    name_from_cache: bool = False

    @property
    def missing_quote_fields(self) -> tuple[str, ...]:
        return tuple(label for label, missing in (
            ("股票名称", not self.name or self.name == self.code or self.name == "-"),
            ("现价", self.current_price is None),
            ("涨跌幅", self.change_percent is None),
        ) if missing)

    @property
    def quote_incomplete(self) -> bool:
        return bool(self.missing_quote_fields or self.name_from_cache)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "code": self.code,
            "name": self.name,
            "change": self.change,
            "current_price": self.current_price,
            "change_percent": self.change_percent,
            "url": self.url,
            "industry": self.industry,
            "name_from_cache": self.name_from_cache,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PopularityRankRow":
        return cls(
            rank=int(data["rank"]),
            code=str(data["code"]),
            name=str(data.get("name") or data["code"]),
            change=_to_int(data.get("change")),
            current_price=_to_float(data.get("current_price")),
            change_percent=_to_float(data.get("change_percent")),
            url=str(data.get("url") or ""),
            industry=str(data["industry"]).strip() if data.get("industry") else None,
            name_from_cache=bool(data.get("name_from_cache", False)),
        )


@dataclass(slots=True)
class OfficialPopularitySnapshot:
    """Latest official Eastmoney popularity state for one refresh.

    ``success_at`` is the actual read time of the last successful board fetch.
    On a failed refresh the previous successful boards are kept and marked
    stale together with the failure reason, so no partial board is produced.
    """

    available: bool = False
    is_stale: bool = False
    success_at: datetime | None = None
    error: str | None = None
    # True only when this refresh reused a prior successful board from the
    # short cache.  It is intentionally separate from ``is_stale``: a fresh
    # successful fetch is eligible for the industry's daily history, while a
    # cache hit may still be perfectly usable for the current board.
    from_cache: bool = False
    popularity: list[PopularityRankRow] = field(default_factory=list)
    surging: list[PopularityRankRow] = field(default_factory=list)
    # Separate from ranking time: quote-only repair must not freshen the ranks.
    quote_attempt_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "is_stale": self.is_stale,
            "success_at": self.success_at.isoformat() if self.success_at else None,
            "error": self.error,
            "from_cache": self.from_cache,
            "popularity": [row.to_dict() for row in self.popularity],
            "surging": [row.to_dict() for row in self.surging],
            "quote_attempt_at": self.quote_attempt_at.isoformat() if self.quote_attempt_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "OfficialPopularitySnapshot":
        if not data:
            return cls()
        return cls(
            available=bool(data.get("available", False)),
            is_stale=bool(data.get("is_stale", False)),
            success_at=_dt(data.get("success_at")),
            error=data.get("error"),
            from_cache=bool(data.get("from_cache", False)),
            popularity=[PopularityRankRow.from_dict(item) for item in data.get("popularity", [])],
            surging=[PopularityRankRow.from_dict(item) for item in data.get("surging", [])],
            quote_attempt_at=_dt(data.get("quote_attempt_at")),
        )


@dataclass(frozen=True, slots=True)
class IndustryHeatRow:
    """One transparent row of the independent industry-heat board.

    ``a`` is the number of distinct stocks from Eastmoney's comprehensive
    popularity Top100.  ``b`` is the number of distinct, successfully parsed
    industry-research articles mapped to this industry in the fixed 24-hour
    window.  The two inputs deliberately remain visible instead of being
    hidden behind an opaque score.
    """

    rank: int
    industry: str
    heat: float
    a: int
    a_percentile: float
    b: int
    b_percentile: float
    mapping_status: str = "mapped"
    source_status: str = "complete"
    article_urls: tuple[str, ...] = ()
    stock_codes: tuple[str, ...] = ()

    @property
    def heat_score(self) -> float:
        """Descriptive alias used by callers that prefer an explicit name."""

        return self.heat

    @property
    def a_count(self) -> int:
        return self.a

    @property
    def b_count(self) -> int:
        return self.b

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "industry": self.industry,
            "heat": self.heat,
            "a": self.a,
            "a_percentile": self.a_percentile,
            "b": self.b,
            "b_percentile": self.b_percentile,
            "mapping_status": self.mapping_status,
            "source_status": self.source_status,
            "article_urls": list(self.article_urls),
            "stock_codes": list(self.stock_codes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndustryHeatRow":
        # ``heat_score``/``a_count``/``b_count`` are accepted for early
        # development snapshots without changing the canonical wire format.
        return cls(
            rank=int(data.get("rank", 0)),
            industry=str(data.get("industry") or ""),
            heat=float(data.get("heat", data.get("heat_score", 0.0))),
            a=int(data.get("a", data.get("a_count", 0))),
            a_percentile=float(data.get("a_percentile", 0.0)),
            b=int(data.get("b", data.get("b_count", 0))),
            b_percentile=float(data.get("b_percentile", 0.0)),
            mapping_status=str(data.get("mapping_status") or "mapped"),
            source_status=str(data.get("source_status") or "complete"),
            article_urls=tuple(
                str(item) for item in data.get("article_urls", []) if str(item).strip()
            ),
            stock_codes=tuple(
                str(item) for item in data.get("stock_codes", []) if str(item).strip()
            ),
        )


@dataclass(slots=True)
class IndustryHeatSnapshot:
    """A serializable industry-heat result and its visible quality counts."""

    snapshot_at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    rows: list[IndustryHeatRow] = field(default_factory=list)
    top100_total: int = 0
    top100_mapped: int = 0
    mapping_coverage: float = 0.0
    research_article_total: int = 0
    research_article_mapped: int = 0
    unmapped_article_count: int = 0
    explicit_article_count: int = 0
    concept_article_count: int = 0
    stock_fallback_article_count: int = 0
    unknown_label_article_count: int = 0
    unknown_concept_article_count: int = 0
    no_evidence_article_count: int = 0
    stock_industry_unmapped_article_count: int = 0
    mapping_status: str = "unavailable"
    source_status: str = "unavailable"
    source_error: str | None = None
    # Runtime-only current-refresh article objects.  The serialized contract
    # remains row-based; daily history keeps article URLs and cache rows.
    articles: list[ParsedArticle] = field(default_factory=list, repr=False, compare=False)

    @property
    def is_complete(self) -> bool:
        return self.source_status == "complete" and self.mapping_status == "complete"

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_at": self.snapshot_at.isoformat() if self.snapshot_at else None,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "rows": [row.to_dict() for row in self.rows],
            "top100_total": self.top100_total,
            "top100_mapped": self.top100_mapped,
            "mapping_coverage": self.mapping_coverage,
            "research_article_total": self.research_article_total,
            "research_article_mapped": self.research_article_mapped,
            "unmapped_article_count": self.unmapped_article_count,
            "explicit_article_count": self.explicit_article_count,
            "concept_article_count": self.concept_article_count,
            "stock_fallback_article_count": self.stock_fallback_article_count,
            "unknown_label_article_count": self.unknown_label_article_count,
            "unknown_concept_article_count": self.unknown_concept_article_count,
            "no_evidence_article_count": self.no_evidence_article_count,
            "stock_industry_unmapped_article_count": self.stock_industry_unmapped_article_count,
            "mapping_status": self.mapping_status,
            "source_status": self.source_status,
            "source_error": self.source_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "IndustryHeatSnapshot":
        if not data:
            return cls()
        return cls(
            snapshot_at=_dt(data.get("snapshot_at")),
            window_start=_dt(data.get("window_start")),
            window_end=_dt(data.get("window_end")),
            rows=[IndustryHeatRow.from_dict(item) for item in data.get("rows", [])],
            top100_total=int(data.get("top100_total", 0)),
            top100_mapped=int(data.get("top100_mapped", 0)),
            mapping_coverage=float(data.get("mapping_coverage", 0.0)),
            research_article_total=int(data.get("research_article_total", 0)),
            research_article_mapped=int(data.get("research_article_mapped", 0)),
            unmapped_article_count=int(data.get("unmapped_article_count", 0)),
            explicit_article_count=int(data.get("explicit_article_count", 0)),
            concept_article_count=int(data.get("concept_article_count", 0)),
            stock_fallback_article_count=int(data.get("stock_fallback_article_count", 0)),
            unknown_label_article_count=int(data.get("unknown_label_article_count", 0)),
            unknown_concept_article_count=int(
                data.get("unknown_concept_article_count", 0)
            ),
            no_evidence_article_count=int(data.get("no_evidence_article_count", 0)),
            stock_industry_unmapped_article_count=int(
                data.get("stock_industry_unmapped_article_count", 0)
            ),
            mapping_status=str(data.get("mapping_status") or "unavailable"),
            source_status=str(data.get("source_status") or "unavailable"),
            source_error=data.get("source_error"),
        )


@dataclass(slots=True)
class Snapshot:
    snapshot_id: int | None
    window_start: datetime
    window_end: datetime
    created_at: datetime
    partial: bool
    coverages: list[SourceCoverage]
    rankings: list[RankingRow]
    events: list[NewsEvent]
    stats: dict[str, int]
    popularity: OfficialPopularitySnapshot = field(default_factory=OfficialPopularitySnapshot)
    interactions: list[InteractionRecord] = field(default_factory=list)
    interaction_rankings: list[InteractionRankingRow] = field(default_factory=list)
    interaction_coverages: list[InteractionCoverage] = field(default_factory=list)
    # v2 里程碑 2：政策观察来源逐源覆盖（只读列表/失败关闭；绝不进入信号管线）。
    policy_coverages: list[SourceCoverage] = field(default_factory=list)
    # 行业热度独立于原始四榜和研究信号；旧快照缺少此字段时为空。
    industry_heat: IndustryHeatSnapshot = field(default_factory=IndustryHeatSnapshot)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "created_at": self.created_at.isoformat(),
            "partial": self.partial,
            "coverages": [coverage.to_dict() for coverage in self.coverages],
            "rankings": [row.to_dict() for row in self.rankings],
            "events": [event.to_dict() for event in self.events],
            "stats": dict(self.stats),
            "popularity": self.popularity.to_dict(),
            "interactions": [record.to_dict() for record in self.interactions],
            "interaction_rankings": [row.to_dict() for row in self.interaction_rankings],
            "interaction_coverages": [coverage.to_dict() for coverage in self.interaction_coverages],
            "policy_coverages": [coverage.to_dict() for coverage in self.policy_coverages],
            "industry_heat": self.industry_heat.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        events = [NewsEvent.from_dict(item) for item in data.get("events", [])]
        rankings = [RankingRow.from_dict(item) for item in data.get("rankings", [])]

        # Snapshots written before source filtering was introduced do not have
        # RankingRow.channels. Their events still retain each article's
        # channel name, so restore the missing data while reading rather than
        # requiring the user to refresh before the filter is usable.
        channels_by_event = {
            event.event_id: tuple(
                sorted(
                    {
                        article.channel_name.strip()
                        for article in event.articles
                        if article.channel_name.strip()
                    }
                )
            )
            for event in events
        }
        sources_by_event = {
            event.event_id: tuple(
                sorted(
                    {
                        (article.provider_name or article.channel_name or "").strip()
                        for article in event.articles
                        if (article.provider_name or article.channel_name or "").strip()
                    }
                )
            )
            for event in events
        }
        content_types_by_event = {
            event.event_id: tuple(
                sorted(
                    {
                        (article.content_type or "新闻").strip()
                        for article in event.articles
                        if (article.content_type or "新闻").strip()
                    }
                )
            )
            for event in events
        }
        rankings = [
            RankingRow(
                rank=row.rank,
                code=row.code,
                name=row.name,
                event_count=row.event_count,
                raw_article_count=row.raw_article_count,
                latest_mention=row.latest_mention,
                event_ids=row.event_ids,
                industry_tags=row.industry_tags,
                channels=tuple(
                    sorted(
                        {
                            channel
                            for event_id in row.event_ids
                            for channel in channels_by_event.get(event_id, ())
                        }
                    )
                ),
                sources=tuple(
                    sorted(
                        {
                            source
                            for event_id in row.event_ids
                            for source in sources_by_event.get(event_id, ())
                        }
                    )
                ),
                content_types=tuple(
                    sorted(
                        {
                            content_type
                            for event_id in row.event_ids
                            for content_type in content_types_by_event.get(event_id, ())
                        }
                    )
                ),
            )
            if not (row.channels and row.sources and row.content_types)
            else row
            for row in rankings
        ]
        return cls(
            snapshot_id=data.get("snapshot_id"),
            window_start=datetime.fromisoformat(data["window_start"]),
            window_end=datetime.fromisoformat(data["window_end"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            partial=bool(data.get("partial", False)),
            coverages=[SourceCoverage.from_dict(item) for item in data.get("coverages", [])],
            rankings=rankings,
            events=events,
            stats={str(key): int(value) for key, value in data.get("stats", {}).items()},
            # Legacy snapshots may still carry a "guba" key from the old
            # self-computed stock-forum ranking; it is intentionally ignored so
            # the historical news snapshot is kept while the old guba result is
            # dropped.
            popularity=OfficialPopularitySnapshot.from_dict(data.get("popularity")),
            # Snapshots written before the interaction board existed default to
            # an empty interaction board instead of failing.
            interactions=[
                InteractionRecord.from_dict(item) for item in data.get("interactions", [])
            ],
            interaction_rankings=[
                InteractionRankingRow.from_dict(item)
                for item in data.get("interaction_rankings", [])
            ],
            interaction_coverages=[
                InteractionCoverage.from_dict(item)
                for item in data.get("interaction_coverages", [])
            ],
            # Snapshots written before policy sources existed default to empty.
            policy_coverages=[
                SourceCoverage.from_dict(item)
                for item in data.get("policy_coverages", [])
            ],
            industry_heat=IndustryHeatSnapshot.from_dict(data.get("industry_heat")),
        )


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """One fetched document (news, official announcement or research
    activity record) together with its parse state and content hash.

    ``body_text`` is the extracted plain text and must never be copied into
    the legacy ``summary`` field; excerpts live in :class:`EvidenceRef`.
    """

    document_id: str
    provider_key: str
    provider_name: str
    kind: str  # news | announcement | research_activity
    source_url: str
    document_url: str | None
    title: str
    published_at: datetime
    stock_codes: tuple[str, ...]
    body_text: str
    content_hash: str
    parse_status: str  # parsed | metadata_only | empty_text | failed
    parse_error: str | None
    page_count: int | None = None
    stock_names: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "provider_key": self.provider_key,
            "provider_name": self.provider_name,
            "kind": self.kind,
            "source_url": self.source_url,
            "document_url": self.document_url,
            "title": self.title,
            "published_at": self.published_at.isoformat(),
            "stock_codes": list(self.stock_codes),
            "body_text": self.body_text,
            "content_hash": self.content_hash,
            "parse_status": self.parse_status,
            "parse_error": self.parse_error,
            "page_count": self.page_count,
            "stock_names": dict(self.stock_names),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceDocument":
        return cls(
            document_id=str(data["document_id"]),
            provider_key=str(data.get("provider_key") or ""),
            provider_name=str(data.get("provider_name") or ""),
            kind=str(data["kind"]),
            source_url=str(data.get("source_url") or ""),
            document_url=data.get("document_url"),
            title=str(data["title"]),
            published_at=datetime.fromisoformat(data["published_at"]),
            stock_codes=tuple(str(item) for item in data.get("stock_codes", [])),
            body_text=str(data.get("body_text") or ""),
            content_hash=str(data.get("content_hash") or ""),
            parse_status=str(data.get("parse_status") or "metadata_only"),
            parse_error=data.get("parse_error"),
            page_count=_to_int(data.get("page_count")),
            stock_names={
                str(code): str(name)
                for code, name in (data.get("stock_names") or {}).items()
            },
        )


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A short excerpt anchored into one document, used to explain signals."""

    evidence_id: str
    document_id: str
    start_offset: int | None
    end_offset: int | None
    excerpt: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "document_id": self.document_id,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "excerpt": self.excerpt,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceRef":
        return cls(
            evidence_id=str(data["evidence_id"]),
            document_id=str(data.get("document_id") or ""),
            start_offset=_to_int(data.get("start_offset")),
            end_offset=_to_int(data.get("end_offset")),
            excerpt=str(data.get("excerpt") or ""),
            source_url=str(data.get("source_url") or ""),
        )


@dataclass(slots=True)
class EventCluster:
    """A persisted cluster of documents describing the same company event.

    ``event_id`` is created once and must never change when new sources join.
    """

    event_id: str
    stock_codes: tuple[str, ...]
    canonical_title: str
    first_seen_at: datetime
    last_seen_at: datetime
    representative_document_id: str
    document_ids: list[str]
    historical_similar_event_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "stock_codes": list(self.stock_codes),
            "canonical_title": self.canonical_title,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "representative_document_id": self.representative_document_id,
            "document_ids": list(self.document_ids),
            "historical_similar_event_id": self.historical_similar_event_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventCluster":
        return cls(
            event_id=str(data["event_id"]),
            stock_codes=tuple(str(item) for item in data.get("stock_codes", [])),
            canonical_title=str(data.get("canonical_title") or ""),
            first_seen_at=datetime.fromisoformat(data["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(data["last_seen_at"]),
            representative_document_id=str(data.get("representative_document_id") or ""),
            document_ids=[str(item) for item in data.get("document_ids", [])],
            historical_similar_event_id=data.get("historical_similar_event_id"),
        )


@dataclass(frozen=True, slots=True)
class EventExtraction:
    """Structured short-term event extraction for one stock of one event."""

    event_id: str
    stock_code: str
    event_type: str
    direction: str
    positive_mechanism: str | None
    metrics: tuple[dict[str, object], ...]
    certainty_stage: str
    certainty: float
    novelty: float
    unexpectedness: float
    materiality_level: int
    counter_evidence: tuple[dict[str, object], ...]
    evidence_ids: tuple[str, ...]
    no_valid_signal: bool
    extractor_kind: str  # rules | llm | rules_fallback
    extractor_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "stock_code": self.stock_code,
            "event_type": self.event_type,
            "direction": self.direction,
            "positive_mechanism": self.positive_mechanism,
            "metrics": [dict(item) for item in self.metrics],
            "certainty_stage": self.certainty_stage,
            "certainty": self.certainty,
            "novelty": self.novelty,
            "unexpectedness": self.unexpectedness,
            "materiality_level": self.materiality_level,
            "counter_evidence": [dict(item) for item in self.counter_evidence],
            "evidence_ids": list(self.evidence_ids),
            "no_valid_signal": self.no_valid_signal,
            "extractor_kind": self.extractor_kind,
            "extractor_version": self.extractor_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventExtraction":
        return cls(
            event_id=str(data["event_id"]),
            stock_code=str(data["stock_code"]),
            event_type=str(data.get("event_type") or ""),
            direction=str(data.get("direction") or ""),
            positive_mechanism=data.get("positive_mechanism"),
            metrics=tuple(dict(item) for item in data.get("metrics", [])),
            certainty_stage=str(data.get("certainty_stage") or ""),
            certainty=float(data.get("certainty", 0.0)),
            novelty=float(data.get("novelty", 0.0)),
            unexpectedness=float(data.get("unexpectedness", 0.0)),
            materiality_level=int(data.get("materiality_level", 0)),
            counter_evidence=tuple(dict(item) for item in data.get("counter_evidence", [])),
            evidence_ids=tuple(str(item) for item in data.get("evidence_ids", [])),
            no_valid_signal=bool(data.get("no_valid_signal", False)),
            extractor_kind=str(data.get("extractor_kind") or "rules"),
            extractor_version=str(data.get("extractor_version") or ""),
        )


# v2 优化计划（plan.md 第三部分）：多事实候选、参与者原始提及与结构化披露
# 总数的固定复核状态。
EVENT_CLAIM_REVIEW_PENDING = "pending_review"
EVENT_CLAIM_REVIEW_VERIFIED = "verified"
EVENT_CLAIM_REVIEW_REJECTED = "rejected"
EVENT_CLAIM_REVIEW_SUPERSEDED = "superseded"

EVENT_CLAIM_REVIEW_STATUSES: tuple[str, ...] = (
    EVENT_CLAIM_REVIEW_PENDING,
    EVENT_CLAIM_REVIEW_VERIFIED,
    EVENT_CLAIM_REVIEW_REJECTED,
    EVENT_CLAIM_REVIEW_SUPERSEDED,
)

PARTICIPANT_MENTION_REVIEW_PENDING = "pending_review"
PARTICIPANT_MENTION_REVIEW_VERIFIED = "verified"
PARTICIPANT_MENTION_REVIEW_REJECTED = "rejected"

PARTICIPANT_MENTION_REVIEW_STATUSES: tuple[str, ...] = (
    PARTICIPANT_MENTION_REVIEW_PENDING,
    PARTICIPANT_MENTION_REVIEW_VERIFIED,
    PARTICIPANT_MENTION_REVIEW_REJECTED,
)


@dataclass(frozen=True, slots=True)
class EventClaim:
    """一个文档对一只股票提取出的一条候选事实（v2 多事实管线）。

    ``EventExtraction`` 保留最终选中事实以兼容现有榜单；``EventClaim`` 记录
    候选事实、拒绝原因、复核状态与逐门控决策轨迹，供待核验明细与人工/AI
    复核使用。``review_status`` 为固定枚举（pending_review/verified/rejected/
    superseded）。
    """

    claim_id: str
    document_id: str
    stock_code: str
    event_type: str
    direction: str
    positive_mechanism: str | None
    metrics: tuple[dict[str, object], ...]
    certainty_stage: str
    certainty: float
    materiality_level: int
    counter_evidence: tuple[dict[str, object], ...]
    evidence_ids: tuple[str, ...]
    rejection_reason: str | None
    review_status: str
    gate_trace: tuple[dict[str, object], ...]
    extractor_kind: str
    extractor_version: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "document_id": self.document_id,
            "stock_code": self.stock_code,
            "event_type": self.event_type,
            "direction": self.direction,
            "positive_mechanism": self.positive_mechanism,
            "metrics": [dict(item) for item in self.metrics],
            "certainty_stage": self.certainty_stage,
            "certainty": self.certainty,
            "materiality_level": self.materiality_level,
            "counter_evidence": [dict(item) for item in self.counter_evidence],
            "evidence_ids": list(self.evidence_ids),
            "rejection_reason": self.rejection_reason,
            "review_status": self.review_status,
            "gate_trace": [dict(item) for item in self.gate_trace],
            "extractor_kind": self.extractor_kind,
            "extractor_version": self.extractor_version,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventClaim":
        return cls(
            claim_id=str(data.get("claim_id") or ""),
            document_id=str(data.get("document_id") or ""),
            stock_code=str(data.get("stock_code") or ""),
            event_type=str(data.get("event_type") or ""),
            direction=str(data.get("direction") or ""),
            positive_mechanism=data.get("positive_mechanism"),
            metrics=tuple(dict(item) for item in data.get("metrics", [])),
            certainty_stage=str(data.get("certainty_stage") or ""),
            certainty=float(data.get("certainty", 0.0)),
            materiality_level=int(data.get("materiality_level", 0)),
            counter_evidence=tuple(
                dict(item) for item in data.get("counter_evidence", [])
            ),
            evidence_ids=tuple(
                str(item) for item in data.get("evidence_ids", [])
            ),
            rejection_reason=data.get("rejection_reason"),
            review_status=str(
                data.get("review_status") or EVENT_CLAIM_REVIEW_PENDING
            ),
            gate_trace=tuple(
                dict(item) for item in data.get("gate_trace", [])
            ),
            extractor_kind=str(data.get("extractor_kind") or "rules"),
            extractor_version=str(data.get("extractor_version") or ""),
            created_at=_dt(data.get("created_at")) or datetime.now(),
        )


@dataclass(frozen=True, slots=True)
class ResearchParticipantMention:
    """原文参与者提及（v2：保存原始名单片段、位置、组织类别与复核状态）。

    ``organization_category`` 固定为 research_institution / other_organization /
    person / excluded；``parse_version`` 记录解析器版本以便历史重算时替换；
    ``review_status`` 为 pending_review / verified / rejected。
    """

    mention_id: str
    document_id: str
    activity_id: str
    raw_name: str
    start_offset: int | None
    end_offset: int | None
    organization_category: str
    parse_version: str
    review_status: str
    evidence_id: str | None
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "mention_id": self.mention_id,
            "document_id": self.document_id,
            "activity_id": self.activity_id,
            "raw_name": self.raw_name,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "organization_category": self.organization_category,
            "parse_version": self.parse_version,
            "review_status": self.review_status,
            "evidence_id": self.evidence_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchParticipantMention":
        return cls(
            mention_id=str(data.get("mention_id") or ""),
            document_id=str(data.get("document_id") or ""),
            activity_id=str(data.get("activity_id") or ""),
            raw_name=str(data.get("raw_name") or ""),
            start_offset=_to_int(data.get("start_offset")),
            end_offset=_to_int(data.get("end_offset")),
            organization_category=str(
                data.get("organization_category") or "other_organization"
            ),
            parse_version=str(data.get("parse_version") or ""),
            review_status=str(
                data.get("review_status") or PARTICIPANT_MENTION_REVIEW_PENDING
            ),
            evidence_id=data.get("evidence_id"),
            created_at=_dt(data.get("created_at")) or datetime.now(),
        )


@dataclass(frozen=True, slots=True)
class ReportedParticipantCount:
    """一份活动的结构化披露总数（v2：禁止混用单位或按总数虚构实体）。

    分开记录“明确列名研究机构数”“全部列名组织数”“披露机构数”“披露人数”；
    后两者只来自原文明确披露，绝不据此生成机构实体。
    """

    activity_id: str
    named_research_count: int
    all_named_org_count: int
    reported_institution_count: int | None
    reported_person_count: int | None
    evidence_id: str | None
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "named_research_count": self.named_research_count,
            "all_named_org_count": self.all_named_org_count,
            "reported_institution_count": self.reported_institution_count,
            "reported_person_count": self.reported_person_count,
            "evidence_id": self.evidence_id,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportedParticipantCount":
        return cls(
            activity_id=str(data.get("activity_id") or ""),
            named_research_count=int(data.get("named_research_count", 0)),
            all_named_org_count=int(data.get("all_named_org_count", 0)),
            reported_institution_count=_to_int(
                data.get("reported_institution_count")
            ),
            reported_person_count=_to_int(data.get("reported_person_count")),
            evidence_id=data.get("evidence_id"),
            updated_at=_dt(data.get("updated_at")) or datetime.now(),
        )


@dataclass(frozen=True, slots=True)
class EventSignal:
    """Final short-term signal on one stock of one event cluster."""

    event_id: str
    stock_code: str
    board: str  # confirmed_positive | potential_catalyst
    score: float
    source_confidence: float
    materiality_level: int
    certainty: float
    unexpectedness: float
    novelty: float
    timeliness: float
    penalty: float
    provisional: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "stock_code": self.stock_code,
            "board": self.board,
            "score": self.score,
            "source_confidence": self.source_confidence,
            "materiality_level": self.materiality_level,
            "certainty": self.certainty,
            "unexpectedness": self.unexpectedness,
            "novelty": self.novelty,
            "timeliness": self.timeliness,
            "penalty": self.penalty,
            "provisional": self.provisional,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventSignal":
        return cls(
            event_id=str(data["event_id"]),
            stock_code=str(data["stock_code"]),
            board=str(data.get("board") or ""),
            score=float(data.get("score", 0.0)),
            source_confidence=float(data.get("source_confidence", 0.0)),
            materiality_level=int(data.get("materiality_level", 0)),
            certainty=float(data.get("certainty", 0.0)),
            unexpectedness=float(data.get("unexpectedness", 0.0)),
            novelty=float(data.get("novelty", 0.0)),
            timeliness=float(data.get("timeliness", 0.0)),
            penalty=float(data.get("penalty", 0.0)),
            provisional=bool(data.get("provisional", False)),
        )


@dataclass(frozen=True, slots=True)
class Institution:
    """A normalized research institution entity."""

    institution_id: str
    canonical_name: str
    group_id: str
    institution_type: str
    verification_status: str  # verified | normalized | needs_review

    def to_dict(self) -> dict[str, Any]:
        return {
            "institution_id": self.institution_id,
            "canonical_name": self.canonical_name,
            "group_id": self.group_id,
            "institution_type": self.institution_type,
            "verification_status": self.verification_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Institution":
        return cls(
            institution_id=str(data["institution_id"]),
            canonical_name=str(data.get("canonical_name") or ""),
            group_id=str(data.get("group_id") or ""),
            institution_type=str(data.get("institution_type") or "other"),
            verification_status=str(data.get("verification_status") or "needs_review"),
        )


@dataclass(frozen=True, slots=True)
class InstitutionAlias:
    normalized_alias: str
    institution_id: str
    source: str  # seed | exact_rule | manual

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized_alias": self.normalized_alias,
            "institution_id": self.institution_id,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstitutionAlias":
        return cls(
            normalized_alias=str(data["normalized_alias"]),
            institution_id=str(data["institution_id"]),
            source=str(data.get("source") or "exact_rule"),
        )


@dataclass(frozen=True, slots=True)
class ResearchActivity:
    """One investor-relations research activity disclosed by a company."""

    activity_id: str
    stock_code: str
    source_document_id: str
    activity_dates: tuple[date, ...]
    activity_type: str
    reported_participant_count: int | None
    named_participant_count: int
    question_count: int
    high_depth_question_count: int
    topic_counts: dict[str, int]
    # Internal parse metadata (plan.md 6.3/12.3): depth buckets per question
    # and how ``activity_dates`` were resolved.  Both are additive and do not
    # change the meaning of the fields above.
    depth_counts: dict[str, int] = field(default_factory=dict)
    date_precision: str = "explicit"  # explicit | disclosure_end

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "stock_code": self.stock_code,
            "source_document_id": self.source_document_id,
            "activity_dates": [value.isoformat() for value in self.activity_dates],
            "activity_type": self.activity_type,
            "reported_participant_count": self.reported_participant_count,
            "named_participant_count": self.named_participant_count,
            "question_count": self.question_count,
            "high_depth_question_count": self.high_depth_question_count,
            "topic_counts": dict(self.topic_counts),
            "depth_counts": {
                str(key): int(value)
                for key, value in (self.depth_counts or {}).items()
            },
            "date_precision": self.date_precision,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchActivity":
        return cls(
            activity_id=str(data["activity_id"]),
            stock_code=str(data.get("stock_code") or ""),
            source_document_id=str(data.get("source_document_id") or ""),
            activity_dates=tuple(
                value
                for value in (_to_date(item) for item in data.get("activity_dates", []))
                if value is not None
            ),
            activity_type=str(data.get("activity_type") or ""),
            reported_participant_count=_to_int(data.get("reported_participant_count")),
            named_participant_count=int(data.get("named_participant_count", 0)),
            question_count=int(data.get("question_count", 0)),
            high_depth_question_count=int(data.get("high_depth_question_count", 0)),
            topic_counts={
                str(key): int(value)
                for key, value in (data.get("topic_counts") or {}).items()
            },
            depth_counts={
                str(key): int(value)
                for key, value in (data.get("depth_counts") or {}).items()
            },
            date_precision=str(data.get("date_precision") or "explicit"),
        )


@dataclass(frozen=True, slots=True)
class ResearchParticipant:
    activity_id: str
    institution_id: str
    analyst_name: str | None
    evidence_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "institution_id": self.institution_id,
            "analyst_name": self.analyst_name,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchParticipant":
        return cls(
            activity_id=str(data["activity_id"]),
            institution_id=str(data["institution_id"]),
            analyst_name=data.get("analyst_name"),
            evidence_id=str(data.get("evidence_id") or ""),
        )


ACTIVITY_DATE_PRECISION_EXPLICIT_DAY = "explicit_day"
ACTIVITY_DATE_PRECISION_EXPLICIT_RANGE = "explicit_range"
ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY = "disclosure_day"
ACTIVITY_DATE_PRECISION_UNKNOWN = "unknown"
ACTIVITY_DATE_PRECISIONS: tuple[str, ...] = (
    ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
    ACTIVITY_DATE_PRECISION_EXPLICIT_RANGE,
    ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY,
    ACTIVITY_DATE_PRECISION_UNKNOWN,
)


@dataclass(frozen=True, slots=True)
class ActivityOccurrence:
    """One date-bearing occurrence of a disclosed research activity.

    ``occurred_on`` is populated only for a specific activity day.  Ranges and
    disclosure-day fallbacks remain auditable through ``period_start`` /
    ``period_end`` and ``date_precision`` but must set ``metric_eligible`` to
    false until a reliable activity day is available.
    """

    occurrence_id: str
    activity_id: str
    occurred_on: date | None
    period_start: date | None
    period_end: date | None
    date_precision: str
    metric_eligible: bool
    exclusion_reason: str | None
    evidence_id: str | None
    parse_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id,
            "activity_id": self.activity_id,
            "occurred_on": self.occurred_on.isoformat() if self.occurred_on else None,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "date_precision": self.date_precision,
            "metric_eligible": self.metric_eligible,
            "exclusion_reason": self.exclusion_reason,
            "evidence_id": self.evidence_id,
            "parse_version": self.parse_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActivityOccurrence":
        return cls(
            occurrence_id=str(data.get("occurrence_id") or ""),
            activity_id=str(data.get("activity_id") or ""),
            occurred_on=_d(data.get("occurred_on")),
            period_start=_d(data.get("period_start")),
            period_end=_d(data.get("period_end")),
            date_precision=str(
                data.get("date_precision") or ACTIVITY_DATE_PRECISION_UNKNOWN
            ),
            metric_eligible=bool(data.get("metric_eligible", False)),
            exclusion_reason=data.get("exclusion_reason"),
            evidence_id=data.get("evidence_id"),
            parse_version=str(data.get("parse_version") or ""),
        )


@dataclass(frozen=True, slots=True)
class ResearchParticipantOccurrence:
    """An explicit institution/person association with one activity day.

    ``research_eligible`` is persisted at the occurrence level so excluded
    companies, law firms, media and other organisations remain visible in the
    detail view without leaking into institution breadth.  Analyst identity is
    kept together with ``institution_id`` so same-name analysts at different
    institutions are never collapsed by the storage contract.
    """

    participant_occurrence_id: str
    activity_occurrence_id: str
    activity_id: str
    institution_id: str
    analyst_name: str | None
    research_eligible: bool
    eligibility_reason: str
    evidence_id: str | None
    parse_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_occurrence_id": self.participant_occurrence_id,
            "activity_occurrence_id": self.activity_occurrence_id,
            "activity_id": self.activity_id,
            "institution_id": self.institution_id,
            "analyst_name": self.analyst_name,
            "research_eligible": self.research_eligible,
            "eligibility_reason": self.eligibility_reason,
            "evidence_id": self.evidence_id,
            "parse_version": self.parse_version,
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any]
    ) -> "ResearchParticipantOccurrence":
        return cls(
            participant_occurrence_id=str(
                data.get("participant_occurrence_id") or ""
            ),
            activity_occurrence_id=str(data.get("activity_occurrence_id") or ""),
            activity_id=str(data.get("activity_id") or ""),
            institution_id=str(data.get("institution_id") or ""),
            analyst_name=data.get("analyst_name"),
            research_eligible=bool(data.get("research_eligible", False)),
            eligibility_reason=str(data.get("eligibility_reason") or ""),
            evidence_id=data.get("evidence_id"),
            parse_version=str(data.get("parse_version") or ""),
        )


@dataclass(frozen=True, slots=True)
class SourceWindowCoverage:
    """Comparable coverage for one research source, market and metric window."""

    source_key: str
    market: str  # sh | sz | bj
    source_kind: str  # research_activity only for institution metrics
    window_kind: str
    source_cohort_id: str
    requested_start: date
    requested_end: date
    covered_start: date | None
    covered_end: date | None
    reached_cutoff: bool
    reconciled: bool
    cohort_eligible: bool
    last_success_at: datetime | None
    error: str | None
    exclusion_reason: str | None
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "market": self.market,
            "source_kind": self.source_kind,
            "window_kind": self.window_kind,
            "source_cohort_id": self.source_cohort_id,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "covered_start": self.covered_start.isoformat() if self.covered_start else None,
            "covered_end": self.covered_end.isoformat() if self.covered_end else None,
            "reached_cutoff": self.reached_cutoff,
            "reconciled": self.reconciled,
            "cohort_eligible": self.cohort_eligible,
            "last_success_at": self.last_success_at.isoformat()
            if self.last_success_at
            else None,
            "error": self.error,
            "exclusion_reason": self.exclusion_reason,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceWindowCoverage":
        return cls(
            source_key=str(data.get("source_key") or ""),
            market=str(data.get("market") or ""),
            source_kind=str(data.get("source_kind") or "research_activity"),
            window_kind=str(data.get("window_kind") or ""),
            source_cohort_id=str(data.get("source_cohort_id") or ""),
            requested_start=date.fromisoformat(str(data["requested_start"])),
            requested_end=date.fromisoformat(str(data["requested_end"])),
            covered_start=_d(data.get("covered_start")),
            covered_end=_d(data.get("covered_end")),
            reached_cutoff=bool(data.get("reached_cutoff", False)),
            reconciled=bool(data.get("reconciled", False)),
            cohort_eligible=bool(data.get("cohort_eligible", False)),
            last_success_at=_dt(data.get("last_success_at")),
            error=data.get("error"),
            exclusion_reason=data.get("exclusion_reason"),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
        )


@dataclass(frozen=True, slots=True)
class InstitutionMetricSnapshotRecord:
    """Versioned institution metric row while legacy and v2 coexist."""

    stock_code: str
    window_kind: str
    window_start: datetime | None
    window_end: datetime | None
    snapshot_at: datetime
    metrics: dict[str, object]
    metric_version: str
    source_cohort_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "window_kind": self.window_kind,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "snapshot_at": self.snapshot_at.isoformat(),
            "metrics": dict(self.metrics),
            "metric_version": self.metric_version,
            "source_cohort_id": self.source_cohort_id,
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any]
    ) -> "InstitutionMetricSnapshotRecord":
        return cls(
            stock_code=str(data.get("stock_code") or ""),
            window_kind=str(data.get("window_kind") or ""),
            window_start=_dt(data.get("window_start")),
            window_end=_dt(data.get("window_end")),
            snapshot_at=datetime.fromisoformat(str(data["snapshot_at"])),
            metrics=dict(data.get("metrics") or {}),
            metric_version=str(data.get("metric_version") or "z20_legacy"),
            source_cohort_id=str(data.get("source_cohort_id") or ""),
        )


@dataclass(frozen=True, slots=True)
class CoverageState:
    """Coverage state of a research backfill window for one source."""

    source_key: str
    requested_start: date
    covered_start: date | None
    covered_end: date | None
    trading_days_covered: int
    reached_cutoff: bool
    provisional: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "requested_start": self.requested_start.isoformat(),
            "covered_start": self.covered_start.isoformat() if self.covered_start else None,
            "covered_end": self.covered_end.isoformat() if self.covered_end else None,
            "trading_days_covered": self.trading_days_covered,
            "reached_cutoff": self.reached_cutoff,
            "provisional": self.provisional,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoverageState":
        return cls(
            source_key=str(data["source_key"]),
            requested_start=date.fromisoformat(data["requested_start"]),
            covered_start=_d(data.get("covered_start")),
            covered_end=_d(data.get("covered_end")),
            trading_days_covered=int(data.get("trading_days_covered", 0)),
            reached_cutoff=bool(data.get("reached_cutoff", False)),
            provisional=bool(data.get("provisional", False)),
            error=data.get("error"),
        )


@dataclass(frozen=True, slots=True)
class SyncCursor:
    """Resumable sync state for one source and query kind.

    ``cursor`` holds adapter-specific pagination/cursor data; page addresses
    must never leak into ranking or UI code.
    """

    source_key: str
    sync_kind: str
    cursor: dict[str, Any] | None
    target_start: date | None
    covered_start: date | None
    last_success_at: datetime | None
    last_error: str | None
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "sync_kind": self.sync_kind,
            "cursor": dict(self.cursor) if self.cursor is not None else None,
            "target_start": self.target_start.isoformat() if self.target_start else None,
            "covered_start": self.covered_start.isoformat() if self.covered_start else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_error": self.last_error,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SyncCursor":
        return cls(
            source_key=str(data["source_key"]),
            sync_kind=str(data.get("sync_kind") or ""),
            cursor=dict(data["cursor"]) if data.get("cursor") is not None else None,
            target_start=_d(data.get("target_start")),
            covered_start=_d(data.get("covered_start")),
            last_success_at=_dt(data.get("last_success_at")),
            last_error=data.get("last_error"),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass(frozen=True, slots=True)
class ResearchSourceCohort:
    """Comparable research-activity source set for one A-share market.

    ``source_keys`` is sticky once a source has entered a cohort: a temporary
    source failure therefore makes the market cold instead of silently
    shrinking both the current bucket and its historical baseline.
    ``supplemental_source_keys`` are visible but excluded from both sides of
    the comparison until they cover the complete requested window.
    """

    market: str  # sh | sz | bj
    window_kind: str
    source_cohort_id: str
    source_keys: tuple[str, ...]
    supplemental_source_keys: tuple[str, ...]
    unavailable_source_keys: tuple[str, ...]
    requested_start: date
    requested_end: date
    covered_start: date | None
    covered_end: date | None
    trading_days_covered: int
    formal_ranking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "window_kind": self.window_kind,
            "source_cohort_id": self.source_cohort_id,
            "source_keys": list(self.source_keys),
            "supplemental_source_keys": list(self.supplemental_source_keys),
            "unavailable_source_keys": list(self.unavailable_source_keys),
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "covered_start": self.covered_start.isoformat()
            if self.covered_start
            else None,
            "covered_end": self.covered_end.isoformat() if self.covered_end else None,
            "trading_days_covered": self.trading_days_covered,
            "formal_ranking": self.formal_ranking,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchSourceCohort":
        return cls(
            market=str(data.get("market") or ""),
            window_kind=str(data.get("window_kind") or "warming_20"),
            source_cohort_id=str(data.get("source_cohort_id") or ""),
            source_keys=tuple(str(item) for item in data.get("source_keys", [])),
            supplemental_source_keys=tuple(
                str(item) for item in data.get("supplemental_source_keys", [])
            ),
            unavailable_source_keys=tuple(
                str(item) for item in data.get("unavailable_source_keys", [])
            ),
            requested_start=date.fromisoformat(str(data["requested_start"])),
            requested_end=date.fromisoformat(str(data["requested_end"])),
            covered_start=_d(data.get("covered_start")),
            covered_end=_d(data.get("covered_end")),
            trading_days_covered=int(data.get("trading_days_covered", 0)),
            formal_ranking=bool(data.get("formal_ranking", False)),
        )


@dataclass(frozen=True, slots=True)
class ResearchCoverage:
    """Coverage state shared by the 20/60/120-day research boards.

    Combines the research-source backfill cursors with the trading calendar
    state so cold start, partial coverage, calendar fallback and staleness are
    all visible to the UI without leaking adapter details.
    """

    requested_start: date
    covered_start: date | None
    covered_end: date | None
    trading_days_covered: int
    sources_scanned: int
    sources_total: int
    reached_cutoff: bool
    calendar_fallback: bool
    last_success_at: datetime | None
    provisional: bool
    error: str | None
    market_cohorts: tuple[ResearchSourceCohort, ...] = ()
    formal_ranking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_start": self.requested_start.isoformat(),
            "covered_start": self.covered_start.isoformat() if self.covered_start else None,
            "covered_end": self.covered_end.isoformat() if self.covered_end else None,
            "trading_days_covered": self.trading_days_covered,
            "sources_scanned": self.sources_scanned,
            "sources_total": self.sources_total,
            "reached_cutoff": self.reached_cutoff,
            "calendar_fallback": self.calendar_fallback,
            "last_success_at": self.last_success_at.isoformat()
            if self.last_success_at
            else None,
            "provisional": self.provisional,
            "error": self.error,
            "market_cohorts": [item.to_dict() for item in self.market_cohorts],
            "formal_ranking": self.formal_ranking,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchCoverage":
        return cls(
            requested_start=date.fromisoformat(data["requested_start"]),
            covered_start=_d(data.get("covered_start")),
            covered_end=_d(data.get("covered_end")),
            trading_days_covered=int(data.get("trading_days_covered", 0)),
            sources_scanned=int(data.get("sources_scanned", 0)),
            sources_total=int(data.get("sources_total", 0)),
            reached_cutoff=bool(data.get("reached_cutoff", False)),
            calendar_fallback=bool(data.get("calendar_fallback", False)),
            last_success_at=_dt(data.get("last_success_at")),
            provisional=bool(data.get("provisional", False)),
            error=data.get("error"),
            market_cohorts=tuple(
                ResearchSourceCohort.from_dict(item)
                for item in data.get("market_cohorts", [])
            ),
            formal_ranking=bool(data.get("formal_ranking", False)),
        )


@dataclass(frozen=True, slots=True)
class Z20Row:
    """One row of the 20-trading-day institution warming board.

    ``z20`` is ``None`` when fewer than 120 trading days are covered (cold
    start); raw current-window metrics are still shown in that case.
    """

    stock_code: str
    industry: str | None
    z20: float | None
    current_unique_groups: int
    new_groups: int
    analyst_count: int
    high_depth_ratio: float
    question_count: int
    recent_activity: date | None
    industry_percentile: float | None
    industry_sample_size: int
    provisional: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "industry": self.industry,
            "z20": self.z20,
            "current_unique_groups": self.current_unique_groups,
            "new_groups": self.new_groups,
            "analyst_count": self.analyst_count,
            "high_depth_ratio": self.high_depth_ratio,
            "question_count": self.question_count,
            "recent_activity": self.recent_activity.isoformat()
            if self.recent_activity
            else None,
            "industry_percentile": self.industry_percentile,
            "industry_sample_size": self.industry_sample_size,
            "provisional": self.provisional,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Z20Row":
        return cls(
            stock_code=str(data["stock_code"]),
            industry=data.get("industry"),
            z20=_to_float(data.get("z20")),
            current_unique_groups=int(data.get("current_unique_groups", 0)),
            new_groups=int(data.get("new_groups", 0)),
            analyst_count=int(data.get("analyst_count", 0)),
            high_depth_ratio=float(data.get("high_depth_ratio", 0.0)),
            question_count=int(data.get("question_count", 0)),
            recent_activity=_d(data.get("recent_activity")),
            industry_percentile=_to_float(data.get("industry_percentile")),
            industry_sample_size=int(data.get("industry_sample_size", 0)),
            provisional=bool(data.get("provisional", False)),
        )


@dataclass(frozen=True, slots=True)
class WarmingV2Row:
    """Descriptive 20-day institution warming metric (``warming_v2``)."""

    stock_code: str
    industry: str | None
    warming_score: float | None
    baseline_mean: float | None
    baseline_variance: float | None
    predictive_variance: float | None
    baseline_bucket_count: int
    coverage_level: str  # full | provisional | raw_only
    absolute_change: float | None
    current_unique_groups: int
    unseen_100d_groups: int | None
    active_days: int
    single_day_concentration: float
    single_day: bool
    recent_activity: date | None
    industry_percentile: float | None
    industry_sample_size: int
    source_cohort_id: str
    date_quality: str = "explicit_day"
    excluded_organization_count: int = 0
    provisional_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "industry": self.industry,
            "warming_score": self.warming_score,
            "baseline_mean": self.baseline_mean,
            "baseline_variance": self.baseline_variance,
            "predictive_variance": self.predictive_variance,
            "baseline_bucket_count": self.baseline_bucket_count,
            "coverage_level": self.coverage_level,
            "absolute_change": self.absolute_change,
            "current_unique_groups": self.current_unique_groups,
            "unseen_100d_groups": self.unseen_100d_groups,
            "active_days": self.active_days,
            "single_day_concentration": self.single_day_concentration,
            "single_day": self.single_day,
            "recent_activity": self.recent_activity.isoformat()
            if self.recent_activity
            else None,
            "industry_percentile": self.industry_percentile,
            "industry_sample_size": self.industry_sample_size,
            "source_cohort_id": self.source_cohort_id,
            "date_quality": self.date_quality,
            "excluded_organization_count": self.excluded_organization_count,
            "provisional_reason": self.provisional_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WarmingV2Row":
        return cls(
            stock_code=str(data["stock_code"]),
            industry=data.get("industry"),
            warming_score=_to_float(data.get("warming_score")),
            baseline_mean=_to_float(data.get("baseline_mean")),
            baseline_variance=_to_float(data.get("baseline_variance")),
            predictive_variance=_to_float(data.get("predictive_variance")),
            baseline_bucket_count=int(data.get("baseline_bucket_count", 0)),
            coverage_level=str(data.get("coverage_level") or "raw_only"),
            absolute_change=_to_float(data.get("absolute_change")),
            current_unique_groups=int(data.get("current_unique_groups", 0)),
            unseen_100d_groups=(
                int(data["unseen_100d_groups"])
                if data.get("unseen_100d_groups") is not None
                else None
            ),
            active_days=int(data.get("active_days", 0)),
            single_day_concentration=float(
                data.get("single_day_concentration", 0.0)
            ),
            single_day=bool(data.get("single_day", False)),
            recent_activity=_d(data.get("recent_activity")),
            industry_percentile=_to_float(data.get("industry_percentile")),
            industry_sample_size=int(data.get("industry_sample_size", 0)),
            source_cohort_id=str(data.get("source_cohort_id") or ""),
            date_quality=str(data.get("date_quality") or "explicit_day"),
            excluded_organization_count=int(
                data.get("excluded_organization_count", 0)
            ),
            provisional_reason=data.get("provisional_reason"),
        )


@dataclass(frozen=True, slots=True)
class PersistenceRow:
    """One row of the 60/120-trading-day persistence board."""

    stock_code: str
    window_kind: str  # persistence_60 | persistence_120
    persistence_score: float | None
    active_weeks: int
    active_week_ratio: float
    unique_groups: int
    repeat_followup_ratio: float
    depth_score: float | None
    single_day_concentration: float
    topics: dict[str, int]
    recent_activity: date | None
    covered_trading_days: int
    provisional: bool = False
    question_data_status: str = "available"
    date_mapping_complete: bool = True
    metric_version: str = "persistence_legacy"
    provisional_reason: str | None = None
    source_cohort_id: str = ""
    date_quality: str = "explicit_day"
    excluded_organization_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "window_kind": self.window_kind,
            "persistence_score": self.persistence_score,
            "active_weeks": self.active_weeks,
            "active_week_ratio": self.active_week_ratio,
            "unique_groups": self.unique_groups,
            "repeat_followup_ratio": self.repeat_followup_ratio,
            "depth_score": self.depth_score,
            "single_day_concentration": self.single_day_concentration,
            "topics": dict(self.topics),
            "recent_activity": self.recent_activity.isoformat()
            if self.recent_activity
            else None,
            "covered_trading_days": self.covered_trading_days,
            "provisional": self.provisional,
            "question_data_status": self.question_data_status,
            "date_mapping_complete": self.date_mapping_complete,
            "metric_version": self.metric_version,
            "provisional_reason": self.provisional_reason,
            "source_cohort_id": self.source_cohort_id,
            "date_quality": self.date_quality,
            "excluded_organization_count": self.excluded_organization_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersistenceRow":
        return cls(
            stock_code=str(data["stock_code"]),
            window_kind=str(data.get("window_kind") or ""),
            persistence_score=_to_float(data.get("persistence_score")),
            active_weeks=int(data.get("active_weeks", 0)),
            active_week_ratio=float(data.get("active_week_ratio", 0.0)),
            unique_groups=int(data.get("unique_groups", 0)),
            repeat_followup_ratio=float(data.get("repeat_followup_ratio", 0.0)),
            depth_score=_to_float(data.get("depth_score")),
            single_day_concentration=float(data.get("single_day_concentration", 0.0)),
            topics={
                str(key): int(value)
                for key, value in (data.get("topics") or {}).items()
            },
            recent_activity=_d(data.get("recent_activity")),
            covered_trading_days=int(data.get("covered_trading_days", 0)),
            provisional=bool(data.get("provisional", False)),
            question_data_status=str(
                data.get("question_data_status") or "available"
            ),
            date_mapping_complete=bool(data.get("date_mapping_complete", True)),
            metric_version=str(
                data.get("metric_version") or "persistence_legacy"
            ),
            provisional_reason=data.get("provisional_reason"),
            source_cohort_id=str(data.get("source_cohort_id") or ""),
            date_quality=str(data.get("date_quality") or "explicit_day"),
            excluded_organization_count=int(
                data.get("excluded_organization_count", 0)
            ),
        )


@dataclass(frozen=True, slots=True)
class ShortTermViewRow:
    """Display row of a short-term research board (UI layer, milestone 5).

    Composes persisted EventSignal/EventExtraction/EventCluster data into one
    stable display contract so table, CSV and copy share exactly the same
    columns.  Full evidence for the detail panel is loaded from storage by
    ``event_id`` + ``stock_code``.
    """

    rank: int
    stock_code: str
    stock_name: str
    industry: str | None
    event_type: str
    positive_mechanism: str | None
    materiality_level: int
    key_metric: str
    certainty: float
    counter_evidence: str
    event_time: datetime | None
    quality_state: str  # ok | provisional | partial | cold_start | error
    extractor_label: str  # 规则 | AI增强 | 规则降级
    provisional: bool
    event_id: str
    board: str  # confirmed_positive | potential_catalyst
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "industry": self.industry,
            "event_type": self.event_type,
            "positive_mechanism": self.positive_mechanism,
            "materiality_level": self.materiality_level,
            "key_metric": self.key_metric,
            "certainty": self.certainty,
            "counter_evidence": self.counter_evidence,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "quality_state": self.quality_state,
            "extractor_label": self.extractor_label,
            "provisional": self.provisional,
            "event_id": self.event_id,
            "board": self.board,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShortTermViewRow":
        return cls(
            rank=int(data.get("rank", 0)),
            stock_code=str(data["stock_code"]),
            stock_name=str(data.get("stock_name") or data["stock_code"]),
            industry=data.get("industry"),
            event_type=str(data.get("event_type") or ""),
            positive_mechanism=data.get("positive_mechanism"),
            materiality_level=int(data.get("materiality_level", 0)),
            key_metric=str(data.get("key_metric") or ""),
            certainty=float(data.get("certainty", 0.0)),
            counter_evidence=str(data.get("counter_evidence") or ""),
            event_time=_dt(data.get("event_time")),
            quality_state=str(data.get("quality_state") or "ok"),
            extractor_label=str(data.get("extractor_label") or "规则"),
            provisional=bool(data.get("provisional", False)),
            event_id=str(data["event_id"]),
            board=str(data.get("board") or ""),
            score=float(data.get("score", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class InstitutionZ20ViewRow:
    """Display row of the 20-trading-day institution warming board."""

    rank: int
    stock_code: str
    stock_name: str
    industry: str | None
    z20: float | None
    current_unique_groups: int
    new_groups: int
    analyst_count: int
    high_depth_ratio: float
    question_count: int
    recent_activity: date | None
    industry_percentile: float | None
    industry_sample_size: int
    provisional: bool
    coverage_state: str
    metric_version: str = "z20_legacy"
    source_cohort_id: str = ""
    absolute_change: float | None = None
    unseen_100d_groups: int | None = None
    active_days: int = 0
    single_day_concentration: float = 0.0
    single_day: bool = False
    coverage_level: str = "raw_only"
    date_quality: str = "legacy_unknown"
    excluded_organization_count: int = 0
    provisional_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "industry": self.industry,
            "z20": self.z20,
            "current_unique_groups": self.current_unique_groups,
            "new_groups": self.new_groups,
            "analyst_count": self.analyst_count,
            "high_depth_ratio": self.high_depth_ratio,
            "question_count": self.question_count,
            "recent_activity": self.recent_activity.isoformat()
            if self.recent_activity
            else None,
            "industry_percentile": self.industry_percentile,
            "industry_sample_size": self.industry_sample_size,
            "provisional": self.provisional,
            "coverage_state": self.coverage_state,
            "metric_version": self.metric_version,
            "source_cohort_id": self.source_cohort_id,
            "absolute_change": self.absolute_change,
            "unseen_100d_groups": self.unseen_100d_groups,
            "active_days": self.active_days,
            "single_day_concentration": self.single_day_concentration,
            "single_day": self.single_day,
            "coverage_level": self.coverage_level,
            "date_quality": self.date_quality,
            "excluded_organization_count": self.excluded_organization_count,
            "provisional_reason": self.provisional_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstitutionZ20ViewRow":
        return cls(
            rank=int(data.get("rank", 0)),
            stock_code=str(data["stock_code"]),
            stock_name=str(data.get("stock_name") or data["stock_code"]),
            industry=data.get("industry"),
            z20=_to_float(data.get("z20")),
            current_unique_groups=int(data.get("current_unique_groups", 0)),
            new_groups=int(data.get("new_groups", 0)),
            analyst_count=int(data.get("analyst_count", 0)),
            high_depth_ratio=float(data.get("high_depth_ratio", 0.0)),
            question_count=int(data.get("question_count", 0)),
            recent_activity=_d(data.get("recent_activity")),
            industry_percentile=_to_float(data.get("industry_percentile")),
            industry_sample_size=int(data.get("industry_sample_size", 0)),
            provisional=bool(data.get("provisional", False)),
            coverage_state=str(data.get("coverage_state") or "ok"),
            metric_version=str(data.get("metric_version") or "z20_legacy"),
            source_cohort_id=str(data.get("source_cohort_id") or ""),
            absolute_change=_to_float(data.get("absolute_change")),
            unseen_100d_groups=(
                int(data["unseen_100d_groups"])
                if data.get("unseen_100d_groups") is not None
                else None
            ),
            active_days=int(data.get("active_days", 0)),
            single_day_concentration=float(
                data.get("single_day_concentration", 0.0)
            ),
            single_day=bool(data.get("single_day", False)),
            coverage_level=str(data.get("coverage_level") or "raw_only"),
            date_quality=str(data.get("date_quality") or "legacy_unknown"),
            excluded_organization_count=int(
                data.get("excluded_organization_count", 0)
            ),
            provisional_reason=data.get("provisional_reason"),
        )


@dataclass(frozen=True, slots=True)
class PersistenceViewRow:
    """Display row of the 60/120-trading-day persistence board."""

    rank: int
    stock_code: str
    stock_name: str
    window_kind: str  # persistence_60 | persistence_120
    persistence_score: float | None
    active_weeks: int
    active_week_ratio: float
    unique_groups: int
    repeat_followup_ratio: float
    depth_score: float | None
    single_day_concentration: float
    topics: dict[str, int]
    recent_activity: date | None
    covered_trading_days: int
    coverage_state: str
    provisional: bool = False
    metric_version: str = "persistence_legacy"
    source_cohort_id: str = ""
    question_data_status: str = "available"
    date_mapping_complete: bool = True
    date_quality: str = "legacy_unknown"
    excluded_organization_count: int = 0
    provisional_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "window_kind": self.window_kind,
            "persistence_score": self.persistence_score,
            "active_weeks": self.active_weeks,
            "active_week_ratio": self.active_week_ratio,
            "unique_groups": self.unique_groups,
            "repeat_followup_ratio": self.repeat_followup_ratio,
            "depth_score": self.depth_score,
            "single_day_concentration": self.single_day_concentration,
            "topics": dict(self.topics),
            "recent_activity": self.recent_activity.isoformat()
            if self.recent_activity
            else None,
            "covered_trading_days": self.covered_trading_days,
            "coverage_state": self.coverage_state,
            "provisional": self.provisional,
            "metric_version": self.metric_version,
            "source_cohort_id": self.source_cohort_id,
            "question_data_status": self.question_data_status,
            "date_mapping_complete": self.date_mapping_complete,
            "date_quality": self.date_quality,
            "excluded_organization_count": self.excluded_organization_count,
            "provisional_reason": self.provisional_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersistenceViewRow":
        return cls(
            rank=int(data.get("rank", 0)),
            stock_code=str(data["stock_code"]),
            stock_name=str(data.get("stock_name") or data["stock_code"]),
            window_kind=str(data.get("window_kind") or "persistence_60"),
            persistence_score=_to_float(data.get("persistence_score")),
            active_weeks=int(data.get("active_weeks", 0)),
            active_week_ratio=float(data.get("active_week_ratio", 0.0)),
            unique_groups=int(data.get("unique_groups", 0)),
            repeat_followup_ratio=float(data.get("repeat_followup_ratio", 0.0)),
            depth_score=_to_float(data.get("depth_score")),
            single_day_concentration=float(data.get("single_day_concentration", 0.0)),
            topics={
                str(key): int(value)
                for key, value in (data.get("topics") or {}).items()
            },
            recent_activity=_d(data.get("recent_activity")),
            covered_trading_days=int(data.get("covered_trading_days", 0)),
            coverage_state=str(data.get("coverage_state") or "ok"),
            provisional=bool(data.get("provisional", False)),
            metric_version=str(
                data.get("metric_version") or "persistence_legacy"
            ),
            source_cohort_id=str(data.get("source_cohort_id") or ""),
            question_data_status=str(
                data.get("question_data_status") or "available"
            ),
            date_mapping_complete=bool(data.get("date_mapping_complete", True)),
            date_quality=str(data.get("date_quality") or "legacy_unknown"),
            excluded_organization_count=int(
                data.get("excluded_organization_count", 0)
            ),
            provisional_reason=data.get("provisional_reason"),
        )


@dataclass(frozen=True, slots=True)
class StructuralComparison:
    """120-day detail: recent 60 vs prior 60 trading days of research behavior.

    Only describes the structure of disclosed research participation; it never
    infers investment opinions.
    """

    stock_code: str
    new_groups: tuple[str, ...]
    lost_groups: tuple[str, ...]
    type_share_changes: dict[str, float]
    high_depth_ratio_change: float | None
    active_week_ratio_change: float | None
    single_day_concentration_change: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "new_groups": list(self.new_groups),
            "lost_groups": list(self.lost_groups),
            "type_share_changes": {
                str(key): float(value)
                for key, value in self.type_share_changes.items()
            },
            "high_depth_ratio_change": self.high_depth_ratio_change,
            "active_week_ratio_change": self.active_week_ratio_change,
            "single_day_concentration_change": self.single_day_concentration_change,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StructuralComparison":
        return cls(
            stock_code=str(data["stock_code"]),
            new_groups=tuple(str(item) for item in data.get("new_groups", [])),
            lost_groups=tuple(str(item) for item in data.get("lost_groups", [])),
            type_share_changes={
                str(key): float(value)
                for key, value in (data.get("type_share_changes") or {}).items()
            },
            high_depth_ratio_change=_to_float(data.get("high_depth_ratio_change")),
            active_week_ratio_change=_to_float(data.get("active_week_ratio_change")),
            single_day_concentration_change=_to_float(
                data.get("single_day_concentration_change")
            ),
        )


@dataclass(frozen=True, slots=True)
class DiscoveryViewRow:
    """Display row of the 待核验 discovery view (后 1.1.0 可靠性里程碑).

    Table, CSV and clipboard copy share exactly this column contract; the row
    never pretends to be a research conclusion.
    """

    rank: int
    stock_code: str
    stock_name: str
    discovery_type: str  # enum key
    discovery_type_label: str
    title: str
    trigger_reason: str
    parse_status: str  # pending_attachment | awaiting_review | empty_text | failed
    parse_status_label: str
    published_at: datetime | None
    source_name: str
    document_id: str
    document_url: str | None
    quality_state: str  # ok | provisional | partial | cold_start | error

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "discovery_type": self.discovery_type,
            "discovery_type_label": self.discovery_type_label,
            "title": self.title,
            "trigger_reason": self.trigger_reason,
            "parse_status": self.parse_status,
            "parse_status_label": self.parse_status_label,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "source_name": self.source_name,
            "document_id": self.document_id,
            "document_url": self.document_url,
            "quality_state": self.quality_state,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryViewRow":
        return cls(
            rank=int(data.get("rank", 0)),
            stock_code=str(data["stock_code"]),
            stock_name=str(data.get("stock_name") or data["stock_code"]),
            discovery_type=str(data.get("discovery_type") or "other_disclosure"),
            discovery_type_label=str(data.get("discovery_type_label") or "其他需核验披露"),
            title=str(data.get("title") or ""),
            trigger_reason=str(data.get("trigger_reason") or ""),
            parse_status=str(data.get("parse_status") or "awaiting_review"),
            parse_status_label=str(data.get("parse_status_label") or "待核验"),
            published_at=_dt(data.get("published_at")),
            source_name=str(data.get("source_name") or ""),
            document_id=str(data.get("document_id") or ""),
            document_url=data.get("document_url"),
            quality_state=str(data.get("quality_state") or "ok"),
        )


# ---------------------------------------------------------------------------
# v1.2 官方市场覆盖闭环 (plan.md 第二部分, v1.2 里程碑 0)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FailureInterval:
    """One failure interval persisted on a source manifest.

    ``ended_at`` stays ``None`` while the source is still failing so the
    coverage center can show an open failure range; the reason is a short
    human-readable adapter error (no credentials, keys or page internals).
    """

    started_at: datetime
    ended_at: datetime | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailureInterval":
        return cls(
            started_at=datetime.fromisoformat(data["started_at"]),
            ended_at=_dt(data.get("ended_at")),
            reason=str(data.get("reason") or ""),
        )


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """One day's reconciliation manifest for one public source.

    Persists the per-source daily total, the local document-ID set summary
    (count + digest via ``coverage.summarize_document_ids``), the adapter
    watermark, failure intervals, OCR state and the last scheduled-task
    result.  ``coverage_status`` is one of the fixed plan.md v1.2 statuses;
    only a zero manifest difference plus processed bodies may show
    ``list_reconciled`` (列表已对账).
    """

    source_key: str
    manifest_date: date
    total_count: int
    document_id_count: int
    document_id_set_hash: str | None
    watermark: dict[str, Any] | None
    failure_intervals: tuple[FailureInterval, ...]
    ocr_status: str
    scheduled_task_result: dict[str, Any] | None
    coverage_status: str
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "manifest_date": self.manifest_date.isoformat(),
            "total_count": self.total_count,
            "document_id_count": self.document_id_count,
            "document_id_set_hash": self.document_id_set_hash,
            "watermark": dict(self.watermark) if self.watermark is not None else None,
            "failure_intervals": [
                interval.to_dict() for interval in self.failure_intervals
            ],
            "ocr_status": self.ocr_status,
            "scheduled_task_result": (
                dict(self.scheduled_task_result)
                if self.scheduled_task_result is not None
                else None
            ),
            "coverage_status": self.coverage_status,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceManifest":
        return cls(
            source_key=str(data["source_key"]),
            manifest_date=date.fromisoformat(data["manifest_date"]),
            total_count=int(data.get("total_count", 0)),
            document_id_count=int(data.get("document_id_count", 0)),
            document_id_set_hash=data.get("document_id_set_hash"),
            watermark=(
                dict(data["watermark"]) if data.get("watermark") is not None else None
            ),
            failure_intervals=tuple(
                FailureInterval.from_dict(item)
                for item in data.get("failure_intervals", [])
            ),
            ocr_status=str(data.get("ocr_status") or "not_applicable"),
            scheduled_task_result=(
                dict(data["scheduled_task_result"])
                if data.get("scheduled_task_result") is not None
                else None
            ),
            coverage_status=str(data.get("coverage_status") or "unavailable"),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    """One policy document from the ten fixed national policy sources.

    ``body_status`` reuses the document parse vocabulary
    (``parsed | metadata_only | empty_text | failed``); a policy without a
    verified body may stay in the industry watch but never produces a
    ``direct_policy_benefit`` stock signal on its own.
    """

    document_id: str
    source_key: str
    title: str
    published_at: datetime
    source_url: str
    document_url: str | None
    body_text: str
    body_hash: str | None
    body_status: str
    body_error: str | None
    content_hash: str
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_key": self.source_key,
            "title": self.title,
            "published_at": self.published_at.isoformat(),
            "source_url": self.source_url,
            "document_url": self.document_url,
            "body_text": self.body_text,
            "body_hash": self.body_hash,
            "body_status": self.body_status,
            "body_error": self.body_error,
            "content_hash": self.content_hash,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyDocument":
        return cls(
            document_id=str(data["document_id"]),
            source_key=str(data["source_key"]),
            title=str(data.get("title") or ""),
            published_at=datetime.fromisoformat(data["published_at"]),
            source_url=str(data.get("source_url") or ""),
            document_url=data.get("document_url"),
            body_text=str(data.get("body_text") or ""),
            body_hash=data.get("body_hash"),
            body_status=str(data.get("body_status") or "metadata_only"),
            body_error=data.get("body_error"),
            content_hash=str(data.get("content_hash") or ""),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass(frozen=True, slots=True)
class PolicyLink:
    """One dual-attribution link between a policy and a company announcement.

    ``link_kind`` is one of the fixed plan.md v1.2 kinds
    (``named_company | named_project | official_policy_ref | industry_watch``).
    Only the first three may feed a ``direct_policy_benefit`` signal, and each
    must carry a short excerpt that goes back to the policy/announcement body.
    """

    link_id: str
    policy_document_id: str
    target_document_id: str | None
    stock_code: str | None
    link_kind: str
    evidence_excerpt: str
    evidence_id: str | None
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "policy_document_id": self.policy_document_id,
            "target_document_id": self.target_document_id,
            "stock_code": self.stock_code,
            "link_kind": self.link_kind,
            "evidence_excerpt": self.evidence_excerpt,
            "evidence_id": self.evidence_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyLink":
        return cls(
            link_id=str(data["link_id"]),
            policy_document_id=str(data["policy_document_id"]),
            target_document_id=data.get("target_document_id"),
            stock_code=data.get("stock_code"),
            link_kind=str(data.get("link_kind") or "industry_watch"),
            evidence_excerpt=str(data.get("evidence_excerpt") or ""),
            evidence_id=data.get("evidence_id"),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    """One OCR'd page for a scanned PDF (plan.md v1.2 里程碑 0).

    Keeps the page index, confidence, model version and evidence URL so the
    coverage center can show exactly which page was recognised and how
    trustworthy it is.  Low-confidence, encrypted, oversized or failed files
    keep their candidate rows and degrade the body-coverage status; they never
    generate a strict positive signal.
    """

    document_id: str
    page_index: int
    confidence: float | None
    text: str
    model_version: str | None
    evidence_url: str | None
    status: str  # ok | low_confidence | failed | skipped
    error: str | None
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page_index": self.page_index,
            "confidence": self.confidence,
            "text": self.text,
            "model_version": self.model_version,
            "evidence_url": self.evidence_url,
            "status": self.status,
            "error": self.error,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OcrPageResult":
        return cls(
            document_id=str(data["document_id"]),
            page_index=int(data.get("page_index", 0)),
            confidence=(
                float(data["confidence"]) if data.get("confidence") is not None else None
            ),
            text=str(data.get("text") or ""),
            model_version=data.get("model_version"),
            evidence_url=data.get("evidence_url"),
            status=str(data.get("status") or "failed"),
            error=data.get("error"),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass(frozen=True, slots=True)
class CoverageSnapshot:
    """One persisted coverage snapshot across all manifest sources.

    ``statuses`` maps ``source_key -> coverage_status`` (fixed plan.md v1.2
    values); the counts let the UI show the reconciliation totals without
    scanning every manifest row.  ``provisional`` marks the snapshot as a
    degraded/partial view when any source failed or is still cold-starting.
    """

    snapshot_id: str
    snapshot_ts: datetime
    statuses: dict[str, str]
    manifest_count: int
    policy_document_count: int
    ocr_pending_count: int
    provisional: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_ts": self.snapshot_ts.isoformat(),
            "statuses": dict(self.statuses),
            "manifest_count": self.manifest_count,
            "policy_document_count": self.policy_document_count,
            "ocr_pending_count": self.ocr_pending_count,
            "provisional": self.provisional,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoverageSnapshot":
        return cls(
            snapshot_id=str(data["snapshot_id"]),
            snapshot_ts=datetime.fromisoformat(data["snapshot_ts"]),
            statuses={
                str(key): str(value) for key, value in data.get("statuses", {}).items()
            },
            manifest_count=int(data.get("manifest_count", 0)),
            policy_document_count=int(data.get("policy_document_count", 0)),
            ocr_pending_count=int(data.get("ocr_pending_count", 0)),
            provisional=bool(data.get("provisional", False)),
            error=data.get("error"),
        )
