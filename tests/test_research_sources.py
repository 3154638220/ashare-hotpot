from __future__ import annotations

import json
import threading
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ, SourceConfig
from ashare_hotpot.models import SyncCursor
from ashare_hotpot.parsing import (
    parse_cninfo_page,
    parse_irm_ircs_page,
    parse_sse_publish_feed,
)
from ashare_hotpot.pdf import (
    PDF_EMPTY_TEXT,
    cleanup_stale_pdf_temp,
    extract_attachment_text,
    extract_doc_text,
    extract_docx_text,
    extract_pdf_text,
    fetch_and_extract_pdf,
    pdf_parse_status,
    sha256_hex,
)
from ashare_hotpot.research_sync import (
    ResearchSyncResult,
    ResearchSyncService,
    _split_budget,
)
from ashare_hotpot.service import RefreshService
from ashare_hotpot.sources import (
    CninfoSource,
    IrmIrcsSource,
    PoliteHttpClient,
    RefreshCancelled,
    ResearchPageResult,
    SsePublishSource,
)
from ashare_hotpot.storage import Storage

from _office_fixtures import build_legacy_doc


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _load_json(name: str) -> dict[str, object]:
    return json.loads(_load(name))


# ---------------------------------------------------------------------------
# 巨潮资讯公告/调研列表解析契约
# ---------------------------------------------------------------------------


def test_parse_cninfo_announcement_page() -> None:
    items, total = parse_cninfo_page(
        _load_json("cninfo_page.json"),
        source_key="cninfo_announcement",
        source_name="巨潮资讯公告",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        now=NOW,
        base_url="https://www.cninfo.com.cn/new/hisAnnouncement/query",
    )
    assert total == 355474
    assert len(items) == 3

    by_id = {item.document_id: item for item in items}
    first = by_id["cninfo:1225461893"]
    assert first.kind == "announcement"
    assert first.title == "关于控股股东股份被轮候冻结的公告"
    assert first.stock_codes == ("600180",)
    assert first.stock_names == {"600180": "*ST瑞茂"}
    assert first.published_at == datetime(2026, 8, 7, 0, 0, tzinfo=SHANGHAI_TZ)
    assert first.document_url == (
        "https://static.cninfo.com.cn/finalpage/2026-08-07/1225461893.PDF"
    )
    assert first.attachment_type == "PDF"
    assert first.source_url == "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    assert first.provider_key == "cninfo"

    # 无 PDF 附件的公告保留元数据，document_url 为空。
    no_adjunct = by_id["cninfo:1225461752"]
    assert no_adjunct.document_url is None
    assert no_adjunct.attachment_type is None
    assert no_adjunct.stock_codes == ("000001",)
    assert no_adjunct.stock_names == {"000001": "平安银行"}


def test_parse_cninfo_research_page_kind_and_time() -> None:
    items, total = parse_cninfo_page(
        _load_json("cninfo_research_page.json"),
        source_key="cninfo_research",
        source_name="巨潮资讯调研",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="research_activity",
        now=NOW,
        base_url="https://www.cninfo.com.cn/new/disclosure",
    )
    assert total == 75
    assert len(items) == 2
    assert all(item.kind == "research_activity" for item in items)
    assert all(item.document_id.startswith("cninfo:") for item in items)
    first = items[0]
    assert first.title == "关于终止重大资产重组投资者说明会的投资者活动记录表"
    assert first.stock_codes == ("300423",)
    assert first.stock_names == {"300423": "昇辉科技"}
    assert first.attachment_type == "PDF"
    assert first.published_at.tzinfo is not None


def test_parse_cninfo_empty_page_returns_no_items() -> None:
    items, total = parse_cninfo_page(
        _load_json("cninfo_empty_page.json"),
        source_key="cninfo_announcement",
        source_name="巨潮资讯公告",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        now=NOW,
        base_url="https://www.cninfo.com.cn/new/hisAnnouncement/query",
    )
    assert items == []
    assert total == 0


def test_parse_cninfo_structure_break_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="结构异常"):
        parse_cninfo_page(
            _load_json("cninfo_structure_break.json"),
            source_key="cninfo_announcement",
            source_name="巨潮资讯公告",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            now=NOW,
            base_url="https://www.cninfo.com.cn/new/hisAnnouncement/query",
        )


# ---------------------------------------------------------------------------
# 深交所互动易“投资者关系活动”公开流（searchTypes=4）
# ---------------------------------------------------------------------------


def _irm_ircs_config():
    from ashare_hotpot.config import SourceConfig

    return SourceConfig(
        "irm_ircs",
        "互动易投资者关系",
        "https://irm.cninfo.com.cn/newircs/index/search",
        adapter="irm_ircs",
        provider_key="irm",
        provider_name="深交所互动易",
        kind="research_activity",
    )


def test_parse_irm_ircs_page_items() -> None:
    items, total = parse_irm_ircs_page(
        _load_json("irm_ircs_page.json"),
        source_key="irm_ircs",
        source_name="互动易投资者关系",
        provider_key="irm",
        provider_name="深交所互动易",
        now=NOW,
        base_url="https://irm.cninfo.com.cn/newircs/index/search",
    )
    assert total == 245
    assert len(items) == 3
    assert all(item.kind == "research_activity" for item in items)
    assert all(item.document_id.startswith("irm_ircs:") for item in items)
    by_id = {item.document_id: item for item in items}
    first = by_id["irm_ircs:1225464497"]
    assert first.title == "000933神火股份投资者关系管理信息20260806"
    assert first.stock_codes == ("000933",)
    assert first.stock_names == {"000933": "神火股份"}
    assert first.published_at == datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)
    assert first.document_url == (
        "https://static.cninfo.com.cn/finalpage/2026-08-06/1225464497.PDF"
    )
    assert first.attachment_type == "PDF"
    assert first.provider_name == "深交所互动易"
    activity = by_id["irm_ircs:1225464250"]
    assert activity.title == "2026年8月4日-2026年8月5日投资者关系活动记录表"
    assert activity.stock_codes == ("301270",)


def test_parse_irm_ircs_empty_page_returns_no_items() -> None:
    items, total = parse_irm_ircs_page(
        _load_json("irm_ircs_empty_page.json"),
        source_key="irm_ircs",
        source_name="互动易投资者关系",
        provider_key="irm",
        provider_name="深交所互动易",
        now=NOW,
        base_url="https://irm.cninfo.com.cn/newircs/index/search",
    )
    assert items == []
    assert total == 0


def test_parse_irm_ircs_structure_break_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="结构异常"):
        parse_irm_ircs_page(
            _load_json("irm_ircs_structure_break.json"),
            source_key="irm_ircs",
            source_name="互动易投资者关系",
            provider_key="irm",
            provider_name="深交所互动易",
            now=NOW,
            base_url="https://irm.cninfo.com.cn/newircs/index/search",
        )


def test_irm_ircs_source_sends_query_params_and_parses() -> None:
    url = "https://irm.cninfo.com.cn/newircs/index/search"
    query = urllib.parse.urlencode(
        {
            "stockCodes": "",
            "keywords": "",
            "searchTypes": "4",
            "startDate": "2026-01-01 00:00:00",
            "endDate": "2026-08-06 23:59:59",
            "pageNo": "1",
            "pageSize": "30",
            "onlyAttentionCompany": "2",
        }
    )
    client = StubClient(
        post_map={(url + "?" + query, "1"): _load_json("irm_ircs_page.json")}
    )
    source = IrmIrcsSource(_irm_ircs_config(), client)
    result = source.fetch_page(
        1,
        NOW,
        date_start=datetime(2026, 1, 1, tzinfo=SHANGHAI_TZ).date(),
        date_end=datetime(2026, 8, 6, tzinfo=SHANGHAI_TZ).date(),
    )
    assert isinstance(result, ResearchPageResult)
    assert len(result.items) == 3
    assert result.exhausted is False
    assert client.post_calls


def test_irm_ircs_source_empty_page_is_exhausted() -> None:
    url = "https://irm.cninfo.com.cn/newircs/index/search"
    query = urllib.parse.urlencode(
        {
            "stockCodes": "",
            "keywords": "",
            "searchTypes": "4",
            "startDate": "",
            "endDate": "",
            "pageNo": "1",
            "pageSize": "30",
            "onlyAttentionCompany": "2",
        }
    )
    client = StubClient(
        post_map={(url + "?" + query, "1"): _load_json("irm_ircs_empty_page.json")}
    )
    source = IrmIrcsSource(_irm_ircs_config(), client)
    result = source.fetch_page(1, NOW)
    assert result.items == ()
    assert result.exhausted is True


def test_irm_ircs_source_paginates_on_page_no() -> None:
    """Regression: 线上接口以 pageNo 分页；pageNum 会被忽略并永远返回第一页。

    2026-08-08 真实回填时发现旧实现发送 pageNum，导致 27 页全部返回同一批
    30 条记录；本测试锁定请求必须携带 pageNo 且第 2 页返回不同文档。
    """

    url = "https://irm.cninfo.com.cn/newircs/index/search"
    page_two = _load_json("irm_ircs_page.json")
    page_two["pageNo"] = 2
    page_two["results"] = [dict(page_two["results"][0])]
    page_two["results"][0]["indexId"] = "9999999999"
    page_two["results"][0]["esId"] = "9999999999"
    page_two["count"] = 1

    def build_query(page_no: int) -> str:
        return urllib.parse.urlencode(
            {
                "stockCodes": "",
                "keywords": "",
                "searchTypes": "4",
                "startDate": "2026-01-01 00:00:00",
                "endDate": "2026-08-06 23:59:59",
                "pageNo": str(page_no),
                "pageSize": "30",
                "onlyAttentionCompany": "2",
            }
        )

    client = StubClient(
        post_map={
            (url + "?" + build_query(1), "1"): _load_json("irm_ircs_page.json"),
            (url + "?" + build_query(2), "1"): page_two,
        }
    )
    source = IrmIrcsSource(_irm_ircs_config(), client)
    first = source.fetch_page(
        1,
        NOW,
        date_start=datetime(2026, 1, 1, tzinfo=SHANGHAI_TZ).date(),
        date_end=datetime(2026, 8, 6, tzinfo=SHANGHAI_TZ).date(),
    )
    second = source.fetch_page(
        2,
        NOW,
        date_start=datetime(2026, 1, 1, tzinfo=SHANGHAI_TZ).date(),
        date_end=datetime(2026, 8, 6, tzinfo=SHANGHAI_TZ).date(),
    )
    first_ids = {item.document_id for item in first.items}
    second_ids = {item.document_id for item in second.items}
    assert "irm_ircs:9999999999" in second_ids
    assert first_ids.isdisjoint(second_ids)
    # 请求必须携带 pageNo（线上接口忽略 pageNum，会永远返回第一页）。
    assert "pageNo=2" in client.post_calls[1][0]


# ---------------------------------------------------------------------------
# 上证e互动“上市公司发布”解析契约
# ---------------------------------------------------------------------------


def test_parse_sse_publish_feed_items() -> None:
    items = parse_sse_publish_feed(
        _load("sse_publish_feed.html"),
        source_key="sse_publish",
        source_name="上证e互动发布",
        provider_key="sse",
        provider_name="上证e互动",
        now=NOW,
    )
    assert len(items) == 3
    by_id = {item.document_id: item for item in items}

    docx_item = by_id["ssepub:1777290"]
    assert docx_item.kind == "research_activity"
    assert docx_item.stock_codes == ("603459",)
    assert docx_item.stock_names == {"603459": "红板科技"}
    assert docx_item.title == "江西红板科技股份有限公司投资者关系活动记录表0806"
    assert docx_item.published_at == NOW - timedelta(minutes=17)
    assert docx_item.attachment_type == "DOCX"
    assert docx_item.document_url.startswith("https://sns.sseinfo.com/resources/")
    assert "特定对象调研活动" in docx_item.description

    pdf_item = by_id["ssepub:1777268"]
    assert pdf_item.stock_codes == ("600926",)
    assert pdf_item.stock_names == {"600926": "杭州银行"}
    assert pdf_item.attachment_type == "PDF"
    assert pdf_item.published_at == NOW - timedelta(hours=1)
    assert pdf_item.document_url.endswith(".pdf")

    doc_item = by_id["ssepub:1777264"]
    assert doc_item.stock_codes == ("603998",)
    assert doc_item.stock_names == {"603998": "方盛制药"}
    assert doc_item.attachment_type == "DOC"
    assert doc_item.document_url.endswith(".doc")
    assert doc_item.published_at == NOW - timedelta(minutes=38)


def test_parse_sse_publish_empty_feed_is_empty_signal() -> None:
    items = parse_sse_publish_feed(
        _load("sse_publish_empty.html"),
        source_key="sse_publish",
        source_name="上证e互动发布",
        provider_key="sse",
        provider_name="上证e互动",
        now=NOW,
    )
    assert items == []


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------


class StubClient:
    def __init__(
        self,
        *,
        post_map: dict[tuple[str, str], dict[str, object]] | None = None,
        text_map: dict[str, str] | None = None,
    ) -> None:
        self.post_map = post_map or {}
        self.text_map = text_map or {}
        self.post_calls: list[tuple[str, str, dict[str, object]]] = []
        self.text_calls: list[str] = []

    def post_form(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        page = str(payload.get("pageNum") or "1")
        key = (url, page)
        self.post_calls.append((url, page, dict(payload)))
        if key in self.post_map:
            return self.post_map[key]
        raise RuntimeError(f"未配置响应：{key}")

    def post_query(self, url: str, params: dict[str, object]) -> dict[str, object]:
        separator = "&" if "?" in url else "?"
        query = urllib.parse.urlencode(
            {str(key): str(value) for key, value in params.items()}
        )
        return self.post_form(url + separator + query, {})

    def get_text(self, url: str, *, accept: str = "") -> str:
        self.text_calls.append(url)
        for template, value in self.text_map.items():
            if template in url:
                return value
        raise RuntimeError(f"未配置响应：{url}")


def test_cninfo_source_fetch_page_and_date_range() -> None:
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    client = StubClient(post_map={(url, "1"): _load_json("cninfo_page.json")})
    source = CninfoSource(
        _cninfo_config(kind="announcement", base_url=url, column="szse"),
        client,
    )
    result = source.fetch_page(
        1,
        NOW,
        date_start=datetime(2026, 1, 19, tzinfo=SHANGHAI_TZ).date(),
        date_end=datetime(2026, 8, 6, tzinfo=SHANGHAI_TZ).date(),
    )
    assert isinstance(result, ResearchPageResult)
    assert len(result.items) == 3
    assert result.exhausted is False
    posted_url, page, payload = client.post_calls[0]
    assert posted_url == url
    assert page == "1"
    assert payload["pageNum"] == "1"
    assert payload["column"] == "szse"
    assert payload["tabName"] == "fulltext"
    assert payload["seDate"] == "2026-01-19~2026-08-06"


def test_cninfo_research_source_uses_relation_endpoint() -> None:
    url = "https://www.cninfo.com.cn/new/disclosure"
    client = StubClient(post_map={(url, "1"): _load_json("cninfo_research_page.json")})
    source = CninfoSource(
        _cninfo_config(
            kind="research_activity", base_url=url, column="szse_relation"
        ),
        client,
    )
    result = source.fetch_page(1, NOW)
    assert len(result.items) == 2
    assert all(item.kind == "research_activity" for item in result.items)
    _, _, payload = client.post_calls[0]
    assert payload["column"] == "szse_relation"
    assert payload["tabName"] == "relation"
    assert payload["seDate"] == ""


def test_cninfo_empty_page_is_exhausted() -> None:
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    client = StubClient(post_map={(url, "1"): _load_json("cninfo_empty_page.json")})
    source = CninfoSource(
        _cninfo_config(kind="announcement", base_url=url, column="szse"),
        client,
    )
    result = source.fetch_page(1, NOW)
    assert result.items == ()
    assert result.exhausted is True


def test_sse_publish_source_reads_type_30_feed() -> None:
    client = StubClient(text_map={"type=30": _load("sse_publish_feed.html")})
    source = SsePublishSource(_sse_publish_config(), client)
    result = source.fetch_page(1, NOW)
    assert len(result.items) == 3
    assert result.exhausted is False
    assert "type=30" in client.text_calls[0]
    assert "page=1" in client.text_calls[0]


def test_sse_publish_source_empty_feed_is_exhausted() -> None:
    client = StubClient(text_map={"type=30": _load("sse_publish_empty.html")})
    source = SsePublishSource(_sse_publish_config(), client)
    result = source.fetch_page(1, NOW)
    assert result.items == ()
    assert result.exhausted is True


def _cninfo_config(kind: str, base_url: str, column: str):
    from ashare_hotpot.config import SourceConfig

    return SourceConfig(
        "cninfo_announcement" if kind == "announcement" else "cninfo_research",
        "巨潮资讯公告" if kind == "announcement" else "巨潮资讯调研",
        base_url,
        adapter="cninfo",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        column=column,
        tab_name="fulltext" if kind == "announcement" else "relation",
        kind=kind,
    )


def _sse_publish_config():
    from ashare_hotpot.config import SourceConfig

    return SourceConfig(
        "sse_publish",
        "上证e互动发布",
        "https://sns.sseinfo.com/ajax/feeds.do",
        adapter="sse_publish",
        provider_key="sse",
        provider_name="上证e互动",
        kind="research_activity",
    )


# ---------------------------------------------------------------------------
# PDF 哈希、文本提取、临时文件与失败状态
# ---------------------------------------------------------------------------


def _pdf_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_extract_pdf_text_parses_sample_document() -> None:
    content = _pdf_bytes("sample_announcement.pdf")
    result = extract_pdf_text(content)
    assert result.error is None
    assert result.page_count == 1
    assert result.content_hash == sha256_hex(content)
    assert "Investor Relations Activity Record" in result.text
    assert "Q4" in result.text


def test_extract_pdf_text_empty_document_is_empty_text() -> None:
    content = _pdf_bytes("empty_scanned.pdf")
    result = extract_pdf_text(content)
    assert result.error == PDF_EMPTY_TEXT
    assert result.text == ""
    assert result.page_count == 1
    assert pdf_parse_status(result) == ("empty_text", "PDF 未提取到文本（可能为扫描件或空文档）")


def test_extract_pdf_text_corrupt_document_fails() -> None:
    content = _pdf_bytes("corrupt.pdf")
    result = extract_pdf_text(content)
    assert result.error is not None
    assert result.error != PDF_EMPTY_TEXT
    assert result.page_count is None
    status, message = pdf_parse_status(result)
    assert status == "failed"
    assert message == result.error


def test_fetch_and_extract_pdf_uses_temp_and_deletes(tmp_path) -> None:
    class BytesClient:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def get_bytes(self, url: str, *, accept: str = "") -> bytes:
            return self.payload

    content = _pdf_bytes("sample_announcement.pdf")
    temp_dir = tmp_path / "pdf_tmp"
    result = fetch_and_extract_pdf(
        BytesClient(content),  # type: ignore[arg-type]
        "https://example.test/a.pdf",
        temp_dir,
        threading.Event(),
    )
    assert result.error is None
    assert result.content_hash == sha256_hex(content)
    # 原始 PDF 处理结束后必须删除，不留缓存。
    assert list(temp_dir.iterdir()) == []


def test_cleanup_stale_pdf_temp_removes_old_files_only(tmp_path) -> None:
    temp_dir = tmp_path / "pdf_tmp"
    temp_dir.mkdir()
    stale = temp_dir / "stale.pdf"
    fresh = temp_dir / "fresh.pdf"
    stale.write_bytes(b"old")
    fresh.write_bytes(b"new")
    old = datetime.now(SHANGHAI_TZ) - timedelta(days=3)
    import os

    os.utime(stale, (old.timestamp(), old.timestamp()))
    now = datetime.now(SHANGHAI_TZ)
    assert cleanup_stale_pdf_temp(temp_dir, now) == 1
    assert not stale.exists()
    assert fresh.exists()


def test_get_bytes_rejects_html_identity_pages(monkeypatch) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html;charset=UTF-8"},
            content=b"<html><body>login</body></html>",
        )
    )

    def fake_client(*_args, **_kwargs) -> httpx.Client:
        return real_client(transport=transport)

    monkeypatch.setattr("ashare_hotpot.sources.httpx.Client", fake_client)
    settings = AppSettings(request_retries=1, minimum_request_interval_seconds=0)
    with PoliteHttpClient(settings, threading.Event()) as client:
        with pytest.raises(RuntimeError, match="非预期响应类型"):
            client.get_bytes("https://example.test/a.pdf")


def test_get_bytes_downloads_pdf_and_404_propagates(monkeypatch) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=_pdf_bytes("sample_announcement.pdf"),
        )
    )

    def fake_client(*_args, **_kwargs) -> httpx.Client:
        return real_client(transport=transport)

    monkeypatch.setattr("ashare_hotpot.sources.httpx.Client", fake_client)
    settings = AppSettings(request_retries=1, minimum_request_interval_seconds=0)
    with PoliteHttpClient(settings, threading.Event()) as client:
        content = client.get_bytes("https://example.test/a.pdf")
        assert content.startswith(b"%PDF")

    transport_404 = httpx.MockTransport(
        lambda request: httpx.Response(404, content=b"not found")
    )

    def fake_client_404(*_args, **_kwargs) -> httpx.Client:
        return real_client(transport=transport_404)

    monkeypatch.setattr("ashare_hotpot.sources.httpx.Client", fake_client_404)
    settings = AppSettings(request_retries=1, minimum_request_interval_seconds=0)
    from ashare_hotpot.sources import Http404Error

    with PoliteHttpClient(settings, threading.Event()) as client:
        with pytest.raises(Http404Error):
            client.get_bytes("https://example.test/missing.pdf")


# ---------------------------------------------------------------------------
# 渐进回填：恢复、取消、页级原子提交与预算
# ---------------------------------------------------------------------------


class ResearchStubClient:
    """Stub for the sync service: cninfo JSON pages + PDF bytes by URL."""

    def __init__(
        self,
        *,
        pages: dict[int, dict[str, object]] | None = None,
        pages_by_url: dict[str, dict[int, dict[str, object]]] | None = None,
        page_errors: dict[int, Exception] | None = None,
        pdfs: dict[str, bytes] | None = None,
        text_map: dict[str, str] | None = None,
    ) -> None:
        self.pages = pages or {}
        self.pages_by_url = pages_by_url or {}
        self.page_errors = page_errors or {}
        self.pdfs = pdfs or {}
        self.text_map = text_map or {}
        self.post_calls: list[tuple[str, int]] = []
        self.text_calls: list[str] = []
        self.get_bytes_calls: list[str] = []

    def post_form(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        page = int(str(payload["pageNum"]))
        self.post_calls.append((url, page))
        if page in self.page_errors:
            raise self.page_errors[page]
        if url in self.pages_by_url:
            return self.pages_by_url[url].get(page, _load_json("cninfo_empty_page.json"))
        return self.pages.get(page, _load_json("cninfo_empty_page.json"))

    def get_text(self, url: str, *, accept: str = "") -> str:
        self.text_calls.append(url)
        for template, value in self.text_map.items():
            if template in url:
                return value
        return _load("sse_publish_empty.html")

    def get_bytes(self, url: str, *, accept: str = "") -> bytes:
        self.get_bytes_calls.append(url)
        return self.pdfs.get(url, b"")

    def __enter__(self) -> "ResearchStubClient":
        return self

    def __exit__(self, *_args: object) -> None:
        pass


def _research_settings(
    tmp_path,
    *,
    sources: tuple[SourceConfig, ...],
    max_pages: int = 20,
    max_pdfs: int = 50,
    backfill_days: int = 200,
) -> AppSettings:
    return AppSettings(
        app_root=tmp_path,
        sources=(),
        interaction_sources=(),
        research_sources=sources,
        research_max_pages_per_run=max_pages,
        research_max_pdfs_per_run=max_pdfs,
        backfill_days=backfill_days,
        minimum_request_interval_seconds=0,
        request_retries=1,
    )


def _cninfo_announcement_source() -> SourceConfig:
    return SourceConfig(
        "cninfo_announcement",
        "巨潮资讯公告",
        "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        adapter="cninfo",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        column="szse",
        kind="announcement",
    )


def _sample_pdf_url() -> str:
    return "https://static.cninfo.com.cn/finalpage/2026-08-07/1225461893.PDF"


def _sample_pdf_url2() -> str:
    return "https://static.cninfo.com.cn/finalpage/2026-08-06/1225461816.PDF"


def test_backfill_persists_page_advances_cursor_and_extracts_pdfs(
    tmp_path,
) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={1: _priority_page(), 2: _load_json("cninfo_empty_page.json")},
        pdfs={
            _sample_pdf_url(): _pdf_bytes("sample_announcement.pdf"),
            _sample_pdf_url2(): _pdf_bytes("sample_announcement.pdf"),
        },
    )
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.documents_added == 3
    assert result.documents_skipped == 0
    assert result.pdfs_consumed == 2
    assert result.pdf_failures == 0
    assert result.pages_consumed == 2
    assert result.budget_exhausted is False
    assert len(client.get_bytes_calls) == 2

    parsed = storage.get_source_document("cninfo:1225461893")
    assert parsed is not None
    assert parsed.parse_status == "parsed"
    assert parsed.page_count == 1
    assert "Investor Relations Activity Record" in parsed.body_text
    assert parsed.stock_codes == ("600180",)
    assert parsed.stock_names == {"600180": "*ST瑞茂"}

    metadata_only = storage.get_source_document("cninfo:1225461752")
    assert metadata_only is not None
    assert metadata_only.parse_status == "metadata_only"
    assert metadata_only.document_url is None

    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor is not None
    # 顶部回扫先消费两页（第 1 页新文档 + 第 2 页空页结束），历史回填游标
    # 保持在第 1 页；下一次刷新回扫命中全已知页后，回填从第 2 页继续。
    assert cursor.cursor == {"page": 1, "covered_end": "2026-08-07"}
    assert "fresh_page" not in cursor.cursor
    assert cursor.covered_start == datetime(2026, 8, 6, tzinfo=SHANGHAI_TZ).date()
    assert cursor.last_success_at == NOW

    coverage = result.coverages[0]
    assert coverage.source_key == "cninfo_announcement"
    assert coverage.requested_start == datetime(2026, 1, 18, tzinfo=SHANGHAI_TZ).date()
    assert coverage.covered_start == datetime(2026, 8, 6, tzinfo=SHANGHAI_TZ).date()
    assert coverage.covered_end == datetime(2026, 8, 7, tzinfo=SHANGHAI_TZ).date()
    assert coverage.reached_cutoff is True
    assert coverage.error is None


def test_backfill_resume_skips_known_documents_and_never_redownloads(
    tmp_path,
) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={1: _priority_page(), 2: _load_json("cninfo_empty_page.json")},
        pdfs={_sample_pdf_url(): _pdf_bytes("sample_announcement.pdf")},
    )
    service = ResearchSyncService(settings, storage)

    first = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert first.documents_added == 3
    assert len(client.get_bytes_calls) == 2

    # 下一次运行：第 2 页重新列出了已完成的文档（游标重叠场景）。
    client.pages[2] = _priority_page()
    client.get_bytes_calls.clear()
    client.post_calls.clear()
    second = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    # 恢复后先从第 1 页做顶部回扫（全已知页即前沿），再从回填游标继续：
    # 第 2 页整体重复列出已完成的文档（循环分页/回绕）→ 视为已到流末端并停止，
    # 已完成的 PDF 绝不重新下载。
    # 两页均为已知文档（第 1 页回扫 3 条 + 第 2 页回绕 3 条）。
    assert second.documents_skipped == 6
    assert second.documents_added == 0
    assert second.pdfs_consumed == 0
    assert client.get_bytes_calls == []
    assert client.post_calls == [
        ("https://www.cninfo.com.cn/new/hisAnnouncement/query", 1),
        ("https://www.cninfo.com.cn/new/hisAnnouncement/query", 2),
    ]
    assert second.coverages[0].reached_cutoff is True
    # 已解析的 PDF 正文仍然存在。
    assert storage.get_source_document("cninfo:1225461893").parse_status == "parsed"


def test_backfill_circular_pagination_stops_at_fully_known_page(tmp_path) -> None:
    """Regression: 线上接口在真实流末端之后会回绕返回已见内容（循环分页）。

    2026-08-08 互动易投资者关系流真实回填时发现：pageNo 超过真实末尾后，
    接口重新返回第一页内容；若整页均为已持久化文档仍继续翻页，将无限消耗
    预算且永不结束。整页已知（且位于前沿之后）必须视为已到流末端。
    """

    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={
            1: _priority_page(),
            2: _priority_page(),
            3: _priority_page(),
            4: _priority_page(),
        },
        pdfs={_sample_pdf_url(): _pdf_bytes("sample_announcement.pdf")},
    )
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    # 第 1 页新文档 → 第 2 页全已知即前沿 → 回填第 3 页又见同一批内容（回绕）
    # → 停止，不再翻第 4 页。
    assert result.pages_consumed == 3
    assert result.documents_added == 3
    assert result.budget_exhausted is False
    assert result.coverages[0].reached_cutoff is True
    assert result.coverages[0].error is None
    assert client.post_calls == [
        ("https://www.cninfo.com.cn/new/hisAnnouncement/query", 1),
        ("https://www.cninfo.com.cn/new/hisAnnouncement/query", 2),
        ("https://www.cninfo.com.cn/new/hisAnnouncement/query", 3),
    ]
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor is not None
    assert cursor.last_error is None


def test_backfill_backfills_names_for_already_persisted_documents(tmp_path) -> None:
    """Documents persisted before research sources carried stock names must
    receive them on the next sync without being re-inserted or re-downloaded."""

    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={1: _priority_page(), 2: _load_json("cninfo_empty_page.json")},
        pdfs={_sample_pdf_url(): _pdf_bytes("sample_announcement.pdf")},
    )
    service = ResearchSyncService(settings, storage)
    first = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert first.documents_added == 3

    # Simulate a database created by an earlier build: names were not stored.
    with storage._connect() as connection:
        connection.execute("UPDATE source_document_stocks SET stock_name = NULL")
    assert storage.get_source_document("cninfo:1225461893").stock_names == {}

    client.pages[2] = _priority_page()
    client.get_bytes_calls.clear()
    client.post_calls.clear()
    second = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert second.documents_skipped == 6
    assert second.documents_added == 0
    assert client.get_bytes_calls == []

    restored = storage.get_source_document("cninfo:1225461893")
    assert restored is not None
    assert restored.stock_names == {"600180": "*ST瑞茂"}
    assert storage.get_stock_names({"600180"})["600180"] == "*ST瑞茂"


def test_backfill_preserves_full_covered_date_range_across_pages(tmp_path) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    newest_page = _load_json("cninfo_page.json")
    older_page = _load_json("cninfo_page.json")
    for page, day_offset, prefix in (
        (newest_page, 0, "new"),
        (older_page, 40, "old"),
    ):
        for index, item in enumerate(page["announcements"]):
            item["announcementId"] = f"{prefix}-{index}"
            item["adjunctUrl"] = ""
            item["adjunctType"] = None
            if day_offset:
                published = NOW - timedelta(days=day_offset + index)
                item["announcementTime"] = int(published.timestamp() * 1000)
    client = ResearchStubClient(
        pages={
            1: newest_page,
            2: older_page,
            3: _load_json("cninfo_empty_page.json"),
        }
    )

    result = ResearchSyncService(settings, storage).sync_once(
        now=NOW,
        cancel=threading.Event(),
        client=client,
    )

    coverage = result.coverages[0]
    assert coverage.covered_start == (NOW - timedelta(days=42)).date()
    assert coverage.covered_end == date(2026, 8, 7)
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.cursor["covered_end"] == "2026-08-07"


def test_backfill_cancel_keeps_committed_pages_and_resumes(tmp_path) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={1: _priority_page()},
        page_errors={2: RefreshCancelled("刷新已取消")},
        pdfs={_sample_pdf_url(): _pdf_bytes("sample_announcement.pdf")},
    )
    service = ResearchSyncService(settings, storage)

    with pytest.raises(RefreshCancelled):
        service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    # 已提交的第 1 页保留；顶部回扫游标停在下一页，历史回填游标不动；
    # 附件尚未下载（附件工作队列在页面扫描之后运行，取消时未执行）。
    assert storage.get_source_document("cninfo:1225461893") is not None
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.cursor == {
        "page": 1,
        "fresh_page": 2,
        "covered_end": "2026-08-07",
    }

    # 恢复：顶部回扫第 1 页全已知（候选已在队列中，不再重复入队），随后回填
    # 从第 2 页继续并命中全已知前沿，第 3 页空页结束；恢复后附件工作队列
    # 补下载 2 份排队中的 PDF（可恢复性：取消不永久跳过附件）。
    client.page_errors.clear()
    client.pages[2] = _priority_page()
    client.get_bytes_calls.clear()
    resumed = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert resumed.documents_skipped == 6
    assert resumed.documents_added == 0
    assert resumed.pdfs_consumed == 2
    assert len(client.get_bytes_calls) == 2
    assert (
        storage.get_source_document("cninfo:1225461893").parse_status == "parsed"
    )
    cursor_after = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor_after.cursor == {"page": 3, "covered_end": "2026-08-07"}


def test_backfill_respects_pdf_budget(tmp_path) -> None:
    settings = _research_settings(
        tmp_path, sources=(_cninfo_announcement_source(),), max_pdfs=1
    )
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={1: _priority_page()},
        pdfs={
            _sample_pdf_url(): _pdf_bytes("sample_announcement.pdf"),
            _sample_pdf_url2(): _pdf_bytes("sample_announcement.pdf"),
        },
    )
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.pdfs_consumed == 1
    assert result.documents_added == 3
    assert result.discoveries_added == 3
    assert result.budget_exhausted is True
    assert len(client.get_bytes_calls) == 1
    # 高优先级（信号标题）文档先入队下载：“关于签订重大合同的公告”已解析；
    # 预算耗尽的“2026年半年度报告”保留待解析，仅标记延后而非永久跳过。
    priority = storage.get_source_document("cninfo:1225461816")
    assert priority.parse_status == "parsed"
    deferred = storage.get_source_document("cninfo:1225461893")
    assert deferred.parse_status == "metadata_only"
    pending = storage.get_discovery_candidates(
        source_key="cninfo_announcement", statuses=("pending_attachment",)
    )
    assert [candidate.document_id for candidate in pending] == ["cninfo:1225461893"]
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.cursor == {"page": 1, "covered_end": "2026-08-07"}


def test_backfill_upgrades_metadata_only_pdf_when_budget_available(
    tmp_path,
) -> None:
    settings = _research_settings(
        tmp_path, sources=(_cninfo_announcement_source(),), max_pdfs=1
    )
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={1: _priority_page()},
        pdfs={
            _sample_pdf_url(): _pdf_bytes("sample_announcement.pdf"),
            _sample_pdf_url2(): _pdf_bytes("sample_announcement.pdf"),
        },
    )
    service = ResearchSyncService(settings, storage)

    first = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert first.pdfs_consumed == 1
    assert storage.get_source_document("cninfo:1225461816").parse_status == "parsed"
    assert storage.get_source_document("cninfo:1225461893").parse_status == "metadata_only"

    # 第二次刷新预算充足时，附件工作队列为延后的 PDF 文档补下载正文；
    # 页面回扫不再重复入队（候选已处于待解析状态）。
    client.get_bytes_calls.clear()
    second_settings = _research_settings(
        tmp_path, sources=(_cninfo_announcement_source(),), max_pdfs=50
    )
    second_service = ResearchSyncService(second_settings, storage)
    second = second_service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert second.documents_added == 0
    assert second.documents_skipped == 3
    assert second.pdfs_consumed == 1
    assert len(client.get_bytes_calls) == 1
    assert second.pdf_failures == 0
    upgraded = storage.get_source_document("cninfo:1225461893")
    assert upgraded.parse_status == "parsed"
    assert "Investor Relations Activity Record" in upgraded.body_text


def test_backfill_respects_page_budget(tmp_path) -> None:
    settings = _research_settings(
        tmp_path, sources=(_cninfo_announcement_source(),), max_pages=1
    )
    storage = Storage(settings.database_path)
    client = ResearchStubClient(pages={1: _load_json("cninfo_page.json")})
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.pages_consumed == 1
    assert result.budget_exhausted is True
    assert result.documents_added == 3
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    # 单页预算全部用于顶部回扫：回扫游标停在下一页，历史回填尚未开始。
    assert cursor.cursor == {
        "page": 1,
        "fresh_page": 2,
        "covered_end": "2026-08-07",
    }


def _page_with_ids(page: dict[str, object], prefix: str) -> dict[str, object]:
    """Return a fresh cninfo page with unique announcement IDs (no PDFs)."""

    for index, item in enumerate(page["announcements"]):
        item["announcementId"] = f"{prefix}-{index}"
        item["adjunctUrl"] = ""
        item["adjunctType"] = None
    return page


def _priority_page() -> dict[str, object]:
    """cninfo page whose PDF attachments carry signal-priority titles."""

    page = _load_json("cninfo_page.json")
    for index, item in enumerate(page["announcements"]):
        if item.get("adjunctUrl"):
            item["announcementTitle"] = (
                "2026年半年度报告" if index == 0 else "关于签订重大合同的公告"
            )
    return page


def test_fresh_rescan_restarts_when_new_announcements_arrive_at_top(
    tmp_path,
) -> None:
    settings = _research_settings(
        tmp_path, sources=(_cninfo_announcement_source(),), max_pages=1
    )
    storage = Storage(settings.database_path)
    client = ResearchStubClient(pages={1: _load_json("cninfo_page.json")})
    service = ResearchSyncService(settings, storage)

    # 第一次刷新：单页预算全部用于顶部回扫，游标停在回扫第 2 页。
    first = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert first.documents_added == 3
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.cursor == {
        "page": 1,
        "fresh_page": 2,
        "covered_end": "2026-08-07",
    }

    # 新公告在顶部出现：第 1 页换成更高 ID 的新公告，旧内容排到第 2 页。
    client.pages = {
        1: _page_with_ids(_load_json("cninfo_page.json"), "late"),
        2: _load_json("cninfo_page.json"),
        3: _load_json("cninfo_empty_page.json"),
    }
    second_service = ResearchSyncService(
        _research_settings(
            tmp_path, sources=(_cninfo_announcement_source(),), max_pages=5
        ),
        storage,
    )
    second = second_service.sync_once(
        now=NOW, cancel=threading.Event(), client=client
    )
    # 顶部出现新内容后放弃旧回扫位置，从第 1 页重新开始：新公告全部入库，
    # 第 2 页的旧文档跳过（前沿），回填从第 3 页遇到空页结束。
    assert second.documents_added == 3
    assert second.documents_skipped == 3
    assert storage.get_source_document("cninfo:late-0") is not None
    cursor_after = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor_after.cursor == {"page": 3, "covered_end": "2026-08-07"}


def test_fresh_rescan_continues_gap_after_budget(tmp_path) -> None:
    settings = _research_settings(
        tmp_path, sources=(_cninfo_announcement_source(),), max_pages=1
    )
    storage = Storage(settings.database_path)
    client = ResearchStubClient(pages={1: _load_json("cninfo_page.json")})
    service = ResearchSyncService(settings, storage)

    first = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert first.documents_added == 3

    # 没有新公告，但第 2 页存在上次预算没扫到的“间隙”文档，第 3 页是旧内容。
    client.pages = {
        1: _load_json("cninfo_page.json"),
        2: _page_with_ids(_load_json("cninfo_page.json"), "gap"),
        3: _load_json("cninfo_page.json"),
        4: _load_json("cninfo_empty_page.json"),
    }
    second_service = ResearchSyncService(
        _research_settings(
            tmp_path, sources=(_cninfo_announcement_source(),), max_pages=5
        ),
        storage,
    )
    second = second_service.sync_once(
        now=NOW, cancel=threading.Event(), client=client
    )
    # 第 1 页全已知 → 从上次回扫位置第 2 页继续，抓取间隙文档；
    # 第 3 页全已知作为前沿，回填从第 4 页遇到空页结束。
    assert second.documents_added == 3
    assert second.documents_skipped == 6
    assert storage.get_source_document("cninfo:gap-0") is not None
    cursor_after = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor_after.cursor == {"page": 4, "covered_end": "2026-08-07"}


def test_fresh_rescan_non_priority_pdf_does_not_block_scan(tmp_path) -> None:
    settings = _research_settings(
        tmp_path, sources=(_cninfo_announcement_source(),), max_pdfs=1
    )
    storage = Storage(settings.database_path)
    page = _priority_page()
    # 第 2 条公告改为非信号标题，但仍带 PDF 附件：验证它不会抢占预算，
    # 也不会阻塞回扫前进。
    page["announcements"][1]["announcementTitle"] = "2026年7月销售情况简报"
    client = ResearchStubClient(
        pages={1: page, 2: _load_json("cninfo_empty_page.json")},
        pdfs={_sample_pdf_url(): _pdf_bytes("sample_announcement.pdf")},
    )

    result = ResearchSyncService(settings, storage).sync_once(
        now=NOW, cancel=threading.Event(), client=client
    )

    # 唯一 1 个 PDF 预算给了信号标题文档；非信号文档保持元数据且不阻塞，
    # 回扫继续到第 2 页空页结束（cutoff），游标正常完成。
    assert result.pdfs_consumed == 1
    # PDF 预算已用尽（服务层按来源统计受限），但游标未因非信号文档停步。
    assert result.budget_exhausted is True
    assert storage.get_source_document("cninfo:1225461893").parse_status == "parsed"
    assert (
        storage.get_source_document("cninfo:1225461816").parse_status
        == "metadata_only"
    )
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.cursor == {"page": 1, "covered_end": "2026-08-07"}


def test_fresh_rescan_queue_downloads_all_attachments_without_page_cap(
    tmp_path,
) -> None:
    """附件工作队列不再受单页配额限制：同一页面 30 份附件全部可解析，不因
    每页封顶而把文档永久留在元数据层（plan.md 里程碑 7 防漏）。"""

    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    page: dict[str, object] = {"announcements": []}
    base = _load_json("cninfo_page.json")
    pdf_bytes = _pdf_bytes("sample_announcement.pdf")
    pdfs: dict[str, bytes] = {}
    for index in range(30):
        item = dict(base["announcements"][index % 3])
        item["announcementId"] = f"cap-{index}"
        item["announcementTitle"] = "2026年半年度报告"
        item["adjunctUrl"] = f"finalpage/2026-08-07/cap-{index}.PDF"
        item["adjunctType"] = "PDF"
        page["announcements"].append(item)
        pdfs[
            f"https://static.cninfo.com.cn/finalpage/2026-08-07/cap-{index}.PDF"
        ] = pdf_bytes
    client = ResearchStubClient(
        pages={1: page, 2: _load_json("cninfo_empty_page.json")},
        pdfs=pdfs,
    )
    service = ResearchSyncService(settings, storage)

    first = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    # 队列跨页面公平处理：预算充足时 30 份附件全部下载解析，不设每页封顶。
    assert first.pdfs_consumed == 30
    assert first.documents_added == 30
    assert first.budget_exhausted is False
    parsed = sum(
        1
        for index in range(30)
        if storage.get_source_document(f"cninfo:cap-{index}").parse_status == "parsed"
    )
    assert parsed == 30
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.cursor == {"page": 1, "covered_end": "2026-08-07"}

    # 第二次回扫：全部已解析 → 全已知前沿，队列为空，不再下载。
    client.get_bytes_calls.clear()
    second = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert second.pdfs_consumed == 0
    assert second.documents_skipped == 30
    assert client.get_bytes_calls == []


def test_queue_round_robin_serves_all_three_buckets(tmp_path) -> None:
    """附件队列按“新调研资料 → 高优先级待核验事件 → 最旧普通待解析资料”
    轮询：预算 3 时三类各处理 1 份，任何一类都不会饿死其他类。"""

    from ashare_hotpot.models import DiscoveryCandidate, SourceDocument, SyncCursor

    settings = _research_settings(
        tmp_path, sources=(_cninfo_announcement_source(),), max_pdfs=3
    )
    storage = Storage(settings.database_path)
    now = NOW
    pdf_urls = {
        "research": "https://static.cninfo.com.cn/finalpage/r.PDF",
        "high": "https://static.cninfo.com.cn/finalpage/h.PDF",
        "ordinary": "https://static.cninfo.com.cn/finalpage/o.PDF",
    }
    pdf_bytes = _pdf_bytes("sample_announcement.pdf")
    client = ResearchStubClient(pdfs={url: pdf_bytes for url in pdf_urls.values()})
    documents: list[SourceDocument] = []
    candidates: list[DiscoveryCandidate] = []
    for key, (kind, title, discovery_type, priority) in {
        "research": ("research_activity", "投资者关系活动记录表", "other_disclosure", False),
        "high": ("announcement", "关于签订重大合同的公告", "contract_order", True),
        "ordinary": ("announcement", "关于召开临时股东大会的通知", "other_disclosure", False),
    }.items():
        documents.append(
            SourceDocument(
                document_id=f"doc-{key}",
                provider_key="cninfo",
                provider_name="巨潮资讯",
                kind=kind,
                source_url="https://example.test/list",
                document_url=pdf_urls[key],
                title=title,
                published_at=now,
                stock_codes=("600390",),
                body_text="",
                content_hash=f"h-{key}",
                parse_status="metadata_only",
                parse_error=None,
            )
        )
        candidates.append(
            DiscoveryCandidate(
                document_id=f"doc-{key}",
                source_key="cninfo_announcement",
                source_name="巨潮资讯公告",
                provider_key="cninfo",
                provider_name="巨潮资讯",
                kind=kind,
                stock_codes=("600390",),
                title=title,
                published_at=now,
                discovery_type=discovery_type,
                trigger_reason="测试",
                queue_status="pending_attachment",
                attachment_type="PDF",
                document_url=pdf_urls[key],
                enqueued_at=now,
                updated_at=now,
                signal_priority=priority,
            )
        )
    storage.save_research_batch(documents, candidates, _sync_cursor(), now)

    config = _cninfo_announcement_source()
    service = ResearchSyncService(settings, storage)
    consumed, failures, has_more = service._consume_attachment_queue(
        config, client, now=now, cancel=threading.Event(), pdfs_budget=3
    )

    # 预算 3 = 三个桶各处理 1 份；research 桶最新优先、其他桶最早优先。
    assert consumed == 3
    assert failures == 0
    assert has_more is False
    downloaded = set(client.get_bytes_calls)
    assert downloaded == set(pdf_urls.values())
    for key in ("research", "high", "ordinary"):
        assert storage.get_source_document(f"doc-{key}").parse_status == "parsed"
    assert storage.get_discovery_candidates(statuses=("pending_attachment",)) == []


def _sync_cursor() -> SyncCursor:
    return SyncCursor(
        source_key="cninfo_announcement",
        sync_kind="announcement",
        cursor={"page": 1},
        target_start=NOW.date(),
        covered_start=NOW.date(),
        last_success_at=NOW,
        last_error=None,
        updated_at=NOW,
    )


def test_split_budget_allocates_even_shares() -> None:
    assert _split_budget(20, 3) == (7, 7, 6)
    assert _split_budget(50, 3) == (17, 17, 16)
    assert _split_budget(3, 3) == (1, 1, 1)
    assert _split_budget(1, 3) == (1, 0, 0)
    assert _split_budget(0, 3) == (0, 0, 0)
    assert _split_budget(10, 0) == ()


def test_backfill_budget_is_split_per_source(tmp_path) -> None:
    announcement = _cninfo_announcement_source()
    research = SourceConfig(
        "cninfo_research",
        "巨潮资讯调研",
        "https://www.cninfo.com.cn/new/disclosure",
        adapter="cninfo",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        column="szse_relation",
        tab_name="relation",
        kind="research_activity",
    )
    sse = SourceConfig(
        "sse_publish",
        "上证e互动发布",
        "https://sns.sseinfo.com/ajax/feeds.do",
        adapter="sse_publish",
        provider_key="sse",
        provider_name="上证e互动",
        kind="research_activity",
    )
    settings = _research_settings(
        tmp_path,
        sources=(announcement, research, sse),
        max_pages=3,
        max_pdfs=3,
    )
    storage = Storage(settings.database_path)
    research_pdf = "https://static.cninfo.com.cn/finalpage/2026-08-07/1225461888.PDF"
    sse_pdf = (
        "https://sns.sseinfo.com/resources/images/upload/202608/"
        "202608061542021475728666.pdf"
    )
    sse_docx = (
        "https://sns.sseinfo.com/resources/images/upload/202608/"
        "202608061622000533054084.docx"
    )
    client = ResearchStubClient(
        pages_by_url={
            "https://www.cninfo.com.cn/new/hisAnnouncement/query": {
                1: _priority_page()
            },
            "https://www.cninfo.com.cn/new/disclosure": {
                1: _load_json("cninfo_research_page.json")
            },
        },
        text_map={"type=30": _load("sse_publish_feed.html")},
        pdfs={
            _sample_pdf_url(): _pdf_bytes("sample_announcement.pdf"),
            research_pdf: _pdf_bytes("sample_announcement.pdf"),
            sse_pdf: _pdf_bytes("sample_announcement.pdf"),
            sse_docx: _docx_bytes("测试公司投资者关系活动记录表"),
        },
    )

    result = ResearchSyncService(settings, storage).sync_once(
        now=NOW, cancel=threading.Event(), client=client
    )

    # 三个来源各分到 1 页/1 附件额度，公告流不再吃光全部预算。
    assert result.pages_consumed == 3
    assert result.pdfs_consumed == 3
    assert result.budget_exhausted is True
    assert {coverage.source_key for coverage in result.coverages} == {
        "cninfo_announcement",
        "cninfo_research",
        "sse_publish",
    }
    # 调研来源的附件确实拿到了下载额度。
    assert research_pdf in client.get_bytes_calls
    assert sse_docx in client.get_bytes_calls
    assert storage.get_source_document("cninfo:1225461888").parse_status == "parsed"
    assert storage.get_source_document("ssepub:1777290").parse_status == "parsed"


def test_backfill_first_page_empty_fails_closed(tmp_path) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(pages={1: _load_json("cninfo_empty_page.json")})
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.documents_added == 0
    assert result.pages_consumed == 1
    coverage = result.coverages[0]
    assert coverage.error == "首屏空数据或结构变化"
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.last_error == "首屏空数据或结构变化"
    assert cursor.cursor == {"page": 1}


def test_backfill_error_preserves_last_success_and_coverage_cursor(tmp_path) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    previous_success = NOW - timedelta(hours=6)
    storage.save_sync_state(
        SyncCursor(
            source_key="cninfo_announcement",
            sync_kind="announcement",
            cursor={"page": 1, "covered_end": "2026-08-06"},
            target_start=(NOW - timedelta(days=200)).date(),
            covered_start=(NOW - timedelta(days=30)).date(),
            last_success_at=previous_success,
            last_error=None,
            updated_at=previous_success,
        )
    )
    client = ResearchStubClient(pages={1: _load_json("cninfo_empty_page.json")})

    result = ResearchSyncService(settings, storage).sync_once(
        now=NOW,
        cancel=threading.Event(),
        client=client,
    )

    assert result.coverages[0].error == "首屏空数据或结构变化"
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.last_success_at == previous_success
    assert cursor.covered_start == (NOW - timedelta(days=30)).date()
    assert cursor.cursor == {"page": 1, "covered_end": "2026-08-06"}


def test_backfill_structure_break_fails_closed(tmp_path) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(pages={1: _load_json("cninfo_structure_break.json")})
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.documents_added == 0
    assert "结构异常" in (result.coverages[0].error or "")


def test_backfill_pdf_failure_keeps_document_and_continues(tmp_path) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={1: _priority_page()},
        pdfs={
            _sample_pdf_url(): _pdf_bytes("corrupt.pdf"),
            _sample_pdf_url2(): _pdf_bytes("corrupt.pdf"),
        },
    )
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.pdf_failures == 2
    assert result.documents_added == 3
    failed = storage.get_source_document("cninfo:1225461893")
    assert failed.parse_status == "failed"
    assert failed.parse_error is not None
    assert storage.get_source_document("cninfo:1225461752").parse_status == "metadata_only"


def test_backfill_pdf_empty_text_is_recorded(tmp_path) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(
        pages={1: _priority_page()},
        pdfs={
            _sample_pdf_url(): _pdf_bytes("empty_scanned.pdf"),
            _sample_pdf_url2(): _pdf_bytes("empty_scanned.pdf"),
        },
    )
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.pdf_failures == 2
    doc = storage.get_source_document("cninfo:1225461893")
    assert doc.parse_status == "empty_text"
    doc2 = storage.get_source_document("cninfo:1225461816")
    assert doc2.parse_status == "empty_text"


def _docx_bytes(marker: str) -> bytes:
    """Build a minimal OOXML ``.docx`` (paragraph + one table row)."""

    import io
    import zipfile

    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w='
        '"http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f"<w:p><w:r><w:t>{marker}</w:t></w:r></w:p>"
        "<w:tbl><w:tr>"
        "<w:tc><w:p><w:r><w:t>测试机构</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>2026-08-06</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl>"
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_extract_docx_text_parses_paragraphs_and_tables() -> None:
    content = _docx_bytes("测试公司投资者关系活动记录表")
    result = extract_docx_text(content)
    assert result.error is None
    assert result.format == "docx"
    assert result.page_count is None
    assert "测试公司投资者关系活动记录表" in result.text
    assert "测试机构" in result.text
    assert "2026-08-06" in result.text


def test_extract_doc_text_parses_utf16_and_ansi_pieces() -> None:
    # 压缩分片按 MS-DOC 语义是单字节 ANSI 字符，只放 ASCII。
    content = build_legacy_doc("投资者关系活动记录表\r", "IR Record 2026")
    result = extract_doc_text(content)
    assert result.error is None
    assert result.format == "doc"
    assert result.text == "投资者关系活动记录表\nIR Record 2026"


def test_extract_doc_text_corrupt_content_fails() -> None:
    result = extract_doc_text(b"this is not a compound file at all")
    assert result.error is not None
    assert result.error != PDF_EMPTY_TEXT
    status, message = pdf_parse_status(result)
    assert status == "failed"
    assert message == result.error


def test_extract_attachment_unknown_type_fails() -> None:
    result = extract_attachment_text(b"not an attachment", "XLS")
    assert result.error is not None
    assert "不支持的附件格式" in result.error
    status, message = pdf_parse_status(result)
    assert status == "failed"
    assert message == result.error


def test_backfill_sse_publish_office_attachments_are_parsed(tmp_path) -> None:
    source_config = SourceConfig(
        "sse_publish",
        "上证e互动发布",
        "https://sns.sseinfo.com/ajax/feeds.do",
        adapter="sse_publish",
        provider_key="sse",
        provider_name="上证e互动",
        kind="research_activity",
    )
    settings = _research_settings(tmp_path, sources=(source_config,))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(text_map={"type=30": _load("sse_publish_feed.html")})
    client.pdfs = {
        "https://sns.sseinfo.com/resources/images/upload/202608/202608061542021475728666.pdf": _pdf_bytes(
            "sample_announcement.pdf"
        ),
        "https://sns.sseinfo.com/resources/images/upload/202608/202608061622000533054084.docx": _docx_bytes(
            "江西红板科技投资者关系活动记录表0806"
        ),
        "https://sns.sseinfo.com/resources/images/upload/202608/20260806153605882497660.doc": build_legacy_doc(
            "湖南方盛制药股份有限公司投资者关系活动记录表",
            "IR Record 2026\r",
        ),
    }
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.documents_added == 3
    # PDF、DOCX、DOC 附件全部下载并提取正文。
    assert result.pdfs_consumed == 3
    assert result.pdf_failures == 0
    assert len(client.get_bytes_calls) == 3
    docx = storage.get_source_document("ssepub:1777290")
    assert docx is not None
    assert docx.parse_status == "parsed"
    assert "江西红板科技投资者关系活动记录表0806" in docx.body_text
    assert docx.kind == "research_activity"
    assert docx.stock_codes == ("603459",)

    doc = storage.get_source_document("ssepub:1777264")
    assert doc is not None
    assert doc.parse_status == "parsed"
    assert "湖南方盛制药股份有限公司投资者关系活动记录表" in doc.body_text
    assert "IR Record 2026" in doc.body_text
    assert doc.stock_codes == ("603998",)

    pdf = storage.get_source_document("ssepub:1777268")
    assert pdf is not None
    assert pdf.parse_status == "parsed"


def test_backfill_page_commit_is_atomic_on_failure(tmp_path, monkeypatch) -> None:
    settings = _research_settings(tmp_path, sources=(_cninfo_announcement_source(),))
    storage = Storage(settings.database_path)
    client = ResearchStubClient(pages={1: _load_json("cninfo_page.json")})
    service = ResearchSyncService(settings, storage)

    def boom(*_args, **_kwargs):
        raise RuntimeError("磁盘写入失败")

    monkeypatch.setattr(storage, "save_research_batch", boom)
    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.documents_added == 0
    assert storage.get_source_document("cninfo:1225461893") is None
    cursor = storage.get_sync_state("cninfo_announcement", "announcement")
    assert cursor.cursor == {"page": 1}
    assert cursor.last_error == "磁盘写入失败"
    assert (result.coverages[0].error or "").startswith("磁盘写入失败")


def test_sync_once_without_research_sources_is_empty(tmp_path) -> None:
    settings = _research_settings(tmp_path, sources=())
    storage = Storage(settings.database_path)
    service = ResearchSyncService(settings, storage)
    result = service.sync_once(now=NOW, cancel=threading.Event())
    assert result == ResearchSyncResult(
        pages_consumed=0,
        pdfs_consumed=0,
        documents_added=0,
        documents_skipped=0,
        discoveries_added=0,
        pdf_failures=0,
        budget_exhausted=False,
        coverages=(),
    )


def test_refresh_runs_research_backfill_and_records_stats(
    monkeypatch, tmp_path
) -> None:
    import ashare_hotpot.service as service_module

    captured: dict[str, object] = {}

    class FakeResearchService:
        def __init__(self, settings, storage) -> None:
            pass

        def sync_once(self, **kwargs) -> ResearchSyncResult:
            captured.update(kwargs)
            return ResearchSyncResult(
                pages_consumed=2,
                pdfs_consumed=1,
                documents_added=4,
                documents_skipped=0,
                discoveries_added=0,
                pdf_failures=1,
                budget_exhausted=False,
                coverages=(),
            )

    monkeypatch.setattr(service_module, "ResearchSyncService", FakeResearchService)
    monkeypatch.setattr(service_module, "PoliteHttpClient", _FakeHttpClient)
    monkeypatch.setattr(service_module, "NewsSource", _EmptyNewsSource)
    monkeypatch.setattr(service_module, "fetch_official_popularity", lambda client: ([], []))
    settings = AppSettings(
        app_root=tmp_path,
        sources=(),
        interaction_sources=(),
        research_sources=(_cninfo_announcement_source(),),
    )
    storage = Storage(settings.database_path)
    snapshot = RefreshService(settings, storage).refresh(now=NOW)

    assert snapshot.stats["research_pages"] == 2
    assert snapshot.stats["research_pdfs"] == 1
    assert snapshot.stats["research_documents_added"] == 4
    assert snapshot.stats["research_pdf_failures"] == 1
    assert captured["max_pages"] == 40
    assert captured["max_pdfs"] == 100
    assert captured["backfill_days"] == 200


class _FakeHttpClient:
    def __init__(self, settings, cancel_event) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass


class _EmptyNewsSource:
    def __init__(self, config, client) -> None:
        pass

    def fetch_page(self, page: int, now: datetime):
        from ashare_hotpot.sources import PageResult

        return PageResult(page, "https://example.test", ())
