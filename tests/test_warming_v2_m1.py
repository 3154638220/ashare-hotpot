"""Institution warming v2 milestone 1: date semantics and eligibility."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.institution_metrics import _load_activity_data
from ashare_hotpot.institutions import InstitutionRegistry, infer_institution_type
from ashare_hotpot.models import (
    ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY,
    ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
    ACTIVITY_DATE_PRECISION_EXPLICIT_RANGE,
    SourceDocument,
)
from ashare_hotpot.research_activities import (
    OCCURRENCE_PARSE_VERSION,
    parse_research_activity,
    research_eligibility,
)
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=SHANGHAI_TZ)
FIXTURES = Path(__file__).parent / "fixtures"


def _document(body: str, *, document_id: str = "warming-m1") -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="irm_ircs",
        provider_name="互动易",
        kind="research_activity",
        source_url=f"https://example.test/{document_id}",
        document_url=None,
        title="投资者关系活动记录表",
        published_at=NOW,
        stock_codes=("000001",),
        stock_names={"000001": "示例科技"},
        body_text=body,
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def _parse(tmp_path, body: str, *, document_id: str = "warming-m1"):
    storage = Storage(tmp_path / f"{document_id}.db")
    document = _document(body, document_id=document_id)
    registry = InstitutionRegistry(storage, now=NOW)
    result = parse_research_activity(document, registry)
    assert result is not None
    return storage, document, result


def _persist_parse_result(storage: Storage, document: SourceDocument, result) -> None:
    storage.upsert_source_document(document, NOW)
    storage.upsert_research_activity(result.activity, NOW)
    for evidence in result.evidence_refs:
        storage.upsert_evidence_ref(evidence)
    for participant in result.participants:
        storage.add_research_participant(participant)
    storage.replace_research_occurrences(
        result.activity.activity_id,
        list(result.activity_occurrences),
        list(result.participant_occurrences),
    )


def test_activity_dates_only_use_structured_fields_and_ignore_future(tmp_path) -> None:
    _storage, _document_row, result = _parse(
        tmp_path,
        """活动时间：2026年8月8日 14:00-15:00
参与单位：中信证券
问：公司2025年12月31日收入情况及2027年1月1日规划？
答：详见定期报告。
""",
    )

    occurrences = result.activity_occurrences
    assert [(item.occurred_on, item.date_precision) for item in occurrences] == [
        (date(2026, 8, 8), ACTIVITY_DATE_PRECISION_EXPLICIT_DAY)
    ]
    assert occurrences[0].metric_eligible is True
    assert all(
        item.occurred_on is None or item.occurred_on <= NOW.date()
        for item in occurrences
    )


def test_range_and_disclosure_fallback_are_not_metric_eligible(tmp_path) -> None:
    _storage, _document_row, ranged = _parse(
        tmp_path,
        "活动安排：2026年8月4日至2026年8月6日\n参与单位：中信证券\n",
        document_id="range",
    )
    assert len(ranged.activity_occurrences) == 1
    occurrence = ranged.activity_occurrences[0]
    assert occurrence.occurred_on is None
    assert occurrence.period_start == date(2026, 8, 4)
    assert occurrence.period_end == date(2026, 8, 6)
    assert occurrence.date_precision == ACTIVITY_DATE_PRECISION_EXPLICIT_RANGE
    assert occurrence.metric_eligible is False

    _storage, _document_row, fallback = _parse(
        tmp_path,
        "活动时间：待定\n参与单位：中信证券\n正文提到2027年1月5日计划。\n",
        document_id="fallback",
    )
    assert len(fallback.activity_occurrences) == 1
    occurrence = fallback.activity_occurrences[0]
    assert occurrence.occurred_on == NOW.date()
    assert occurrence.date_precision == ACTIVITY_DATE_PRECISION_DISCLOSURE_DAY
    assert occurrence.metric_eligible is False


def test_cross_day_list_maps_each_institution_to_its_explicit_day(tmp_path) -> None:
    _storage, _document_row, result = _parse(
        tmp_path,
        """活动安排：
2026年8月7日  中信证券  张明研究员
2026年8月8日  易方达基金  李华研究员
交流内容：略
""",
        document_id="cross-day",
    )

    by_day = {
        occurrence.occurrence_id: occurrence.occurred_on
        for occurrence in result.activity_occurrences
    }
    mapped = {
        (
            by_day[item.activity_occurrence_id],
            item.institution_id,
            item.analyst_name,
        )
        for item in result.participant_occurrences
    }
    assert mapped == {
        (date(2026, 8, 7), "inst:seed:citic_securities", "张明"),
        (date(2026, 8, 8), "inst:seed:yifangda", "李华"),
    }


def test_research_eligibility_retains_details_but_excludes_non_research_orgs(
    tmp_path,
) -> None:
    storage, document, result = _parse(
        tmp_path,
        """活动时间：2026年8月8日
参与单位：中信证券、示例产业有限公司、某律师事务所、中国工商银行研究部、吉林省信托证券投资部、Example Technology Limited、DM Capital Limited、Deutsche Bank
交流内容：略
""",
        document_id="eligibility",
    )
    _persist_parse_result(storage, document, result)

    participant_rows = storage.get_research_participant_occurrences(
        result.activity.activity_id
    )
    assert len(participant_rows) == len(result.participants) == 8
    eligible_names = {
        storage.get_institution(row.institution_id).canonical_name
        for row in participant_rows
        if row.research_eligible
    }
    assert eligible_names == {
        "中信证券股份有限公司",
        "中国工商银行研究部",
        "吉林省信托证券投资部",
        "DM Capital Limited",
    }
    excluded_reasons = {
        storage.get_institution(row.institution_id).canonical_name: row.eligibility_reason
        for row in participant_rows
        if not row.research_eligible
    }
    assert "产业公司" in excluded_reasons["示例产业有限公司"]
    assert "律所" in excluded_reasons["某律师事务所"]
    assert "Limited/Ltd" in excluded_reasons["Example Technology Limited"]
    assert "银行未明确研究部门" in excluded_reasons["Deutsche Bank"]

    loaded = _load_activity_data(storage, date(2026, 8, 1), NOW.date())
    assert len(loaded) == 1
    assert len(loaded[0].groups) == 4
    assert loaded[0].activity_dates == (date(2026, 8, 8),)
    assert set(loaded[0].group_dates) == set(loaded[0].groups)


def test_same_analyst_name_is_scoped_by_institution_and_multi_analyst_is_kept(
    tmp_path,
) -> None:
    _storage, _document_row, result = _parse(
        tmp_path,
        """活动时间：2026年8月8日
参与单位：
中信证券 张明研究员
中信证券 李明研究员
华泰证券 张明研究员
交流内容：略
""",
        document_id="analysts",
    )

    identities = {
        (item.institution_id, item.analyst_name)
        for item in result.participant_occurrences
    }
    assert identities == {
        ("inst:seed:citic_securities", "张明"),
        ("inst:seed:citic_securities", "李明"),
        ("inst:seed:huatai", "张明"),
    }
    assert all(item.parse_version == OCCURRENCE_PARSE_VERSION for item in result.participant_occurrences)


def test_limited_or_ltd_alone_no_longer_implies_foreign_research_institution() -> None:
    assert infer_institution_type("Example Technology Limited") == "other"
    assert infer_institution_type("Example Technology Ltd") == "other"
    assert infer_institution_type("DM Capital Limited") == "foreign_institution"
    assert infer_institution_type("Example Asset Management Ltd") == "foreign_institution"
    assert infer_institution_type("Deutsche Bank") == "other"


def test_frozen_institution_eligibility_precision_and_recall_reach_92_percent() -> None:
    payload = json.loads(
        (FIXTURES / "warming_v2_m1_institution_cases.json").read_text(
            encoding="utf-8"
        )
    )
    assert "LLM 标注口径" in payload["labeling_basis"]
    labels: list[bool] = []
    predictions: list[bool] = []
    for case in payload["cases"]:
        institution_type = infer_institution_type(case["name"])
        eligible, _reason = research_eligibility(
            case["name"], institution_type, context=case["context"]
        )
        labels.append(bool(case["eligible"]))
        predictions.append(eligible)

    true_positive = sum(prediction and label for prediction, label in zip(predictions, labels))
    false_positive = sum(prediction and not label for prediction, label in zip(predictions, labels))
    false_negative = sum(not prediction and label for prediction, label in zip(predictions, labels))
    precision = true_positive / (true_positive + false_positive)
    recall = true_positive / (true_positive + false_negative)
    assert precision >= 0.92
    assert recall >= 0.92
