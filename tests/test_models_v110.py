from __future__ import annotations

import json
from datetime import date, datetime

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import (
    CoverageState,
    EventCluster,
    EventExtraction,
    EventSignal,
    EvidenceRef,
    Institution,
    InstitutionAlias,
    PersistenceRow,
    ResearchActivity,
    ResearchCoverage,
    ResearchParticipant,
    SourceDocument,
    StructuralComparison,
    SyncCursor,
    Z20Row,
)


def _roundtrip(value) -> object:
    return type(value).from_dict(json.loads(json.dumps(value.to_dict(), ensure_ascii=False)))


def test_source_document_roundtrip() -> None:
    document = SourceDocument(
        document_id="doc-1",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url="https://example.test/list",
        document_url="https://example.test/pdf",
        title="投资者关系活动记录表",
        published_at=datetime(2026, 8, 5, 9, 30, tzinfo=SHANGHAI_TZ),
        stock_codes=("000001", "600519"),
        body_text="正文内容",
        content_hash="abc123",
        parse_status="parsed",
        parse_error=None,
        page_count=3,
        stock_names={"000001": "平安银行", "600519": "贵州茅台"},
    )
    restored = _roundtrip(document)
    assert restored == document
    assert restored.stock_codes == ("000001", "600519")
    assert restored.page_count == 3
    assert restored.stock_names == {"000001": "平安银行", "600519": "贵州茅台"}


def test_evidence_ref_roundtrip_with_and_without_offsets() -> None:
    with_offset = EvidenceRef(
        evidence_id="ev-1",
        document_id="doc-1",
        start_offset=10,
        end_offset=40,
        excerpt="短摘录",
        source_url="https://example.test/pdf#page=3",
    )
    assert _roundtrip(with_offset) == with_offset

    pdf_without_offsets = EvidenceRef(
        evidence_id="ev-2",
        document_id="doc-2",
        start_offset=None,
        end_offset=None,
        excerpt="PDF 摘录",
        source_url="https://example.test/pdf",
    )
    assert _roundtrip(pdf_without_offsets) == pdf_without_offsets


def test_event_cluster_roundtrip_keeps_mutable_document_ids() -> None:
    cluster = EventCluster(
        event_id="event-1",
        stock_codes=("000001",),
        canonical_title="平安银行获得重大订单",
        first_seen_at=datetime(2026, 8, 5, 10, 0, tzinfo=SHANGHAI_TZ),
        last_seen_at=datetime(2026, 8, 5, 11, 0, tzinfo=SHANGHAI_TZ),
        representative_document_id="doc-1",
        document_ids=["doc-1", "doc-2"],
        historical_similar_event_id="event-0",
    )
    restored = _roundtrip(cluster)
    assert restored.event_id == cluster.event_id
    assert restored.document_ids == ["doc-1", "doc-2"]
    assert restored.historical_similar_event_id == "event-0"
    # The cluster is mutable so later documents can join the same event.
    restored.document_ids.append("doc-3")
    assert restored.document_ids == ["doc-1", "doc-2", "doc-3"]


def test_event_extraction_roundtrip_preserves_metrics_and_counter_evidence() -> None:
    extraction = EventExtraction(
        event_id="event-1",
        stock_code="000001",
        event_type="major_contract",
        direction="positive",
        positive_mechanism="中标后按合同确认收入",
        metrics=(
            {
                "name": "合同金额",
                "value": 120000000,
                "unit": "元",
                "comparison_basis": "上年营收",
                "comparison_ratio": 0.05,
                "evidence_id": "ev-1",
            },
        ),
        certainty_stage="signed",
        certainty=0.9,
        novelty=0.8,
        unexpectedness=0.7,
        materiality_level=3,
        counter_evidence=({"kind": "counter", "evidence_id": "ev-2"},),
        evidence_ids=("ev-1",),
        no_valid_signal=False,
        extractor_kind="rules",
        extractor_version="1.0",
    )
    restored = _roundtrip(extraction)
    assert restored == extraction
    assert restored.metrics[0]["value"] == 120000000


def test_event_signal_roundtrip() -> None:
    signal = EventSignal(
        event_id="event-1",
        stock_code="000001",
        board="confirmed_positive",
        score=85.5,
        source_confidence=0.9,
        materiality_level=3,
        certainty=0.9,
        unexpectedness=0.7,
        novelty=0.8,
        timeliness=0.6,
        penalty=0.0,
        provisional=False,
    )
    assert _roundtrip(signal) == signal


def test_institution_and_alias_roundtrip() -> None:
    institution = Institution(
        institution_id="inst-1",
        canonical_name="某基金管理有限公司",
        group_id="group-1",
        institution_type="public_fund",
        verification_status="normalized",
    )
    assert _roundtrip(institution) == institution

    alias = InstitutionAlias(
        normalized_alias="某基金",
        institution_id="inst-1",
        source="exact_rule",
    )
    assert _roundtrip(alias) == alias


def test_research_activity_roundtrip_with_dates_and_topics() -> None:
    activity = ResearchActivity(
        activity_id="activity-1",
        stock_code="000001",
        source_document_id="doc-1",
        activity_dates=(date(2026, 8, 4), date(2026, 8, 5)),
        activity_type="investor_relations",
        reported_participant_count=12,
        named_participant_count=3,
        question_count=20,
        high_depth_question_count=5,
        topic_counts={"growth": 8, "customers": 4},
        depth_counts={"low": 6, "medium": 9, "high": 5},
        date_precision="explicit",
    )
    restored = _roundtrip(activity)
    assert restored == activity
    assert restored.activity_dates == (date(2026, 8, 4), date(2026, 8, 5))
    assert restored.topic_counts == {"growth": 8, "customers": 4}
    assert restored.depth_counts == {"low": 6, "medium": 9, "high": 5}
    assert restored.date_precision == "explicit"

    legacy = ResearchActivity(
        activity_id="activity-old",
        stock_code="000001",
        source_document_id="doc-old",
        activity_dates=(date(2026, 8, 4),),
        activity_type="investor_relations",
        reported_participant_count=None,
        named_participant_count=0,
        question_count=0,
        high_depth_question_count=0,
        topic_counts={},
    )
    restored_legacy = _roundtrip(legacy)
    assert restored_legacy.depth_counts == {}
    assert restored_legacy.date_precision == "explicit"


def test_research_participant_roundtrip_with_and_without_analyst() -> None:
    named = ResearchParticipant(
        activity_id="activity-1",
        institution_id="inst-1",
        analyst_name="张三",
        evidence_id="ev-1",
    )
    assert _roundtrip(named) == named

    unnamed = ResearchParticipant(
        activity_id="activity-1",
        institution_id="inst-2",
        analyst_name=None,
        evidence_id="ev-2",
    )
    assert _roundtrip(unnamed) == unnamed


def test_coverage_state_roundtrip_full_and_cold_start() -> None:
    full = CoverageState(
        source_key="irm",
        requested_start=date(2026, 1, 1),
        covered_start=date(2026, 1, 2),
        covered_end=date(2026, 7, 31),
        trading_days_covered=140,
        reached_cutoff=True,
        provisional=False,
        error=None,
    )
    assert _roundtrip(full) == full

    cold_start = CoverageState(
        source_key="irm",
        requested_start=date(2026, 1, 1),
        covered_start=None,
        covered_end=None,
        trading_days_covered=0,
        reached_cutoff=False,
        provisional=True,
        error="无历史数据",
    )
    assert _roundtrip(cold_start) == cold_start


def test_sync_cursor_roundtrip() -> None:
    cursor = SyncCursor(
        source_key="cninfo",
        sync_kind="announcement_list",
        cursor={"page": 3, "last_ts": 1754000000},
        target_start=date(2026, 1, 1),
        covered_start=date(2026, 3, 1),
        last_success_at=datetime(2026, 8, 5, 12, 0, tzinfo=SHANGHAI_TZ),
        last_error=None,
        updated_at=datetime(2026, 8, 5, 12, 5, tzinfo=SHANGHAI_TZ),
    )
    restored = _roundtrip(cursor)
    assert restored == cursor
    assert restored.cursor == {"page": 3, "last_ts": 1754000000}

    failed = SyncCursor(
        source_key="cninfo",
        sync_kind="announcement_list",
        cursor=None,
        target_start=None,
        covered_start=None,
        last_success_at=None,
        last_error="接口超时",
        updated_at=datetime(2026, 8, 5, 12, 5, tzinfo=SHANGHAI_TZ),
    )
    assert _roundtrip(failed) == failed


def test_research_coverage_roundtrip() -> None:
    full = ResearchCoverage(
        requested_start=date(2026, 1, 1),
        covered_start=date(2026, 1, 2),
        covered_end=date(2026, 7, 31),
        trading_days_covered=140,
        sources_scanned=3,
        sources_total=3,
        reached_cutoff=True,
        calendar_fallback=False,
        last_success_at=datetime(2026, 8, 5, 12, 0, tzinfo=SHANGHAI_TZ),
        provisional=False,
        error=None,
    )
    assert _roundtrip(full) == full

    cold = ResearchCoverage(
        requested_start=date(2026, 1, 1),
        covered_start=None,
        covered_end=None,
        trading_days_covered=0,
        sources_scanned=0,
        sources_total=3,
        reached_cutoff=False,
        calendar_fallback=True,
        last_success_at=None,
        provisional=True,
        error="冷启动",
    )
    assert _roundtrip(cold) == cold


def test_z20_row_roundtrip_full_and_cold_start() -> None:
    full = Z20Row(
        stock_code="000001",
        industry="半导体",
        z20=2.5,
        current_unique_groups=8,
        new_groups=3,
        analyst_count=4,
        high_depth_ratio=0.5,
        question_count=20,
        recent_activity=date(2026, 8, 6),
        industry_percentile=90.0,
        industry_sample_size=6,
        provisional=False,
    )
    assert _roundtrip(full) == full

    cold = Z20Row(
        stock_code="600519",
        industry=None,
        z20=None,
        current_unique_groups=2,
        new_groups=2,
        analyst_count=0,
        high_depth_ratio=0.0,
        question_count=0,
        recent_activity=None,
        industry_percentile=None,
        industry_sample_size=0,
        provisional=True,
    )
    assert _roundtrip(cold) == cold


def test_persistence_row_roundtrip() -> None:
    row = PersistenceRow(
        stock_code="000001",
        window_kind="persistence_120",
        persistence_score=61.5,
        active_weeks=8,
        active_week_ratio=0.5,
        unique_groups=9,
        repeat_followup_ratio=0.33,
        depth_score=0.6,
        single_day_concentration=0.25,
        topics={"customers": 3, "growth": 2},
        recent_activity=date(2026, 8, 6),
        covered_trading_days=120,
    )
    assert _roundtrip(row) == row


def test_structural_comparison_roundtrip() -> None:
    comparison = StructuralComparison(
        stock_code="000001",
        new_groups=("group-b",),
        lost_groups=("group-a",),
        type_share_changes={"brokerage": 0.1, "public_fund": -0.1},
        high_depth_ratio_change=0.2,
        active_week_ratio_change=0.05,
        single_day_concentration_change=-0.1,
    )
    assert _roundtrip(comparison) == comparison
