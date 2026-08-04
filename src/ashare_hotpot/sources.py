from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from .config import AppSettings, SourceConfig
from .models import ArticleCandidate
from .parsing import decode_html, parse_list_page


class RefreshCancelled(RuntimeError):
    pass


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
                "Accept": "text/html,application/xhtml+xml",
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

    def get_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self._settings.request_retries):
            if self._cancel_event.is_set():
                raise RefreshCancelled("刷新已取消")
            try:
                self._wait_for_slot()
                response = self._client.get(url)
                response.raise_for_status()
                return decode_html(response.content, response.headers.get("content-type", ""))
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


@dataclass(frozen=True, slots=True)
class PageResult:
    page: int
    url: str
    items: tuple[ArticleCandidate, ...]


class NewsSource:
    def __init__(self, config: SourceConfig, client: PoliteHttpClient) -> None:
        self.config = config
        self.client = client

    def page_url(self, page: int) -> str:
        if page <= 1:
            return self.config.base_url
        return f"{self.config.base_url}index_{page}.shtml"

    def fetch_page(self, page: int, now: datetime) -> PageResult:
        url = self.page_url(page)
        html = self.client.get_text(url)
        items = parse_list_page(
            html,
            source_key=self.config.key,
            source_name=self.config.name,
            base_url=self.config.base_url,
            now=now,
        )
        return PageResult(page=page, url=url, items=tuple(items))
