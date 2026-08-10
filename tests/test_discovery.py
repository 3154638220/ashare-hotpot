from __future__ import annotations

from datetime import datetime

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.discovery import (
    DISCOVERY_TYPES,
    HIGH_PRIORITY_DISCOVERY_TYPES,
    QUEUE_STATUS_LABELS,
    QUEUE_STATUS_PENDING_ATTACHMENT,
    QUEUE_STATUS_AWAITING_REVIEW,
    QUEUE_STATUS_EMPTY_TEXT,
    QUEUE_STATUS_FAILED,
    classify_discovery,
    discovery_type_label,
    queue_status_label,
)
from ashare_hotpot.models import DiscoveryCandidate, DiscoveryViewRow


# ---------------------------------------------------------------------------
# 固定发现枚举与分类（宽松、防漏；不计分、不称为利好）
# ---------------------------------------------------------------------------


def test_discovery_enum_is_fixed_and_labels_are_complete() -> None:
    assert DISCOVERY_TYPES == (
        "financial_report",
        "contract_order",
        "approval_customer",
        "capital_action",
        "capacity_project",
        "policy_subsidy",
        "other_disclosure",
    )
    assert len(discovery_type_label("financial_report")) > 0
    assert len(discovery_type_label("unknown_enum")) > 0  # 不动态创建枚举
    assert all(label for label in QUEUE_STATUS_LABELS.values())


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("2026年半年度报告摘要", "financial_report"),
        ("2026年半年度业绩预告", "financial_report"),
        ("关于拟签订重大合同的公告", "contract_order"),
        ("重大合同中标公告", "contract_order"),
        ("关于获得药品注册证书的公告", "approval_customer"),
        ("关于获得客户定点通知的公告", "approval_customer"),
        ("回购报告书", "capital_action"),
        ("关于回购实施结果的公告", "capital_action"),
        ("关于完成工商变更登记暨签订募集资金三方监管协议的公告", "contract_order"),
        ("关于全资子公司投资建设新生产基地的公告", "capacity_project"),
        ("关于获得政府补助的公告", "policy_subsidy"),
        ("关于召开临时股东大会的通知", "other_disclosure"),
        ("关于会计政策变更的公告", "other_disclosure"),
    ],
)
def test_classify_discovery_loose_mapping(title: str, expected: str) -> None:
    discovery_type, reason = classify_discovery(title)
    assert discovery_type == expected
    assert reason


def test_classify_discovery_research_activity_kind() -> None:
    discovery_type, reason = classify_discovery(
        "投资者关系活动记录表", kind="research_activity"
    )
    assert discovery_type == "other_disclosure"
    assert "活动" in reason


def test_classify_discovery_never_returns_empty_or_new_enum() -> None:
    for title in ("", "   ", "关于高级管理人员辞职的公告"):
        discovery_type, reason = classify_discovery(title)
        assert discovery_type in DISCOVERY_TYPES
        assert reason


def test_high_priority_discovery_types_exclude_reports_and_catch_all() -> None:
    assert "contract_order" in HIGH_PRIORITY_DISCOVERY_TYPES
    assert "financial_report" not in HIGH_PRIORITY_DISCOVERY_TYPES
    assert "other_disclosure" not in HIGH_PRIORITY_DISCOVERY_TYPES


# ---------------------------------------------------------------------------
# DiscoveryCandidate / DiscoveryViewRow 序列化往返
# ---------------------------------------------------------------------------


def _candidate() -> DiscoveryCandidate:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=SHANGHAI_TZ)
    return DiscoveryCandidate(
        document_id="cninfo:123",
        source_key="cninfo_announcement",
        source_name="巨潮资讯公告",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        stock_codes=("600390",),
        title="关于拟签订重大合同的公告",
        published_at=now,
        discovery_type="contract_order",
        trigger_reason="标题含“重大合同”",
        queue_status=QUEUE_STATUS_PENDING_ATTACHMENT,
        attachment_type="PDF",
        document_url="https://static.cninfo.com.cn/finalpage/x.PDF",
        enqueued_at=now,
        updated_at=now,
    )


def test_discovery_candidate_roundtrip() -> None:
    candidate = _candidate()
    restored = DiscoveryCandidate.from_dict(candidate.to_dict())
    assert restored == candidate


def test_discovery_view_row_roundtrip() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=SHANGHAI_TZ)
    row = DiscoveryViewRow(
        rank=1,
        stock_code="600390",
        stock_name="五矿资本",
        discovery_type="contract_order",
        discovery_type_label="合同订单",
        title="关于拟签订重大合同的公告",
        trigger_reason="标题含“重大合同”",
        parse_status=QUEUE_STATUS_AWAITING_REVIEW,
        parse_status_label="待核验",
        published_at=now,
        source_name="巨潮资讯公告",
        document_id="cninfo:123",
        document_url="https://static.cninfo.com.cn/finalpage/x.PDF",
        quality_state="ok",
    )
    restored = DiscoveryViewRow.from_dict(row.to_dict())
    assert restored == row


def test_queue_status_labels_cover_all_states() -> None:
    assert queue_status_label(QUEUE_STATUS_PENDING_ATTACHMENT) == "待解析"
    assert queue_status_label(QUEUE_STATUS_AWAITING_REVIEW) == "待核验"
    assert queue_status_label(QUEUE_STATUS_EMPTY_TEXT) == "空文本"
    assert queue_status_label(QUEUE_STATUS_FAILED) == "解析失败"
    assert queue_status_label("bogus") == "bogus"
