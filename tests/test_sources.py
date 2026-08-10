from __future__ import annotations

from datetime import datetime

from ashare_hotpot.config import SHANGHAI_TZ, SourceConfig
from ashare_hotpot.sources import Http404Error, NewsSource, PageResult


LIST_HTML = """
<html><body><ul class="list-con">
  <li><a class="news-link" title="标题一" href="http://stock.10jqka.com.cn/20260804/c111.shtml">标题一</a><span>08月04日 10:00</span></li>
</ul></body></html>
"""

EMPTY_HTML = "<html><body><ul class='list-con'></ul></body></html>"


class StubClient:
    def __init__(self, pages: dict[str, str | Exception]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get_text(self, url: str, *, accept: str = "") -> str:
        self.calls.append(url)
        if url in self.pages:
            value = self.pages[url]
            if isinstance(value, Exception):
                raise value
            return value
        return EMPTY_HTML


def _source() -> NewsSource:
    config = SourceConfig("test", "测试栏目", "https://example.test/list/")
    return NewsSource(config, StubClient({}))  # type: ignore[arg-type]


def test_fetch_archive_page_uses_previous_day_url() -> None:
    source = _source()
    archive_url = "https://example.test/list/20260804/"
    source.client.pages[archive_url] = LIST_HTML  # type: ignore[attr-defined]
    now = datetime(2026, 8, 5, 22, 0, tzinfo=SHANGHAI_TZ)
    result = source.fetch_page(21, now)
    assert isinstance(result, PageResult)
    assert result.url == archive_url
    assert [item.seq for item in result.items] == ["111"]
    assert source.client.calls == [archive_url]  # type: ignore[attr-defined]


def test_fetch_archive_skips_empty_days() -> None:
    source = _source()
    empty = "https://example.test/list/20260804/"
    filled = "https://example.test/list/20260803/"
    source.client.pages[empty] = EMPTY_HTML  # type: ignore[attr-defined]
    source.client.pages[filled] = LIST_HTML  # type: ignore[attr-defined]
    now = datetime(2026, 8, 5, 22, 0, tzinfo=SHANGHAI_TZ)
    result = source.fetch_page(21, now)
    assert result.url == filled
    assert [item.seq for item in result.items] == ["111"]
    # the cursor advanced past the empty day, so page 22 goes one day further
    next_result = source.fetch_page(22, now)
    assert next_result.url.startswith("https://example.test/list/2026")
    assert next_result.items == ()


def test_deep_list_page_404_returns_empty() -> None:
    source = _source()
    url = "https://example.test/list/index_5.shtml"
    source.client.pages[url] = Http404Error("页面不存在")  # type: ignore[attr-defined]
    now = datetime(2026, 8, 5, 22, 0, tzinfo=SHANGHAI_TZ)
    result = source.fetch_page(5, now)
    assert result.url == url
    assert result.items == ()


def test_first_page_404_propagates() -> None:
    source = _source()
    source.client.pages["https://example.test/list/"] = Http404Error("页面不存在")  # type: ignore[attr-defined]
    now = datetime(2026, 8, 5, 22, 0, tzinfo=SHANGHAI_TZ)
    try:
        source.fetch_page(1, now)
    except Http404Error:
        return
    raise AssertionError("first-page 404 should propagate")
