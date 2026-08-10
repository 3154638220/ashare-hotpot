"""Offline tests for the milestone-6 evaluation tooling (scripts/evaluation)."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import (
    DiscoveryCandidate,
    EventCluster,
    EventExtraction,
    EventSignal,
    EvidenceRef,
    Institution,
    ResearchActivity,
    ResearchParticipant,
    SourceDocument,
    SyncCursor,
)
from ashare_hotpot.storage import Storage

EVAL_DIR = Path(__file__).resolve().parents[1] / "scripts" / "evaluation"
sys.path.insert(0, str(EVAL_DIR))

import export_eval_sets  # noqa: E402
import score_eval_sets  # noqa: E402


def _ts(days_back: int) -> datetime:
    return datetime.now(SHANGHAI_TZ) - timedelta(days=days_back)


def _seed_db(storage: Storage) -> None:
    now = _ts(0)
    documents = [
        SourceDocument(
            document_id="doc-a",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            source_url="https://example.com/doc-a",
            document_url=None,
            title="关于中标重大合同的公告",
            published_at=_ts(2),
            stock_codes=("000001",),
            body_text="公司近期中标重大项目。",
            content_hash="hash-a",
            parse_status="parsed",
            parse_error=None,
        ),
        SourceDocument(
            document_id="doc-b",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            source_url="https://example.com/doc-b",
            document_url=None,
            title="2026 年半年度业绩预告上修",
            published_at=_ts(3),
            stock_codes=("000002",),
            body_text="预计净利润同比大幅增长。",
            content_hash="hash-b",
            parse_status="parsed",
            parse_error=None,
        ),
        SourceDocument(
            document_id="doc-c",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="research_activity",
            source_url="https://example.com/doc-c",
            document_url="https://example.com/doc-c.pdf",
            title="投资者关系活动记录表",
            published_at=_ts(5),
            stock_codes=("000003",),
            body_text="多家机构参与本次调研。",
            content_hash="hash-c",
            parse_status="parsed",
            parse_error=None,
        ),
    ]
    for document in documents:
        storage.upsert_source_document(document, now)
        storage.upsert_evidence_ref(
            EvidenceRef(
                evidence_id=f"ev-{document.document_id}",
                document_id=document.document_id,
                start_offset=0,
                end_offset=12,
                excerpt=f"摘录 {document.title}",
                source_url=document.source_url,
            )
        )

    storage.upsert_event_cluster(
        EventCluster(
            event_id="evt-1",
            stock_codes=("000001",),
            canonical_title="中标重大合同",
            first_seen_at=_ts(2),
            last_seen_at=_ts(1),
            representative_document_id="doc-a",
            document_ids=["doc-a"],
            historical_similar_event_id=None,
        )
    )
    storage.upsert_event_cluster(
        EventCluster(
            event_id="evt-2",
            stock_codes=("000002",),
            canonical_title="业绩预告上修",
            first_seen_at=_ts(4),
            last_seen_at=_ts(3),
            representative_document_id="doc-b",
            document_ids=["doc-b"],
            historical_similar_event_id=None,
        )
    )
    storage.upsert_event_cluster(
        EventCluster(
            event_id="evt-3",
            stock_codes=("000003",),
            canonical_title="调研活动",
            first_seen_at=_ts(6),
            last_seen_at=_ts(5),
            representative_document_id="doc-c",
            document_ids=["doc-c"],
            historical_similar_event_id=None,
        )
    )

    storage.upsert_event_extraction(
        EventExtraction(
            event_id="evt-1",
            stock_code="000001",
            event_type="major_contract",
            direction="positive",
            positive_mechanism="合同收入有望增厚当期利润",
            metrics=(
                {
                    "name": "合同金额",
                    "value": 2.0,
                    "unit": "亿元",
                    "comparison_basis": "最近一年营收",
                    "comparison_ratio": 0.12,
                    "evidence_id": "ev-doc-a",
                },
            ),
            certainty_stage="signed_contract",
            certainty=0.9,
            novelty=1.0,
            unexpectedness=0.75,
            materiality_level=3,
            counter_evidence=(),
            evidence_ids=("ev-doc-a",),
            no_valid_signal=False,
            extractor_kind="rules",
            extractor_version="rules-v1",
        ),
        now,
    )
    storage.upsert_event_signal(
        EventSignal(
            event_id="evt-1",
            stock_code="000001",
            board="confirmed_positive",
            score=75.0,
            source_confidence=1.0,
            materiality_level=3,
            certainty=0.9,
            unexpectedness=0.75,
            novelty=1.0,
            timeliness=0.9,
            penalty=0.0,
            provisional=False,
        ),
        created_at=now,
    )
    storage.upsert_event_extraction(
        EventExtraction(
            event_id="evt-2",
            stock_code="000002",
            event_type="earnings_upgrade",
            direction="positive",
            positive_mechanism="利润增速上修",
            metrics=(),
            certainty_stage="board_approved",
            certainty=0.7,
            novelty=0.6,
            unexpectedness=0.5,
            materiality_level=2,
            counter_evidence=(),
            evidence_ids=("ev-doc-b",),
            no_valid_signal=False,
            extractor_kind="rules",
            extractor_version="rules-v1",
        ),
        now,
    )
    storage.upsert_event_signal(
        EventSignal(
            event_id="evt-2",
            stock_code="000002",
            board="potential_catalyst",
            score=40.0,
            source_confidence=0.75,
            materiality_level=2,
            certainty=0.7,
            unexpectedness=0.5,
            novelty=0.6,
            timeliness=0.8,
            penalty=0.0,
            provisional=True,
        ),
        created_at=now,
    )

    for institution in (
        Institution(
            institution_id="inst-1",
            canonical_name="国泰君安证券",
            group_id="grp-1",
            institution_type="brokerage",
            verification_status="verified",
        ),
        Institution(
            institution_id="inst-2",
            canonical_name="易方达基金",
            group_id="grp-2",
            institution_type="public_fund",
            verification_status="verified",
        ),
    ):
        storage.upsert_institution(institution, now)
    storage.upsert_research_activity(
        ResearchActivity(
            activity_id="act-1",
            stock_code="000003",
            source_document_id="doc-c",
            activity_dates=(date(2026, 7, 10),),
            activity_type="research",
            reported_participant_count=5,
            named_participant_count=2,
            question_count=3,
            high_depth_question_count=1,
            topic_counts={"growth": 2},
        ),
        now,
    )
    storage.add_research_participant(
        ResearchParticipant(
            activity_id="act-1",
            institution_id="inst-1",
            analyst_name=None,
            evidence_id="ev-doc-c",
        )
    )
    storage.add_research_participant(
        ResearchParticipant(
            activity_id="act-1",
            institution_id="inst-2",
            analyst_name="张三",
            evidence_id="ev-doc-c",
        )
    )


@pytest.fixture
def seeded_db(tmp_path: Path) -> Storage:
    storage = Storage(tmp_path / "eval.db")
    _seed_db(storage)
    return storage


def _export_files(tmp_path: Path, db_path: Path) -> tuple[dict, dict]:
    short_term = export_eval_sets.export_short_term(
        db_path,
        tmp_path / export_eval_sets.SHORT_TERM_FILE,
        seed=42,
        max_events=300,
    )
    institution = export_eval_sets.export_institution(
        db_path,
        tmp_path / export_eval_sets.INSTITUTION_FILE,
        seed=42,
        max_records=100,
    )
    return short_term, institution


def test_export_short_term_candidate_structure(seeded_db: Storage, tmp_path: Path) -> None:
    data, _ = _export_files(tmp_path, seeded_db.database_path)
    assert data["kind"] == "short_term_events"
    assert data["schema_version"] == 2
    assert data["meta"]["event_cluster_count_total"] == 3
    assert data["meta"]["event_cluster_count_sampled"] == 3
    # Board rows follow plan.md 10.6 order: score desc.
    assert [row["score"] for row in data["board"]] == [75.0, 40.0]
    assert [row["rank"] for row in data["board"]] == [1, 2]
    # Labels must be null in exported candidates.
    assert all(row["relevant"] is None and row["duplicate"] is None for row in data["board"])
    assert all(event["label"] is None for event in data["events"])
    by_id = {event["event_id"]: event for event in data["events"]}
    assert by_id["evt-1"]["canonical_title"] == "中标重大合同"
    assert by_id["evt-1"]["representative"]["document_id"] == "doc-a"
    assert by_id["evt-1"]["evidence"][0]["evidence_id"] == "ev-doc-a"
    # v2 分层与错误账本标签位（plan.md 第三部分 里程碑 1）。
    assert by_id["evt-1"]["stratum"] == "cninfo_announcement"
    assert by_id["evt-1"]["layout"] == "unknown"
    assert by_id["evt-1"]["engine"]["event_type"] == "major_contract"
    assert by_id["evt-1"]["engine"]["direction"] == "positive"
    assert by_id["evt-1"]["must_hit_candidate"] is None
    assert by_id["evt-1"]["error_types"] is None
    assert data["must_hit"] == []


def test_export_never_generates_deterministic_must_hit(
    seeded_db: Storage, tmp_path: Path
) -> None:
    """v2 里程碑 1：废弃 event_extractions 反向生成必达集的自证口径。"""

    data = export_eval_sets.export_short_term(
        seeded_db.database_path,
        tmp_path / export_eval_sets.SHORT_TERM_FILE,
        seed=42,
        max_events=300,
    )
    # evt-1/evt-2 在库中都有正向抽取，但导出器绝不据此填充 must_hit。
    assert data["must_hit"] == []
    assert all(event["must_hit_candidate"] is None for event in data["events"])


def test_export_is_deterministic(seeded_db: Storage, tmp_path: Path) -> None:
    first, first_inst = _export_files(tmp_path, seeded_db.database_path)
    second, second_inst = _export_files(tmp_path, seeded_db.database_path)
    assert first["board"] == second["board"]
    assert first["events"] == second["events"]
    assert first_inst["records"] == second_inst["records"]


def test_export_institution_candidate_structure(
    seeded_db: Storage, tmp_path: Path
) -> None:
    _, data = _export_files(tmp_path, seeded_db.database_path)
    assert data["kind"] == "institution_records"
    assert data["meta"]["activity_count_sampled"] == 1
    record = data["records"][0]
    assert record["activity_id"] == "act-1"
    assert record["activity_dates"] == ["2026-07-10"]
    # 活动正文评估导出：原文正文必须随样本导出，供“原文明确列名机构”标注。
    assert record["source"]["body_text"] == "多家机构参与本次调研。"
    assert record["source"]["provider_key"] == "cninfo"
    assert record["stratum"] == "cninfo_research"
    assert [p["institution_id"] for p in record["participants"]] == [
        "inst-1",
        "inst-2",
    ]
    assert all(
        p["entity_ok"] is None and p["group_ok"] is None
        for p in record["participants"]
    )
    assert record["participants"][1]["analyst_name"] == "张三"
    # No cached articles/interactions -> stock name falls back to the code.
    assert record["stock_name"] == "000003"


def test_export_refuses_non_v110_database(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    Storage(db_path)
    import sqlite3

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()
    with pytest.raises(SystemExit):
        export_eval_sets.export_short_term(
            db_path, tmp_path / "out.json", seed=1, max_events=300
        )


def _short_term_data(
    *,
    board_rows: list[dict] | None = None,
    must_hit_event_ids: set[str] | None = None,
) -> dict:
    must_hit_event_ids = must_hit_event_ids or set()
    events = [
        {
            "event_id": f"evt-{index:04d}",
            "stock_codes": ["000001"],
            "canonical_title": f"事件 {index}",
            "first_seen_at": "2026-07-01T00:00:00+08:00",
            "last_seen_at": "2026-07-02T00:00:00+08:00",
            "historical_similar_event_id": None,
            "representative": {},
            "document_count": 1,
            "evidence": [],
            "label": "positive_signal",
            "must_hit_candidate": f"evt-{index:04d}" in must_hit_event_ids,
            "error_types": [],
        }
        for index in range(300)
    ]
    if board_rows is None:
        board_rows = [
            {
                "rank": index + 1,
                "event_id": f"evt-{index:04d}",
                "stock_code": "000001",
                "board": "confirmed_positive",
                "score": 100.0 - index,
                "materiality_level": 2,
                "certainty": 0.9,
                "provisional": False,
                "event_type": "major_contract",
                "extractor_kind": "rules",
                "relevant": True,
                "duplicate": False,
            }
            for index in range(20)
        ]
    return {
        "schema_version": 2,
        "kind": "short_term_events",
        "meta": {},
        "board": board_rows,
        "events": events,
        "must_hit": [],
    }


def test_score_short_term_passes_all_gates() -> None:
    data = _short_term_data(
        must_hit_event_ids={"evt-0000", "evt-0001"}
    )
    score = score_eval_sets.score_short_term(data)
    assert score.passed
    assert score.precision_at_10 == 1.0
    assert score.top20_irrelevant_ratio == 0.0
    assert score.top20_duplicate_ratio == 0.0
    assert score.must_hit_recall == 1.0
    assert score.must_hit_count == 2
    assert score.error_ledger["counts"] == {
        "type_error": 0,
        "direction_error": 0,
        "materiality_error": 0,
        "duplicate_clustering": 0,
    }
    assert score.event_cluster_count == 300


def test_score_short_term_must_hit_uses_independent_labels() -> None:
    """v2 里程碑 1：must-hit 只来自独立标注，不由引擎抽取生成。"""

    # 引擎旧的 data["must_hit"] 字段即使被填满也不参与评分。
    data = _short_term_data(must_hit_event_ids={"evt-0000"})
    data["must_hit"] = [
        {"event_id": "evt-0099", "stock_code": "000001", "note": "deterministic:"}
    ]
    score = score_eval_sets.score_short_term(data)
    # evt-0099 不在标注必达集内，旧字段不产生任何影响。
    assert score.must_hit_count == 1
    assert score.must_hit_recall == 1.0


def test_score_short_term_unparsed_must_hit_excluded_by_evidence_rule() -> None:
    """v2 证据规则（plan §6）：parse_status 非 parsed 的事件没有正文，标题
    不足以支撑证据，不进入必达召回分母（M2 附件队列缺口单独跟踪）。"""

    data = _short_term_data(must_hit_event_ids={"evt-0000", "evt-0001"})
    data["events"][0]["representative"] = {
        "document_id": "doc-1",
        "title": "关于…的公告",
        "parse_status": "metadata_only",
    }
    data["events"][1]["representative"] = {
        "document_id": "doc-2",
        "title": "关于…的公告",
        "parse_status": "parsed",
    }
    score = score_eval_sets.score_short_term(data)
    # evt-0000（无正文）被排除，evt-0001 保留 → 分母 1，命中 1。
    assert score.must_hit_count == 1
    assert score.unparsed_must_hit_count == 1
    assert score.must_hit_recall == 1.0


def test_score_short_term_missing_must_hit_label_raises() -> None:
    data = _short_term_data(must_hit_event_ids={"evt-0000"})
    data["events"][5]["must_hit_candidate"] = None
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_short_term(data)


def test_score_short_term_error_ledger_missing_label_raises() -> None:
    data = _short_term_data()
    data["events"][2]["error_types"] = None
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_short_term(data)


def test_score_short_term_error_ledger_counts_and_samples() -> None:
    data = _short_term_data()
    data["events"][0]["error_types"] = ["direction_error"]
    data["events"][1]["error_types"] = ["duplicate_clustering", "type_error"]
    data["events"][2]["error_types"] = []
    score = score_eval_sets.score_short_term(data)
    counts = score.error_ledger["counts"]
    assert counts["direction_error"] == 1
    assert counts["duplicate_clustering"] == 1
    assert counts["type_error"] == 1
    assert counts["materiality_error"] == 0
    assert score.error_ledger["total_events"] == 300
    assert score.error_ledger["samples"][0]["event_id"] == "evt-0000"


def test_score_short_term_fails_irrelevant_top20() -> None:
    rows = _short_term_data()["board"]
    for row in rows[17:20]:
        row["relevant"] = False
    score = score_eval_sets.score_short_term(
        {**_short_term_data(), "board": rows}
    )
    assert not score.passed
    gate = next(gate for gate in score.gates if gate.name == "top20_irrelevant")
    assert gate.value == pytest.approx(0.15)
    assert not gate.passed


def test_score_short_term_missing_label_raises() -> None:
    rows = _short_term_data()["board"]
    rows[3]["relevant"] = None
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_short_term({**_short_term_data(), "board": rows})


def _institution_data(entity_ok_count: int, group_ok_count: int) -> dict:
    records = []
    for index in range(100):
        records.append(
            {
                "activity_id": f"act-{index:04d}",
                "stock_code": "000003",
                "stock_name": "000003",
                "activity_type": "research",
                "activity_dates": ["2026-07-10"],
                "reported_participant_count": 2,
                "named_participant_count": 1,
                "question_count": 3,
                "high_depth_question_count": 1,
                "date_precision": "explicit",
                "source": {
                    "document_id": f"doc-{index:04d}",
                    "title": "投资者关系活动记录表",
                    "source_url": "http://x",
                    "document_url": None,
                    "published_at": "2026-08-02T00:00:00+08:00",
                    "parse_status": "parsed",
                    "provider_key": "cninfo",
                    "body_text": f"参与单位：机构 {index}",
                    "body_truncated": False,
                },
                "stratum": "cninfo_research",
                "named_institutions": [f"机构 {index}"],
                "participants": [
                    {
                        "institution_id": f"inst-{index:04d}",
                        "canonical_name": f"机构 {index}",
                        "group_id": f"grp-{index:04d}",
                        "institution_type": "brokerage",
                        "verification_status": "verified",
                        "analyst_name": None,
                        "evidence_id": "ev-1",
                        "entity_ok": index < entity_ok_count,
                        "group_ok": index < group_ok_count,
                    }
                ],
            }
        )
    return {
        "schema_version": 1,
        "kind": "institution_records",
        "meta": {},
        "records": records,
    }


def test_score_institution_passes_all_gates() -> None:
    score = score_eval_sets.score_institution(_institution_data(100, 100))
    assert score.passed
    assert score.entity_precision == 1.0
    assert score.group_precision == 1.0
    assert score.named_institution_recall == 1.0
    assert score.record_count == 100
    assert score.named_institution_count == 100


def test_score_institution_fails_entity_precision() -> None:
    score = score_eval_sets.score_institution(_institution_data(85, 100))
    assert not score.passed
    gate = next(gate for gate in score.gates if gate.name == "entity_precision")
    assert gate.value == pytest.approx(0.85)
    assert not gate.passed


def test_score_institution_missing_label_raises() -> None:
    data = _institution_data(100, 100)
    data["records"][0]["participants"][0]["group_ok"] = None
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_institution(data)


def test_score_institution_named_recall_matches_seed_short_name() -> None:
    data = _institution_data(100, 100)
    # 原文列名用品牌短名，系统实体用法定全称：种子别名应匹配（不模糊合并）。
    data["records"][0]["named_institutions"] = ["国泰君安"]
    data["records"][0]["participants"][0]["canonical_name"] = (
        "国泰君安证券股份有限公司"
    )
    score = score_eval_sets.score_institution(data)
    assert score.named_institution_recall == 1.0
    gate = next(
        gate for gate in score.gates if gate.name == "named_institution_recall"
    )
    assert gate.passed


def test_score_institution_named_recall_counts_only_entity_ok() -> None:
    data = _institution_data(100, 100)
    data["records"][0]["named_institutions"] = ["机构 0"]
    # 该机构虽被提取，但实体标注错误（entity_ok=False）→ 不应计入召回命中。
    data["records"][0]["participants"][0]["entity_ok"] = False
    score = score_eval_sets.score_institution(data)
    assert score.named_institution_recall == pytest.approx(99 / 100)
    gate = next(
        gate for gate in score.gates if gate.name == "named_institution_recall"
    )
    assert gate.value == pytest.approx(0.99)


def test_score_institution_named_recall_missing_name_fails() -> None:
    data = _institution_data(100, 100)
    # 前 20 份记录原文列名“易方达基金”但参与者中无该机构 → 召回 80% < 90%。
    for record in data["records"][:20]:
        record["named_institutions"] = ["易方达基金"]
    score = score_eval_sets.score_institution(data)
    assert score.named_institution_recall == pytest.approx(80 / 100)
    assert score.passed is False
    gate = next(
        gate for gate in score.gates if gate.name == "named_institution_recall"
    )
    assert gate.value == pytest.approx(0.80)
    assert not gate.passed


def test_score_institution_missing_named_institutions_raises() -> None:
    data = _institution_data(100, 100)
    data["records"][0]["named_institutions"] = None
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_institution(data)


def test_score_institution_invalid_named_institutions_raises() -> None:
    data = _institution_data(100, 100)
    data["records"][0]["named_institutions"] = "机构 0"
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_institution(data)


def test_score_rejects_unknown_kind() -> None:
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_short_term({"kind": "other", "schema_version": 1})


def test_exported_files_roundtrip_through_scorer_requires_labels(
    seeded_db: Storage, tmp_path: Path
) -> None:
    short_term, institution = _export_files(tmp_path, seeded_db.database_path)
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_short_term(short_term)
    with pytest.raises(score_eval_sets.LabelError):
        score_eval_sets.score_institution(institution)


def _seed_discovery_rows(storage: Storage) -> None:
    now = _ts(0)
    documents = [
        SourceDocument(
            document_id="dc-1",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            source_url="https://example.com/list",
            document_url="https://static.cninfo.com.cn/finalpage/dc-1.PDF",
            title="五矿资本股份有限公司关于公司拟签订重大合同暨关联交易的公告",
            published_at=_ts(1),
            stock_codes=("600390",),
            body_text="",
            content_hash="hash-dc-1",
            parse_status="metadata_only",
            parse_error=None,
        ),
        SourceDocument(
            document_id="dc-2",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            source_url="https://example.com/list",
            document_url=None,
            title="西安炬光科技股份有限公司2026年半年度报告摘要",
            published_at=_ts(2),
            stock_codes=("688167",),
            body_text="",
            content_hash="hash-dc-2",
            parse_status="metadata_only",
            parse_error=None,
        ),
    ]
    candidates = [
        DiscoveryCandidate(
            document_id="dc-1",
            source_key="cninfo_announcement",
            source_name="巨潮资讯公告",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            stock_codes=("600390",),
            title=documents[0].title,
            published_at=_ts(1),
            discovery_type="contract_order",
            trigger_reason="标题含“重大合同”",
            queue_status="pending_attachment",
            attachment_type="PDF",
            document_url=documents[0].document_url,
            enqueued_at=_ts(1),
            updated_at=now,
            signal_priority=True,
        ),
        DiscoveryCandidate(
            document_id="dc-2",
            source_key="cninfo_announcement",
            source_name="巨潮资讯公告",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            stock_codes=("688167",),
            title=documents[1].title,
            published_at=_ts(2),
            discovery_type="financial_report",
            trigger_reason="标题含“半年报”",
            queue_status="awaiting_review",
            attachment_type=None,
            document_url=None,
            enqueued_at=None,
            updated_at=now,
            signal_priority=False,
        ),
    ]
    storage.save_research_batch(
        documents,
        candidates,
        SyncCursor(
            source_key="cninfo_announcement",
            sync_kind="announcement",
            cursor={"page": 1},
            target_start=now.date(),
            covered_start=now.date(),
            last_success_at=now,
            last_error=None,
            updated_at=now,
        ),
        now,
    )


def test_export_discovery_candidate_structure_and_fixed_cases(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "eval.db")
    _seed_db(storage)
    _seed_discovery_rows(storage)

    data = export_eval_sets.export_discovery(
        storage.database_path,
        tmp_path / export_eval_sets.DISCOVERY_FILE,
        seed=42,
        max_candidates=300,
    )

    assert data["kind"] == "discovery_candidates"
    assert data["schema_version"] == 1
    assert data["meta"]["min_discovery_samples"] == 300
    by_id = {item["document_id"]: item for item in data["items"]}
    dc_1 = by_id["dc-1"]
    assert dc_1["in_discovery_layer"] is True
    assert dc_1["queue_status"] == "pending_attachment"
    assert dc_1["discovery_type"] == "contract_order"
    assert dc_1["label"] is None
    assert dc_1["fixed_case"] is True
    dc_2 = by_id["dc-2"]
    assert dc_2["queue_status"] == "awaiting_review"
    assert dc_2["fixed_case"] is True
    # 未进入发现层的文档（直接写入，未生成候选）如实标记，不伪造完整榜。
    assert any(
        item["document_id"] == "doc-a" and item["in_discovery_layer"] is False
        for item in data["items"]
    )


def test_score_discovery_recall_and_fixed_cases(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "eval.db")
    _seed_db(storage)
    _seed_discovery_rows(storage)
    data = export_eval_sets.export_discovery(
        storage.database_path,
        tmp_path / export_eval_sets.DISCOVERY_FILE,
        seed=42,
        max_candidates=300,
    )
    for item in data["items"]:
        item["label"] = (
            "should_discover"
            if item["document_id"] in {"dc-1", "dc-2", "doc-a"}
            else "not_discover"
        )
    # doc-a 在发现层缺失 → 召回 = 2/3 < 95% → 门禁失败；固定案例零遗漏
    # 只看样本中出现的固定案例。
    score = score_eval_sets.score_discovery(data)
    assert score.candidate_recall == 2 / 3
    assert score.passed is False
    assert any(gate.name == "candidate_recall" and not gate.passed for gate in score.gates)

    # 补上候选后召回 1.0，固定案例零遗漏，全部门禁通过。
    storage.save_research_batch(
        [],
        [
            DiscoveryCandidate(
                document_id="doc-a",
                source_key="cninfo_announcement",
                source_name="巨潮资讯公告",
                provider_key="cninfo",
                provider_name="巨潮资讯",
                kind="announcement",
                stock_codes=("000001",),
                title="关于中标重大合同的公告",
                published_at=_ts(2),
                discovery_type="contract_order",
                trigger_reason="标题含“中标”",
                queue_status="awaiting_review",
                attachment_type=None,
                document_url=None,
                enqueued_at=None,
                updated_at=_ts(0),
                signal_priority=True,
            )
        ],
        SyncCursor(
            source_key="cninfo_announcement",
            sync_kind="announcement",
            cursor={"page": 1},
            target_start=_ts(0).date(),
            covered_start=_ts(0).date(),
            last_success_at=_ts(0),
            last_error=None,
            updated_at=_ts(0),
        ),
        _ts(0),
    )
    data = export_eval_sets.export_discovery(
        storage.database_path,
        tmp_path / export_eval_sets.DISCOVERY_FILE,
        seed=42,
        max_candidates=300,
    )
    for item in data["items"]:
        item["label"] = (
            "should_discover"
            if item["document_id"] in {"dc-1", "dc-2", "doc-a"}
            else "not_discover"
        )
    score = score_eval_sets.score_discovery(data)
    assert score.candidate_recall == 1.0
    assert score.fixed_case_misses == ()
    assert all(
        gate.passed
        for gate in score.gates
        if gate.name in {"candidate_recall", "fixed_case_zero_miss"}
    )


def test_score_discovery_full_gates_pass_with_300_sample(tmp_path: Path) -> None:
    data = {
        "schema_version": 1,
        "kind": "discovery_candidates",
        "meta": {"fixed_case_titles": []},
        "items": [
            {
                "document_id": f"doc-{index}",
                "title": f"公告 {index}",
                "in_discovery_layer": True,
                "fixed_case": index == 0,
                "label": "should_discover" if index < 300 else "not_discover",
            }
            for index in range(310)
        ],
    }
    score = score_eval_sets.score_discovery(data)
    assert score.sample_count == 310
    assert score.candidate_recall == 1.0
    assert score.passed is True


def test_score_discovery_fails_when_fixed_case_missing(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "eval.db")
    _seed_db(storage)
    _seed_discovery_rows(storage)
    data = export_eval_sets.export_discovery(
        storage.database_path,
        tmp_path / export_eval_sets.DISCOVERY_FILE,
        seed=42,
        max_candidates=300,
    )
    for item in data["items"]:
        item["label"] = "should_discover"
    # 模拟样本中没有任何固定案例（数据库缺少本机公开样本）→ 零遗漏门禁失败。
    data["items"] = [
        item for item in data["items"] if not item.get("fixed_case")
    ]
    score = score_eval_sets.score_discovery(data)
    assert score.fixed_case_misses
    assert score.passed is False
