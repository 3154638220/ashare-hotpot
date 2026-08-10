"""v2 优化计划模型往返：EventClaim / ResearchParticipantMention /
ReportedParticipantCount (plan.md 第三部分, schema 121)."""

from __future__ import annotations

from datetime import datetime

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import (
    EVENT_CLAIM_REVIEW_PENDING,
    EVENT_CLAIM_REVIEW_REJECTED,
    EVENT_CLAIM_REVIEW_STATUSES,
    EVENT_CLAIM_REVIEW_SUPERSEDED,
    EVENT_CLAIM_REVIEW_VERIFIED,
    PARTICIPANT_MENTION_REVIEW_PENDING,
    PARTICIPANT_MENTION_REVIEW_STATUSES,
    EventClaim,
    ReportedParticipantCount,
    ResearchParticipantMention,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=SHANGHAI_TZ)


def test_event_claim_roundtrip_with_chinese_content() -> None:
    claim = EventClaim(
        claim_id="claim-1",
        document_id="doc-1",
        stock_code="600196",
        event_type="approval",
        direction="positive",
        positive_mechanism="获批/认证为相关产品打开准入或商业化空间",
        metrics=(
            {
                "name": "获批产品",
                "value": "药品注册证书",
                "unit": None,
                "comparison_basis": None,
                "comparison_ratio": None,
                "evidence_id": "ev-1",
            },
        ),
        certainty_stage="executed",
        certainty=1.0,
        materiality_level=2,
        counter_evidence=(),
        evidence_ids=("ev-1",),
        rejection_reason=None,
        review_status=EVENT_CLAIM_REVIEW_PENDING,
        gate_trace=(
            {"gate": "mechanism", "passed": True, "reason": "正向机制存在"},
            {"gate": "materiality", "passed": True, "reason": "重大性≥2"},
        ),
        extractor_kind="rules",
        extractor_version="rules-v1",
        created_at=NOW,
    )
    restored = EventClaim.from_dict(claim.to_dict())
    assert restored == claim


def test_event_claim_from_dict_defaults() -> None:
    claim = EventClaim.from_dict(
        {
            "claim_id": "c",
            "document_id": "d",
            "stock_code": "000001",
            "event_type": "mna",
            "direction": "negative",
            "created_at": NOW.isoformat(),
        }
    )
    assert claim.review_status == EVENT_CLAIM_REVIEW_PENDING
    assert claim.metrics == ()
    assert claim.gate_trace == ()
    assert claim.materiality_level == 0


def test_event_claim_review_statuses_fixed() -> None:
    assert EVENT_CLAIM_REVIEW_STATUSES == (
        "pending_review",
        "verified",
        "rejected",
        "superseded",
    )
    assert EVENT_CLAIM_REVIEW_VERIFIED == "verified"
    assert EVENT_CLAIM_REVIEW_REJECTED == "rejected"
    assert EVENT_CLAIM_REVIEW_SUPERSEDED == "superseded"


def test_participant_mention_roundtrip() -> None:
    mention = ResearchParticipantMention(
        mention_id="mention-1",
        document_id="doc-1",
        activity_id="act-1",
        raw_name="大筝资管",
        start_offset=12,
        end_offset=16,
        organization_category="research_institution",
        parse_version="v2-20260809",
        review_status=PARTICIPANT_MENTION_REVIEW_PENDING,
        evidence_id="ev-1",
        created_at=NOW,
    )
    restored = ResearchParticipantMention.from_dict(mention.to_dict())
    assert restored == mention


def test_participant_mention_defaults_and_enum() -> None:
    mention = ResearchParticipantMention.from_dict(
        {
            "mention_id": "m",
            "document_id": "d",
            "activity_id": "a",
            "raw_name": "中信证券",
            "created_at": NOW.isoformat(),
        }
    )
    assert mention.organization_category == "other_organization"
    assert mention.review_status == PARTICIPANT_MENTION_REVIEW_PENDING
    assert PARTICIPANT_MENTION_REVIEW_STATUSES == (
        "pending_review",
        "verified",
        "rejected",
    )


def test_reported_participant_count_roundtrip() -> None:
    count = ReportedParticipantCount(
        activity_id="act-1",
        named_research_count=6,
        all_named_org_count=9,
        reported_institution_count=31,
        reported_person_count=45,
        evidence_id="ev-1",
        updated_at=NOW,
    )
    restored = ReportedParticipantCount.from_dict(count.to_dict())
    assert restored == count


def test_reported_participant_count_optional_totals() -> None:
    count = ReportedParticipantCount.from_dict(
        {
            "activity_id": "act-2",
            "named_research_count": 2,
            "all_named_org_count": 3,
            "reported_institution_count": None,
            "reported_person_count": None,
            "updated_at": NOW.isoformat(),
        }
    )
    assert count.reported_institution_count is None
    assert count.reported_person_count is None
