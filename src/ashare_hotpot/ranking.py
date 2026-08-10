from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .models import InteractionRankingRow, InteractionRecord, NewsEvent, RankingRow


class RankingService:
    def build_rankings(self, events: list[NewsEvent]) -> list[RankingRow]:
        sources_by_event = {
            event.event_id: tuple(
                sorted(
                    {
                        article.provider_name or article.channel_name
                        for article in event.articles
                        if (article.provider_name or article.channel_name)
                    }
                )
            )
            for event in events
        }
        content_types_by_event = {
            event.event_id: tuple(
                sorted(
                    {
                        article.content_type or "新闻"
                        for article in event.articles
                        if (article.content_type or "新闻")
                    }
                )
            )
            for event in events
        }
        event_ids: dict[str, list[str]] = defaultdict(list)
        names: dict[str, str] = {}
        latest: dict[str, datetime] = {}
        raw_counts: dict[str, int] = defaultdict(int)
        industry_tags: dict[str, set[str]] = defaultdict(set)
        channels: dict[str, set[str]] = defaultdict(set)

        for event in events:
            event_industry_tags = {
                tag.strip()
                for article in event.articles
                for tag in article.industry_tags
                if tag.strip()
            }
            event_channels = {
                channel.strip()
                for article in event.articles
                if (channel := article.channel_name.strip())
            }
            for stock in event.stocks:
                event_ids[stock.code].append(event.event_id)
                if stock.code not in names or names[stock.code] == stock.code:
                    names[stock.code] = stock.name
                previous = latest.get(stock.code)
                if previous is None or event.published_at > previous:
                    latest[stock.code] = event.published_at
                industry_tags[stock.code].update(event_industry_tags)
                channels[stock.code].update(event_channels)
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
                channels=tuple(sorted(channels[code])),
                sources=tuple(
                    sorted(
                        {
                            source
                            for event_id in event_ids[code]
                            for source in sources_by_event.get(event_id, ())
                        }
                    )
                ),
                content_types=tuple(
                    sorted(
                        {
                            content_type
                            for event_id in event_ids[code]
                            for content_type in content_types_by_event.get(event_id, ())
                        }
                    )
                ),
            )
            for index, code in enumerate(ordered_codes, start=1)
        ]


class InteractionRankingService:
    """Build the official Q&A proxy ranking.

    口径（v2）：只有已回复的提问才计为有效提问，时间基准为回复时间。
    Sorting is deterministic: question count desc, latest reply time desc,
    then stock code asc.
    """

    def build_rankings(self, records: list[InteractionRecord]) -> list[InteractionRankingRow]:
        by_code: dict[str, dict[str, object]] = {}
        for record in records:
            entry = by_code.setdefault(
                record.code,
                {
                    "name": record.stock_name,
                    "record_ids": [],
                    "latest": record.reply_time or record.question_time,
                    "platforms": set(),
                    "industries": set(),
                },
            )
            entry["record_ids"].append(record.record_id)
            record_time = record.reply_time or record.question_time
            if record_time > entry["latest"]:
                entry["latest"] = record_time
            entry["platforms"].add(record.platform_name)
            entry["industries"].update(record.industry_tags)

        ordered_codes = sorted(
            by_code,
            key=lambda code: (
                -len(by_code[code]["record_ids"]),
                -by_code[code]["latest"].timestamp(),
                code,
            ),
        )
        return [
            InteractionRankingRow(
                rank=index,
                code=code,
                name=str(by_code[code]["name"]),
                question_count=len(by_code[code]["record_ids"]),
                replied_count=len(by_code[code]["record_ids"]),
                latest_reply=by_code[code]["latest"],
                record_ids=tuple(sorted(by_code[code]["record_ids"])),
                industry_tags=tuple(sorted(by_code[code]["industries"])),
                platforms=tuple(sorted(by_code[code]["platforms"])),
            )
            for index, code in enumerate(ordered_codes, start=1)
        ]
