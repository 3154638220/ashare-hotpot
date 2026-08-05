from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timedelta

from .config import AppSettings, SHANGHAI_TZ
from .dedupe import Deduplicator
from .filtering import filter_brokerage_research_mentions, template_filter_reason
from .industries import fetch_stock_industries
from .models import (
    ArticleCandidate,
    OfficialPopularitySnapshot,
    ParsedArticle,
    RankingRow,
    Snapshot,
    SourceCoverage,
)
from .parsing import parse_article_detail
from .popularity import fetch_official_popularity
from .ranking import RankingService
from .sources import NewsSource, PoliteHttpClient, RefreshCancelled
from .storage import Storage


ProgressCallback = Callable[[int, str], None]
logger = logging.getLogger(__name__)


class RefreshService:
    def __init__(self, settings: AppSettings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self.deduplicator = Deduplicator()
        self.ranking_service = RankingService()

    @staticmethod
    def _progress(callback: ProgressCallback | None, value: int, message: str) -> None:
        if callback:
            callback(max(0, min(100, value)), message)

    def _filtered_article(self, candidate: ArticleCandidate, reason: str) -> ParsedArticle:
        return ParsedArticle(
            seq=candidate.seq,
            url=candidate.url,
            title=candidate.title,
            summary=candidate.summary,
            published_at=candidate.published_at,
            channel_key=candidate.channel_key,
            channel_name=candidate.channel_name,
            source_name="同花顺财经",
            filtered_reason=reason,
        )

    def _error_article(self, candidate: ArticleCandidate, message: str) -> ParsedArticle:
        return ParsedArticle(
            seq=candidate.seq,
            url=candidate.url,
            title=candidate.title,
            summary=candidate.summary,
            published_at=candidate.published_at,
            channel_key=candidate.channel_key,
            channel_name=candidate.channel_name,
            source_name="同花顺财经",
            fetch_error=message[:1000],
        )

    def _fetch_and_parse(self, candidate: ArticleCandidate, client: PoliteHttpClient) -> ParsedArticle:
        html = client.get_text(candidate.url)
        return parse_article_detail(candidate, html)

    def _attach_stock_industries(
        self,
        rankings: list[RankingRow],
        *,
        now: datetime,
        cancel: threading.Event,
    ) -> list[RankingRow]:
        codes = {row.code for row in rankings}
        industries = self.storage.get_stock_industries(codes)
        missing_codes = codes.difference(industries)
        if missing_codes:
            with PoliteHttpClient(self.settings, cancel) as client:
                fetched_industries = fetch_stock_industries(client, missing_codes)
            resolved_industries = {
                code: industry
                for code, industry in fetched_industries.items()
                if code in missing_codes
            }
            self.storage.upsert_stock_industries(resolved_industries, now)
            industries.update(resolved_industries)

        return [
            replace(
                row,
                industry_tags=(industries[row.code],),
            )
            if row.code in industries
            else row
            for row in rankings
        ]

    def _refresh_popularity(
        self,
        *,
        now: datetime,
        cancel: threading.Event,
        progress: ProgressCallback | None,
    ) -> OfficialPopularitySnapshot:
        """Read the official popularity board at low frequency.

        Within the configured cache window a successful read is reused; any
        whole-board failure (identity check, empty data, structure change) falls
        back to the last successful boards and marks them stale.
        """

        cached = self.storage.get_popularity_state()
        cache_minutes = max(1, self.settings.popularity_cache_minutes)
        if (
            cached is not None
            and cached.available
            and cached.success_at is not None
            and (now - cached.success_at) < timedelta(minutes=cache_minutes)
        ):
            self._progress(progress, 97, f"东方财富人气榜：{cache_minutes} 分钟内已读取，复用缓存")
            return cached
        try:
            with PoliteHttpClient(self.settings, cancel) as client:
                popularity, surging = fetch_official_popularity(client)
            snapshot = OfficialPopularitySnapshot(
                available=True,
                is_stale=False,
                success_at=now,
                error=None,
                popularity=popularity,
                surging=surging,
            )
            self.storage.set_popularity_state(snapshot, now)
            return snapshot
        except RefreshCancelled:
            raise
        except Exception as exc:
            logger.warning("official popularity refresh failed: %s", exc)
            if cached is not None and cached.available:
                cached.is_stale = True
                cached.error = str(exc)[:1000]
                return cached
            return OfficialPopularitySnapshot(
                available=False,
                is_stale=False,
                success_at=None,
                error=str(exc)[:1000],
            )

    def refresh(
        self,
        *,
        now: datetime | None = None,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Snapshot:
        cancel = cancel_event or threading.Event()
        window_end = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
        window_start = window_end - timedelta(hours=self.settings.window_hours)
        run_id = self.storage.create_run(window_end)
        stats = {
            "list_items": 0,
            "unique_urls": 0,
            "filtered": 0,
            "cached": 0,
            "fetched": 0,
            "failed": 0,
            "unmapped": 0,
            "ranked_articles": 0,
            "events": 0,
            "industry_mapped": 0,
        }
        try:
            self._progress(progress, 1, "准备刷新…")
            candidates: dict[str, ArticleCandidate] = {}
            coverages: list[SourceCoverage] = []
            with PoliteHttpClient(self.settings, cancel) as client:
                for source_index, source_config in enumerate(self.settings.sources):
                    if cancel.is_set():
                        raise RefreshCancelled("刷新已取消")
                    source = NewsSource(source_config, client)
                    source_items: list[ArticleCandidate] = []
                    pages_scanned = 0
                    reached_cutoff = False
                    source_error: str | None = None
                    for page in range(1, self.settings.max_pages_per_source + 1):
                        base_progress = 2 + int(28 * (source_index / max(1, len(self.settings.sources))))
                        self._progress(progress, base_progress, f"{source_config.name}：读取第 {page} 页…")
                        try:
                            result = source.fetch_page(page, window_end)
                        except RefreshCancelled:
                            raise
                        except Exception as exc:  # keep other sources usable
                            source_error = str(exc)
                            logger.warning("source page failed: %s page %s: %s", source_config.key, page, exc)
                            break
                        pages_scanned += 1
                        if not result.items:
                            if page == 1:
                                source_error = "列表页未解析到任何新闻"
                            else:
                                reached_cutoff = True
                            break
                        source_items.extend(result.items)
                        oldest_on_page = min(item.published_at for item in result.items)
                        if oldest_on_page <= window_start:
                            reached_cutoff = True
                            break

                    in_window = [
                        item for item in source_items if window_start <= item.published_at <= window_end
                    ]
                    stats["list_items"] += len(in_window)
                    for candidate in in_window:
                        candidates.setdefault(candidate.seq or candidate.url, candidate)
                    all_times = [item.published_at for item in source_items]
                    coverages.append(
                        SourceCoverage(
                            source_key=source_config.key,
                            source_name=source_config.name,
                            pages_scanned=pages_scanned,
                            article_count=len(in_window),
                            oldest_seen=min(all_times) if all_times else None,
                            newest_seen=max(all_times) if all_times else None,
                            reached_cutoff=reached_cutoff,
                            error=source_error,
                        )
                    )

                stats["unique_urls"] = len(candidates)
                to_fetch: list[ArticleCandidate] = []
                for candidate in candidates.values():
                    if cancel.is_set():
                        raise RefreshCancelled("刷新已取消")
                    reason = template_filter_reason(candidate.title, candidate.summary)
                    if reason:
                        article = self._filtered_article(candidate, reason)
                        self.storage.upsert_article(article, window_end)
                        stats["filtered"] += 1
                        continue
                    cached = self.storage.get_cached_article(candidate.url)
                    if cached is not None:
                        stats["cached"] += 1
                        continue
                    to_fetch.append(candidate)

                total_to_fetch = len(to_fetch)
                completed_count = 0
                future_map: dict[Future[ParsedArticle], ArticleCandidate] = {}
                with ThreadPoolExecutor(
                    max_workers=self.settings.detail_workers,
                    thread_name_prefix="article-fetch",
                ) as executor:
                    for candidate in to_fetch:
                        future_map[executor.submit(self._fetch_and_parse, candidate, client)] = candidate
                    for future in as_completed(future_map):
                        if cancel.is_set():
                            for pending in future_map:
                                pending.cancel()
                            raise RefreshCancelled("刷新已取消")
                        candidate = future_map[future]
                        completed_count += 1
                        try:
                            article = future.result()
                            stats["fetched"] += 1
                            if not article.stocks:
                                stats["unmapped"] += 1
                        except RefreshCancelled:
                            raise
                        except Exception as exc:
                            logger.warning("article failed: %s: %s", candidate.url, exc)
                            article = self._error_article(candidate, str(exc))
                            stats["failed"] += 1
                        self.storage.upsert_article(article, window_end)
                        fetch_progress = 32 + int(58 * completed_count / max(1, total_to_fetch))
                        self._progress(
                            progress,
                            fetch_progress,
                            f"解析新闻 {completed_count}/{total_to_fetch}",
                        )

            if cancel.is_set():
                raise RefreshCancelled("刷新已取消")
            self._progress(progress, 92, "正在去重并生成排行榜…")
            stored_articles = self.storage.get_articles_between(window_start, window_end)
            # Cached articles from an earlier version did not have this filter
            # applied. Re-run the title/summary portion here so users benefit on
            # the next refresh without clearing their local cache.
            ranking_articles = [
                replace(
                    article,
                    stocks=filter_brokerage_research_mentions(
                        article.stocks,
                        title=article.title,
                        summary=article.summary,
                    ),
                )
                for article in stored_articles
            ]
            usable_articles = [
                article
                for article in ranking_articles
                if not article.filtered_reason and not article.fetch_error and article.stocks
            ]
            stats["ranked_articles"] = len(usable_articles)
            events = self.deduplicator.group(usable_articles)
            rankings = self.ranking_service.build_rankings(events)
            stats["events"] = len(events)
            if rankings:
                self._progress(progress, 94, "正在补全股票所属行业…")
                try:
                    rankings = self._attach_stock_industries(
                        rankings,
                        now=window_end,
                        cancel=cancel,
                    )
                except RefreshCancelled:
                    raise
                except Exception as exc:
                    logger.warning("stock industry lookup failed: %s", exc)
                stats["industry_mapped"] = sum(bool(row.industry_tags) for row in rankings)
            self._progress(progress, 95, "正在读取东方财富综合人气榜…")
            popularity = self._refresh_popularity(
                now=window_end,
                cancel=cancel,
                progress=progress,
            )
            partial = any(not item.reached_cutoff or bool(item.error) for item in coverages)
            snapshot = Snapshot(
                snapshot_id=None,
                window_start=window_start,
                window_end=window_end,
                created_at=datetime.now(SHANGHAI_TZ),
                partial=partial,
                coverages=coverages,
                rankings=rankings,
                events=events,
                stats=stats,
                popularity=popularity,
            )
            self.storage.save_snapshot(snapshot)
            self.storage.purge_older_than(window_end - timedelta(days=self.settings.retention_days))
            self.storage.finish_run(run_id, "completed", "", datetime.now(SHANGHAI_TZ))
            self._progress(progress, 100, "刷新完成")
            return snapshot
        except RefreshCancelled as exc:
            self.storage.finish_run(run_id, "cancelled", str(exc), datetime.now(SHANGHAI_TZ))
            raise
        except Exception as exc:
            self.storage.finish_run(run_id, "failed", str(exc), datetime.now(SHANGHAI_TZ))
            raise
