"""待核验事件发现层（后 1.1.0 可靠性里程碑，plan.md 里程碑 7）。

The discovery layer guarantees that public list items are never silently lost
to title gating, attachment budgets or parse failures.  Every list item is
persisted as a :class:`~ashare_hotpot.models.DiscoveryCandidate` with a fixed,
loose discovery enum; attachment downloads run through a recoverable work
queue.  Candidates never carry a score, never claim a positive mechanism and
never infer amounts, customers or investment opinions — the strict boards
decide that from body evidence.
"""

from __future__ import annotations


# Fixed discovery enum (plan.md 里程碑 7).  The UI, storage and tests only map
# these persisted values to display text; nothing creates new enum values.
DISCOVERY_TYPES: tuple[str, ...] = (
    "financial_report",  # 财务报告
    "contract_order",  # 合同订单
    "approval_customer",  # 审批客户
    "capital_action",  # 资本动作
    "capacity_project",  # 产能项目
    "policy_subsidy",  # 政策补贴
    "other_disclosure",  # 其他需核验披露
)

DISCOVERY_TYPE_LABELS: dict[str, str] = {
    "financial_report": "财务报告",
    "contract_order": "合同订单",
    "approval_customer": "审批客户",
    "capital_action": "资本动作",
    "capacity_project": "产能项目",
    "policy_subsidy": "政策补贴",
    "other_disclosure": "其他需核验披露",
}

# Queue states persisted in ``discovery_candidates.queue_status``.  The 待核验
# view groups them as 待解析 / 待核验 / 解析失败.
QUEUE_STATUS_PENDING_ATTACHMENT = "pending_attachment"  # 待解析
QUEUE_STATUS_AWAITING_REVIEW = "awaiting_review"  # 待核验
QUEUE_STATUS_EMPTY_TEXT = "empty_text"  # 空文本（解析失败组）
QUEUE_STATUS_FAILED = "failed"  # 解析失败

DISCOVERY_QUEUE_STATUSES: tuple[str, ...] = (
    QUEUE_STATUS_PENDING_ATTACHMENT,
    QUEUE_STATUS_AWAITING_REVIEW,
    QUEUE_STATUS_EMPTY_TEXT,
    QUEUE_STATUS_FAILED,
)

QUEUE_STATUS_LABELS: dict[str, str] = {
    QUEUE_STATUS_PENDING_ATTACHMENT: "待解析",
    QUEUE_STATUS_AWAITING_REVIEW: "待核验",
    QUEUE_STATUS_EMPTY_TEXT: "空文本",
    QUEUE_STATUS_FAILED: "解析失败",
}

# Queue buckets (plan.md 里程碑 7): 新调研资料 → 高优先级待核验事件 →
# 最旧普通待解析资料.  High-priority announcement candidates are the titles
# that can possibly produce a short-term signal (the strict pipeline's own
# ten event types), so the queue never starves signal-worthy documents.
HIGH_PRIORITY_DISCOVERY_TYPES: frozenset[str] = frozenset(
    {
        "contract_order",
        "approval_customer",
        "capital_action",
        "capacity_project",
        "policy_subsidy",
    }
)

# Loose keyword rules in fixed priority order; the first matched keyword wins.
# The classification is deliberately broad (防漏), not a scoring gate.
_DISCOVERY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "financial_report",
        (
            "年报",
            "半年报",
            "半年度报告",
            "年度报告",
            "一季报",
            "三季报",
            "季度报告",
            "业绩预告",
            "业绩快报",
            "财务报告",
            "财务报表",
            "审计报告",
            "主要经营数据",
            "经营情况简报",
            "定期报告",
        ),
    ),
    (
        "contract_order",
        (
            "重大合同",
            "合同",
            "订单",
            "中标",
            "签约",
            "采购协议",
            "销售合同",
            "供货",
            "框架协议",
            "意向协议",
            "协议",
        ),
    ),
    (
        "approval_customer",
        (
            "获批",
            "获得批准",
            "核准",
            "批复",
            "注册证",
            "许可证",
            "批件",
            "认证",
            "客户",
            "定点",
            "入选",
            "备案",
            "过会",
            "审核通过",
        ),
    ),
    (
        "capital_action",
        (
            "回购",
            "增持",
            "减持",
            "定增",
            "非公开发行",
            "可转债",
            "配股",
            "发行",
            "重组",
            "并购",
            "收购",
            "吸收合并",
            "股权激励",
            "分红",
            "派息",
            "送转",
            "质押",
            "解禁",
        ),
    ),
    (
        "capacity_project",
        (
            "投产",
            "扩产",
            "产能",
            "项目",
            "工程",
            "基地",
            "产线",
            "车间",
            "新设",
            "投资建设",
        ),
    ),
    (
        "policy_subsidy",
        (
            "补贴",
            "补助",
            "退税",
            "税收优惠",
            "政策",
            "奖励",
            "专项资金",
        ),
    ),
)


def classify_discovery(
    title: str, kind: str = "announcement"
) -> tuple[str, str]:
    """Map a list-item title to a fixed discovery enum plus a transparent reason.

    Research/投资者关系 activity records are classified through the same
    loose rules; titles that match nothing land in ``other_disclosure`` so no
    public list item is ever left outside the discovery layer.
    """

    text = (title or "").strip()
    if "会计政策" in text:
        # 会计政策变更属于披露类事项，不是政策补贴；宽松分类下仍必须落入
        # 固定枚举，故显式归入“其他需核验披露”，避免误挂“政策补贴”。
        return "other_disclosure", "会计政策披露"
    for discovery_type, keywords in _DISCOVERY_RULES:
        for keyword in keywords:
            if keyword in text:
                return discovery_type, f"标题含“{keyword}”"
    if kind == "research_activity":
        return "other_disclosure", "投资者关系/调研活动记录"
    return "other_disclosure", "其他需核验披露"


def discovery_type_label(discovery_type: str) -> str:
    return DISCOVERY_TYPE_LABELS.get(discovery_type, discovery_type or "其他需核验披露")


def queue_status_label(queue_status: str) -> str:
    return QUEUE_STATUS_LABELS.get(queue_status, queue_status or "待核验")
