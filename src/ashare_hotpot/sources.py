from __future__ import annotations

import json
import random
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from .config import AppSettings, SourceConfig
from .models import ArticleCandidate, InteractionRecord, ResearchCandidate
from .parsing import (
    decode_html,
    parse_bse_announcement_page,
    parse_bse_performance_page,
    parse_cninfo_page,
    parse_irm_page,
    parse_irm_ircs_page,
    parse_jsonp_payload,
    parse_list_page,
    parse_sse_announcement_page,
    parse_sse_feed,
    parse_sse_publish_feed,
)
from .trading_calendar import SSE_CLOSED_URL, parse_sse_closed_html


class RefreshCancelled(RuntimeError):
    pass


class Http404Error(RuntimeError):
    """The server answered 404, used to detect the end of a list/archive."""


HTML_ACCEPT = "text/html,application/xhtml+xml"
JSON_ACCEPT = "application/json, text/plain, */*"
# 10jqka list pages only render index_1..index_20 server-side; deeper pages
# answer 404 and the crawl continues through the per-day archive URLs instead.
INDEX_MAX_PAGES = 20
ARCHIVE_SKIP_DAYS = 8



class PoliteHttpClient:
    def __init__(self, settings: AppSettings, cancel_event: threading.Event) -> None:
        self._settings = settings
        self._cancel_event = cancel_event
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            headers={
                "User-Agent": "AshareHotPot/0.1 (+personal desktop news index; contact via project README)",
                "Accept": HTML_ACCEPT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _wait_for_slot(self) -> None:
        with self._rate_lock:
            if self._cancel_event.is_set():
                raise RefreshCancelled("刷新已取消")
            elapsed = time.monotonic() - self._last_request_at
            delay = self._settings.minimum_request_interval_seconds - elapsed
            if delay > 0 and self._cancel_event.wait(delay):
                raise RefreshCancelled("刷新已取消")
            self._last_request_at = time.monotonic()

    def get_text(
        self,
        url: str,
        *,
        accept: str = HTML_ACCEPT,
        headers: dict[str, str] | None = None,
    ) -> str:
        """GET and decode the response body as text.

        ``headers`` are merged over the client defaults (used for Referer
        requirements such as 上交所公告 JSONP API).
        """

        last_error: Exception | None = None
        for attempt in range(self._settings.request_retries):
            if self._cancel_event.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                self._wait_for_slot()
                request_headers = {"Accept": accept}
                if headers:
                    request_headers.update(headers)
                response = self._client.get(url, headers=request_headers)
                response.raise_for_status()
                return decode_html(response.content, response.headers.get("content-type", ""))
            except RefreshCancelled:
                raise
            except (httpx.HTTPError, OSError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                    raise Http404Error(f"页面不存在：{url}") from exc
                last_error = exc
                if attempt + 1 >= self._settings.request_retries:
                    break
                wait_seconds = (2**attempt) + random.uniform(0.0, 0.3)
                if self._cancel_event.wait(wait_seconds):
                    raise RefreshCancelled("刷新已取消")
        raise RuntimeError(f"请求失败：{url}（{last_error}）")

    def get_json(self, url: str) -> dict[str, object]:
        try:
            payload = json.loads(self.get_text(url, accept=JSON_ACCEPT))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"接口返回的不是有效 JSON：{url}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"接口返回格式异常：{url}")
        return payload

    def get_bytes(self, url: str, *, accept: str = "application/pdf,*/*") -> bytes:
        """Download raw attachment bytes under the same rate limits.

        HTML/plain-text responses (login/identity pages, gateway errors,
        structure changes) fail closed so callers never treat a web page as a
        PDF document.
        """

        last_error: Exception | None = None
        for attempt in range(self._settings.request_retries):
            if self._cancel_event.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                self._wait_for_slot()
                response = self._client.get(url, headers={"Accept": accept})
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                if "text/html" in content_type or "text/plain" in content_type:
                    raise RuntimeError(f"非预期响应类型：{content_type}（{url}）")
                return response.content
            except RefreshCancelled:
                raise
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                    raise Http404Error(f"文档不存在：{url}") from exc
                last_error = exc
                if attempt + 1 >= self._settings.request_retries:
                    break
                wait_seconds = (2**attempt) + random.uniform(0.0, 0.3)
                if self._cancel_event.wait(wait_seconds):
                    raise RefreshCancelled("刷新已取消")
        raise RuntimeError(f"下载失败：{url}（{last_error}）")

    def post_json(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        last_error: Exception | None = None
        for attempt in range(self._settings.request_retries):
            if self._cancel_event.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                self._wait_for_slot()
                response = self._client.post(url, json=payload, headers={"Accept": JSON_ACCEPT})
                response.raise_for_status()
                try:
                    parsed = json.loads(decode_html(response.content, response.headers.get("content-type", "")))
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"接口返回的不是有效 JSON：{url}") from exc
                if not isinstance(parsed, dict):
                    raise RuntimeError(f"接口返回格式异常：{url}")
                return parsed
            except RefreshCancelled:
                raise
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                last_error = exc
                if attempt + 1 >= self._settings.request_retries:
                    break
                wait_seconds = (2**attempt) + random.uniform(0.0, 0.3)
                if self._cancel_event.wait(wait_seconds):
                    raise RefreshCancelled("刷新已取消")
        raise RuntimeError(f"请求失败：{url}（{last_error}）")

    def post_form(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        """POST ``application/x-www-form-urlencoded`` data and parse JSON."""

        last_error: Exception | None = None
        for attempt in range(self._settings.request_retries):
            if self._cancel_event.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                self._wait_for_slot()
                response = self._client.post(
                    url,
                    data={str(key): str(value) for key, value in payload.items()},
                    headers={"Accept": JSON_ACCEPT},
                )
                response.raise_for_status()
                try:
                    parsed = json.loads(
                        decode_html(response.content, response.headers.get("content-type", ""))
                    )
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"接口返回的不是有效 JSON：{url}") from exc
                if not isinstance(parsed, dict):
                    raise RuntimeError(f"接口返回格式异常：{url}")
                return parsed
            except RefreshCancelled:
                raise
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                last_error = exc
                if attempt + 1 >= self._settings.request_retries:
                    break
                wait_seconds = (2**attempt) + random.uniform(0.0, 0.3)
                if self._cancel_event.wait(wait_seconds):
                    raise RefreshCancelled("刷新已取消")
        raise RuntimeError(f"请求失败：{url}（{last_error}）")

    def post_form_text(
        self,
        url: str,
        payload: dict[str, object],
        *,
        accept: str = JSON_ACCEPT,
        headers: dict[str, str] | None = None,
    ) -> str:
        """POST form data and return the raw decoded body.

        Used by JSONP endpoints (北交所公告/业绩说明会) whose wrapper
        (``callback(...)``) is not valid JSON; parsers strip the wrapper.
        """

        last_error: Exception | None = None
        for attempt in range(self._settings.request_retries):
            if self._cancel_event.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                self._wait_for_slot()
                request_headers = {"Accept": accept}
                if headers:
                    request_headers.update(headers)
                response = self._client.post(
                    url,
                    data={
                        str(key): (
                            tuple(str(item) for item in value)
                            if isinstance(value, (list, tuple))
                            else str(value)
                        )
                        for key, value in payload.items()
                    },
                    headers=request_headers,
                )
                response.raise_for_status()
                return decode_html(
                    response.content, response.headers.get("content-type", "")
                )
            except RefreshCancelled:
                raise
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt + 1 >= self._settings.request_retries:
                    break
                wait_seconds = (2**attempt) + random.uniform(0.0, 0.3)
                if self._cancel_event.wait(wait_seconds):
                    raise RefreshCancelled("刷新已取消")
        raise RuntimeError(f"请求失败：{url}（{last_error}）")

    def post_query(self, url: str, params: dict[str, object]) -> dict[str, object]:
        """POST with query-string parameters and an empty form body.

        Used by the 互动易投资者关系活动流 (``searchTypes=4``) which reads its
        filter from the query string.  HTML/plain-text responses (login or
        identity pages) fail closed via the JSON structure checks in the
        parsers, and 401/403/5xx raise so callers never treat a login page as
        an empty board.
        """

        separator = "&" if "?" in url else "?"
        query = urllib.parse.urlencode(
            {str(key): str(value) for key, value in params.items()}
        )
        return self.post_form(url + separator + query, {})


@dataclass(frozen=True, slots=True)
class PageResult:
    page: int
    url: str
    items: tuple[ArticleCandidate, ...]
    exhausted: bool = False


class NewsSource:
    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client
        self._archive_day: date | None = None

    def page_url(self, page: int) -> str:
        if page <= 1:
            return self.config.base_url
        return f"{self.config.base_url}index_{page}.shtml"

    def fetch_page(self, page: int, now: datetime) -> PageResult:
        if page > INDEX_MAX_PAGES:
            return self._fetch_archive(page, now)
        url = self.page_url(page)
        try:
            html = self.client.get_text(url)
        except Http404Error:
            if page <= 1:
                raise
            # Deeper list pages do not exist; treat it as the end of the list.
            return PageResult(page=page, url=url, items=())
        items = parse_list_page(
            html,
            source_key=self.config.key,
            source_name=self.config.name,
            base_url=self.config.base_url,
            now=now,
            provider_key=self.config.provider_key,
            provider_name=self.config.provider_name,
        )
        return PageResult(page=page, url=url, items=tuple(items))

    def _fetch_archive(self, page: int, now: datetime) -> PageResult:
        """Fetch per-day archive pages once the index list is exhausted.

        10jqka list channels publish a complete per-day archive at
        ``<base_url>YYYYMMDD/``.  Days with no articles are skipped so the
        crawl can cross weekends without stopping early.
        """

        if self._archive_day is None:
            self._archive_day = now.date() - timedelta(days=1)
        for _ in range(ARCHIVE_SKIP_DAYS):
            day = self._archive_day
            self._archive_day = day - timedelta(days=1)
            url = f"{self.config.base_url}{day:%Y%m%d}/"
            try:
                html = self.client.get_text(url)
            except Http404Error:
                return PageResult(page=page, url=url, items=())
            items = parse_list_page(
                html,
                source_key=self.config.key,
                source_name=self.config.name,
                base_url=self.config.base_url,
                now=now,
                provider_key=self.config.provider_key,
                provider_name=self.config.provider_name,
            )
            if items:
                return PageResult(page=page, url=url, items=tuple(items))
        return PageResult(page=page, url=url, items=())


@dataclass(frozen=True, slots=True)
class InteractionPageResult:
    page: int
    url: str
    items: tuple[InteractionRecord, ...]
    oldest_feed_time: datetime | None
    exhausted: bool = False


class IrmSource:
    """深交所互动易 full-market latest Q&A stream adapter.

    口径（v2）：只有公司已回复的提问才计为有效提问，因此只读取已回复
    问答流（``searchTypes=11``，按 updateDate 降序），未回复提问不下发。
    """

    PAGE_SIZE = 50

    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def fetch_page(self, page: int, now: datetime) -> InteractionPageResult:
        payload = self.client.post_form(
            self.config.base_url,
            {
                "keyword": "",
                "pageNo": str(page),
                "pageSize": str(self.PAGE_SIZE),
                "searchTypes": "11",
            },
        )
        records, total = parse_irm_page(
            payload,
            source_key=self.config.key,
            source_name=self.config.name,
            now=now,
        )
        oldest = None
        for record in records:
            feed_time = record.reply_time or record.question_time
            if oldest is None or feed_time < oldest:
                oldest = feed_time
        exhausted = not records and total == 0
        return InteractionPageResult(
            page=page,
            url=self.config.base_url,
            items=tuple(records),
            oldest_feed_time=oldest,
            exhausted=exhausted,
        )


class SseInteractionSource:
    """上证e互动 adapter reading the latest-reply feed.

    口径（v2）：只统计已回复提问，因此只读取 ``type=11``（最新回复）信息
    流，按回复时间降序排列，窗口边界以回复时间为准。
    """

    PAGE_SIZE = 100

    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def _feed_url(self, feed_type: int, page: int) -> str:
        separator = "&" if "?" in self.config.base_url else "?"
        return (
            f"{self.config.base_url}{separator}page={page}&type={feed_type}"
            f"&pageSize={self.PAGE_SIZE}&lastid=-1&show=1"
        )

    def fetch_page(self, page: int, now: datetime) -> InteractionPageResult:
        url = self._feed_url(11, page)
        html = self.client.get_text(url)
        records = parse_sse_feed(
            html,
            source_key=self.config.key,
            source_name=self.config.name,
            now=now,
        )
        oldest: datetime | None = None
        for record in records:
            # 最新回复流按回复时间排序，窗口边界以回复时间为准。
            feed_time = record.reply_time or record.question_time
            if oldest is None or feed_time < oldest:
                oldest = feed_time
        return InteractionPageResult(
            page=page,
            url=self.config.base_url,
            items=tuple(records),
            oldest_feed_time=oldest,
            exhausted=not records,
        )


@dataclass(frozen=True, slots=True)
class ResearchPageResult:
    page: int
    url: str
    items: tuple[ResearchCandidate, ...]
    exhausted: bool = False
    total: int | None = None


class CninfoSource:
    """巨潮资讯公告/调研列表适配器（公开接口，无需登录）。

    The 公告流 uses ``hisAnnouncement/query`` with ``column=szse`` (deep-SH
    full-market announcement stream); the 调研流 uses ``disclosure`` with
    ``column=szse_relation`` (投资者关系活动记录表/调研).  Both endpoints
    accept the same form payload and return the same JSON shape, ordered by
    announcement time descending, so a plain page cursor can resume a
    backfill.  ``seDate`` restricts a page to ``[start, end]``.
    """

    PAGE_SIZE = 30

    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def fetch_page(
        self,
        page: int,
        now: datetime,
        *,
        date_start: date | None = None,
        date_end: date | None = None,
    ) -> ResearchPageResult:
        payload: dict[str, object] = {
            "pageNum": str(page),
            "pageSize": str(self.PAGE_SIZE),
            "column": self.config.column or "szse",
            "tabName": self.config.tab_name or "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": "",
            "secid": "",
            "category": self.config.category or "",
            "trade": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
            "seDate": (
                f"{date_start.isoformat()}~{date_end.isoformat()}"
                if date_start and date_end
                else ""
            ),
        }
        response = self.client.post_form(self.config.base_url, payload)
        items, total = parse_cninfo_page(
            response,
            source_key=self.config.key,
            source_name=self.config.name,
            provider_key=self.config.provider_key,
            provider_name=self.config.provider_name,
            kind=self.config.kind,
            now=now,
            base_url=self.config.base_url,
        )
        return ResearchPageResult(
            page=page,
            url=self.config.base_url,
            items=tuple(items),
            exhausted=not items and total == 0,
            total=total,
        )


class SsePublishSource:
    """上证e互动“上市公司发布”适配器（type=30 信息流）。"""

    PAGE_SIZE = 30
    FEED_TYPE = 30

    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def _feed_url(self, page: int) -> str:
        separator = "&" if "?" in self.config.base_url else "?"
        return (
            f"{self.config.base_url}{separator}page={page}&type={self.FEED_TYPE}"
            f"&pageSize={self.PAGE_SIZE}&lastid=-1&show=1"
        )

    def fetch_page(self, page: int, now: datetime) -> ResearchPageResult:
        url = self._feed_url(page)
        html = self.client.get_text(url)
        items = parse_sse_publish_feed(
            html,
            source_key=self.config.key,
            source_name=self.config.name,
            provider_key=self.config.provider_key,
            provider_name=self.config.provider_name,
            now=now,
        )
        return ResearchPageResult(
            page=page,
            url=url,
            items=tuple(items),
            exhausted=not items,
            total=None,
        )


class IrmIrcsSource:
    """深交所互动易“投资者关系活动”公开流适配器（后 1.1.0 可靠性里程碑）。

    The 互动易 search API serves full-market 投资者关系活动记录
    (``searchTypes=4``) without login: rows carry the original title,
    stock code/name, publish time and the attachment PDF URL on
    ``static.cninfo.com.cn``.  The stream is public, paginated and filtered by
    a date window, so it satisfies the 低频、有限分页、无需登录 constraint.
    Login/identity pages, 401/403/5xx and unexpected JSON shapes raise so the
    caller fails closed and never fabricates an empty board.
    """

    PAGE_SIZE = 30
    SEARCH_TYPES = "4"

    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def fetch_page(
        self,
        page: int,
        now: datetime,
        *,
        date_start: date | None = None,
        date_end: date | None = None,
    ) -> ResearchPageResult:
        params: dict[str, object] = {
            "stockCodes": "",
            "keywords": "",
            "searchTypes": self.SEARCH_TYPES,
            "startDate": (
                f"{date_start.isoformat()} 00:00:00" if date_start else ""
            ),
            "endDate": f"{date_end.isoformat()} 23:59:59" if date_end else "",
            # 线上接口以 pageNo 分页（响应字段同为 pageNo）；pageNum 会被忽略
            # 并永远返回第一页（2026-08-08 真实回填时验证）。
            "pageNo": str(page),
            "pageSize": str(self.PAGE_SIZE),
            "onlyAttentionCompany": "2",
        }
        payload = self.client.post_query(self.config.base_url, params)
        items, total = parse_irm_ircs_page(
            payload,
            source_key=self.config.key,
            source_name=self.config.name,
            provider_key=self.config.provider_key,
            provider_name=self.config.provider_name,
            now=now,
            base_url=self.config.base_url,
        )
        return ResearchPageResult(
            page=page,
            url=self.config.base_url,
            items=tuple(items),
            exhausted=not items and total == 0,
            total=total,
        )


class SseAnnouncementSource:
    """上交所公司公告列表适配器（``queryCompanyBulletinNew.do`` JSONP）。

    The API is public and paginated (``pageHelp.pageSize=25``); rows carry the
    announcement title, security code/name, publish date and the PDF URL on
    ``www.sse.com.cn``.  The endpoint requires a ``Referer`` header (returns
    ``success=false`` without it), so the adapter passes one explicitly.
    Gateway errors (``success=false``), non-JSONP responses and unexpected
    shapes raise so the caller fails closed instead of fabricating an empty
    board.
    """

    PAGE_SIZE = 25
    QUERY_URL = (
        "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do"
    )
    REFERER = "https://www.sse.com.cn/"

    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def _query_url(
        self, page: int, date_start: date | None, date_end: date | None
    ) -> str:
        params = {
            "jsonCallBack": "callback",
            "isPagination": "true",
            "pageHelp.pageSize": str(self.PAGE_SIZE),
            "pageHelp.cacheSize": "1",
            "pageHelp.pageNo": str(page),
            # 实测：beginPage/endPage 必须等于 pageNo，否则接口恒返回第一页。
            "pageHelp.beginPage": str(page),
            "pageHelp.endPage": str(page),
            "START_DATE": date_start.isoformat() if date_start else "",
            "END_DATE": date_end.isoformat() if date_end else "",
            "SECURITY_CODE": "",
            "TITLE": "",
            "BULLETIN_TYPE": "",
            "stockType": "",
        }
        return f"{self.QUERY_URL}?{urllib.parse.urlencode(params)}"

    def fetch_page(
        self,
        page: int,
        now: datetime,
        *,
        date_start: date | None = None,
        date_end: date | None = None,
    ) -> ResearchPageResult:
        url = self._query_url(page, date_start, date_end)
        text = self.client.get_text(
            url,
            accept=JSON_ACCEPT,
            headers={"Referer": self.REFERER},
        )
        payload = parse_jsonp_payload(text, source_label="上交所公告")
        items, total = parse_sse_announcement_page(
            payload,
            source_key=self.config.key,
            source_name=self.config.name,
            provider_key=self.config.provider_key,
            provider_name=self.config.provider_name,
            kind=self.config.kind,
            now=now,
            base_url=self.config.base_url,
        )
        return ResearchPageResult(
            page=page,
            url=url,
            items=tuple(items),
            exhausted=not items and total == 0,
            total=total,
        )


class BseAnnouncementSource:
    """北交所上市公司公告列表适配器（官网 JSONP，免登录）。

    ``disclosure/announcement.html`` uses
    ``disclosureInfoController/initDisclosureList.do`` for its initial and
    paginated result set.  The public page is 0-based and submits repeated
    ``xxfcbj[]``/``needFields[]`` form fields.  The adapter mirrors that
    request contract and keeps the local source interface 1-based.
    """

    QUERY_URL = (
        "https://www.bse.cn/disclosureInfoController/initDisclosureList.do"
    )
    REFERER = "https://www.bse.cn/disclosure/announcement.html"
    NEED_FIELDS = (
        "companyCd",
        "companyName",
        "disclosureTitle",
        "disclosurePostTitle",
        "destFilePath",
        "publishDate",
        "xxfcbj",
        "destFilePath",
        "fileExt",
        "xxzrlx",
    )

    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def _payload(
        self, page: int, date_start: date | None, date_end: date | None
    ) -> dict[str, object]:
        return {
            "siteId": "6",
            "flag": "0",
            "disclosureType": "",
            "disclosureSubtype[]": ("",),
            "page": str(max(page - 1, 0)),
            "companyCd": "",
            "isNewThree": "1",
            "startTime": date_start.isoformat() if date_start else "",
            "endTime": date_end.isoformat() if date_end else "",
            "keyword": "",
            "hyType": "",
            # xxfcbj=2 is the BSE listed-company universe selected by the page.
            "xxfcbj[]": ("2",),
            "needFields[]": self.NEED_FIELDS,
        }

    def fetch_page(
        self,
        page: int,
        now: datetime,
        *,
        date_start: date | None = None,
        date_end: date | None = None,
    ) -> ResearchPageResult:
        url = f"{self.QUERY_URL}?callback=cb"
        text = self.client.post_form_text(
            url,
            self._payload(page, date_start, date_end),
            accept=JSON_ACCEPT,
            headers={"Referer": self.REFERER},
        )
        payload = parse_jsonp_payload(text, source_label="北交所公告")
        items, total = parse_bse_announcement_page(
            payload,
            source_key=self.config.key,
            source_name=self.config.name,
            provider_key=self.config.provider_key,
            provider_name=self.config.provider_name,
            kind=self.config.kind,
            now=now,
            base_url=self.config.base_url,
        )
        return ResearchPageResult(
            page=page,
            url=url,
            items=tuple(items),
            exhausted=not items and total == 0,
            total=total,
        )


class BsePerformanceSource:
    """北交所业绩说明会/投资者关系活动列表适配器（JSONP，免登录）。

    ``performanceController/list.do`` serves 业绩说明会、集体接待日等投资者
    关系活动（``ssgs=2`` 北交所上市公司），分页字段为 ``page``/``pageSize``，
    响应为 ``[{"listInfo":{"content":[...],"totalElements":N}}]``。结构异常、
    非 JSONP 或首屏空数据由解析层/同步层失败关闭。
    """

    PAGE_SIZE = 20
    QUERY_URL = "https://www.bse.cn/performanceController/list.do"

    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def _payload(
        self, page: int, date_start: date | None, date_end: date | None
    ) -> dict[str, object]:
        return {
            "page": str(page),
            "pageSize": str(self.PAGE_SIZE),
            "stockCode": "",
            "startDate": date_start.isoformat() if date_start else "",
            "endDate": date_end.isoformat() if date_end else "",
            "type": "",
            "ssgs": "2",
            "needPic": "1",
        }

    def fetch_page(
        self,
        page: int,
        now: datetime,
        *,
        date_start: date | None = None,
        date_end: date | None = None,
    ) -> ResearchPageResult:
        url = f"{self.QUERY_URL}?callback=cb"
        text = self.client.post_form_text(
            url,
            self._payload(page, date_start, date_end),
            accept=JSON_ACCEPT,
        )
        payload = parse_jsonp_payload(text, source_label="北交所业绩说明会")
        items, total = parse_bse_performance_page(
            payload,
            source_key=self.config.key,
            source_name=self.config.name,
            provider_key=self.config.provider_key,
            provider_name=self.config.provider_name,
            kind=self.config.kind,
            now=now,
            base_url=self.config.base_url,
        )
        return ResearchPageResult(
            page=page,
            url=url,
            items=tuple(items),
            exhausted=not items and total == 0,
            total=total,
        )


def research_source(
    config: SourceConfig, client: PoliteHttpClient
) -> (
    CninfoSource
    | SsePublishSource
    | IrmIrcsSource
    | SseAnnouncementSource
    | BseAnnouncementSource
    | BsePerformanceSource
):
    if config.adapter == "sse_publish":
        return SsePublishSource(config, client)
    if config.adapter == "irm_ircs":
        return IrmIrcsSource(config, client)
    if config.adapter == "sse_announcement":
        return SseAnnouncementSource(config, client)
    if config.adapter == "bse_announcement":
        return BseAnnouncementSource(config, client)
    if config.adapter == "bse_performance":
        return BsePerformanceSource(config, client)
    return CninfoSource(config, client)


class SseCalendarSource:
    """上交所年度休市安排 adapter.

    Fetches the closed-market schedule page and parses the holiday dates;
    :class:`~ashare_hotpot.trading_calendar.TradingCalendarService` persists
    and serves the resulting trading-day cache.
    """

    def __init__(self, client: PoliteHttpClient) -> None:
        self.client = client

    def fetch_holidays(self) -> tuple[int, tuple[date, ...]]:
        html = self.client.get_text(SSE_CLOSED_URL)
        return parse_sse_closed_html(html)
