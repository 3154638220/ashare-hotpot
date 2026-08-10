"""v2 里程碑 5：歧义项 AI 复核器（``AmbiguityReviewer``）。

规则仍是离线主链；AI 只复核低置信边界/门控失败的候选事实，只能选择固定枚举、
标注原文跨度与建议分类，最终重大性/确定性/反证/排名仍由确定性规则计算。
无密钥、超时、非法跨度、未知枚举或规则冲突时保留规则结果并标记复核状态，
不得直接晋升严格榜。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from .ai_extractor import AiCredentialStore, OpenAiClient, _parse_strict_json
from .config import AppSettings
from .extraction import DIRECTIONS, EVENT_TYPES
from .models import EventClaim, SourceDocument


logger = logging.getLogger(__name__)

# 复核状态（固定枚举）：未复核 / 复核失败 / 规则与AI一致 / 规则与AI分歧。
REVIEW_STATUS_NOT_REVIEWED = "not_reviewed"
REVIEW_STATUS_REVIEW_FAILED = "review_failed"
REVIEW_STATUS_AGREE = "agree"
REVIEW_STATUS_DIVERGE = "diverge"

REVIEW_STATUSES: tuple[str, ...] = (
    REVIEW_STATUS_NOT_REVIEWED,
    REVIEW_STATUS_REVIEW_FAILED,
    REVIEW_STATUS_AGREE,
    REVIEW_STATUS_DIVERGE,
)

REVIEW_SYSTEM_PROMPT = (
    "你是 A 股公开披露事件的歧义复核器，只输出合法 JSON 对象，不要输出任何其他文字。\n"
    "输入是规则引擎已抽取的一条候选事实（含门控轨迹与证据摘录）。请判断规则结论是否可信：\n"
    '输出 {\"review\": {\"suggestion\": \"agree\" 或 \"diverge\", '
    '"suggested_event_type\": 固定枚举之一或 null, '
    '"suggested_direction\": \"positive\"/\"negative\"/\"neutral\" 或 null, '
    '"mechanism_excerpt\": {\"document_id\": str, \"start\": 整数, \"end\": 整数} 或 null, '
    '"rationale\": str}}。\n'
    "suggested_event_type 只能是：" + "、".join(EVENT_TYPES) + "；\n"
    "mechanism_excerpt 必须指向输入 document_id 正文中可精确校验的原文跨度（start/end 为字符偏移）；\n"
    "你只能给出建议，最终重大性、确定性、反证与排名仍由规则计算。"
)


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    status: str
    suggested_event_type: str | None = None
    suggested_direction: str | None = None
    rationale: str = ""
    mechanism_document_id: str | None = None
    mechanism_start: int | None = None
    mechanism_end: int | None = None


class AmbiguityReviewer(Protocol):
    """只处理歧义项的复核器协议（v2 里程碑 5）。"""

    def review(
        self, claim: EventClaim, document: SourceDocument | None
    ) -> ReviewOutcome: ...


class RuleOnlyAmbiguityReviewer:
    """AI 关闭/无密钥时的默认实现：所有候选事实标记“未复核”。"""

    def review(
        self, claim: EventClaim, document: SourceDocument | None
    ) -> ReviewOutcome:
        return ReviewOutcome(status=REVIEW_STATUS_NOT_REVIEWED)


class OpenAICompatibleAmbiguityReviewer:
    """OpenAI-compatible 歧义复核：固定枚举 + 原文跨度 + 严格校验。"""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        store = AiCredentialStore(settings.app_root)
        api_key = store.load()
        self._client = OpenAiClient(
            base_url=settings.ai_base_url,
            model=settings.ai_model,
            api_key=api_key or "",
            timeout=settings.ai_timeout_seconds,
        )

    def review(
        self, claim: EventClaim, document: SourceDocument | None
    ) -> ReviewOutcome:
        try:
            user = _review_prompt(claim, document)
            output = self._client.chat_completion(
                [
                    {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ]
            )
            content = output["choices"][0]["message"]["content"]
            payload = _parse_strict_json(content)
            return _validate_outcome(payload, claim, document)
        except Exception as exc:  # noqa: BLE001 - 复核失败降级
            logger.warning("ambiguity review failed for %s: %s", claim.claim_id, exc)
            return ReviewOutcome(
                status=REVIEW_STATUS_REVIEW_FAILED,
                rationale=str(exc)[:200],
            )


def _review_prompt(
    claim: EventClaim, document: SourceDocument | None
) -> str:
    evidence = "; ".join(
        str(item.get("excerpt") or "")[:200]
        for item in claim.metrics
        if item.get("evidence_id")
    )
    return (
        "候选事实：\n"
        f"document_id={claim.document_id}\n"
        f"event_type={claim.event_type} direction={claim.direction}\n"
        f"materiality={claim.materiality_level} certainty={claim.certainty}\n"
        f"positive_mechanism={claim.positive_mechanism or ''}\n"
        f"gate_trace={claim.gate_trace}\n"
        f"正文（前2000字）：{(document.body_text or '')[:2000] if document else ''}\n"
        f"证据摘录：{evidence}\n"
        "请只输出 JSON。"
    )


def _validate_outcome(
    payload: object,
    claim: EventClaim,
    document: SourceDocument | None,
) -> ReviewOutcome:
    """严格校验模型输出：固定枚举、可校验原文跨度；非法即失败降级。"""

    if not isinstance(payload, dict) or not isinstance(
        payload.get("review"), dict
    ):
        raise ValueError("模型输出缺少 review 对象")
    review = payload["review"]
    suggestion = review.get("suggestion")
    if suggestion not in (REVIEW_STATUS_AGREE, REVIEW_STATUS_DIVERGE):
        raise ValueError(f"未知 suggestion={suggestion!r}")
    suggested_event_type = review.get("suggested_event_type")
    if suggested_event_type is not None and suggested_event_type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型 {suggested_event_type!r}")
    suggested_direction = review.get("suggested_direction")
    if suggested_direction is not None and suggested_direction not in DIRECTIONS:
        raise ValueError(f"未知方向 {suggested_direction!r}")
    mechanism_document_id: str | None = None
    mechanism_start: int | None = None
    mechanism_end: int | None = None
    excerpt = review.get("mechanism_excerpt")
    if excerpt is not None:
        if not isinstance(excerpt, dict):
            raise ValueError("mechanism_excerpt 必须是对象")
        mechanism_document_id = str(excerpt.get("document_id") or "")
        start = excerpt.get("start")
        end = excerpt.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("机制跨度 start/end 必须是整数")
        if start < 0 or end < start:
            raise ValueError(f"非法跨度 start={start} end={end}")
        if (
            mechanism_document_id == claim.document_id
            and document is not None
            and end > len(document.body_text or "")
        ):
            raise ValueError("机制跨度超出正文范围")
        mechanism_start, mechanism_end = start, end
    return ReviewOutcome(
        status=suggestion,
        suggested_event_type=suggested_event_type,
        suggested_direction=suggested_direction,
        rationale=str(review.get("rationale") or "")[:200],
        mechanism_document_id=mechanism_document_id,
        mechanism_start=mechanism_start,
        mechanism_end=mechanism_end,
    )


def should_review_claim(claim: EventClaim) -> bool:
    """只复核低置信边界/门控失败的候选事实；正常高置信样本不调用 AI。"""

    if claim.certainty < 0.70:
        return True
    for gate in claim.gate_trace:
        if gate.get("gate") in ("score", "materiality", "certainty") and not gate.get(
            "passed"
        ):
            return True
    return False


def build_ambiguity_reviewer(settings: AppSettings) -> AmbiguityReviewer:
    """无密钥/未启用时返回规则-only（所有事实标记“未复核”）。"""

    if not settings.ai_enabled or not settings.ai_base_url or not settings.ai_model:
        return RuleOnlyAmbiguityReviewer()
    try:
        store = AiCredentialStore(settings.app_root)
        if not store.load():
            return RuleOnlyAmbiguityReviewer()
        return OpenAICompatibleAmbiguityReviewer(settings)
    except Exception as exc:  # noqa: BLE001 - 密钥不可用则降级
        logger.warning("ambiguity reviewer unavailable: %s", exc)
        return RuleOnlyAmbiguityReviewer()
