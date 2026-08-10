"""v1.2 覆盖层数据契约 (plan.md 第二部分, v1.2 里程碑 0).

Covers the fixed enums, the document-ID set summary and the
``to_dict``/``from_dict`` round trips of the six new public data types.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.coverage import (
    COVERAGE_STATUSES,
    COVERAGE_STATUS_LABELS,
    COVERAGE_STATUS_LIST_RECONCILED,
    COVERAGE_STATUS_REALTIME_PROVISIONAL,
    OCR_PAGE_STATUSES,
    OCR_PAGE_STATUS_LABELS,
    OCR_STATUSES,
    OCR_STATUS_DONE,
    OCR_STATUS_LABELS,
    POLICY_LINK_KINDS,
    POLICY_LINK_LABELS,
    POLICY_LINK_NAMED_COMPANY,
    POLICY_LINK_OFFICIAL_REF,
    summarize_document_ids,
)
from ashare_hotpot.models import (
    CoverageSnapshot,
    FailureInterval,
    OcrPageResult,
    PolicyDocument,
    PolicyLink,
    SourceManifest,
)


def _roundtrip(value) -> object:
    return type(value).from_dict(
        json.loads(json.dumps(value.to_dict(), ensure_ascii=False))
    )


def _now() -> datetime:
    return datetime(2026, 8, 9, 10, 30, tzinfo=SHANGHAI_TZ)


# ---------------------------------------------------------------------------
# 固定枚举 (plan.md v1.2 口径, 不允许运行期新增)
# ---------------------------------------------------------------------------


def test_coverage_statuses_are_fixed_and_labeled() -> None:
    assert COVERAGE_STATUSES == (
        "realtime_provisional",
        "list_reconciled",
        "body_pending_verification",
        "partial_coverage",
        "unavailable",
    )
    assert len(COVERAGE_STATUS_LABELS) == len(COVERAGE_STATUSES)
    assert COVERAGE_STATUS_LABELS[COVERAGE_STATUS_REALTIME_PROVISIONAL] == "实时暂定"
    assert COVERAGE_STATUS_LABELS[COVERAGE_STATUS_LIST_RECONCILED] == "列表已对账"


def test_ocr_and_policy_enums_are_fixed_and_labeled() -> None:
    assert OCR_STATUSES == (
        "not_applicable",
        "pending",
        "done",
        "partial",
        "failed",
    )
    assert OCR_PAGE_STATUSES == ("ok", "low_confidence", "failed", "skipped")
    assert POLICY_LINK_KINDS == (
        "named_company",
        "named_project",
        "official_policy_ref",
        "industry_watch",
    )
    assert OCR_STATUS_LABELS[OCR_STATUS_DONE] == "已完成"
    assert POLICY_LINK_LABELS[POLICY_LINK_NAMED_COMPANY] == "政策点名公司"
    assert POLICY_LINK_LABELS[POLICY_LINK_OFFICIAL_REF] == "公告文号关联"
    # 双证据口径：行业观察/点名/文号关联种类固定，UI 只做映射。
    assert len(OCR_PAGE_STATUS_LABELS) == len(OCR_PAGE_STATUSES)
    assert len(POLICY_LINK_LABELS) == len(POLICY_LINK_KINDS)


def test_summarize_document_ids_is_order_independent_and_dedupes() -> None:
    count, digest = summarize_document_ids(["b", "a", "b", ""])
    assert count == 2
    assert summarize_document_ids(["a", "b"])[1] == digest
    assert summarize_document_ids(["b", "a"])[1] == digest
    assert digest != summarize_document_ids(["a", "c"])[1]


# ---------------------------------------------------------------------------
# 新数据类型往返
# ---------------------------------------------------------------------------


def test_failure_interval_roundtrip_open_and_closed() -> None:
    now = _now()
    open_interval = FailureInterval(started_at=now, ended_at=None, reason="连接超时")
    assert _roundtrip(open_interval) == open_interval

    closed = FailureInterval(started_at=now, ended_at=now, reason="结构突变")
    restored = _roundtrip(closed)
    assert restored == closed
    assert restored.ended_at == now


def test_source_manifest_roundtrip_preserves_reconciliation_fields() -> None:
    now = _now()
    manifest = SourceManifest(
        source_key="sse_announcement",
        manifest_date=date(2026, 8, 9),
        total_count=12,
        document_id_count=2,
        document_id_set_hash="abc123",
        watermark={"page": 3, "cursor": "x"},
        failure_intervals=(
            FailureInterval(started_at=now, ended_at=None, reason="分页超时"),
        ),
        ocr_status=OCR_STATUS_DONE,
        scheduled_task_result={"triggered": True, "exit_code": 0},
        coverage_status=COVERAGE_STATUS_LIST_RECONCILED,
        updated_at=now,
    )
    restored = _roundtrip(manifest)
    assert restored == manifest
    assert restored.watermark == {"page": 3, "cursor": "x"}
    assert restored.scheduled_task_result == {"triggered": True, "exit_code": 0}
    assert restored.failure_intervals[0].reason == "分页超时"
    assert restored.manifest_date == date(2026, 8, 9)


def test_source_manifest_roundtrip_empty_optional_fields() -> None:
    now = _now()
    manifest = SourceManifest(
        source_key="policy_miit",
        manifest_date=date(2026, 8, 9),
        total_count=0,
        document_id_count=0,
        document_id_set_hash=None,
        watermark=None,
        failure_intervals=(),
        ocr_status="not_applicable",
        scheduled_task_result=None,
        coverage_status="unavailable",
        updated_at=now,
    )
    restored = _roundtrip(manifest)
    assert restored == manifest
    assert restored.failure_intervals == ()


def test_policy_document_roundtrip() -> None:
    now = _now()
    document = PolicyDocument(
        document_id="pol-1",
        source_key="policy_gov",
        title="关于促进制造业高质量发展的意见",
        published_at=now,
        source_url="https://www.gov.cn/zhengce/1",
        document_url=None,
        body_text="正文摘录",
        body_hash="body-hash",
        body_status="parsed",
        body_error=None,
        content_hash="content-hash",
        updated_at=now,
    )
    restored = _roundtrip(document)
    assert restored == document
    assert restored.body_status == "parsed"


def test_policy_link_roundtrip_with_optional_targets() -> None:
    now = _now()
    link = PolicyLink(
        link_id="link-1",
        policy_document_id="pol-1",
        target_document_id="ann-1",
        stock_code="600390",
        link_kind=POLICY_LINK_NAMED_COMPANY,
        evidence_excerpt="政策明确点名五矿资本",
        evidence_id="ev-1",
        created_at=now,
    )
    restored = _roundtrip(link)
    assert restored == link
    assert restored.stock_code == "600390"

    watch = PolicyLink(
        link_id="link-2",
        policy_document_id="pol-2",
        target_document_id=None,
        stock_code=None,
        link_kind="industry_watch",
        evidence_excerpt="行业观察",
        evidence_id=None,
        created_at=now,
    )
    assert _roundtrip(watch) == watch


def test_ocr_page_result_roundtrip_with_and_without_confidence() -> None:
    now = _now()
    page = OcrPageResult(
        document_id="ann-1",
        page_index=0,
        confidence=0.93,
        text="第一页识别文本",
        model_version="ppocr-v4",
        evidence_url="https://example.test/1.pdf#page=1",
        status="ok",
        error=None,
        updated_at=now,
    )
    restored = _roundtrip(page)
    assert restored == page
    assert restored.page_index == 0
    assert restored.confidence == 0.93

    failed = OcrPageResult(
        document_id="ann-1",
        page_index=1,
        confidence=None,
        text="",
        model_version=None,
        evidence_url=None,
        status="failed",
        error="加密文档",
        updated_at=now,
    )
    assert _roundtrip(failed) == failed


def test_coverage_snapshot_roundtrip() -> None:
    now = _now()
    snapshot = CoverageSnapshot(
        snapshot_id="snap-1",
        snapshot_ts=now,
        statuses={
            "sse_announcement": COVERAGE_STATUS_LIST_RECONCILED,
            "policy_miit": COVERAGE_STATUS_REALTIME_PROVISIONAL,
        },
        manifest_count=2,
        policy_document_count=5,
        ocr_pending_count=1,
        provisional=True,
        error=None,
    )
    restored = _roundtrip(snapshot)
    assert restored == snapshot
    assert restored.statuses["sse_announcement"] == COVERAGE_STATUS_LIST_RECONCILED
    assert restored.provisional is True
