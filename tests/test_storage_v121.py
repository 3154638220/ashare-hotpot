"""v2 schema 120→121 迁移与多事实/参与者提及层存储 (plan.md 第三部分)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import (
    EVENT_CLAIM_REVIEW_VERIFIED,
    EventClaim,
    EventCluster,
    EventExtraction,
    ResearchActivity,
    ReportedParticipantCount,
    ResearchParticipantMention,
    SourceDocument,
)
from ashare_hotpot.storage import BACKUP_NAME_121, SCHEMA_VERSION, Storage


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=SHANGHAI_TZ)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _create_120_database(path: Path) -> Storage:
    """Create a true version-120 database by dropping the v2 tables."""

    storage = Storage(path)
    with storage._connect() as connection:
        connection.execute("DROP TABLE IF EXISTS reported_participant_counts")
        connection.execute(
            "DROP TABLE IF EXISTS research_participant_mentions"
        )
        connection.execute("DROP TABLE IF EXISTS event_claims")
        connection.execute("PRAGMA user_version = 120")
    return storage


def _sample_document(document_id: str = "doc-1") -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url="https://example.test/doc-1",
        document_url=None,
        title="关于签订重大合同的公告",
        published_at=NOW - timedelta(days=1),
        stock_codes=("600000",),
        body_text="公司签订重大合同，合同金额1.2亿元。",
        content_hash="hash-doc-1",
        parse_status="parsed",
        parse_error=None,
    )


def _claim(claim_id: str = "claim-1", document_id: str = "doc-1") -> EventClaim:
    return EventClaim(
        claim_id=claim_id,
        document_id=document_id,
        stock_code="600000",
        event_type="major_contract",
        direction="positive",
        positive_mechanism="新增合同或订单预计增厚未来营业收入",
        metrics=(
            {
                "name": "合同金额",
                "value": 1.2,
                "unit": "亿元",
                "comparison_basis": "最近一个会计年度营业收入",
                "comparison_ratio": 0.12,
                "evidence_id": "ev-1",
            },
        ),
        certainty_stage="signed_contract",
        certainty=0.9,
        materiality_level=2,
        counter_evidence=(),
        evidence_ids=("ev-1",),
        rejection_reason=None,
        review_status="pending_review",
        gate_trace=(
            {"gate": "mechanism", "passed": True, "reason": "正向机制存在"},
        ),
        extractor_kind="rules",
        extractor_version="rules-v1",
        created_at=NOW,
    )


def _mention(mention_id: str = "mention-1") -> ResearchParticipantMention:
    return ResearchParticipantMention(
        mention_id=mention_id,
        document_id="doc-act-1",
        activity_id="act-1",
        raw_name="中信证券",
        start_offset=0,
        end_offset=4,
        organization_category="research_institution",
        parse_version="v2-20260809",
        review_status="pending_review",
        evidence_id="ev-1",
        created_at=NOW,
    )


def _seed_cluster(storage: Storage) -> None:
    storage.upsert_event_cluster(
        EventCluster(
            event_id="evt-1",
            stock_codes=("600000",),
            canonical_title="关于签订重大合同的公告",
            first_seen_at=NOW - timedelta(days=2),
            last_seen_at=NOW - timedelta(days=1),
            representative_document_id="doc-1",
            document_ids=["doc-1"],
            historical_similar_event_id=None,
        )
    )


def _seed_activity(storage: Storage) -> None:
    storage.upsert_research_activity(
        ResearchActivity(
            activity_id="act-1",
            stock_code="600000",
            source_document_id="doc-act-1",
            activity_dates=(NOW.date(),),
            activity_type="research",
            reported_participant_count=None,
            named_participant_count=2,
            question_count=0,
            high_depth_question_count=0,
            topic_counts={},
        ),
        NOW,
    )


EXPECTED_V121_TABLES = {
    "event_claims",
    "research_participant_mentions",
    "reported_participant_counts",
}


def test_fresh_database_reports_121_and_v2_schema(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V121_TABLES <= _table_names(connection)


def test_120_database_upgrades_to_121_with_backup_once_and_stays_idempotent(
    tmp_path,
) -> None:
    path = tmp_path / "hotpot.db"
    storage = _create_120_database(path)
    storage.upsert_source_document(_sample_document(), NOW)
    _seed_cluster(storage)
    storage.upsert_event_extraction(
        EventExtraction(
            event_id="evt-1",
            stock_code="600000",
            event_type="major_contract",
            direction="positive",
            positive_mechanism="正向",
            metrics=(),
            certainty_stage="signed_contract",
            certainty=0.9,
            novelty=1.0,
            unexpectedness=0.5,
            materiality_level=2,
            counter_evidence=(),
            evidence_ids=(),
            no_valid_signal=False,
            extractor_kind="rules",
            extractor_version="rules-v1",
        ),
        NOW,
    )

    storage = Storage(path)
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V121_TABLES <= _table_names(connection)

    # pre-121 备份只创建一次；重复初始化不覆盖备份、不重复建表。
    backup_path = path.with_name(BACKUP_NAME_121)
    assert backup_path.exists()
    backup_before = backup_path.read_bytes()
    storage.initialize()
    assert backup_path.read_bytes() == backup_before

    # 120 时代的 legacy 抽取结果仍可读。
    extraction = storage.get_event_extraction("evt-1", "600000")
    assert extraction is not None
    assert extraction.event_type == "major_contract"


def test_120_migration_rolls_back_and_keeps_120_database(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "hotpot.db"
    storage = _create_120_database(path)
    storage.upsert_source_document(_sample_document(), NOW)

    monkeypatch.setattr(
        "ashare_hotpot.storage.V121_TABLE_STATEMENTS", ("THIS IS NOT SQL",)
    )
    with pytest.raises(sqlite3.OperationalError):
        Storage(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 120
        assert "event_claims" not in _table_names(connection)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_documents"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()
    assert path.with_name(BACKUP_NAME_121).exists()

    # 修复后重试可升级到 121。
    monkeypatch.undo()
    storage = Storage(path)
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V121_TABLES <= _table_names(connection)
    assert storage.get_source_document("doc-1") is not None


def test_event_claim_crud_roundtrip_and_update(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_sample_document(), NOW)
    storage.upsert_event_claim(_claim())
    storage.upsert_event_claim(
        EventClaim(
            claim_id="claim-2",
            document_id="doc-1",
            stock_code="600000",
            event_type="major_contract",
            direction="positive",
            positive_mechanism="另一候选事实",
            metrics=(),
            certainty_stage="framework",
            certainty=0.45,
            materiality_level=1,
            counter_evidence=(),
            evidence_ids=(),
            rejection_reason="重大性不足",
            review_status="rejected",
            gate_trace=(),
            extractor_kind="rules",
            extractor_version="rules-v1",
            created_at=NOW,
        )
    )

    by_doc = storage.get_event_claims_by_document("doc-1")
    assert len(by_doc) == 2
    by_stock = storage.get_event_claims_by_stock("600000")
    assert len(by_stock) == 2
    assert {claim.claim_id for claim in by_stock} == {"claim-1", "claim-2"}

    # 复核状态更新（同一 claim_id upsert 覆盖）。
    storage.upsert_event_claim(
        EventClaim(
            claim_id="claim-1",
            document_id="doc-1",
            stock_code="600000",
            event_type="major_contract",
            direction="positive",
            positive_mechanism="新增合同或订单预计增厚未来营业收入",
            metrics=(),
            certainty_stage="signed_contract",
            certainty=0.9,
            materiality_level=2,
            counter_evidence=(),
            evidence_ids=("ev-1",),
            rejection_reason=None,
            review_status=EVENT_CLAIM_REVIEW_VERIFIED,
            gate_trace=(),
            extractor_kind="rules",
            extractor_version="rules-v1",
            created_at=NOW,
        )
    )
    claims = storage.get_event_claims_by_stock("600000")
    by_id = {claim.claim_id: claim for claim in claims}
    assert by_id["claim-1"].review_status == EVENT_CLAIM_REVIEW_VERIFIED
    assert len(by_id["claim-2"].gate_trace) == 0


def test_participant_mentions_replace_atomically(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    document = SourceDocument(
        document_id="doc-act-1",
        provider_key="irm",
        provider_name="深交所互动易",
        kind="research_activity",
        source_url="https://example.test/doc-act-1",
        document_url=None,
        title="投资者关系活动记录表",
        published_at=NOW,
        stock_codes=("600000",),
        body_text="参与单位：中信证券、易方达基金",
        content_hash="hash-act-1",
        parse_status="parsed",
        parse_error=None,
    )
    storage.upsert_source_document(document, NOW)
    _seed_activity(storage)
    storage.replace_participant_mentions(
        "act-1",
        [_mention("mention-1"), _mention("mention-2")],
    )
    mentions = storage.get_participant_mentions("act-1")
    assert len(mentions) == 2
    assert mentions[0].raw_name == "中信证券"

    # 新解析版本原子替换旧提及。
    storage.replace_participant_mentions("act-1", [_mention("mention-3")])
    mentions = storage.get_participant_mentions("act-1")
    assert [m.mention_id for m in mentions] == ["mention-3"]


def test_reported_participant_count_upsert_and_read(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(
        SourceDocument(
            document_id="doc-act-1",
            provider_key="irm",
            provider_name="深交所互动易",
            kind="research_activity",
            source_url="https://example.test/doc-act-1",
            document_url=None,
            title="投资者关系活动记录表",
            published_at=NOW,
            stock_codes=("600000",),
            body_text="参与单位：中信证券",
            content_hash="hash-act-1",
            parse_status="parsed",
            parse_error=None,
        ),
        NOW,
    )
    _seed_activity(storage)
    storage.upsert_reported_participant_count(
        ReportedParticipantCount(
            activity_id="act-1",
            named_research_count=6,
            all_named_org_count=9,
            reported_institution_count=31,
            reported_person_count=45,
            evidence_id="ev-1",
            updated_at=NOW,
        )
    )
    count = storage.get_reported_participant_count("act-1")
    assert count is not None
    assert count.named_research_count == 6
    assert count.reported_institution_count == 31
    assert storage.get_reported_participant_count("missing") is None


def test_clear_all_removes_v2_tables_and_stats_include_counts(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_sample_document(), NOW)
    storage.upsert_event_claim(_claim())
    storage.upsert_source_document(
        SourceDocument(
            document_id="doc-act-1",
            provider_key="irm",
            provider_name="深交所互动易",
            kind="research_activity",
            source_url="https://example.test/doc-act-1",
            document_url=None,
            title="投资者关系活动记录表",
            published_at=NOW,
            stock_codes=("600000",),
            body_text="参与单位：中信证券",
            content_hash="hash-act-1",
            parse_status="parsed",
            parse_error=None,
        ),
        NOW,
    )
    _seed_activity(storage)
    storage.upsert_reported_participant_count(
        ReportedParticipantCount(
            activity_id="act-1",
            named_research_count=1,
            all_named_org_count=1,
            reported_institution_count=None,
            reported_person_count=None,
            evidence_id=None,
            updated_at=NOW,
        )
    )
    stats = storage.get_storage_stats()
    assert stats.event_claim_count == 1
    assert stats.reported_participant_count_count == 1

    storage.clear_all()
    stats = storage.get_storage_stats()
    assert stats.event_claim_count == 0
    assert stats.participant_mention_count == 0
    assert stats.reported_participant_count_count == 0
    assert storage.get_event_claims_by_stock("600000") == []


def test_retention_cascades_v2_rows_with_parents(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_sample_document(), NOW)
    storage.upsert_event_claim(_claim())
    assert len(storage.get_event_claims_by_document("doc-1")) == 1

    # 删除父文档时事件候选事实级联删除。
    with storage._connect() as connection:
        connection.execute(
            "DELETE FROM source_documents WHERE document_id = ?", ("doc-1",)
        )
    assert storage.get_event_claims_by_document("doc-1") == []
