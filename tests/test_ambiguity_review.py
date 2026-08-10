"""v2 里程碑 5：歧义项 AI 复核器（AmbiguityReviewer）测试。

规则结果始终保留；AI 只能选择固定枚举、标注可校验原文跨度；无密钥/非法
枚举/非法跨度/超时均降级为“复核失败/未复核”，不得晋升严格榜。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from ashare_hotpot.ambiguity_review import (
    REVIEW_STATUS_AGREE,
    REVIEW_STATUS_DIVERGE,
    REVIEW_STATUS_NOT_REVIEWED,
    REVIEW_STATUS_REVIEW_FAILED,
    OpenAICompatibleAmbiguityReviewer,
    RuleOnlyAmbiguityReviewer,
    _validate_outcome,
    build_ambiguity_reviewer,
    should_review_claim,
)
from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.models import EVENT_CLAIM_REVIEW_PENDING, EventClaim


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=SHANGHAI_TZ)


def _claim(
    *,
    certainty: float = 0.9,
    materiality: int = 2,
    gate_trace: tuple[dict[str, object], ...] = (),
) -> EventClaim:
    return EventClaim(
        claim_id="claim:test",
        document_id="doc-1",
        stock_code="600000",
        event_type="approval",
        direction="positive",
        positive_mechanism="获批打开商业化空间",
        metrics=(),
        certainty_stage="executed",
        certainty=certainty,
        materiality_level=materiality,
        counter_evidence=(),
        evidence_ids=(),
        rejection_reason=None,
        review_status=EVENT_CLAIM_REVIEW_PENDING,
        gate_trace=gate_trace,
        extractor_kind="rules",
        extractor_version="rules-v1",
        created_at=NOW,
    )


def _document(body: str = "公司产品获得《药品注册证书》。" * 10):
    from ashare_hotpot.models import SourceDocument

    return SourceDocument(
        document_id="doc-1",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url="https://example.test/doc-1",
        document_url=None,
        title="关于获得药品注册证书的公告",
        published_at=NOW,
        stock_codes=("600000",),
        body_text=body,
        content_hash="hash",
        parse_status="parsed",
        parse_error=None,
    )


def test_should_review_claim_only_ambiguous_candidates() -> None:
    # 高确定性 + 全通过 → 不调用 AI。
    assert not should_review_claim(
        _claim(
            certainty=0.9,
            gate_trace=(
                {"gate": "score", "passed": True, "reason": "入榜"},
            ),
        )
    )
    # 低置信边界 → 复核。
    assert should_review_claim(_claim(certainty=0.45))
    # 门控失败（score/materiality/certainty）→ 复核。
    assert should_review_claim(
        _claim(
            certainty=0.9,
            gate_trace=(
                {"gate": "score", "passed": False, "reason": "得分不足35"},
            ),
        )
    )
    assert should_review_claim(
        _claim(
            certainty=0.9,
            gate_trace=(
                {"gate": "certainty", "passed": False, "reason": "确定性不足"},
            ),
        )
    )


def test_rule_only_reviewer_marks_not_reviewed() -> None:
    reviewer = RuleOnlyAmbiguityReviewer()
    outcome = reviewer.review(_claim(), _document())
    assert outcome.status == REVIEW_STATUS_NOT_REVIEWED


def test_build_reviewer_without_key_returns_rule_only(tmp_path) -> None:
    settings = AppSettings(
        app_root=tmp_path, ai_enabled=True, ai_base_url="https://x", ai_model="m"
    )
    reviewer = build_ambiguity_reviewer(settings)
    assert isinstance(reviewer, RuleOnlyAmbiguityReviewer)
    settings2 = AppSettings(app_root=tmp_path, ai_enabled=False)
    assert isinstance(
        build_ambiguity_reviewer(settings2), RuleOnlyAmbiguityReviewer
    )


class _StubClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def chat_completion(self, messages):
        return {"choices": [{"message": {"content": str(self.payload)}}]}


def _reviewer_with_stub(payload: dict, tmp_path) -> OpenAICompatibleAmbiguityReviewer:
    import json

    class _Client(_StubClient):
        def chat_completion(self, messages):
            return {
                "choices": [
                    {"message": {"content": json.dumps(self.payload)}}
                ]
            }

    from ashare_hotpot import ambiguity_review as ar

    settings = AppSettings(
        app_root=tmp_path,
        ai_enabled=True,
        ai_base_url="https://example.invalid",
        ai_model="stub",
    )
    # 绕过 DPAPI 密钥文件：直接替换 OpenAiClient 工厂。
    original = ar.OpenAiClient
    ar.OpenAiClient = lambda **kwargs: _Client(payload)  # type: ignore[assignment]
    try:
        return OpenAICompatibleAmbiguityReviewer(settings)
    finally:
        ar.OpenAiClient = original


def test_agree_review_marks_verified(tmp_path) -> None:
    document = _document()
    reviewer = _reviewer_with_stub(
        {
            "review": {
                "suggestion": "agree",
                "suggested_event_type": None,
                "suggested_direction": None,
                "mechanism_excerpt": {
                    "document_id": "doc-1",
                    "start": 0,
                    "end": 12,
                },
                "rationale": "规则结论可信",
            }
        },
        tmp_path,
    )
    outcome = reviewer.review(_claim(), document)
    assert outcome.status == REVIEW_STATUS_AGREE
    assert outcome.mechanism_document_id == "doc-1"
    assert outcome.mechanism_start == 0


def test_diverge_review_keeps_rules(tmp_path) -> None:
    reviewer = _reviewer_with_stub(
        {
            "review": {
                "suggestion": "diverge",
                "suggested_event_type": "approval",
                "suggested_direction": "neutral",
                "mechanism_excerpt": None,
                "rationale": "证据不足",
            }
        },
        tmp_path,
    )
    outcome = reviewer.review(_claim(), _document())
    assert outcome.status == REVIEW_STATUS_DIVERGE
    assert outcome.suggested_event_type == "approval"


def test_invalid_enum_fails_closed(tmp_path) -> None:
    reviewer = _reviewer_with_stub(
        {
            "review": {
                "suggestion": "agree",
                "suggested_event_type": "not_an_event",
                "suggested_direction": None,
                "mechanism_excerpt": None,
                "rationale": "",
            }
        },
        tmp_path,
    )
    outcome = reviewer.review(_claim(), _document())
    assert outcome.status == REVIEW_STATUS_REVIEW_FAILED


def test_offset_out_of_range_fails_closed(tmp_path) -> None:
    document = _document("很短。")
    reviewer = _reviewer_with_stub(
        {
            "review": {
                "suggestion": "agree",
                "suggested_event_type": None,
                "suggested_direction": None,
                "mechanism_excerpt": {
                    "document_id": "doc-1",
                    "start": 0,
                    "end": 9999,
                },
                "rationale": "",
            }
        },
        tmp_path,
    )
    outcome = reviewer.review(_claim(), document)
    assert outcome.status == REVIEW_STATUS_REVIEW_FAILED


def test_validate_outcome_requires_fixed_enums() -> None:
    with pytest.raises(ValueError):
        _validate_outcome(
            {"review": {"suggestion": "maybe", "rationale": ""}},
            _claim(),
            _document(),
        )
    with pytest.raises(ValueError):
        _validate_outcome(
            {"review": {"suggestion": "agree", "suggested_direction": "sideways"}},
            _claim(),
            _document(),
        )
