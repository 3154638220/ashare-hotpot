from __future__ import annotations

import hashlib
import re
from datetime import timedelta

try:
    from rapidfuzz.fuzz import ratio as similarity_ratio
except ImportError:  # pragma: no cover - development fallback
    from difflib import SequenceMatcher

    def similarity_ratio(left: str, right: str) -> float:
        return SequenceMatcher(None, left, right).ratio() * 100

from .models import NewsEvent, ParsedArticle, StockMention


LEADING_SOURCE_PATTERN = re.compile(r"^(?:[【\[][^】\]]{1,30}[】\]]|快讯[：:]?|消息[：:]?)")
TITLE_NOISE_PATTERN = re.compile(r"[\s\u3000，,。.!！?？:：;；'\"“”‘’（）()《》<>【】\[\]、·—_-]+")


def normalize_title(title: str) -> str:
    value = LEADING_SOURCE_PATTERN.sub("", title.strip())
    return TITLE_NOISE_PATTERN.sub("", value).lower()


def _stock_map(articles: list[ParsedArticle]) -> dict[str, StockMention]:
    result: dict[str, StockMention] = {}
    for article in articles:
        for stock in article.stocks:
            current = result.get(stock.code)
            if current is None or current.name == current.code:
                result[stock.code] = stock
    return result


def _event_id(articles: list[ParsedArticle]) -> str:
    keys = sorted(article.seq or article.url for article in articles)
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:16]


class Deduplicator:
    def __init__(self, similarity_threshold: float = 90.0, max_time_gap_hours: int = 6) -> None:
        self.similarity_threshold = similarity_threshold
        self.max_time_gap = timedelta(hours=max_time_gap_hours)

    def _matches(self, article: ParsedArticle, event: NewsEvent) -> bool:
        if abs(article.published_at - event.published_at) > self.max_time_gap:
            return False
        article_codes = {stock.code for stock in article.stocks}
        event_codes = {stock.code for stock in event.stocks}
        if not article_codes.intersection(event_codes):
            return False
        left = normalize_title(article.title)
        right = normalize_title(event.title)
        if not left or not right:
            return False
        return similarity_ratio(left, right) >= self.similarity_threshold

    def group(self, articles: list[ParsedArticle]) -> list[NewsEvent]:
        unique: dict[str, ParsedArticle] = {}
        for article in articles:
            if article.filtered_reason or article.fetch_error or not article.stocks:
                continue
            key = article.seq or article.url
            unique.setdefault(key, article)

        events: list[NewsEvent] = []
        for article in sorted(unique.values(), key=lambda item: item.published_at, reverse=True):
            matched: NewsEvent | None = None
            for event in events:
                if self._matches(article, event):
                    matched = event
                    break
            if matched is None:
                events.append(
                    NewsEvent(
                        event_id="",
                        title=article.title,
                        published_at=article.published_at,
                        stocks=article.stocks,
                        articles=[article],
                    )
                )
                continue
            matched.articles.append(article)
            if article.published_at > matched.published_at:
                matched.published_at = article.published_at
                matched.title = article.title
            matched.stocks = tuple(_stock_map(matched.articles).values())

        for event in events:
            event.articles.sort(key=lambda item: item.published_at, reverse=True)
            event.stocks = tuple(sorted(_stock_map(event.articles).values(), key=lambda item: item.code))
            event.event_id = _event_id(event.articles)
        events.sort(key=lambda item: item.published_at, reverse=True)
        return events

