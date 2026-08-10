"""十个国家级政策源的统一适配器（v1.2/v2 政策双层归因采集层）。

政策只进入 ``policy_documents`` + 每日 manifest（行业政策观察），绝不进入
``source_documents`` 信号管线；只有政策点名上市公司/项目或公司公告以政策
文号明确关联时才可能形成 ``direct_policy_benefit`` 个股信号（由后续里程碑
实现双层归因）。

来源契约（2026-08-09 实测）：

- 服务器端渲染列表页（共享启发式解析，要求锚文本 + 同一区块内的日期）：
  国务院政策文件库 /zhengce/、发改委 zcfb/fzggwl/、工信部 zwgk/zcwj/、
  财政部 zhengcefabu/、商务部 zcfb/、生态环境部 xxgk06/。
- 分页：发改委/财政部/生态环境部使用 ``index_N.html/.htm`` 后缀；其余来源
  未发现可用的服务端分页（单页 + 部分覆盖）。
- 失败关闭：药监局（WAF 412）、能源局（JS 框架页）、市场监管总局（JS 壳）、
  证监会（栏目为政府信息公开年度报告，非政策列表，启发式 0 条）均按“首屏
  空数据/结构变化”失败关闭并在覆盖中心显示缺口，不生成伪空榜。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime

from bs4 import BeautifulSoup

from .config import AppSettings, PolicySourceConfig, SHANGHAI_TZ
from .coverage import (
    COVERAGE_STATUS_UNAVAILABLE,
    COVERAGE_STATUS_PARTIAL,
    COVERAGE_STATUS_REALTIME_PROVISIONAL,
    OCR_STATUS_NOT_APPLICABLE,
    summarize_document_ids,
)
from .models import FailureInterval, PolicyDocument, SourceManifest
from .sources import PoliteHttpClient, RefreshCancelled
from .storage import Storage


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PolicyListItem:
    document_id: str
    title: str
    url: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class PolicyPageResult:
    page: int
    url: str
    items: tuple[PolicyListItem, ...]
    exhausted: bool = False


@dataclass(frozen=True, slots=True)
class PolicySyncResult:
    pages_consumed: int
    documents_added: int
    documents_skipped: int
    failure_sources: tuple[str, ...]
    coverages: tuple[CoverageResult, ...]


@dataclass(frozen=True, slots=True)
class CoverageResult:
    source_key: str
    status: str
    reached_cutoff: bool
    error: str | None


# 实测锚点日期格式：2026-08-03 / 2026/07/31 / 2026年8月3日。
_DATE_PATTERN = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?")
_ARTICLE_HREF = re.compile(r"\.(?:html?|shtml?)$", re.IGNORECASE)


def _normalize_url(url: str, base_url: str) -> str:
    from urllib.parse import urljoin

    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base_url, url)


def parse_policy_list_page(
    html: str,
    *,
    source_key: str,
    list_url: str,
    now: datetime,
) -> list[PolicyListItem]:
    """Parse one server-rendered policy list page (shared heuristic).

    Keeps anchors whose text is >= 8 chars, whose href is an article-like
    ``.html/.htm/.shtml`` link and whose surrounding block carries an explicit
    date.  Empty pages (JS shells, WAF challenges, structure changes) raise
    :class:`RuntimeError` so the sync service fails closed and never fabricates
    an empty policy library.
    """

    soup = BeautifulSoup(html or "", "html.parser")
    items: list[PolicyListItem] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        if len(title) < 8 or href.startswith(("javascript", "#")):
            continue
        if not _ARTICLE_HREF.search(href):
            continue
        block = anchor.find_parent(["li", "div", "td"]) or anchor
        block_text = block.get_text(" ", strip=True)
        date_match = _DATE_PATTERN.search(block_text)
        if date_match is None:
            continue
        year, month, day = (int(g) for g in date_match.groups())
        try:
            published_at = datetime(
                year, month, day, tzinfo=SHANGHAI_TZ
            )
        except ValueError:
            continue
        url = _normalize_url(href, list_url)
        document_id = "policy:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        if document_id in seen:
            continue
        seen.add(document_id)
        items.append(
            PolicyListItem(
                document_id=document_id,
                title=title[:300],
                url=url,
                published_at=published_at,
            )
        )
    if not items:
        raise RuntimeError("政策源返回空列表或结构异常（JS 壳/WAF/栏目变化）")
    return items


class PolicySource:
    """One policy source adapter: page 1 + optional index_N pagination."""

    def __init__(self, config: PolicySourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def _page_url(self, page: int) -> str | None:
        if page == 1:
            return self.config.list_url
        if not self.config.pagination_template:
            return None
        return self.config.pagination_template.format(n=page - 1)

    def fetch_page(
        self, page: int, now: datetime
    ) -> PolicyPageResult:
        url = self._page_url(page)
        if url is None:
            return PolicyPageResult(page=page, url="", items=(), exhausted=True)
        html = self.client.get_text(url)
        items = parse_policy_list_page(
            html,
            source_key=self.config.key,
            list_url=self.config.list_url,
            now=now,
        )
        return PolicyPageResult(
            page=page,
            url=url,
            items=tuple(items),
            exhausted=not items,
        )


class PolicySyncService:
    """Progressive policy enumeration: list -> PolicyDocument + manifest.

    Every listed policy item is persisted as a ``PolicyDocument`` (metadata
    only) and reconciled through ``source_manifests``; a failing source keeps
    its previous documents and records an open failure interval.  Policy
    documents never enter the short-term signal pipeline.
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
        max_pages_per_source: int = 5,
    ) -> PolicySyncResult:
        own_client = client is None
        if own_client:
            client = PoliteHttpClient(self.settings, cancel)
        pages_consumed = 0
        documents_added = 0
        documents_skipped = 0
        failure_sources: list[str] = []
        coverages: list[CoverageResult] = []
        try:
            for config in self.settings.policy_sources:
                if cancel.is_set():
                    raise RefreshCancelled("刷新已取消")
                coverage = self._sync_source(
                    config, client, now=now, cancel=cancel,
                    max_pages=max_pages_per_source,
                )
                pages_consumed += coverage.pages
                documents_added += coverage.added
                documents_skipped += coverage.skipped
                if coverage.error is not None:
                    failure_sources.append(config.key)
                coverages.append(
                    CoverageResult(
                        source_key=config.key,
                        status=coverage.status,
                        reached_cutoff=coverage.reached_cutoff,
                        error=coverage.error,
                    )
                )
        finally:
            if own_client:
                client.close()
        return PolicySyncResult(
            pages_consumed=pages_consumed,
            documents_added=documents_added,
            documents_skipped=documents_skipped,
            failure_sources=tuple(failure_sources),
            coverages=tuple(coverages),
        )

    def _sync_source(
        self,
        config: PolicySourceConfig,
        client: PoliteHttpClient,
        *,
        now: datetime,
        cancel: threading.Event,
        max_pages: int,
    ) -> _SourceRun:
        source = PolicySource(config, client)
        pages = 0
        added = 0
        skipped = 0
        error: str | None = None
        reached_cutoff = False
        partial_no_pagination = False
        for page in range(1, max_pages + 1):
            if cancel.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                result = source.fetch_page(page, now)
            except RefreshCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - per-source failure
                error = str(exc)[:500]
                logger.warning("policy source failed: %s: %s", config.key, exc)
                break
            pages += 1
            if not result.items:
                if config.pagination_template is None and page > 1:
                    # 无分页模板：只能枚举首页，无法对账全库，覆盖为部分。
                    partial_no_pagination = True
                reached_cutoff = True
                break
            for item in result.items:
                existing = self.storage.get_policy_document(item.document_id)
                if existing is not None:
                    skipped += 1
                    continue
                self.storage.upsert_policy_document(
                    PolicyDocument(
                        document_id=item.document_id,
                        source_key=config.key,
                        title=item.title,
                        published_at=item.published_at,
                        source_url=result.url,
                        document_url=item.url,
                        body_text="",
                        body_hash=None,
                        body_status="metadata_only",
                        body_error=None,
                        content_hash=hashlib.sha256(
                            item.url.encode("utf-8")
                        ).hexdigest(),
                        updated_at=now,
                    )
                )
                added += 1
            self._write_manifest(config, now=now, error=None)
            if result.exhausted:
                reached_cutoff = True
                break
        if error is not None:
            status = COVERAGE_STATUS_UNAVAILABLE
            self._write_manifest(config, now=now, error=error)
        elif partial_no_pagination or not reached_cutoff:
            # 无分页模板或分页未确认：只枚举了首页/有限页，覆盖为部分。
            status = COVERAGE_STATUS_PARTIAL
        else:
            status = COVERAGE_STATUS_REALTIME_PROVISIONAL
        return _SourceRun(
            pages=pages,
            added=added,
            skipped=skipped,
            status=status,
            reached_cutoff=reached_cutoff,
            error=error,
        )

    def _write_manifest(
        self,
        config: PolicySourceConfig,
        *,
        now: datetime,
        error: str | None,
    ) -> None:
        """Upsert today's policy manifest for one source."""

        try:
            manifest_date = now.date()
            count, digest = self.storage.summarize_policy_day(
                config.key, manifest_date
            )
            existing = self.storage.get_source_manifests(
                config.key, manifest_date
            )
            previous = existing[0] if existing else None
            intervals = list(previous.failure_intervals) if previous else []
            if error is not None:
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
            elif intervals and intervals[-1].ended_at is None:
                intervals[-1] = replace(intervals[-1], ended_at=now)
            self.storage.upsert_source_manifest(
                SourceManifest(
                    source_key=config.key,
                    manifest_date=manifest_date,
                    total_count=(
                        count if previous is None else max(previous.total_count, count)
                    ),
                    document_id_count=count,
                    document_id_set_hash=digest,
                    watermark=None,
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
            logger.warning("policy manifest write failed: %s: %s", config.key, exc)


@dataclass(frozen=True, slots=True)
class _SourceRun:
    pages: int
    added: int
    skipped: int
    status: str
    reached_cutoff: bool
    error: str | None
