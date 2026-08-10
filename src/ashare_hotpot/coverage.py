"""v1.2 官方市场覆盖闭环：固定枚举与可审计摘要工具。

The coverage layer guarantees that every public list item in the auditable
universe (SSE / SZSE / BSE official disclosures and investor-relation
activities, plus the ten fixed national policy sources) is accounted for by a
reconcilable manifest.  This module only defines the persisted enums fixed by
plan.md (v1.2 里程碑 0) and pure helpers; adapters and the UI must never
create new enum values at runtime.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


# Coverage statuses (plan.md v1.2): strictly one of these five persisted
# values.  ``list_reconciled`` (列表已对账) may only be displayed when the
# source total equals the local manifest count and the required bodies have
# been processed; empty lists must never masquerade as reconciled success.
COVERAGE_STATUS_REALTIME_PROVISIONAL = "realtime_provisional"  # 实时暂定
COVERAGE_STATUS_LIST_RECONCILED = "list_reconciled"  # 列表已对账
COVERAGE_STATUS_BODY_PENDING = "body_pending_verification"  # 正文待验证
COVERAGE_STATUS_PARTIAL = "partial_coverage"  # 部分覆盖
COVERAGE_STATUS_UNAVAILABLE = "unavailable"  # 不可用

COVERAGE_STATUSES: tuple[str, ...] = (
    COVERAGE_STATUS_REALTIME_PROVISIONAL,
    COVERAGE_STATUS_LIST_RECONCILED,
    COVERAGE_STATUS_BODY_PENDING,
    COVERAGE_STATUS_PARTIAL,
    COVERAGE_STATUS_UNAVAILABLE,
)

COVERAGE_STATUS_LABELS: dict[str, str] = {
    COVERAGE_STATUS_REALTIME_PROVISIONAL: "实时暂定",
    COVERAGE_STATUS_LIST_RECONCILED: "列表已对账",
    COVERAGE_STATUS_BODY_PENDING: "正文待验证",
    COVERAGE_STATUS_PARTIAL: "部分覆盖",
    COVERAGE_STATUS_UNAVAILABLE: "不可用",
}


# Manifest-level OCR state for one source and day (plan.md v1.2 里程碑 0).
OCR_STATUS_NOT_APPLICABLE = "not_applicable"
OCR_STATUS_PENDING = "pending"
OCR_STATUS_DONE = "done"
OCR_STATUS_PARTIAL = "partial"
OCR_STATUS_FAILED = "failed"

OCR_STATUSES: tuple[str, ...] = (
    OCR_STATUS_NOT_APPLICABLE,
    OCR_STATUS_PENDING,
    OCR_STATUS_DONE,
    OCR_STATUS_PARTIAL,
    OCR_STATUS_FAILED,
)

OCR_STATUS_LABELS: dict[str, str] = {
    OCR_STATUS_NOT_APPLICABLE: "不适用",
    OCR_STATUS_PENDING: "待 OCR",
    OCR_STATUS_DONE: "已完成",
    OCR_STATUS_PARTIAL: "部分完成",
    OCR_STATUS_FAILED: "OCR 失败",
}


# Per-page OCR result state persisted in ``ocr_pages``.
OCR_PAGE_STATUS_OK = "ok"
OCR_PAGE_STATUS_LOW_CONFIDENCE = "low_confidence"
OCR_PAGE_STATUS_FAILED = "failed"
OCR_PAGE_STATUS_SKIPPED = "skipped"

OCR_PAGE_STATUSES: tuple[str, ...] = (
    OCR_PAGE_STATUS_OK,
    OCR_PAGE_STATUS_LOW_CONFIDENCE,
    OCR_PAGE_STATUS_FAILED,
    OCR_PAGE_STATUS_SKIPPED,
)

OCR_PAGE_STATUS_LABELS: dict[str, str] = {
    OCR_PAGE_STATUS_OK: "识别成功",
    OCR_PAGE_STATUS_LOW_CONFIDENCE: "低置信度",
    OCR_PAGE_STATUS_FAILED: "识别失败",
    OCR_PAGE_STATUS_SKIPPED: "跳过",
}


# Policy dual attribution link kinds (plan.md v1.2): all policies enter the
# industry watch; only explicit company/project naming or an official
# announcement that references the policy document number may produce a
# ``direct_policy_benefit`` stock signal.  Industry mapping alone is never a
# stock-level positive signal.
POLICY_LINK_NAMED_COMPANY = "named_company"  # 政策明确点名上市公司
POLICY_LINK_NAMED_PROJECT = "named_project"  # 政策明确点名项目
POLICY_LINK_OFFICIAL_REF = "official_policy_ref"  # 公司公告以政策文号明确关联
POLICY_LINK_INDUSTRY_WATCH = "industry_watch"  # 行业政策观察（不入个股榜）

POLICY_LINK_KINDS: tuple[str, ...] = (
    POLICY_LINK_NAMED_COMPANY,
    POLICY_LINK_NAMED_PROJECT,
    POLICY_LINK_OFFICIAL_REF,
    POLICY_LINK_INDUSTRY_WATCH,
)

POLICY_LINK_LABELS: dict[str, str] = {
    POLICY_LINK_NAMED_COMPANY: "政策点名公司",
    POLICY_LINK_NAMED_PROJECT: "政策点名项目",
    POLICY_LINK_OFFICIAL_REF: "公告文号关联",
    POLICY_LINK_INDUSTRY_WATCH: "行业政策观察",
}


# Policy body parse states reuse the document parse vocabulary
# (``parsed | metadata_only | empty_text | failed``) so the coverage views can
# share the existing quality labels instead of inventing a parallel enum.
POLICY_BODY_STATUSES: tuple[str, ...] = (
    "parsed",
    "metadata_only",
    "empty_text",
    "failed",
)


def summarize_document_ids(document_ids: Iterable[str]) -> tuple[int, str]:
    """Return ``(count, digest)`` for a set of document IDs.

    The digest is the SHA-256 of the sorted unique document IDs joined by
    newlines, so two manifests for the same day and source are comparable
    regardless of insertion order.  This is the reconciliation summary stored
    in :class:`~ashare_hotpot.models.SourceManifest`.
    """

    unique = sorted({str(document_id) for document_id in document_ids if document_id})
    digest = hashlib.sha256("\n".join(unique).encode("utf-8")).hexdigest()
    return len(unique), digest
