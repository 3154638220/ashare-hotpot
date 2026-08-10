"""发布前人工抽检工具核心逻辑测试（plan v2：较低口径裁决）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1] / "scripts" / "evaluation"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import human_review  # noqa: E402
import score_eval_sets  # noqa: E402


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
                "named_institution_types": ["research"],
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


def test_review_matches_scorer_without_overrides() -> None:
    data = _institution_data(100, 100)
    result = human_review.apply_institution_review(data["records"], {})
    score = score_eval_sets.score_institution(data)
    assert result["research_named_recall"] == score.research_named_recall
    assert result["all_org_named_recall"] == score.all_org_named_recall
    assert result["entity_precision"] == score.all_org_entity_precision
    assert result["group_precision"] == score.group_precision


def test_review_type_override_moves_named_to_other() -> None:
    data = _institution_data(100, 100)
    # 前 10 条 named 人工改为 other（如产业公司被 LLM 误标 research）。
    state = {
        "named_overrides": {
            f"act-{index:04d}": {f"机构 {index}": "other"}
            for index in range(10)
        }
    }
    result = human_review.apply_institution_review(data["records"], state)
    assert result["research_named_recall"] == 1.0  # 90 research / 90 research
    assert result["all_org_named_recall"] == 1.0


def test_review_delete_drops_named_from_denominator() -> None:
    data = _institution_data(100, 100)
    state = {
        "named_overrides": {
            f"act-{index:04d}": {f"机构 {index}": "delete"}
            for index in range(20)
        }
    }
    result = human_review.apply_institution_review(data["records"], state)
    # 80 条保留且全部匹配 → 召回 1.0。
    assert result["research_named_recall"] == 1.0
    assert result["all_org_named_recall"] == 1.0


def test_review_participant_ok_override() -> None:
    data = _institution_data(100, 100)
    # 前 10 条参与者人工判定 entity_ok=False（虽然 LLM 标 true）。
    state = {
        "participant_ok": {
            f"act-{index:04d}": {f"inst-{index:04d}": False}
            for index in range(10)
        }
    }
    result = human_review.apply_institution_review(data["records"], state)
    assert result["entity_precision"] == 0.9
    assert result["research_named_recall"] == 0.9


def test_event_review_matches_scorer_without_overrides() -> None:
    rows = [
        {
            "rank": index + 1,
            "event_id": f"evt-{index:04d}",
            "stock_code": "000001",
            "relevant": True,
            "duplicate": False,
        }
        for index in range(20)
    ]
    result = human_review.apply_event_review([], rows, {})
    assert result["precision_at_10"] == 1.0
    assert result["top20_irrelevant"] == 0.0
    assert result["top20_duplicate"] == 0.0

    rows[0]["relevant"] = True
    state = {"relevant": {"evt-0000": False}}
    result2 = human_review.apply_event_review([], rows, state)
    assert result2["precision_at_10"] == 0.9
    assert result2["top20_irrelevant"] == 0.05


def test_institution_csv_roundtrip(tmp_path: Path) -> None:
    items = [
        {
            "activity_id": "act-0000",
            "stock_code": "000003",
            "title": "投资者关系活动记录表",
            "llm_named_institutions": ["机构 A", "机构 B"],
            "llm_named_types": ["research", "other"],
            "pipeline_participants": [
                {"institution_id": "inst-a", "canonical_name": "机构 A"},
            ],
        }
    ]
    csv_path = tmp_path / "review.csv"
    human_review.export_institution_csv(items, csv_path)
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "named" in text and "participant" in text
    # 人工填写：机构 A 改 other、删除机构 B、参与者 inst-a 判 true。
    import csv

    rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8-sig", newline="")))
    for row in rows:
        if row["kind"] == "named" and row["name"] == "机构 A":
            row["human_value"] = "other"
        elif row["kind"] == "named" and row["name"] == "机构 B":
            row["human_value"] = "delete"
        elif row["kind"] == "participant":
            row["human_value"] = "true"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    state = human_review.import_institution_csv(csv_path)
    assert state["named_overrides"]["act-0000"] == {
        "机构 A": "other",
        "机构 B": "delete",
    }
    assert state["participant_ok"]["act-0000"] == {"inst-a": True}


def test_events_csv_roundtrip(tmp_path: Path) -> None:
    items = [
        {
            "event_id": "evt-0000",
            "stock_codes": ["000001"],
            "canonical_title": "事件 A",
            "label_llm": "positive_signal",
            "must_hit_llm": True,
            "on_board": True,
            "error_types": [],
        }
    ]
    csv_path = tmp_path / "events.csv"
    human_review.export_events_csv(items, csv_path)
    import csv

    rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8-sig", newline="")))
    rows[0]["relevant"] = "false"
    rows[0]["duplicate"] = "true"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    state = human_review.import_events_csv(csv_path)
    assert state["relevant"] == {"evt-0000": False}
    assert state["duplicate"] == {"evt-0000": True}
