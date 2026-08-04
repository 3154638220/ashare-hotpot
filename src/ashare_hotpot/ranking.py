from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .models import NewsEvent, RankingRow


class RankingService:
    def build_rankings(self, events: list[NewsEvent]) -> list[RankingRow]:
        event_ids: dict[str, list[str]] = defaultdict(list)
        names: dict[str, str] = {}
        latest: dict[str, datetime] = {}
        raw_counts: dict[str, int] = defaultdict(int)
        industry_tags: dict[str, set[str]] = defaultdict(set)

        for event in events:
            event_industry_tags = {
                tag.strip()
                for article in event.articles
                for tag in article.industry_tags
                if tag.strip()
            }
            for stock in event.stocks:
                event_ids[stock.code].append(event.event_id)
                if stock.code not in names or names[stock.code] == stock.code:
                    names[stock.code] = stock.name
                previous = latest.get(stock.code)
                if previous is None or event.published_at > previous:
                    latest[stock.code] = event.published_at
                industry_tags[stock.code].update(event_industry_tags)
            for article in event.articles:
                for stock in article.stocks:
                    raw_counts[stock.code] += 1

        ordered_codes = sorted(
            event_ids,
            key=lambda code: (-len(event_ids[code]), -latest[code].timestamp(), code),
        )
        return [
            RankingRow(
                rank=index,
                code=code,
                name=names.get(code, code),
                event_count=len(event_ids[code]),
                raw_article_count=raw_counts[code],
                latest_mention=latest[code],
                event_ids=tuple(event_ids[code]),
                industry_tags=tuple(sorted(industry_tags[code])),
            )
            for index, code in enumerate(ordered_codes, start=1)
        ]
