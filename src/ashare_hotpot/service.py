from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timedelta

from .config import AppSettings, SHANGHAI_TZ, SourceConfig
from .dedupe import Deduplicator, dedupe_interactions
from .filtering import (
    filter_brokerage_research_mentions,
    interaction_noise_reason,
    template_filter_reason,
)
from .industries import fetch_stock_industries
from .industry_heat import build_industry_heat_snapshot
from .models import (
    ArticleCandidate,
    IndustryHeatSnapshot,
    InteractionCoverage,
    InteractionRankingRow,
    InteractionRecord,
    OfficialPopularitySnapshot,
    ParsedArticle,
    RankingRow,
    Snapshot,
    SourceCoverage,
    SourceDocument,
)
from .parsing import extract_body_text, is_a_share_code, parse_article_detail
from .pdf import sha256_hex
from .popularity import fetch_official_popularity
from .ranking import InteractionRankingService, RankingService
from .research_sync import ResearchSyncResult, ResearchSyncService
from .policy_sources import PolicySyncResult, PolicySyncService
from .signals import ShortTermBoardService, ShortTermRunResult
from .sources import (
    InteractionPageResult,
    IrmSource,
    NewsSource,
    PoliteHttpClient,
    RefreshCancelled,
    SseInteractionSource,
)
from .storage import Storage


ProgressCallback = Callable[[int, str], None]
logger = logging.getLogger(__name__)


def _cache_covers(
    cached_start: datetime | None,
    _cached_end: datetime | None,
    window_start: datetime,
    _window_end: datetime,
) -> bool:
    # The cached stream must extend back to the requested window start; the
    # trailing gap (window end moved forward) is bounded by the cache
    # freshness window and therefore accepted.
    return cached_start is not None and cached_start <= window_start


def _interaction_source(
    config: SourceConfig, client: PoliteHttpClient
) -> IrmSource | SseInteractionSource:
    if config.adapter == "sse":
        return SseInteractionSource(config, client)
    return IrmSource(config, client)


class RefreshService:
    def __init__(self, settings: AppSettings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self.deduplicator = Deduplicator()
        self.ranking_service = RankingService()
        self.interaction_ranking_service = InteractionRankingService()
        self.signal_service = ShortTermBoardService(settings, storage)

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
            source_name=candidate.provider_name,
            provider_key=candidate.provider_key,
            provider_name=candidate.provider_name,
            content_type=candidate.content_type,
            stocks=candidate.stocks,
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
            source_name=candidate.provider_name,
            provider_key=candidate.provider_key,
            provider_name=candidate.provider_name,
            content_type=candidate.content_type,
            stocks=candidate.stocks,
            fetch_error=message[:1000],
        )

    def _candidate_article(self, candidate: ArticleCandidate) -> ParsedArticle:
        """Convert a candidate whose list page already mapped its stocks."""

        return ParsedArticle(
            seq=candidate.seq,
            url=candidate.url,
            title=candidate.title,
            summary=candidate.summary,
            published_at=candidate.published_at,
            channel_key=candidate.channel_key,
            channel_name=candidate.channel_name,
            source_name=candidate.provider_name,
            provider_key=candidate.provider_key,
            provider_name=candidate.provider_name,
            content_type=candidate.content_type,
            stocks=candidate.stocks,
        )

    def _fetch_and_parse(self, candidate: ArticleCandidate, client: PoliteHttpClient) -> ParsedArticle:
        html = client.get_text(candidate.url)
        return parse_article_detail(candidate, html)

    def _fetch_and_parse_signal_feed(
        self, candidate: ArticleCandidate, client: PoliteHttpClient
    ) -> tuple[ParsedArticle, str]:
        """Fetch one signal-feed article together with its plain body text.

        Signal-feed sources (``SourceConfig.signal_feed``) additionally feed
        the short-term signal pipeline, which requires the extracted body
        text (titles alone never carry materiality or counter-evidence).
        """

        html = client.get_text(candidate.url)
        article = parse_article_detail(candidate, html)
        return article, extract_body_text(html)

    def _persist_signal_feed_document(
        self, article: ParsedArticle, body_text: str, *, now: datetime
    ) -> None:
        """Persist one signal-feed article as a news ``SourceDocument``.

        The document enters the existing persistent clustering pipeline
        (``source_documents``) so the short-term boards can score the
        company-level statement with the configured 0.60 media-confidence
        tier.  A persistence failure degrades only this article; the article
        itself was already stored in the legacy ``articles`` table by the
        caller.
        """

        document = SourceDocument(
            document_id=f"ths_news:{article.url}",
            provider_key=article.provider_key,
            provider_name=article.source_name or article.provider_name,
            kind="news",
            source_url=article.url,
            document_url=article.url,
            title=article.title,
            published_at=article.published_at,
            stock_codes=tuple(sorted(stock.code for stock in article.stocks)),
            stock_names={stock.code: stock.name for stock in article.stocks},
            body_text=body_text,
            content_hash=sha256_hex(body_text.encode("utf-8")),
            parse_status="parsed",
            parse_error=None,
        )
        try:
            self.storage.upsert_source_document(document, now)
        except Exception as exc:  # degrade one article, never the refresh
            logger.warning(
                "signal feed document persist failed: %s: %s", article.url, exc
            )

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

    def _resolve_stock_industries(
        self,
        codes: set[str],
        *,
        now: datetime,
        cancel: threading.Event,
    ) -> dict[str, str]:
        """Resolve a stock universe using the existing cache and public API."""

        industries = self.storage.get_stock_industries(codes)
        missing_codes = codes.difference(industries)
        if not missing_codes:
            return industries
        with PoliteHttpClient(self.settings, cancel) as client:
            fetched_industries = fetch_stock_industries(client, missing_codes)
        resolved = {
            code: industry
            for code, industry in fetched_industries.items()
            if code in missing_codes
        }
        if resolved:
            self.storage.upsert_stock_industries(resolved, now)
            industries.update(resolved)
        return industries

    def _build_industry_heat(
        self,
        *,
        popularity: OfficialPopularitySnapshot,
        current_articles: list[ParsedArticle],
        coverages: list[SourceCoverage],
        now: datetime,
        cancel: threading.Event,
        article_failures: int = 0,
    ) -> IndustryHeatSnapshot:
        """Build the current industry board from the 24-hour source window.

        The normal news window is intentionally not reused here.  The source
        coverage row is the single completeness signal for the fixed
        ``industry_research`` source; cached article bodies are still useful
        for the current board, but they never turn a failed source into a
        complete result.
        """

        if not any(config.key == "industry_research" for config in self.settings.sources):
            return IndustryHeatSnapshot(
                snapshot_at=now,
                window_start=now - timedelta(hours=24),
                window_end=now,
                source_status="unavailable",
            )

        window_start = now - timedelta(hours=24)
        cached_articles = [
            article
            for article in self.storage.get_articles_between(window_start, now)
            if article.channel_key == "industry_research"
            and not article.filtered_reason
            and not article.fetch_error
        ]
        # Current parsed articles precede cached rows so explicit body tags are
        # retained for this refresh even before the additive article-cache
        # migration stores them for future refreshes.
        articles: list[ParsedArticle] = []
        seen_urls: set[str] = set()
        for article in (*current_articles, *cached_articles):
            key = article.url or article.seq
            if key in seen_urls:
                continue
            seen_urls.add(key)
            articles.append(article)

        coverage = next(
            (item for item in coverages if item.source_key == "industry_research"),
            None,
        )
        source_complete = bool(
            coverage
            and coverage.reached_cutoff
            and not coverage.error
            and article_failures == 0
        )
        source_error = coverage.error if coverage else "行业研究来源未生成覆盖状态"
        if article_failures and not source_error:
            source_error = f"行业研究文章解析失败 {article_failures} 条"
        codes = {row.code for row in popularity.popularity}
        codes.update(stock.code for article in articles for stock in article.stocks)
        try:
            industries = self._resolve_stock_industries(
                codes,
                now=now,
                cancel=cancel,
            )
        except RefreshCancelled:
            raise
        except Exception as exc:
            logger.warning("industry heat stock mapping failed: %s", exc)
            industries = self.storage.get_stock_industries(codes)
            source_error = source_error or f"行业映射失败：{exc}"
            source_complete = False

        result = build_industry_heat_snapshot(
            popularity.popularity,
            articles,
            industries,
            window_end=now,
            source_complete=source_complete,
            source_error=source_error,
            popularity_available=popularity.available,
            popularity_stale=popularity.is_stale,
        )
        result.articles = sorted(articles, key=lambda item: item.published_at, reverse=True)
        return result

    def _publish_industry_daily_snapshot(
        self,
        snapshot: IndustryHeatSnapshot,
        *,
        popularity: OfficialPopularitySnapshot,
        now: datetime,
    ) -> bool:
        """Publish the first complete industry point after 18:00 Shanghai time.

        The current board is allowed to be partial or based on stale/cache
        data.  Historical points are stricter: the popularity board must have
        been fetched during this refresh, and the fixed 24-hour research
        source plus EM2016 mapping must be complete.  Storage enforces the
        second half of the rule (same-day immutability).
        """

        local_now = now.astimezone(SHANGHAI_TZ)
        if local_now.hour < 18:
            return False
        if popularity.from_cache or popularity.is_stale or not popularity.available:
            return False
        if not snapshot.is_complete:
            return False
        return self.storage.save_industry_daily_snapshot(
            snapshot,
            local_now.date(),
        )

    def _attach_interaction_industries(
        self,
        rankings: list[InteractionRankingRow],
        *,
        now: datetime,
        cancel: threading.Event,
    ) -> list[InteractionRankingRow]:
        codes = {row.code for row in rankings if not row.industry_tags}
        if not codes:
            return rankings
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
                industry_tags=tuple(dict.fromkeys((*row.industry_tags, industries[row.code]))),
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
            cached.from_cache = True
            return cached
        try:
            with PoliteHttpClient(self.settings, cancel) as client:
                popularity, surging = fetch_official_popularity(client)
            snapshot = OfficialPopularitySnapshot(
                available=True,
                is_stale=False,
                success_at=now,
                error=None,
                from_cache=False,
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
                cached.from_cache = False
                return cached
            return OfficialPopularitySnapshot(
                available=False,
                is_stale=False,
                success_at=None,
                error=str(exc)[:1000],
                from_cache=False,
            )

    def _collect_interaction_source(
        self,
        source_config: SourceConfig,
        client: PoliteHttpClient,
        *,
        now: datetime,
        window_start: datetime,
        window_end: datetime,
        cancel: threading.Event,
        max_pages: int,
        stats: dict[str, int],
    ) -> tuple[list[InteractionRecord], InteractionCoverage]:
        """Collect one official Q&A platform stream.

        The stream is read in memory to the window boundary first, then the
        in-window records are written atomically. A platform failure keeps the
        last cached records and is marked stale.
        """

        cache_minutes = max(1, self.settings.interaction_cache_minutes)
        cached = self.storage.get_source_cache(source_config.key)
        if cached is not None:
            cached_at, cached_cutoff, cached_start, cached_end, cached_items = cached
            if (now - cached_at) < timedelta(minutes=cache_minutes) and _cache_covers(
                cached_start, cached_end, window_start, window_end
            ):
                records = [
                    record
                    for record in cached_items
                    if isinstance(record, InteractionRecord)
                    and record.reply_time is not None
                    and window_start <= record.reply_time <= window_end
                ]
                stats["interaction_sources_cached"] += 1
                coverage = InteractionCoverage(
                    source_key=source_config.key,
                    source_name=source_config.name,
                    pages_scanned=0,
                    record_count=len(records),
                    oldest_seen=min((r.question_time for r in records), default=None),
                    newest_seen=max((r.question_time for r in records), default=None),
                    reached_cutoff=cached_cutoff,
                )
                return records, coverage
        try:
            source = _interaction_source(source_config, client)
            collected: list[InteractionRecord] = []
            pages_scanned = 0
            reached_cutoff = False
            for page in range(1, max_pages + 1):
                if cancel.is_set():
                    raise RefreshCancelled("刷新已取消")
                result: InteractionPageResult = source.fetch_page(page, now)
                pages_scanned += 1
                collected.extend(result.items)
                if page == 1 and not result.items:
                    raise RuntimeError("首屏空数据或登录页")
                if result.exhausted or (
                    result.oldest_feed_time is not None
                    and result.oldest_feed_time < window_start
                ):
                    reached_cutoff = True
                    break
            in_window = [
                record
                for record in collected
                if record.reply_time is not None
                and window_start <= record.reply_time <= window_end
            ]
            if cancel.is_set():
                raise RefreshCancelled("刷新已取消")
            for record in in_window:
                self.storage.upsert_interaction(record, now)
            self.storage.set_source_cache(
                source_config.key,
                records=in_window,
                fetched_at=now,
                reached_cutoff=reached_cutoff,
                window_start=window_start,
                window_end=window_end,
            )
            coverage = InteractionCoverage(
                source_key=source_config.key,
                source_name=source_config.name,
                pages_scanned=pages_scanned,
                record_count=len(in_window),
                oldest_seen=min((r.question_time for r in in_window), default=None),
                newest_seen=max((r.question_time for r in in_window), default=None),
                reached_cutoff=reached_cutoff,
            )
            return in_window, coverage
        except RefreshCancelled:
            raise
        except Exception as exc:
            logger.warning("interaction source failed: %s: %s", source_config.key, exc)
            if cached is not None:
                _, _cutoff, _cached_start, _cached_end, cached_items = cached
                records = [
                    record
                    for record in cached_items
                    if isinstance(record, InteractionRecord)
                    and record.reply_time is not None
                    and window_start <= record.reply_time <= window_end
                ]
                for record in records:
                    self.storage.upsert_interaction(record, now)
                coverage = InteractionCoverage(
                    source_key=source_config.key,
                    source_name=source_config.name,
                    pages_scanned=0,
                    record_count=len(records),
                    oldest_seen=min((r.question_time for r in records), default=None),
                    newest_seen=max((r.question_time for r in records), default=None),
                    reached_cutoff=False,
                    error=str(exc)[:1000],
                )
                return records, coverage
            return [], InteractionCoverage(
                source_key=source_config.key,
                source_name=source_config.name,
                pages_scanned=0,
                record_count=0,
                oldest_seen=None,
                newest_seen=None,
                reached_cutoff=False,
                error=str(exc)[:1000],
            )

    def _build_interaction_rankings(
        self,
        records: list[InteractionRecord],
        *,
        now: datetime,
        cancel: threading.Event,
        stats: dict[str, int],
    ) -> list[InteractionRankingRow]:
        usable: list[InteractionRecord] = []
        for record in records:
            if record.reply_time is None or not record.replied:
                # 口径（v2）：只有已回复的提问才计为有效提问。
                stats["interaction_filtered"] += 1
                continue
            if not is_a_share_code(record.code):
                stats["interaction_filtered"] += 1
                continue
            reason = interaction_noise_reason(record.question)
            if reason is not None:
                stats["interaction_filtered"] += 1
                continue
            usable.append(record)
        stats["interaction_records"] = len(records)
        stats["interaction_usable"] = len(usable)
        unique = dedupe_interactions(usable)
        stats["interaction_unique"] = len(unique)
        rankings = self.interaction_ranking_service.build_rankings(unique)
        stats["interaction_ranked_stocks"] = len(rankings)
        if rankings:
            try:
                rankings = self._attach_interaction_industries(
                    rankings,
                    now=now,
                    cancel=cancel,
                )
            except RefreshCancelled:
                raise
            except Exception as exc:
                logger.warning("interaction industry lookup failed: %s", exc)
        return rankings

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
            "news_sources_cached": 0,
            "interaction_records": 0,
            "interaction_filtered": 0,
            "interaction_usable": 0,
            "interaction_unique": 0,
            "interaction_ranked_stocks": 0,
            "interaction_sources_cached": 0,
            "signal_documents": 0,
            "signal_clusters_created": 0,
            "signal_clusters_merged": 0,
            "signal_clusters_processed": 0,
            "signal_extractions": 0,
            "signal_confirmed": 0,
            "signal_catalyst": 0,
            "signal_rejected": 0,
            "signal_errors": 0,
            "research_calendar_days": 0,
            "research_calendar_fallback": 0,
            "research_calendar_errors": 0,
            "industry_heat_rows": 0,
            "industry_heat_top100_total": 0,
            "industry_heat_top100_mapped": 0,
            "industry_heat_articles": 0,
            "industry_heat_articles_mapped": 0,
            "industry_heat_article_failures": 0,
            "industry_heat_source_complete": 0,
            "industry_heat_daily_published": 0,
        }
        try:
            self._progress(progress, 1, "准备刷新…")
            candidates: dict[str, ArticleCandidate] = {}
            coverages: list[SourceCoverage] = []
            current_industry_articles: list[ParsedArticle] = []
            industry_article_failures = 0
            with PoliteHttpClient(self.settings, cancel) as client:
                for source_index, source_config in enumerate(self.settings.sources):
                    if cancel.is_set():
                        raise RefreshCancelled("刷新已取消")

                    source = NewsSource(source_config, client)
                    source_items: list[ArticleCandidate] = []
                    pages_scanned = 0
                    reached_cutoff = False
                    source_error: str | None = None
                    source_window_start = (
                        window_end - timedelta(hours=24)
                        if source_config.key == "industry_research"
                        else window_start
                    )
                    for page in range(1, self.settings.max_pages_per_source + 1):
                        base_progress = 2 + int(30 * (source_index / max(1, len(self.settings.sources))))
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
                        if oldest_on_page <= source_window_start:
                            reached_cutoff = True
                            break

                    in_window = [
                        item
                        for item in source_items
                        if source_window_start <= item.published_at <= window_end
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
                            provider_key=source_config.provider_key,
                            provider_name=source_config.provider_name,
                        )
                    )

                stats["unique_urls"] = len(candidates)
                to_fetch: list[ArticleCandidate] = []
                for candidate in candidates.values():
                    if cancel.is_set():
                        raise RefreshCancelled("刷新已取消")
                    # 行业研究的 B 指标 counts successfully parsed research
                    # articles.  The ordinary news-board brokerage/template
                    # filter must not erase a valid industry article merely
                    # because its title contains words such as "融资".
                    reason = (
                        None
                        if candidate.channel_key == "industry_research"
                        else template_filter_reason(candidate.title, candidate.summary)
                    )
                    if reason:
                        article = self._filtered_article(candidate, reason)
                        self.storage.upsert_article(article, window_end)
                        stats["filtered"] += 1
                        continue
                    cached = self.storage.get_cached_article(candidate.url)
                    if cached is not None:
                        if candidate.channel_key == "industry_research" and cached.fetch_error:
                            # A failed industry detail is retryable.  Treating
                            # it as a valid cache would otherwise make a
                            # transient failure eligible for a daily point.
                            to_fetch.append(candidate)
                            continue
                        stats["cached"] += 1
                        if candidate.channel_key == "industry_research":
                            current_industry_articles.append(cached)
                        continue
                    to_fetch.append(candidate)

                signal_feed_keys = frozenset(
                    source_config.key
                    for source_config in self.settings.sources
                    if source_config.signal_feed
                )
                total_to_fetch = len(to_fetch)
                completed_count = 0
                future_map: dict[
                    Future[ParsedArticle | tuple[ParsedArticle, str]],
                    ArticleCandidate,
                ] = {}
                with ThreadPoolExecutor(
                    max_workers=self.settings.detail_workers,
                    thread_name_prefix="article-fetch",
                ) as executor:
                    for candidate in to_fetch:
                        if candidate.channel_key in signal_feed_keys:
                            future = executor.submit(
                                self._fetch_and_parse_signal_feed, candidate, client
                            )
                        else:
                            future = executor.submit(
                                self._fetch_and_parse, candidate, client
                            )
                        future_map[future] = candidate
                    for future in as_completed(future_map):
                        if cancel.is_set():
                            for pending in future_map:
                                pending.cancel()
                            raise RefreshCancelled("刷新已取消")
                        candidate = future_map[future]
                        completed_count += 1
                        body_text: str | None = None
                        try:
                            result = future.result()
                            if isinstance(result, tuple):
                                article, body_text = result
                            else:
                                article = result
                            stats["fetched"] += 1
                            if not article.stocks:
                                stats["unmapped"] += 1
                            if candidate.channel_key == "industry_research":
                                current_industry_articles.append(article)
                        except RefreshCancelled:
                            raise
                        except Exception as exc:
                            logger.warning("article failed: %s: %s", candidate.url, exc)
                            article = self._error_article(candidate, str(exc))
                            stats["failed"] += 1
                            if candidate.channel_key == "industry_research":
                                industry_article_failures += 1
                        self.storage.upsert_article(article, window_end)
                        if body_text and article.stocks:
                            self._persist_signal_feed_document(
                                article, body_text, now=window_end
                            )
                        fetch_progress = 34 + int(56 * completed_count / max(1, total_to_fetch))
                        self._progress(
                            progress,
                            fetch_progress,
                            f"解析新闻 {completed_count}/{total_to_fetch}",
                        )

                interaction_records: list[InteractionRecord] = []
                interaction_coverages: list[InteractionCoverage] = []
                for interaction_index, interaction_config in enumerate(self.settings.interaction_sources):
                    if cancel.is_set():
                        raise RefreshCancelled("刷新已取消")
                    self._progress(
                        progress,
                        90 + int(4 * (interaction_index + 1) / max(1, len(self.settings.interaction_sources))),
                        f"{interaction_config.name}：读取全市场问答流…",
                    )
                    records, coverage = self._collect_interaction_source(
                        interaction_config,
                        client,
                        now=window_end,
                        window_start=window_start,
                        window_end=window_end,
                        cancel=cancel,
                        max_pages=self.settings.max_pages_per_source,
                        stats=stats,
                    )
                    interaction_records.extend(records)
                    interaction_coverages.append(coverage)

                # Institution activity collection is retired from the active
                # refresh pipeline.  Keep announcement-compatible research
                # sources available for legacy/short-term evidence, but never
                # pass research_activity sources to the synchronizer.
                active_research_sources = tuple(
                    source
                    for source in self.settings.research_sources
                    if source.kind != "research_activity"
                )
                research_result: ResearchSyncResult | None = None
                if active_research_sources:
                    self._progress(progress, 93, "正在渐进回填研究来源…")
                    try:
                        research_settings = replace(
                            self.settings,
                            research_sources=active_research_sources,
                        )
                        research_result = ResearchSyncService(
                            research_settings, self.storage
                        ).sync_once(
                            now=window_end,
                            cancel=cancel,
                            client=client,
                            max_pages=self.settings.research_max_pages_per_run,
                            max_pdfs=self.settings.research_max_pdfs_per_run,
                            backfill_days=self.settings.backfill_days,
                        )
                    except RefreshCancelled:
                        raise
                    except Exception as exc:
                        logger.warning("research backfill failed: %s", exc)

                policy_result: PolicySyncResult | None = None
                if self.settings.policy_sources:
                    self._progress(progress, 93, "正在同步政策观察来源…")
                    try:
                        policy_result = PolicySyncService(
                            self.settings, self.storage
                        ).sync_once(
                            now=window_end,
                            cancel=cancel,
                            client=client,
                            max_pages_per_source=5,
                        )
                    except RefreshCancelled:
                        raise
                    except Exception as exc:
                        logger.warning("policy sync failed: %s", exc)

            if cancel.is_set():
                raise RefreshCancelled("刷新已取消")
            if research_result is not None:
                stats["research_pages"] = research_result.pages_consumed
                stats["research_pdfs"] = research_result.pdfs_consumed
                stats["research_documents_added"] = research_result.documents_added
                stats["research_documents_skipped"] = research_result.documents_skipped
                stats["research_discoveries_added"] = (
                    research_result.discoveries_added
                )
                stats["research_pdf_failures"] = research_result.pdf_failures
            if policy_result is not None:
                stats["policy_pages"] = policy_result.pages_consumed
                stats["policy_documents_added"] = policy_result.documents_added
                stats["policy_documents_skipped"] = (
                    policy_result.documents_skipped
                )
                stats["policy_failure_sources"] = len(
                    policy_result.failure_sources
                )
                stats["policy_sources_total"] = len(
                    self.settings.policy_sources
                )
            self._progress(progress, 94, "正在生成短期事件信号…")
            signal_result: ShortTermRunResult | None = None
            try:
                signal_result = self.signal_service.run(
                    now=window_end,
                    window_start=window_start,
                    window_end=window_end,
                    publish=False,
                )
            except RefreshCancelled:
                raise
            except Exception as exc:
                logger.warning("short-term signal pipeline failed: %s", exc)
            if signal_result is not None:
                stats["signal_documents"] = signal_result.documents_considered
                stats["signal_clusters_created"] = signal_result.clusters_created
                stats["signal_clusters_merged"] = signal_result.clusters_merged
                stats["signal_clusters_processed"] = signal_result.clusters_processed
                stats["signal_extractions"] = signal_result.extractions_persisted
                stats["signal_confirmed"] = signal_result.signals_confirmed
                stats["signal_catalyst"] = signal_result.signals_catalyst
                stats["signal_rejected"] = signal_result.rejected
                stats["signal_errors"] = len(signal_result.errors)
            self._progress(progress, 94, "正在去重并生成消息排行…")
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
                self._progress(progress, 95, "正在补全股票所属行业…")
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
            self._progress(progress, 96, "正在生成互动排行…")
            interaction_rankings = self._build_interaction_rankings(
                interaction_records,
                now=window_end,
                cancel=cancel,
                stats=stats,
            )
            self._progress(progress, 97, "正在读取东方财富综合人气榜…")
            popularity = self._refresh_popularity(
                now=window_end,
                cancel=cancel,
                progress=progress,
            )
            self._progress(progress, 98, "正在生成行业热度…")
            industry_heat = self._build_industry_heat(
                popularity=popularity,
                current_articles=current_industry_articles,
                coverages=coverages,
                article_failures=industry_article_failures,
                now=window_end,
                cancel=cancel,
            )
            stats["industry_heat_rows"] = len(industry_heat.rows)
            stats["industry_heat_top100_total"] = industry_heat.top100_total
            stats["industry_heat_top100_mapped"] = industry_heat.top100_mapped
            stats["industry_heat_articles"] = industry_heat.research_article_total
            stats["industry_heat_articles_mapped"] = industry_heat.research_article_mapped
            stats["industry_heat_article_failures"] = industry_article_failures
            stats["industry_heat_source_complete"] = int(
                industry_heat.source_status == "complete"
            )
            partial = any(
                not item.reached_cutoff or bool(item.error) for item in coverages
            ) or any(
                not item.reached_cutoff or bool(item.error)
                for item in interaction_coverages
            )
            policy_coverages: list[SourceCoverage] = []
            if policy_result is not None:
                policy_names = {
                    config.key: config.name
                    for config in self.settings.policy_sources
                }
                for cov in policy_result.coverages:
                    policy_coverages.append(
                        SourceCoverage(
                            source_key=cov.source_key,
                            source_name=policy_names.get(
                                cov.source_key, cov.source_key
                            ),
                            pages_scanned=0,
                            article_count=0,
                            oldest_seen=None,
                            newest_seen=None,
                            reached_cutoff=cov.reached_cutoff,
                            error=cov.error,
                            provider_key="policy",
                            provider_name="政策",
                        )
                    )
                partial = partial or any(
                    not cov.reached_cutoff or bool(cov.error)
                    for cov in policy_coverages
                )
            try:
                stats["industry_heat_daily_published"] = int(
                    self._publish_industry_daily_snapshot(
                        industry_heat,
                        popularity=popularity,
                        now=window_end,
                    )
                )
            except Exception as exc:  # history must not hide a usable current board
                logger.warning("industry daily snapshot publish failed: %s", exc)
                stats["industry_heat_daily_published"] = 0
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
                interactions=interaction_records,
                interaction_rankings=interaction_rankings,
                interaction_coverages=interaction_coverages,
                policy_coverages=policy_coverages,
                industry_heat=industry_heat,
            )
            self.storage.save_snapshot(
                snapshot,
                event_signals=(
                    signal_result.signals
                    if signal_result is not None and signal_result.completed
                    else None
                ),
                institution_metric_batch_at=None,
            )
            self.storage.purge_older_than(window_end - timedelta(days=self.settings.retention_days))
            self.storage.purge_research_retention(window_end)
            self.storage.purge_coverage_retention(window_end)
            self.storage.finish_run(run_id, "completed", "", datetime.now(SHANGHAI_TZ))
            self._progress(progress, 100, "刷新完成")
            return snapshot
        except RefreshCancelled as exc:
            self.storage.finish_run(run_id, "cancelled", str(exc), datetime.now(SHANGHAI_TZ))
            raise
        except Exception as exc:
            self.storage.finish_run(run_id, "failed", str(exc), datetime.now(SHANGHAI_TZ))
            raise
