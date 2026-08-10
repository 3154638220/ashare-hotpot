from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

from ashare_hotpot.clustering import PersistentEventClusterer
from ashare_hotpot.config import SHANGHAI_TZ, SourceConfig
from ashare_hotpot.discovery import classify_discovery
from ashare_hotpot.models import SourceDocument, SyncCursor
from ashare_hotpot.research_sync import ResearchSyncService
from ashare_hotpot.research_views import load_discovery_rows, load_short_term_rows
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=SHANGHAI_TZ)
FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# 本机发现的公开样本回归集（plan.md 里程碑 7）：必须至少进入待核验层
# ---------------------------------------------------------------------------


REGRESSION_TITLES = {
    "600390": "五矿资本股份有限公司关于公司拟签订重大合同暨关联交易的公告",
    "688167": "西安炬光科技股份有限公司2026年半年度报告摘要",
    "300184": "关于以集中竞价交易方式回购公司股份用于注销并减少注册资本报告书",
    "605588": "关于股份回购实施结果暨股份变动的公告",
}


def test_regression_titles_classify_into_fixed_discovery_enums() -> None:
    expected = {
        "600390": "contract_order",
        "688167": "financial_report",
        "300184": "capital_action",
        "605588": "capital_action",
    }
    for code, title in REGRESSION_TITLES.items():
        discovery_type, reason = classify_discovery(title)
        assert discovery_type == expected[code], code
        assert reason


def test_regression_titles_enter_discovery_layer_through_sync(
    tmp_path,
) -> None:
    """四份公开样本经列表同步后全部持久化为待核验候选（不因标题门控或
    附件额度丢失）。"""

    settings = AppSettingsWithSource(tmp_path)
    storage = Storage(settings.database_path)
    client = _RegressionStubClient()
    service = ResearchSyncService(settings, storage)

    result = service.sync_once(now=NOW, cancel=threading.Event(), client=client)

    assert result.discoveries_added == 4
    candidates = storage.get_discovery_candidates()
    by_title = {candidate.title: candidate for candidate in candidates}
    for code, title in REGRESSION_TITLES.items():
        assert title in by_title, code
        candidate = by_title[title]
        assert candidate.stock_codes == (code,)
        assert candidate.queue_status == "pending_attachment"
        assert candidate.source_key == "cninfo_announcement"

    rows = load_discovery_rows(storage)
    row_titles = {row.title for row in rows}
    assert set(REGRESSION_TITLES.values()) <= row_titles


def test_regression_promoted_candidate_transitions_to_strict_board(
    tmp_path,
) -> None:
    """候选 → 严格信号状态转换：文档进入事件簇并产生信号后，从待核验视图
    移出并出现在确定性利好榜（promoted 由证据驱动，动态判定）。"""

    from ashare_hotpot.models import EventCluster, EventExtraction, EventSignal

    settings = AppSettingsWithSource(tmp_path)
    storage = Storage(settings.database_path)
    client = _RegressionStubClient()
    ResearchSyncService(settings, storage).sync_once(
        now=NOW, cancel=threading.Event(), client=client
    )
    document = storage.get_source_document("cninfo:reg-600390")
    assert document is not None

    # 附件下载后正文解析完成 → 待核验。
    storage.upsert_source_document(
        SourceDocument(
            document_id=document.document_id,
            provider_key=document.provider_key,
            provider_name=document.provider_name,
            kind=document.kind,
            source_url=document.source_url,
            document_url=document.document_url,
            title=document.title,
            published_at=document.published_at,
            stock_codes=document.stock_codes,
            body_text="公司拟与客户签订重大合同，合同金额预计不低于10亿元。",
            content_hash="hash-parsed-600390",
            parse_status="parsed",
            parse_error=None,
        ),
        NOW,
    )
    storage.set_discovery_queue_status(
        "cninfo:reg-600390", "awaiting_review", NOW
    )

    event_id = "event-reg-600390"
    storage.upsert_event_cluster(
        EventCluster(
            event_id=event_id,
            stock_codes=("600390",),
            canonical_title=document.title,
            first_seen_at=NOW - timedelta(hours=2),
            last_seen_at=NOW,
            representative_document_id=document.document_id,
            document_ids=[document.document_id],
            historical_similar_event_id=None,
        )
    )
    storage.link_event_document(event_id, document.document_id)
    storage.upsert_event_extraction(
        EventExtraction(
            event_id=event_id,
            stock_code="600390",
            event_type="major_contract",
            direction="positive",
            positive_mechanism="拟签重大合同预计增厚营业收入",
            metrics=(
                {
                    "name": "合同金额",
                    "value": 10,
                    "unit": "亿元",
                    "comparison_basis": None,
                    "comparison_ratio": None,
                    "evidence_id": "e1",
                },
            ),
            certainty_stage="framework",
            certainty=0.45,
            novelty=0.8,
            unexpectedness=0.7,
            materiality_level=2,
            counter_evidence=(),
            evidence_ids=("e1",),
            no_valid_signal=False,
            extractor_kind="rules",
            extractor_version="rules-v1",
        ),
        NOW,
    )
    storage.upsert_event_signal(
        EventSignal(
            event_id=event_id,
            stock_code="600390",
            board="potential_catalyst",
            score=40.0,
            source_confidence=0.7,
            materiality_level=2,
            certainty=0.45,
            unexpectedness=0.7,
            novelty=0.8,
            timeliness=0.8,
            penalty=0.0,
            provisional=False,
        ),
        created_at=NOW,
    )

    discovery_rows = load_discovery_rows(storage)
    assert "cninfo:reg-600390" not in {row.document_id for row in discovery_rows}
    short_rows = load_short_term_rows(storage, "potential_catalyst")
    assert any(row.event_id == event_id for row in short_rows)


def test_cross_source_same_disclosure_dedups_into_one_cluster(tmp_path) -> None:
    """跨来源去重：同一份投资者关系活动记录表（巨潮调研 + 互动易投资者关系）
    内容哈希一致时只形成一个事件簇。"""

    storage = Storage(tmp_path / "hotpot.db")
    body = "2026年8月5日投资者关系活动记录表\n参与单位：中信证券、华泰证券\n"
    cninfo_doc = SourceDocument(
        document_id="cninfo:cross-1",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="research_activity",
        source_url="https://www.cninfo.com.cn/new/disclosure",
        document_url="https://static.cninfo.com.cn/finalpage/cross-1.PDF",
        title="投资者关系活动记录表",
        published_at=NOW,
        stock_codes=("000001",),
        body_text=body,
        content_hash="hash-cross-same",
        parse_status="parsed",
        parse_error=None,
    )
    irm_doc = SourceDocument(
        document_id="irm_ircs:cross-1",
        provider_key="irm",
        provider_name="深交所互动易",
        kind="research_activity",
        source_url="https://irm.cninfo.com.cn/newircs/index/search",
        document_url="https://static.cninfo.com.cn/finalpage/cross-1.PDF",
        title="投资者关系活动记录表",
        published_at=NOW - timedelta(hours=1),
        stock_codes=("000001",),
        body_text=body,
        content_hash="hash-cross-same",
        parse_status="parsed",
        parse_error=None,
    )
    for document in (cninfo_doc, irm_doc):
        storage.upsert_source_document(document, NOW)
    PersistentEventClusterer(storage).process_window(
        NOW - timedelta(days=200), NOW + timedelta(days=1), NOW
    )

    clusters_by_doc = storage.get_event_clusters_by_document("cninfo:cross-1")
    assert len(clusters_by_doc) == 1
    cluster = clusters_by_doc[0]
    assert "irm_ircs:cross-1" in storage.get_event_clusters_by_document(
        "irm_ircs:cross-1"
    )[0].document_ids
    assert set(cluster.document_ids) == {"cninfo:cross-1", "irm_ircs:cross-1"}


# ---------------------------------------------------------------------------
# 本地测试桩
# ---------------------------------------------------------------------------


class AppSettingsWithSource:
    """Minimal settings for the sync regression (single cninfo source)."""

    def __init__(self, tmp_path) -> None:
        self.app_root = tmp_path
        self.pdf_temp_dir = tmp_path / "pdf_tmp"
        self.pdf_temp_dir.mkdir(parents=True, exist_ok=True)
        self.request_timeout_seconds = 15.0
        self.minimum_request_interval_seconds = 0
        self.request_retries = 1
        self.research_max_pages_per_run = 5
        # 附件额度为零：回归验证“额度不足只标记延后，不永久跳过”——四份样本
        # 仍全部进入待核验层（待解析），而不是被静默丢弃。
        self.research_max_pdfs_per_run = 0
        self.backfill_days = 200
        self.research_sources = (
            SourceConfig(
                "cninfo_announcement",
                "巨潮资讯公告",
                "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                adapter="cninfo",
                provider_key="cninfo",
                provider_name="巨潮资讯",
                column="szse",
                tab_name="fulltext",
                kind="announcement",
            ),
        )

    @property
    def database_path(self) -> Path:
        return self.app_root / "data" / "hotpot.db"


class _RegressionStubClient:
    """Serves one cninfo page with the four regression announcements."""

    def __init__(self) -> None:
        self.post_calls: list[tuple[str, int]] = []
        self.get_bytes_calls: list[str] = []

    def post_form(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        page = int(str(payload["pageNum"]))
        self.post_calls.append((url, page))
        if page > 1:
            return {
                "totalAnnouncement": 0,
                "announcements": [],
            }
        announcements = []
        for index, (code, title) in enumerate(REGRESSION_TITLES.items()):
            announcements.append(
                {
                    "announcementId": f"reg-{code}",
                    "announcementTitle": title,
                    "announcementTime": int(NOW.timestamp() * 1000),
                    "adjunctUrl": f"finalpage/2026-08-08/reg-{index}.PDF",
                    "adjunctType": "PDF",
                    "secCode": code,
                    "secName": code,
                }
            )
        return {
            "totalAnnouncement": 4,
            "announcements": announcements,
        }

    def get_text(self, url: str, *, accept: str = "") -> str:
        return ""

    def get_bytes(self, url: str, *, accept: str = "") -> bytes:
        self.get_bytes_calls.append(url)
        return b"%PDF-1.4 fake"

    def close(self) -> None:
        pass

    def __enter__(self) -> "_RegressionStubClient":
        return self

    def __exit__(self, *_args: object) -> None:
        pass
