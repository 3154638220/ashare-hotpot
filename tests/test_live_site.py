from __future__ import annotations

import os
import threading
from datetime import datetime

import pytest

from ashare_hotpot.config import AppSettings, DEFAULT_SOURCES, SHANGHAI_TZ
from ashare_hotpot.parsing import parse_article_detail
from ashare_hotpot.sources import NewsSource, PoliteHttpClient


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ASHARE_HOTPOT_LIVE_TEST") != "1",
        reason="set ASHARE_HOTPOT_LIVE_TEST=1 to access the live site",
    ),
]


def test_live_company_news_page_and_article() -> None:
    settings = AppSettings(request_retries=1, request_timeout_seconds=20)
    with PoliteHttpClient(settings, threading.Event()) as client:
        page = NewsSource(DEFAULT_SOURCES[0], client).fetch_page(1, datetime.now(SHANGHAI_TZ))
        assert 1 <= len(page.items) <= 50
        parsed = [parse_article_detail(candidate, client.get_text(candidate.url)) for candidate in page.items[:10]]
    assert any(article.stocks for article in parsed)

