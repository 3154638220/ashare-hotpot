from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from .config import AppSettings, SourceConfig
from .coverage import (
    COVERAGE_STATUS_REALTIME_PROVISIONAL,
    OCR_STATUS_NOT_APPLICABLE,
    summarize_document_ids,
)
from .discovery import (
    HIGH_PRIORITY_DISCOVERY_TYPES,
    QUEUE_STATUS_AWAITING_REVIEW,
    QUEUE_STATUS_EMPTY_TEXT,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_PENDING_ATTACHMENT,
    classify_discovery,
)
from .extraction import event_type_hint
from .models import (
    CoverageState,
    DiscoveryCandidate,
    FailureInterval,
    ResearchCandidate,
    SourceDocument,
    SourceManifest,
    SyncCursor,
)
from .pdf import (
    SUPPORTED_ATTACHMENT_TYPES,
    cleanup_stale_pdf_temp,
    fetch_and_extract_attachment,
    pdf_parse_status,
    sha256_hex,
)
from .sources import (
    BseAnnouncementSource,
    BsePerformanceSource,
    CninfoSource,
    IrmIrcsSource,
    PoliteHttpClient,
    RefreshCancelled,
    ResearchPageResult,
    SseAnnouncementSource,
    SsePublishSource,
    research_source,
)
from .storage import Storage


logger = logging.getLogger(__name__)

def _split_budget(total: int, count: int) -> tuple[int, ...]:
    """Split a run budget across sources so one source cannot starve others.

    The remainder of an uneven division goes to the first sources; shares are
    non-increasing so callers can stop at the first zero share.
    """

    if count <= 0:
        return ()
    base, extra = divmod(max(total, 0), count)
    return tuple(base + (1 if index < extra else 0) for index in range(count))


@dataclass(frozen=True, slots=True)
class ResearchSyncResult:
    """Outcome of one progressive research backfill run."""

    pages_consumed: int
    pdfs_consumed: int
    documents_added: int
    documents_skipped: int
    discoveries_added: int
    pdf_failures: int
    budget_exhausted: bool
    coverages: tuple[CoverageState, ...]


@dataclass(frozen=True, slots=True)
class _SourceSyncProgress:
    pages: int
    pdfs: int
    added: int
    skipped: int
    discoveries: int
    pdf_failures: int
    budget_exhausted: bool
    coverage: CoverageState


class ResearchSyncService:
    """Progressive 200-natural-day backfill for research sources.

    Each run consumes at most ``research_max_pages_per_run`` list pages and
    ``research_max_pdfs_per_run`` new attachment downloads.  Both budgets are
    split evenly across the configured sources, so the full-market
    announcement stream can never starve the 调研/投资者关系 sources.  One
    complete list page and its advanced cursor are committed in a single
    transaction, so a cancelled or failed run keeps every previously
    committed page and resumes from the stored cursor without re-downloading
    finished documents.

    Current-window data has priority: callers invoke this after the regular
    refresh work, and the small fixed budget never starves the live boards.
    """

    def __init__(self, settings: AppSettings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage

    def sync_once(
        self,
        *,
        now: datetime,
        cancel: threading.Event,
        client: PoliteHttpClient | None = None,
        max_pages: int | None = None,
        max_pdfs: int | None = None,
        backfill_days: int | None = None,
    ) -> ResearchSyncResult:
        max_pages = max_pages or self.settings.research_max_pages_per_run
        max_pdfs = max_pdfs or self.settings.research_max_pdfs_per_run
        backfill_days = backfill_days or self.settings.backfill_days
        target_start = now.date() - timedelta(days=backfill_days)
        cleanup_stale_pdf_temp(self.settings.pdf_temp_dir, now)

        own_client = client is None
        if own_client:
            client = PoliteHttpClient(self.settings, cancel)
        pages_consumed = 0
        pdfs_consumed = 0
        documents_added = 0
        documents_skipped = 0
        discoveries_added = 0
        pdf_failures = 0
        coverages: list[CoverageState] = []
        sources = tuple(self.settings.research_sources)
        page_shares = _split_budget(max_pages, len(sources))
        pdf_shares = _split_budget(max_pdfs, len(sources))
        budget_exhausted = False
        try:
            for config, pages_budget, pdfs_budget in zip(
                sources, page_shares, pdf_shares
            ):
                if pages_budget <= 0:
                    break
                if cancel.is_set():
                    raise RefreshCancelled("刷新已取消")
                progress = self._sync_source(
                    config,
                    client,
                    now=now,
                    cancel=cancel,
                    target_start=target_start,
                    pages_budget=pages_budget,
                    pdfs_budget=pdfs_budget,
                )
                pages_consumed += progress.pages
                pdfs_consumed += progress.pdfs
                documents_added += progress.added
                documents_skipped += progress.skipped
                discoveries_added += progress.discoveries
                pdf_failures += progress.pdf_failures
                budget_exhausted = budget_exhausted or progress.budget_exhausted
                coverages.append(progress.coverage)
        finally:
            if own_client:
                client.close()
        budget_exhausted = budget_exhausted or (
            pages_consumed >= max_pages
            or (max_pdfs > 0 and pdfs_consumed >= max_pdfs)
        )
        return ResearchSyncResult(
            pages_consumed=pages_consumed,
            pdfs_consumed=pdfs_consumed,
            documents_added=documents_added,
            documents_skipped=documents_skipped,
            discoveries_added=discoveries_added,
            pdf_failures=pdf_failures,
            budget_exhausted=budget_exhausted,
            coverages=tuple(coverages),
        )

    def _sync_source(
        self,
        config: SourceConfig,
        client: PoliteHttpClient,
        *,
        now: datetime,
        cancel: threading.Event,
        target_start: date,
        pages_budget: int,
        pdfs_budget: int,
    ) -> _SourceSyncProgress:
        sync_kind = config.kind
        cursor = self.storage.get_sync_state(config.key, sync_kind)
        # ``page`` is the historical backfill cursor; ``fresh_page`` is the
        # newest-first re-scan cursor (1 = start from the top of the stream).
        page = 1
        fresh_page = 1
        covered_start: date | None = None
        covered_end: date | None = None
        if cursor is not None and isinstance(cursor.cursor, dict):
            page = int(cursor.cursor.get("page") or 1)
            fresh_page = int(cursor.cursor.get("fresh_page") or 1)
            covered_start = cursor.covered_start
            raw_covered_end = cursor.cursor.get("covered_end")
            if raw_covered_end:
                covered_end = date.fromisoformat(str(raw_covered_end))

        source = research_source(config, client)
        pages = 0
        pdfs = 0
        added = 0
        skipped = 0
        discoveries = 0
        pdf_failures = 0
        reached_cutoff = False
        error: str | None = None

        def process_page(
            result: ResearchPageResult,
        ) -> tuple[
            list[SourceDocument],
            list[DiscoveryCandidate],
            datetime | None,
            datetime | None,
            int,
            int,
        ]:
            """Turn one list page into metadata documents + discovery rows.

            Every public list item is persisted as a discovery candidate
            (plan.md 里程碑 7); attachment downloads never happen here.  The
            recoverable attachment queue owns them, so title gating, per-page
            caps or budget exhaustion can no longer silently drop documents.
            Returns ``(documents, candidates, oldest, newest, page_added,
            page_skipped)``.
            """

            documents: list[SourceDocument] = []
            candidate_rows: list[DiscoveryCandidate] = []
            queued_statuses = self.storage.get_discovery_queue_statuses(
                item.document_id for item in result.items
            )
            oldest: datetime | None = None
            newest: datetime | None = None
            page_added = 0
            page_skipped = 0
            for candidate in result.items:
                if cancel.is_set():
                    raise RefreshCancelled("刷新已取消")
                published = candidate.published_at
                if oldest is None or published < oldest:
                    oldest = published
                if newest is None or published > newest:
                    newest = published
                existing = self.storage.get_source_document(candidate.document_id)
                supported_attachment = bool(
                    candidate.document_url
                    and candidate.attachment_type
                    and candidate.attachment_type.upper()
                    in SUPPORTED_ATTACHMENT_TYPES
                )
                already_queued = (
                    queued_statuses.get(candidate.document_id)
                    == QUEUE_STATUS_PENDING_ATTACHMENT
                )
                body_final = (
                    existing is not None
                    and existing.parse_status in ("parsed", "empty_text", "failed")
                )
                nothing_to_do = body_final or (
                    existing is not None
                    and existing.parse_status == "metadata_only"
                    and (not supported_attachment or already_queued)
                )
                if nothing_to_do:
                    if candidate.stock_names and not existing.stock_names:
                        self.storage.upsert_source_document_stock_names(
                            candidate.document_id, candidate.stock_names
                        )
                    page_skipped += 1
                else:
                    page_added += 1
                document = existing or self._candidate_to_document(candidate)
                if candidate.stock_names and not document.stock_names:
                    document = replace(document, stock_names=candidate.stock_names)
                documents.append(document)
                candidate_rows.append(
                    self._candidate_to_discovery(candidate, document, config, now)
                )
            return (
                documents,
                candidate_rows,
                oldest,
                newest,
                page_added,
                page_skipped,
            )

        def track_dates(oldest: datetime | None, newest: datetime | None) -> None:
            nonlocal covered_start, covered_end, reached_cutoff
            if oldest is None:
                return
            page_start = oldest.date()
            covered_start = (
                min(covered_start, page_start)
                if covered_start is not None
                else page_start
            )
            if newest is not None:
                page_end = newest.date()
                covered_end = (
                    max(covered_end, page_end)
                    if covered_end is not None
                    else page_end
                )
            if oldest.date() <= target_start:
                reached_cutoff = True

        def cursor_payload(next_page: int, next_fresh: int) -> dict[str, object]:
            """Cursor JSON; ``fresh_page`` is omitted when the re-scan is done."""

            data: dict[str, object] = {"page": next_page}
            if next_fresh != 1:
                data["fresh_page"] = next_fresh
            if covered_end is not None:
                data["covered_end"] = covered_end.isoformat()
            return data

        def commit_batch(
            documents: list[SourceDocument],
            candidates: list[DiscoveryCandidate],
            next_page: int,
            next_fresh: int,
            fail_page: int,
            fail_fresh: int,
        ) -> bool:
            """Persist one page atomically; returns False on commit failure."""

            nonlocal error
            try:
                self.storage.save_research_batch(
                    documents,
                    candidates,
                    SyncCursor(
                        source_key=config.key,
                        sync_kind=sync_kind,
                        cursor=cursor_payload(next_page, next_fresh),
                        target_start=target_start,
                        covered_start=covered_start,
                        last_success_at=now,
                        last_error=None,
                        updated_at=now,
                    ),
                    now,
                )
                return True
            except Exception as exc:
                error = str(exc)[:1000]
                logger.warning("research page commit failed: %s: %s", config.key, exc)
                self._save_error_cursor(
                    config,
                    sync_kind,
                    fail_page,
                    fail_fresh,
                    target_start,
                    covered_start,
                    now,
                    error,
                )
                return False

        # Phase 1: newest-first re-scan.  Announcements are published into the
        # top of the time-descending stream, so a cursor that only advances
        # forward would permanently miss same-day late uploads.  Re-scan from
        # the top each run (resuming at ``fresh_page`` when page 1 is
        # unchanged) until the first fully-known page marks the frontier;
        # everything below it is covered by the historical backfill in
        # phase 2.
        frontier: int | None = None
        fresh_done = False
        fresh_start = 1
        while pages < pages_budget and not fresh_done:
            if cancel.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                result = self._fetch_page(source, fresh_start, now, target_start)
            except RefreshCancelled:
                raise
            except Exception as exc:
                error = str(exc)[:1000]
                logger.warning("research source failed: %s: %s", config.key, exc)
                self._save_error_cursor(
                    config,
                    sync_kind,
                    page,
                    fresh_start,
                    target_start,
                    covered_start,
                    now,
                    error,
                )
                fresh_done = True
                break
            pages += 1

            if fresh_start == 1 and not result.items:
                error = "首屏空数据或结构变化"
                logger.warning("research source empty first page: %s", config.key)
                self._save_error_cursor(
                    config,
                    sync_kind,
                    page,
                    fresh_start,
                    target_start,
                    covered_start,
                    now,
                    error,
                )
                fresh_done = True
                break
            if not result.items:
                reached_cutoff = True
                fresh_done = True
                next_fresh = 1
                self._save_success_cursor(
                    config,
                    sync_kind,
                    page,
                    next_fresh,
                    target_start,
                    covered_start,
                    covered_end,
                    now,
                )
                break

            documents, candidates, oldest, newest, page_added, page_skipped = (
                process_page(result)
            )
            track_dates(oldest, newest)
            skipped += page_skipped
            discoveries += len(candidates)
            fully_known = page_added == 0 and page_skipped == len(result.items)
            if reached_cutoff:
                fresh_done = True
                next_fresh = 1
            elif fresh_start == 1 and fully_known and fresh_page > 1:
                # 第 1 页未变化：从上次重扫位置继续。
                next_fresh = fresh_page
            elif fully_known:
                # 首个全已知页即前沿：顶部已无新内容。
                frontier = fresh_start if fresh_start > 1 else 1
                fresh_done = True
                next_fresh = 1
            elif fresh_start == 1 and page_added > 0 and fresh_page > 1:
                # 顶部出现新公告：放弃旧重扫位置，从第 2 页重新开始。
                next_fresh = 2
            else:
                next_fresh = fresh_start + 1
            if not commit_batch(
                documents, candidates, page, next_fresh, fresh_start, fresh_start
            ):
                fresh_done = True
                break
            added += page_added
            self._write_manifest(
                config,
                result=result,
                cursor_payload=cursor_payload(next_fresh, fresh_start),
                now=now,
            )
            fresh_start = next_fresh

        # Phase 2: historical backfill.  Resume at the deeper of the stored
        # backfill cursor and the position the re-scan reached, so pages the
        # re-scan already covered are not fetched twice.
        if fresh_done:
            fresh_page = 1
        if (
            fresh_done
            and frontier is not None
            and not reached_cutoff
            and error is None
        ):
            page = max(page, frontier + 1)
        elif not fresh_done and error is None:
            page = max(page, fresh_start)
        while (
            pages < pages_budget
            and not reached_cutoff
            and error is None
        ):
            if cancel.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                result = self._fetch_page(source, page, now, target_start)
            except RefreshCancelled:
                raise
            except Exception as exc:
                error = str(exc)[:1000]
                logger.warning("research source failed: %s: %s", config.key, exc)
                self._save_error_cursor(
                    config,
                    sync_kind,
                    page,
                    fresh_page,
                    target_start,
                    covered_start,
                    now,
                    error,
                )
                break
            pages += 1

            if not result.items:
                reached_cutoff = True
                self._save_success_cursor(
                    config,
                    sync_kind,
                    page,
                    fresh_page,
                    target_start,
                    covered_start,
                    covered_end,
                    now,
                )
                break

            documents, candidates, oldest, newest, page_added, page_skipped = (
                process_page(result)
            )
            track_dates(oldest, newest)
            skipped += page_skipped
            discoveries += len(candidates)
            next_page = page + 1
            if (
                page_added == 0
                and page_skipped == len(result.items)
                and (frontier is None or page > frontier)
            ):
                # 循环分页/重复页（线上接口在真实流末端之后会回绕返回已见内容）：
                # 整页均为已知文档且位于前沿之后时，继续翻页只会重复消耗预算，
                # 视为已枚举到该来源当前提供的全部公开列表项。
                reached_cutoff = True
            if not commit_batch(
                documents, candidates, next_page, fresh_page, page, fresh_page
            ):
                break
            added += page_added
            self._write_manifest(
                config,
                result=result,
                cursor_payload=cursor_payload(next_page, fresh_page),
                now=now,
            )
            page = next_page
            if reached_cutoff:
                break

        # Phase 3: recoverable attachment work queue.  Page scans persist
        # metadata + discovery rows; the queue downloads attachments with a
        # round-robin across 新调研资料 / 高优先级待核验事件 / 最旧普通待解析资料.
        queue_has_more = False
        if pdfs_budget > 0 and error is None:
            consumed, failures, has_more = self._consume_attachment_queue(
                config, client, now=now, cancel=cancel, pdfs_budget=pdfs_budget
            )
            pdfs += consumed
            pdf_failures += failures
            queue_has_more = has_more

        coverage = CoverageState(
            source_key=config.key,
            requested_start=target_start,
            covered_start=covered_start,
            covered_end=covered_end,
            trading_days_covered=self._trading_days_covered(covered_start, covered_end),
            reached_cutoff=reached_cutoff,
            provisional=self._calendar_is_provisional(now),
            error=error,
        )
        budget_exhausted = queue_has_more or (
            pages >= pages_budget
            and not reached_cutoff
            and error is None
            and pages > 0
        )
        return _SourceSyncProgress(
            pages,
            pdfs,
            added,
            skipped,
            discoveries,
            pdf_failures,
            budget_exhausted,
            coverage,
        )

    def _consume_attachment_queue(
        self,
        config: SourceConfig,
        client: PoliteHttpClient,
        *,
        now: datetime,
        cancel: threading.Event,
        pdfs_budget: int,
    ) -> tuple[int, int, bool]:
        """Download queued attachments round-robin across the three buckets.

        新调研资料 (kind=research_activity, newest first) →
        高优先级待核验事件 (signal-worthy discovery types, oldest first) →
        最旧普通待解析资料 (remaining announcements, oldest first).
        Budget exhaustion only defers rows (they stay ``pending_attachment``);
        restarts resume from the persisted queue, so nothing is skipped
        permanently.
        """

        buckets: tuple[dict[str, object], ...] = (
            {"kind": "research_activity", "newest_first": True},
            {"kind": "announcement", "signal_priority": True},
            {
                "kind": "announcement",
                "signal_priority": False,
            },
        )
        consumed = 0
        failures = 0
        cycle = 0
        while consumed < pdfs_budget:
            if cancel.is_set():
                raise RefreshCancelled("刷新已取消")
            picked_any = False
            for offset in range(len(buckets)):
                if consumed >= pdfs_budget:
                    break
                if cancel.is_set():
                    raise RefreshCancelled("刷新已取消")
                bucket = buckets[(cycle + offset) % len(buckets)]
                rows = self.storage.get_pending_attachment_queue(
                    config.key, limit=1, **bucket
                )
                if not rows:
                    continue
                picked_any = True
                candidate = rows[0]
                consumed += 1
                if not self._download_attachment(
                    config, client, candidate, now=now, cancel=cancel
                ):
                    failures += 1
            if not picked_any:
                break
            cycle += len(buckets)
        has_more = False
        if consumed >= pdfs_budget:
            for bucket in buckets:
                if self.storage.get_pending_attachment_queue(
                    config.key, limit=1, **bucket
                ):
                    has_more = True
                    break
        return consumed, failures, has_more

    def _download_attachment(
        self,
        config: SourceConfig,
        client: PoliteHttpClient,
        candidate: DiscoveryCandidate,
        *,
        now: datetime,
        cancel: threading.Event,
    ) -> bool:
        """Download + parse one queued attachment; ``False`` on any failure."""

        document = self.storage.get_source_document(candidate.document_id)
        if document is None:
            return False
        try:
            pdf_result = fetch_and_extract_attachment(
                client,
                candidate.document_url,
                self.settings.pdf_temp_dir,
                cancel,
                candidate.attachment_type,
            )
            status, parse_error = pdf_parse_status(pdf_result)
            updated = replace(
                document,
                body_text=pdf_result.text,
                content_hash=pdf_result.content_hash,
                parse_status=status,
                parse_error=parse_error,
                page_count=pdf_result.page_count,
            )
            ok = status == "parsed"
        except RefreshCancelled:
            raise
        except Exception as exc:
            ok = False
            updated = replace(
                document, parse_status="failed", parse_error=str(exc)[:500]
            )
        self.storage.upsert_source_document(updated, now)
        queue_status = (
            QUEUE_STATUS_AWAITING_REVIEW
            if updated.parse_status == "parsed"
            else updated.parse_status
        )
        self.storage.set_discovery_queue_status(
            candidate.document_id, queue_status, now
        )
        return ok

    @staticmethod
    def _candidate_to_discovery(
        candidate: ResearchCandidate,
        document: SourceDocument,
        config: SourceConfig,
        now: datetime,
    ) -> DiscoveryCandidate:
        """Derive the discovery row for one list item (loose, anti-leak)."""

        discovery_type, reason = classify_discovery(candidate.title, candidate.kind)
        signal_priority = bool(event_type_hint(candidate.title)) or (
            discovery_type in HIGH_PRIORITY_DISCOVERY_TYPES
        )
        supported_attachment = bool(
            candidate.document_url
            and candidate.attachment_type
            and candidate.attachment_type.upper() in SUPPORTED_ATTACHMENT_TYPES
        )
        if document.parse_status == "parsed":
            status = QUEUE_STATUS_AWAITING_REVIEW
        elif document.parse_status in (
            QUEUE_STATUS_EMPTY_TEXT,
            QUEUE_STATUS_FAILED,
        ):
            status = document.parse_status
        elif supported_attachment:
            status = QUEUE_STATUS_PENDING_ATTACHMENT
        else:
            status = QUEUE_STATUS_AWAITING_REVIEW
        return DiscoveryCandidate(
            document_id=candidate.document_id,
            source_key=config.key,
            source_name=config.name,
            provider_key=candidate.provider_key,
            provider_name=candidate.provider_name,
            kind=candidate.kind,
            stock_codes=candidate.stock_codes,
            title=candidate.title,
            published_at=candidate.published_at,
            discovery_type=discovery_type,
            trigger_reason=reason,
            queue_status=status,
            attachment_type=candidate.attachment_type,
            document_url=candidate.document_url,
            enqueued_at=now if status == QUEUE_STATUS_PENDING_ATTACHMENT else None,
            updated_at=now,
            signal_priority=signal_priority,
        )

    @staticmethod
    def _fetch_page(
        source: (
            CninfoSource
            | SsePublishSource
            | IrmIrcsSource
            | SseAnnouncementSource
            | BseAnnouncementSource
            | BsePerformanceSource
        ),
        page: int,
        now: datetime,
        target_start: date,
    ) -> ResearchPageResult:
        if isinstance(source, SsePublishSource):
            return source.fetch_page(page, now)
        return source.fetch_page(
            page, now, date_start=target_start, date_end=now.date()
        )

    @staticmethod
    def _candidate_to_document(candidate: ResearchCandidate) -> SourceDocument:
        metadata_hash = sha256_hex(
            "|".join(
                (
                    candidate.document_id,
                    candidate.title,
                    candidate.published_at.isoformat(),
                    candidate.document_url or "",
                )
            ).encode("utf-8")
        )
        return SourceDocument(
            document_id=candidate.document_id,
            provider_key=candidate.provider_key,
            provider_name=candidate.provider_name,
            kind=candidate.kind,
            source_url=candidate.source_url,
            document_url=candidate.document_url,
            title=candidate.title,
            published_at=candidate.published_at,
            stock_codes=candidate.stock_codes,
            stock_names=candidate.stock_names,
            body_text="",
            content_hash=metadata_hash,
            parse_status="metadata_only",
            parse_error=None,
        )

    def _save_success_cursor(
        self,
        config: SourceConfig,
        sync_kind: str,
        page: int,
        fresh_page: int,
        target_start: date,
        covered_start: date | None,
        covered_end: date | None,
        now: datetime,
    ) -> None:
        cursor_payload: dict[str, object] = {"page": page}
        if fresh_page != 1:
            cursor_payload["fresh_page"] = fresh_page
        if covered_end is not None:
            cursor_payload["covered_end"] = covered_end.isoformat()
        cursor = SyncCursor(
            source_key=config.key,
            sync_kind=sync_kind,
            cursor=cursor_payload,
            target_start=target_start,
            covered_start=covered_start,
            last_success_at=now,
            last_error=None,
            updated_at=now,
        )
        self.storage.save_sync_state(cursor)

    def _save_error_cursor(
        self,
        config: SourceConfig,
        sync_kind: str,
        page: int,
        fresh_page: int,
        target_start: date,
        covered_start: date | None,
        now: datetime,
        error: str,
    ) -> None:
        previous = self.storage.get_sync_state(config.key, sync_kind)
        cursor_payload = (
            dict(previous.cursor)
            if previous is not None and isinstance(previous.cursor, dict)
            else {}
        )
        cursor_payload["page"] = page
        if fresh_page != 1:
            cursor_payload["fresh_page"] = fresh_page
        else:
            cursor_payload.pop("fresh_page", None)
        cursor = SyncCursor(
            source_key=config.key,
            sync_kind=sync_kind,
            cursor=cursor_payload,
            target_start=target_start,
            covered_start=(
                covered_start
                if covered_start is not None
                else previous.covered_start if previous is not None else None
            ),
            last_success_at=(
                previous.last_success_at if previous is not None else None
            ),
            last_error=error,
            updated_at=now,
        )
        self.storage.save_sync_state(cursor)
        self._record_manifest_failure(config, now, error)

    def _write_manifest(
        self,
        config: SourceConfig,
        *,
        result: ResearchPageResult,
        cursor_payload: dict[str, object],
        now: datetime,
    ) -> None:
        """Upsert per-source/day reconciliation manifests for one page.

        v1.2/v2 里程碑 2：每源每日 manifest 记录来源总数、本地清单 ID 集合
        摘要（来自 ``discovery_candidates``，累积当日全部列表项）、水位线
        （分页游标）与失败区间；成功提交会关闭此前打开的失败区间。覆盖状态
        保持“实时暂定”，只有总数与本地清单一致且正文已处理才由覆盖中心升级为
        “列表已对账”。
        """

        try:
            days = sorted(
                {item.published_at.date() for item in result.items}
            )
            for manifest_date in days:
                count, digest = self.storage.summarize_discovery_day(
                    config.key, manifest_date
                )
                existing = self.storage.get_source_manifests(
                    config.key, manifest_date
                )
                previous = existing[0] if existing else None
                intervals = list(previous.failure_intervals) if previous else []
                if intervals and intervals[-1].ended_at is None:
                    intervals[-1] = replace(intervals[-1], ended_at=now)
                self.storage.upsert_source_manifest(
                    SourceManifest(
                        source_key=config.key,
                        manifest_date=manifest_date,
                        total_count=(
                            result.total
                            if result.total is not None
                            else (previous.total_count if previous else 0)
                        ),
                        document_id_count=count,
                        document_id_set_hash=digest,
                        watermark=dict(cursor_payload),
                        failure_intervals=tuple(intervals),
                        ocr_status=(
                            previous.ocr_status
                            if previous
                            else OCR_STATUS_NOT_APPLICABLE
                        ),
                        scheduled_task_result=(
                            previous.scheduled_task_result if previous else None
                        ),
                        coverage_status=COVERAGE_STATUS_REALTIME_PROVISIONAL,
                        updated_at=now,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - manifest must not break sync
            logger.warning("manifest write failed for %s: %s", config.key, exc)

    def _record_manifest_failure(
        self, config: SourceConfig, now: datetime, error: str
    ) -> None:
        """Open (or refresh) today's failure interval on the source manifest."""

        try:
            manifest_date = now.date()
            count, digest = self.storage.summarize_discovery_day(
                config.key, manifest_date
            )
            existing = self.storage.get_source_manifests(
                config.key, manifest_date
            )
            previous = existing[0] if existing else None
            intervals = list(previous.failure_intervals) if previous else []
            if intervals and intervals[-1].ended_at is None:
                intervals[-1] = replace(
                    intervals[-1], reason=str(error)[:500]
                )
            else:
                intervals.append(
                    FailureInterval(
                        started_at=now, ended_at=None, reason=str(error)[:500]
                    )
                )
            self.storage.upsert_source_manifest(
                SourceManifest(
                    source_key=config.key,
                    manifest_date=manifest_date,
                    total_count=previous.total_count if previous else 0,
                    document_id_count=count,
                    document_id_set_hash=digest,
                    watermark=dict(previous.watermark) if previous else None,
                    failure_intervals=tuple(intervals),
                    ocr_status=(
                        previous.ocr_status
                        if previous
                        else OCR_STATUS_NOT_APPLICABLE
                    ),
                    scheduled_task_result=(
                        previous.scheduled_task_result if previous else None
                    ),
                    coverage_status=COVERAGE_STATUS_REALTIME_PROVISIONAL,
                    updated_at=now,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "manifest failure record failed for %s: %s", config.key, exc
            )

    def _trading_days_covered(
        self, covered_start: date | None, covered_end: date | None
    ) -> int:
        if covered_start is None or covered_end is None:
            return 0
        return self.storage.trading_day_count_between(covered_start, covered_end)

    def _calendar_is_provisional(self, now: datetime) -> bool:
        source = self.storage.get_trading_day_source(now.year)
        return source is None or source == "fallback"
