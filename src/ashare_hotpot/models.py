from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


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
    filtered_reason: str | None = None
    fetch_error: str | None = None

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
            "stocks": [stock.to_dict() for stock in self.stocks],
            "industry_tags": list(self.industry_tags),
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
            stocks=tuple(StockMention.from_dict(item) for item in data.get("stocks", [])),
            industry_tags=tuple(str(item) for item in data.get("industry_tags", []) if str(item).strip()),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "code": self.code,
            "name": self.name,
            "change": self.change,
            "current_price": self.current_price,
            "change_percent": self.change_percent,
            "url": self.url,
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
    popularity: list[PopularityRankRow] = field(default_factory=list)
    surging: list[PopularityRankRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "is_stale": self.is_stale,
            "success_at": self.success_at.isoformat() if self.success_at else None,
            "error": self.error,
            "popularity": [row.to_dict() for row in self.popularity],
            "surging": [row.to_dict() for row in self.surging],
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
            popularity=[PopularityRankRow.from_dict(item) for item in data.get("popularity", [])],
            surging=[PopularityRankRow.from_dict(item) for item in data.get("surging", [])],
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        return cls(
            snapshot_id=data.get("snapshot_id"),
            window_start=datetime.fromisoformat(data["window_start"]),
            window_end=datetime.fromisoformat(data["window_end"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            partial=bool(data.get("partial", False)),
            coverages=[SourceCoverage.from_dict(item) for item in data.get("coverages", [])],
            rankings=[RankingRow.from_dict(item) for item in data.get("rankings", [])],
            events=[NewsEvent.from_dict(item) for item in data.get("events", [])],
            stats={str(key): int(value) for key, value in data.get("stats", {}).items()},
            # Legacy snapshots may still carry a "guba" key from the old
            # self-computed stock-forum ranking; it is intentionally ignored so
            # the historical news snapshot is kept while the old guba result is
            # dropped.
            popularity=OfficialPopularitySnapshot.from_dict(data.get("popularity")),
        )
