"""v2 优化计划里程碑 2：扩源、清单对账与 OCR/政策约束（plan.md 第三部分）。

覆盖上交所/北交所公司公告与北交所业绩说明会/投资者关系活动三个新来源的 fixture
锁定解析契约、分页/总数/晚到/失败关闭，以及同步服务每源每日 manifest 写入；
并固定 OCR 单一证据不得生成严格利好、政策文档/行业映射不入个股信号管线的
边界。
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from ashare_hotpot.config import (
    RESEARCH_SOURCES,
    AppSettings,
    SHANGHAI_TZ,
    SourceConfig,
)
from ashare_hotpot.coverage import (
    COVERAGE_STATUS_REALTIME_PROVISIONAL,
    OCR_PAGE_STATUS_OK,
    OCR_STATUS_NOT_APPLICABLE,
    POLICY_LINK_INDUSTRY_WATCH,
)
from ashare_hotpot.extraction import RuleBasedSignalExtractor
from ashare_hotpot.models import (
    EventCluster,
    FailureInterval,
    OcrPageResult,
    PolicyDocument,
    PolicyLink,
    SourceDocument,
)
from ashare_hotpot.parsing import (
    parse_bse_announcement_page,
    parse_bse_performance_page,
    parse_jsonp_payload,
    parse_sse_announcement_page,
)
from ashare_hotpot.research_sync import ResearchSyncService
from ashare_hotpot.sources import (
    BseAnnouncementSource,
    BsePerformanceSource,
    PoliteHttpClient,
    SseAnnouncementSource,
)
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=SHANGHAI_TZ)
FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _sse_config() -> SourceConfig:
    return SourceConfig(
        "sse_announcement",
        "上交所公司公告",
        "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do",
        adapter="sse_announcement",
        provider_key="sse",
        provider_name="上海证券交易所",
        kind="announcement",
    )


def _bse_config() -> SourceConfig:
    return SourceConfig(
        "bse_performance",
        "北交所业绩说明会",
        "https://www.bse.cn/performanceController/list.do",
        adapter="bse_performance",
        provider_key="bse",
        provider_name="北京证券交易所",
        kind="research_activity",
    )


def _bse_announcement_config() -> SourceConfig:
    return SourceConfig(
        "bse_announcement",
        "北交所公司公告",
        "https://www.bse.cn/disclosureInfoController/initDisclosureList.do",
        adapter="bse_announcement",
        provider_key="bse",
        provider_name="北京证券交易所",
        kind="announcement",
    )


class JsonpStubClient:
    """Stub PoliteHttpClient for JSONP sources (records calls)."""

    def __init__(self, *, text_map: dict[str, str] | None = None) -> None:
        self.text_map = text_map or {}
        self.text_calls: list[str] = []
        self.header_calls: list[dict[str, str]] = []
        self.post_payloads: list[dict[str, object]] = []

    def get_text(
        self,
        url: str,
        *,
        accept: str = "",
        headers: dict[str, str] | None = None,
    ) -> str:
        self.text_calls.append(url)
        self.header_calls.append(dict(headers or {}))
        for template, value in self.text_map.items():
            if template in url:
                return value
        raise RuntimeError(f"未配置响应：{url}")

    def post_form_text(
        self,
        url: str,
        payload: dict[str, object],
        *,
        accept: str = "",
        headers: dict[str, str] | None = None,
    ) -> str:
        self.text_calls.append(url)
        self.header_calls.append(dict(headers or {}))
        self.post_payloads.append(dict(payload))
        for template, value in self.text_map.items():
            if template in url:
                return value
        raise RuntimeError(f"未配置响应：{url}")


# ---------------------------------------------------------------------------
# JSONP 解析契约
# ---------------------------------------------------------------------------


def test_parse_jsonp_payload_strips_wrapper() -> None:
    assert parse_jsonp_payload(
        'callback({"a": 1})', source_label="测试"
    ) == {"a": 1}
    assert parse_jsonp_payload(
        'cb([{"listInfo": {}}])', source_label="测试"
    ) == [{"listInfo": {}}]


def test_parse_jsonp_payload_rejects_non_jsonp() -> None:
    with pytest.raises(RuntimeError, match="不是 JSONP"):
        parse_jsonp_payload("<html>登录页</html>", source_label="测试")
    with pytest.raises(RuntimeError, match="非法 JSON"):
        parse_jsonp_payload("callback({bad json)", source_label="测试")
    with pytest.raises(RuntimeError, match="结构异常"):
        parse_jsonp_payload('callback("string")', source_label="测试")


# ---------------------------------------------------------------------------
# 上交所公司公告解析契约
# ---------------------------------------------------------------------------


def test_parse_sse_announcement_page_fixture() -> None:
    payload = parse_jsonp_payload(
        _load("sse_announcement_page.json"), source_label="上交所公告"
    )
    items, total = parse_sse_announcement_page(
        payload,
        source_key="sse_announcement",
        source_name="上交所公司公告",
        provider_key="sse",
        provider_name="上海证券交易所",
        kind="announcement",
        now=NOW,
        base_url="https://query.sse.com.cn/",
    )
    assert total == 2814
    # 25 行中一行含 2 条公告（嵌套数组），fixture 固定为 26 条。
    assert len(items) == 26
    first = items[0]
    assert first.document_id.startswith("sse_ann:")
    assert first.title == "华能国际关于第十期中期票据发行的公告"
    assert first.stock_codes == ("600011",)
    assert first.stock_names == {"600011": "华能国际"}
    assert first.kind == "announcement"
    assert first.published_at.date() == date(2026, 8, 8)
    assert first.attachment_type == "PDF"
    assert first.document_url.startswith("https://www.sse.com.cn/disclosure/")


def test_parse_sse_announcement_error_fails_closed() -> None:
    payload = parse_jsonp_payload(
        _load("sse_announcement_error.json"), source_label="上交所公告"
    )
    with pytest.raises(RuntimeError, match="返回失败"):
        parse_sse_announcement_page(
            payload,
            source_key="sse_announcement",
            source_name="上交所公司公告",
            provider_key="sse",
            provider_name="上海证券交易所",
            kind="announcement",
            now=NOW,
            base_url="https://query.sse.com.cn/",
        )


def test_parse_sse_announcement_structure_break_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="结构异常"):
        parse_sse_announcement_page(
            {"pageHelp": {"data": "not-a-list"}},
            source_key="sse_announcement",
            source_name="上交所公司公告",
            provider_key="sse",
            provider_name="上海证券交易所",
            kind="announcement",
            now=NOW,
            base_url="https://query.sse.com.cn/",
        )


def test_sse_announcement_source_paginates_and_sends_referer() -> None:
    client = JsonpStubClient(
        text_map={
            "pageNo=1": _load("sse_announcement_page.json"),
            "pageNo=2": 'callback({"pageHelp":{"data":[],"total":2814,"pageNo":2}})',
        }
    )
    source = SseAnnouncementSource(_sse_config(), client)
    first = source.fetch_page(1, NOW, date_start=date(2026, 8, 1), date_end=date(2026, 8, 8))
    assert len(first.items) == 26
    assert first.total == 2814
    assert "pageNo=1" in client.text_calls[0]
    assert "START_DATE=2026-08-01" in client.text_calls[0]
    # 上交所接口要求 Referer，否则返回 success=false。
    assert client.header_calls[0].get("Referer") == "https://www.sse.com.cn/"
    second = source.fetch_page(2, NOW)
    assert second.items == ()
    assert "pageNo=2" in client.text_calls[1]


def test_sse_announcement_source_error_page_raises() -> None:
    client = JsonpStubClient(
        text_map={"pageNo=1": _load("sse_announcement_error.json")}
    )
    source = SseAnnouncementSource(_sse_config(), client)
    with pytest.raises(RuntimeError, match="返回失败"):
        source.fetch_page(1, NOW)


# ---------------------------------------------------------------------------
# 北交所公司公告解析契约
# ---------------------------------------------------------------------------


def test_parse_bse_announcement_page_fixture() -> None:
    payload = parse_jsonp_payload(
        _load("bse_announcement_page.json"), source_label="北交所公告"
    )
    items, total = parse_bse_announcement_page(
        payload,
        source_key="bse_announcement",
        source_name="北交所公司公告",
        provider_key="bse",
        provider_name="北京证券交易所",
        kind="announcement",
        now=NOW,
        base_url="https://www.bse.cn/disclosureInfoController/initDisclosureList.do",
    )
    assert total == 1450
    assert len(items) == 2
    first = items[0]
    assert first.document_id == "bse_ann:c5f0804783cd4393b8703ab1f419b779"
    assert first.title == "[定期报告]酉立智能:2026年半年度报告"
    assert first.stock_codes == ("920007",)
    assert first.stock_names == {"920007": "酉立智能"}
    assert first.kind == "announcement"
    assert first.published_at.date() == date(2026, 8, 7)
    assert first.attachment_type == "PDF"
    assert first.document_url == (
        "https://www.bse.cn/disclosure/2026/2026-08-07/"
        "c5f0804783cd4393b8703ab1f419b779.pdf"
    )


def test_parse_bse_announcement_empty_page() -> None:
    payload = parse_jsonp_payload(
        _load("bse_announcement_empty.json"), source_label="北交所公告"
    )
    items, total = parse_bse_announcement_page(
        payload,
        source_key="bse_announcement",
        source_name="北交所公司公告",
        provider_key="bse",
        provider_name="北京证券交易所",
        kind="announcement",
        now=NOW,
        base_url="https://www.bse.cn/disclosureInfoController/initDisclosureList.do",
    )
    assert items == []
    assert total == 0


@pytest.mark.parametrize(
    "payload",
    (
        {"data": {"content": "not-a-list"}},
        {"data": {"content": [{"disclosures": "not-a-list"}]}},
        {"data": {"content": [{"disclosures": [{}], "totalElements": 1}]}},
    ),
)
def test_parse_bse_announcement_structure_break_fails_closed(
    payload: object,
) -> None:
    with pytest.raises(RuntimeError, match="结构异常"):
        parse_bse_announcement_page(
            payload,
            source_key="bse_announcement",
            source_name="北交所公司公告",
            provider_key="bse",
            provider_name="北京证券交易所",
            kind="announcement",
            now=NOW,
            base_url="https://www.bse.cn/disclosureInfoController/initDisclosureList.do",
        )


def test_bse_announcement_source_posts_official_pagination_contract() -> None:
    client = JsonpStubClient(
        text_map={"initDisclosureList.do": _load("bse_announcement_page.json")}
    )
    source = BseAnnouncementSource(_bse_announcement_config(), client)
    result = source.fetch_page(
        2, NOW, date_start=date(2026, 8, 1), date_end=date(2026, 8, 9)
    )
    assert len(result.items) == 2
    assert result.total == 1450
    payload = client.post_payloads[0]
    # 本地页码从 1 开始；北交所页面脚本传给后端的是 0-based page。
    assert payload["page"] == "1"
    assert payload["startTime"] == "2026-08-01"
    assert payload["endTime"] == "2026-08-09"
    assert payload["xxfcbj[]"] == ("2",)
    assert "destFilePath" in payload["needFields[]"]
    assert client.header_calls[0]["Referer"].endswith(
        "/disclosure/announcement.html"
    )


def test_bse_announcement_source_is_registered() -> None:
    config = next(
        source for source in RESEARCH_SOURCES if source.key == "bse_announcement"
    )
    assert config.adapter == "bse_announcement"
    assert config.kind == "announcement"


def test_polite_http_client_preserves_repeated_jsonp_form_fields() -> None:
    class Response:
        content = b'cb({"data":{"content":[]}})'
        headers = {"content-type": "application/javascript; charset=utf-8"}

        @staticmethod
        def raise_for_status() -> None:
            return None

    class CapturingClient:
        def __init__(self) -> None:
            self.data: object = None

        def post(self, url, *, data, headers):
            self.data = data
            return Response()

    http_client = PoliteHttpClient(
        AppSettings(request_retries=1), threading.Event()
    )
    http_client._client.close()
    capturing = CapturingClient()
    http_client._client = capturing  # type: ignore[assignment]
    http_client.post_form_text(
        "https://example.test/jsonp?callback=cb",
        {"needFields[]": ("companyCd", "destFilePath")},
    )
    assert capturing.data == {
        "needFields[]": ("companyCd", "destFilePath")
    }


# ---------------------------------------------------------------------------
# 北交所业绩说明会/投资者关系活动解析契约
# ---------------------------------------------------------------------------


def test_parse_bse_performance_page_fixture() -> None:
    payload = parse_jsonp_payload(
        _load("bse_performance_page.json"), source_label="北交所业绩说明会"
    )
    items, total = parse_bse_performance_page(
        payload,
        source_key="bse_performance",
        source_name="北交所业绩说明会",
        provider_key="bse",
        provider_name="北京证券交易所",
        kind="research_activity",
        now=NOW,
        base_url="https://www.bse.cn/performanceController/list.do",
    )
    assert total == 15
    assert len(items) == 15
    first = items[0]
    assert first.document_id == "bse_perf:1568"
    assert first.title == "湖北辖区上市公司2026年投资者网上集体接待日活动"
    assert first.stock_codes == ("920108",)
    assert first.stock_names == {"920108": "宏海科技"}
    assert first.kind == "research_activity"
    assert first.published_at.date() == date(2026, 7, 9)
    assert "直播" in first.description


def test_parse_bse_performance_param_error_fails_closed() -> None:
    payload = parse_jsonp_payload(
        _load("bse_performance_param_error.json"), source_label="北交所业绩说明会"
    )
    with pytest.raises(RuntimeError, match="结构异常"):
        parse_bse_performance_page(
            payload,
            source_key="bse_performance",
            source_name="北交所业绩说明会",
            provider_key="bse",
            provider_name="北京证券交易所",
            kind="research_activity",
            now=NOW,
            base_url="https://www.bse.cn/performanceController/list.do",
        )


def test_parse_bse_performance_empty_page() -> None:
    items, total = parse_bse_performance_page(
        [{"listInfo": {"content": [], "totalElements": 0}}],
        source_key="bse_performance",
        source_name="北交所业绩说明会",
        provider_key="bse",
        provider_name="北京证券交易所",
        kind="research_activity",
        now=NOW,
        base_url="https://www.bse.cn/performanceController/list.do",
    )
    assert items == []
    assert total == 0


def test_bse_performance_source_posts_pagination() -> None:
    client = JsonpStubClient(
        text_map={"list.do": _load("bse_performance_page.json")}
    )
    source = BsePerformanceSource(_bse_config(), client)
    result = source.fetch_page(
        2, NOW, date_start=date(2026, 1, 1), date_end=date(2026, 8, 9)
    )
    assert len(result.items) == 15
    assert result.total == 15
    payload = client.post_payloads[0]
    assert payload["page"] == "2"
    assert payload["pageSize"] == "20"
    assert payload["ssgs"] == "2"
    assert payload["startDate"] == "2026-01-01"


# ---------------------------------------------------------------------------
# 同步服务：每源每日 manifest
# ---------------------------------------------------------------------------


def _single_source_settings(source: SourceConfig) -> AppSettings:
    settings = AppSettings()
    settings.research_sources = (source,)
    settings.research_max_pages_per_run = 1
    settings.research_max_pdfs_per_run = 0
    settings.backfill_days = 10
    return settings


def test_sync_writes_daily_manifest(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    settings = _single_source_settings(_sse_config())
    client = JsonpStubClient(
        text_map={"pageNo=1": _load("sse_announcement_page.json")}
    )
    service = ResearchSyncService(settings, storage)
    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert result.pages_consumed == 1
    # 生产同步必须把回填窗口传给上交所；该接口不带日期会返回空首屏。
    assert "START_DATE=2026-07-30" in client.text_calls[0]
    assert "END_DATE=2026-08-09" in client.text_calls[0]

    manifests = storage.get_source_manifests("sse_announcement")
    assert len(manifests) >= 1
    manifest = manifests[0]
    assert manifest.manifest_date == date(2026, 8, 8)
    assert manifest.total_count == 2814
    assert manifest.document_id_count == 25
    assert manifest.document_id_set_hash
    assert manifest.coverage_status == COVERAGE_STATUS_REALTIME_PROVISIONAL
    assert manifest.ocr_status == OCR_STATUS_NOT_APPLICABLE
    assert manifest.watermark is not None
    assert manifest.failure_intervals == ()

    # 清单摘要与 discovery_candidates 中当日的本地 ID 集合一致（可对账）。
    count, digest = storage.summarize_discovery_day(
        "sse_announcement", date(2026, 8, 8)
    )
    assert count == manifest.document_id_count
    assert digest == manifest.document_id_set_hash


def test_sync_bse_announcement_writes_manifest_and_window(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    settings = _single_source_settings(_bse_announcement_config())
    client = JsonpStubClient(
        text_map={"initDisclosureList.do": _load("bse_announcement_page.json")}
    )
    service = ResearchSyncService(settings, storage)
    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)
    assert result.pages_consumed == 1

    manifests = storage.get_source_manifests("bse_announcement")
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.manifest_date == date(2026, 8, 7)
    assert manifest.total_count == 1450
    assert manifest.document_id_count == 2
    assert manifest.failure_intervals == ()
    payload = client.post_payloads[0]
    assert payload["startTime"] == "2026-07-30"
    assert payload["endTime"] == "2026-08-09"


def test_sync_failure_records_open_failure_interval(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    settings = _single_source_settings(_sse_config())

    class FailingClient(JsonpStubClient):
        def get_text(self, url, *, accept="", headers=None):
            raise RuntimeError("上交所接口超时")

    service = ResearchSyncService(settings, storage)
    service.sync_once(now=NOW, cancel=threading.Event(), client=FailingClient())
    manifests = storage.get_source_manifests("sse_announcement")
    assert manifests
    assert manifests[0].failure_intervals
    assert manifests[0].failure_intervals[-1].ended_at is None
    assert "超时" in manifests[0].failure_intervals[-1].reason


# ---------------------------------------------------------------------------
# OCR 与政策约束
# ---------------------------------------------------------------------------


def test_ocr_only_document_never_generates_strict_signal(tmp_path: Path) -> None:
    """OCR 单一证据不得直接生成严格利好：ocr_pages 文本不进正文、不出信号。"""

    storage = Storage(tmp_path / "hotpot.db")
    document = SourceDocument(
        document_id="ocr-doc-1",
        provider_key="sse",
        provider_name="上海证券交易所",
        kind="announcement",
        source_url="https://example.test/ocr-doc-1",
        document_url="https://example.test/ocr-doc-1.pdf",
        title="关于签订重大合同的公告",
        published_at=NOW - timedelta(days=1),
        stock_codes=("600000",),
        body_text="",  # 文本层缺失：正文为空，OCR 结果只存在 ocr_pages。
        content_hash="hash-ocr-1",
        parse_status="empty_text",
        parse_error=None,
    )
    storage.upsert_source_document(document, NOW)
    storage.save_ocr_page(
        OcrPageResult(
            document_id=document.document_id,
            page_index=0,
            confidence=0.95,
            text="公司签订重大合同，合同金额1.2亿元，占上年营收10%。",
            model_version="stub-ocr",
            evidence_url=document.document_url,
            status=OCR_PAGE_STATUS_OK,
            error=None,
            updated_at=NOW,
        )
    )
    # 正文仍为空：OCR 结果绝不回填 body_text。
    stored = storage.get_source_document(document.document_id)
    assert stored.body_text == ""
    assert stored.parse_status == "empty_text"

    cluster = EventCluster(
        event_id="ocr-event-1",
        stock_codes=("600000",),
        canonical_title=document.title,
        first_seen_at=document.published_at,
        last_seen_at=document.published_at,
        representative_document_id=document.document_id,
        document_ids=[document.document_id],
        historical_similar_event_id=None,
    )
    storage.upsert_event_cluster(cluster)
    extractor = RuleBasedSignalExtractor(storage)
    extraction = extractor.extract_for_stock(
        cluster, (stored,), "600000"
    )
    # 空正文文档不产生任何抽取（None），更不可能生成严格利好。
    assert extraction is None or extraction.no_valid_signal is True


def test_policy_documents_never_enter_signal_pipeline(tmp_path: Path) -> None:
    """政策文档与行业映射只进 policy 表/行业观察，绝不出个股信号。"""

    storage = Storage(tmp_path / "hotpot.db")
    policy = PolicyDocument(
        document_id="policy-1",
        source_key="miit",
        title="关于开展XX行业高质量发展的指导意见",
        published_at=NOW - timedelta(days=2),
        source_url="https://www.miit.gov.cn/zwgk/policy-1.html",
        document_url=None,
        body_text="支持电子元件行业发展，鼓励龙头企业做大做强。",
        body_hash="hash-policy-body",
        body_status="parsed",
        body_error=None,
        content_hash="hash-policy-1",
        updated_at=NOW,
    )
    storage.upsert_policy_document(policy)
    storage.upsert_policy_link(
        PolicyLink(
            link_id="link-1",
            policy_document_id="policy-1",
            target_document_id=None,
            stock_code="600000",
            link_kind=POLICY_LINK_INDUSTRY_WATCH,
            evidence_excerpt="行业政策观察（不构成个股信号）",
            evidence_id=None,
            created_at=NOW,
        )
    )
    # 政策文档不进入 source_documents（信号管线只消费 source_documents）。
    assert storage.get_source_document("policy-1") is None
    docs = storage.get_source_documents_between(
        NOW - timedelta(days=10), NOW
    )
    assert all(doc.document_id != "policy-1" for doc in docs)
    # 行业观察链接保持为观察，不生成 EventSignal。
    assert storage.get_event_cluster("policy-1") is None
    assert storage.get_event_signals() == []
