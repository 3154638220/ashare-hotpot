from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .models import (
    EventCluster,
    EventExtraction,
    EvidenceRef,
    SourceDocument,
)


# Fixed enum values per plan.md section 10.1.  No new event types may be
# invented by source adapters or model outputs at runtime.
EVENT_TYPES: tuple[str, ...] = (
    "earnings_upgrade",
    "major_contract",
    "price_increase",
    "approval",
    "buyback_or_increase",
    "mna",
    "capacity_launch",
    "direct_policy_benefit",
    "customer_breakthrough",
    "subsidy_or_compensation",
    "shareholder_return",
    "rd_milestone",
    "risk_resolution",
    "equity_incentive",
    "financing_completion",
    "asset_disposal",
)
UNSUPPORTED_EVENT_TYPE = "unsupported_event_type"
DIRECTIONS: tuple[str, ...] = ("positive", "negative", "neutral")

EXTRACTOR_VERSION = "rules-v1"
EXCERPT_MAX_CHARS = 240

# Certainty stages fixed by plan.md 10.4.
CERTAINTY_STAGES: dict[str, float] = {
    "executed": 1.00,      # 已执行、已到账、已取得正式批文
    "signed": 0.90,        # 正式合同、董事会/股东会已通过
    "awarded": 0.70,       # 中标或获选但尚未签署正式合同
    "framework": 0.45,     # 框架协议、合作意向、筹划或申请中
    "rumor": 0.20,         # 媒体传闻、市场猜测
}

# Counter-evidence kinds fixed by plan.md 10.7.
COUNTER_EVIDENCE_KINDS: tuple[str, ...] = (
    "none",
    "partial",
    "high_uncertainty",
    "title_body_conflict",
)

FORMAL_DISCLOSURE_PROVIDERS = frozenset({"cninfo", "sse", "irm"})


def is_formal_disclosure(document: SourceDocument) -> bool:
    """Exchange/CNINFO formal disclosure or official company publishing."""

    return document.provider_key in FORMAL_DISCLOSURE_PROVIDERS


def event_type_label(event_type: str) -> str:
    return {
        "earnings_upgrade": "业绩上修",
        "major_contract": "重大订单",
        "price_increase": "产品涨价",
        "approval": "获批认证",
        "buyback_or_increase": "回购增持",
        "mna": "并购重组",
        "capacity_launch": "产能投产",
        "direct_policy_benefit": "直接政策受益",
        "customer_breakthrough": "重要客户突破",
        "subsidy_or_compensation": "补贴赔偿",
        "shareholder_return": "股东回报",
        "rd_milestone": "研发里程碑",
        "risk_resolution": "风险解除",
        "equity_incentive": "股权激励",
        "financing_completion": "融资完成",
        "asset_disposal": "资产处置",
        "unsupported_event_type": "未支持类型",
    }.get(event_type, event_type)


# ---------------------------------------------------------------------------
# Parsing helpers (never invent values; missing fields stay None)
# ---------------------------------------------------------------------------

_AMOUNT_PATTERN = re.compile(
    r"([+-]?\d[\d,]*\.?\d*)\s*(亿元|万元|元|亿|万)"
)


def parse_amount(text: str) -> tuple[float, str, float] | None:
    """Parse the first amount in ``text``.

    Returns ``(normalized_yuan, unit_label, value)``.  ``unit_label`` is the
    source unit verbatim (``亿元``/``万元``/``元``/``亿``/``万``) so the
    original value and unit are never silently merged.
    """

    match = _AMOUNT_PATTERN.search(text)
    if match is None:
        return None
    raw = match.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    unit = match.group(2) or "元"
    multiplier = {"亿元": 1e8, "万元": 1e4, "元": 1.0, "亿": 1e8, "万": 1e4}[unit]
    return value * multiplier, unit, value


_PERCENT_PATTERN = re.compile(
    r"([+-]?\d[\d,]*\.?\d*)\s*(?:%|个百分点)"
)
_RATIO_PATTERN = re.compile(r"([+-]?\d[\d,]*\.?\d*)\s*倍")
_NET_PROFIT_LEVEL_PATTERN = re.compile(
    r"(?:归属于上市公司股东的净利润|归母净利润|净利润)"
    r"(?:达到|为|是|:)?\s*(?:[（(]\s*元\s*[)）]\s*)?"
    r"([+-]?[\d,]+\.?\d*)\s*(亿元|万元)?"
)


def parse_percent(text: str) -> float | None:
    """Parse a percentage into a 0-1 ratio.

    Only explicit ``%``/``个百分点``/``倍`` are accepted; a bare number without
    a unit is ambiguous and returns None (no invented values).
    """

    match = _PERCENT_PATTERN.search(text)
    if match is not None:
        try:
            return float(match.group(1).replace(",", "")) / 100.0
        except ValueError:
            return None
    match = _RATIO_PATTERN.search(text)
    if match is not None:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _net_profit_level_amount(text: str) -> tuple[float, str, float] | None:
    """Parse the reported net-profit level (“净利润达到/为/是 N 万元”).

    Year-on-year deltas (“较上年同期增加 N 万元”) are intentionally not
    matched so the metric never mislabels an increase as the profit level.
    """

    match = _NET_PROFIT_LEVEL_PATTERN.search(text or "")
    if match is None:
        return None
    raw = match.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    unit = match.group(2) or "元"
    multiplier = {"亿元": 1e8, "万元": 1e4, "元": 1.0}[unit]
    return value * multiplier, unit, value


def _clean_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Evidence construction
# ---------------------------------------------------------------------------


def make_evidence(
    document: SourceDocument,
    kind: str,
    text: str,
    start: int,
    end: int,
) -> EvidenceRef:
    """Build a short, deterministic evidence ref anchored in one document."""

    excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
    if len(excerpt) > EXCERPT_MAX_CHARS:
        excerpt = excerpt[:EXCERPT_MAX_CHARS]
    evidence_id = hashlib.sha1(
        f"{document.document_id}:{kind}:{excerpt[:80]}".encode("utf-8")
    ).hexdigest()[:16]
    return EvidenceRef(
        evidence_id=evidence_id,
        document_id=document.document_id,
        start_offset=start,
        end_offset=end,
        excerpt=excerpt,
        source_url=document.source_url or document.document_url or "",
    )


def find_context_matches(
    document: SourceDocument, pattern: re.Pattern[str], kind: str, radius: int = 60
) -> tuple[list[tuple[int, int, str]], EvidenceRef]:
    """Locate pattern matches in the body and build one combined evidence ref.

    Returns ``(matches, evidence)`` where ``matches`` are ``(start, end, text)``
    slices and ``evidence`` is a single evidence ref covering the first match
    with surrounding context.
    """

    body = document.body_text or ""
    matches: list[tuple[int, int, str]] = []
    for match in pattern.finditer(body):
        start = max(0, match.start() - radius)
        end = min(len(body), match.end() + radius)
        matches.append((match.start(), match.end(), body[start:end]))
    if not matches:
        title_match = pattern.search(document.title or "")
        if title_match is not None:
            excerpt = re.sub(r"\s+", " ", document.title).strip()
            evidence_id = hashlib.sha1(
                f"{document.document_id}:{kind}:{excerpt[:80]}".encode("utf-8")
            ).hexdigest()[:16]
            return [], EvidenceRef(
                evidence_id=evidence_id,
                document_id=document.document_id,
                start_offset=None,
                end_offset=None,
                excerpt=excerpt[:EXCERPT_MAX_CHARS],
                source_url=document.source_url or document.document_url or "",
            )
        return [], EvidenceRef(
            evidence_id="",
            document_id=document.document_id,
            start_offset=None,
            end_offset=None,
            excerpt="",
            source_url=document.source_url or document.document_url or "",
        )
    first_start, first_end, _first_text = matches[0]
    context_start = max(0, first_start - radius)
    context_end = min(len(body), first_end + radius)
    evidence = make_evidence(
        document,
        kind,
        body,
        context_start,
        context_end,
    )
    return matches, evidence


# ---------------------------------------------------------------------------
# Shared cue patterns
# ---------------------------------------------------------------------------

_POSITIVE_CUES = re.compile(
    r"增长|增加|提升|上修|预增|扭亏|盈利|利好|受益|突破|中标|签订|签署|"
    r"获批|取得|通过|回购|增持|投产|达产|涨价|获|收到|确认|增强|改善|"
    r"扩大|放量|加速|创新高|上调|上涨|提高"
)
_NEGATIVE_CUES = re.compile(
    r"下降|减少|下滑|预亏|亏损|下修|终止|取消|减持|诉讼|处罚|失败|"
    r"被否|驳回|暂缓|停止|清仓|卖出|计提|减值|风险|不确定"
)
_RUMOR_CUES = re.compile(r"传闻|市场消息|据悉|知情人士|或(?:将|有|存在)")

# v2 优化计划（plan.md 第三部分 里程碑 1）：终止、失败、撤回、未通过等终态
# 优先于正文中的历史正向描述，避免正负词同时出现时被误判为正向
# （“终止重大资产重组说明会”固定为无正向信号）。
_TERMINAL_WORDS = (
    "终止",
    "中止",
    "取消",
    "撤回",
    "停止",
    "暂缓",
    "失败",
    "未通过",
    "被否",
    "驳回",
)
_TERMINAL_RE = re.compile("|".join(re.escape(word) for word in _TERMINAL_WORDS))
_TERMINAL_NEGATION_RE = re.compile(r"(?:未|不|不会|难以)\s*$")
_TERMINAL_DECISION_RE = re.compile(
    r"(?:决定|审议通过|同意|宣布|正式)\s*"
    r"(?:终止|中止|取消|撤回|停止|暂缓|失败|未通过|被否|驳回)"
)
_TERMINAL_UNAMBIGUOUS_RE = re.compile(r"失败|未通过|被否|驳回")
# 终态词必须出现在事件关键词近旁（句段事实定位），避免会计口径
# （“未通过单独主体达成的合营安排”“诉讼被驳回”）把定期报告误判为负向。
_EVENT_KEYWORD_RE = re.compile(
    r"重组|收购|并购|回购|增持|合同|订单|中标|审批|注册|批文|上市|发行|"
    r"投产|量产|达产|产能|项目|方案|计划|交易|股权|资产|客户|供货|"
    r"临床试验|受理|许可|投标|招标"
)

_TITLE_BODY_CONFLICT_CUES = re.compile(
    r"未能[^。；;]{0,16}(?:签订|签署|中标|获批|取得|完成|实施|投产|量产|增持|回购)|"
    r"(?:终止|取消)[^。；;]{0,16}(?:合同|订单|项目|审批|回购|增持|重组|收购)|"
    r"(?:申请|交易|方案)[^。；;]{0,16}(?:被否|驳回|未获通过)|"
    r"不予(?:批准|通过|许可|注册)|不构成(?:正式合同|订单|重大资产重组)|"
    r"无实质(?:进展|影响)"
)

_EXPECTATION_BEAT_CUES = re.compile(
    r"超预期|提前(?:完成|投产|达产|实现)|创新高|刷新纪录|历史最高|首次突破"
)
_FIRST_TIME_CUES = re.compile(r"首次|第一次|开创|填补空白")
_BREAKTHROUGH_CUES = re.compile(r"突破|重大进展|里程碑")
_PROGRESS_CUES = re.compile(r"进展|更新|补充|修订|公告|披露")

_ALREADY_CONFIRMED_CUES = re.compile(
    r"上一年度已(?:确认|计入)|此前已(?:披露|预告|公告)|已计提|已实施完毕"
)
_PARTIAL_OFFSET_CUES = re.compile(
    r"尚未生效|履行周期(?:较|过)长|含税|非经常性|出售资产|资产处置收益|"
    r"扣非后净利润(?:下降|下滑)|仅为方案|是否.{0,8}(?:落地|兑现|执行)|"
    r"能否商业化|能否量产|尚未上市销售|"
    r"尚需[^。；;]{0,16}(?:履行|审批|通过|生效|签署|实施|完成)"
)
_HIGH_UNCERTAINTY_CUES = re.compile(
    r"终止|取消|诉讼|仲裁|审批失败|被否|客户(?:存在|仍存)不确定性|"
    r"历史执行率(?:很|较)低|仅为方案|市场传闻|传闻"
)

# Hypothetical / risk-disclaimer language: a match preceded by one of these
# within a short window describes a possible future event, not an actual
# reversal or uncertainty, and must not be counted as counter-evidence
# (standard buyback/approval/contract announcements all contain such boilerplate).
_HYPOTHETICAL_MARKERS = re.compile(
    r"若|如|如果|可能|或将|预计|有权|可以|会|风险|导致|发生|无法|是否|如发生|"
    r"拟|择机|适时|可由|可自|可决定|或|或者|致使|则"
)


@dataclass(frozen=True, slots=True)
class Detection:
    event_type: str
    direction: str
    positive_mechanism: str | None
    metrics: tuple[dict[str, object], ...]
    certainty_stage: str
    certainty: float
    materiality_level: int
    unexpectedness: float
    counter_evidence: tuple[dict[str, object], ...]
    evidence_ids: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    rejection_reason: str | None = None


def _metric(
    name: str,
    value: object,
    unit: str | None,
    comparison_basis: str | None,
    comparison_ratio: float | None,
    evidence_id: str,
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "comparison_basis": comparison_basis,
        "comparison_ratio": comparison_ratio,
        "evidence_id": evidence_id,
    }


def _direction(text: str, title: str = "") -> str:
    if _has_terminal_state(text, title):
        return "negative"
    if _NEGATIVE_CUES.search(text) and not _POSITIVE_CUES.search(text):
        return "negative"
    if _POSITIVE_CUES.search(text):
        return "positive"
    return "neutral"


def _has_terminal_state(text: str, title: str = "") -> bool:
    """True when the text states a real terminal state (v2 终态优先).

    只有“已发生的终态”才把方向判为负向：标题出现终止/取消/失败/撤回等终态
    词，或正文出现“决定/审议通过/已…终止”等明确决策表述；假设/风险提示
    （“若…则终止”“存在失败风险”）与显式否定（“未终止”“不取消”）不构成
    终态。定期报告正文中与事件无关的会计口径（“借款费用停止资本化”“因取消
    股权激励确认的费用”）不翻转方向。
    """

    if _terminal_marker_in(title):
        return True
    for match in _TERMINAL_DECISION_RE.finditer(text):
        if _HYPOTHETICAL_MARKERS.search(text[max(0, match.start() - 40) : match.start()]):
            continue
        return True
    # 未通过/被否/驳回/失败等无歧义终态：只在事件关键词近旁生效；定期报告
    # 正文中的会计/诉讼口径不参与（v2 里程碑 3 句段事实定位）。
    if not _is_periodic_report_title(title):
        for match in _TERMINAL_UNAMBIGUOUS_RE.finditer(text):
            start, end = match.start(), match.end()
            if "风险" in text[end : end + 4]:
                continue
            # 临床“治疗失败/化疗失败”指患者既往治疗无效，不是公司事件终态。
            if match.group(0) == "失败" and re.search(
                r"治疗|化疗|放疗|用药|临床",
                text[max(0, start - 8) : start],
            ):
                continue
            # “亦未通过…获知”是否定介词用法，不是“审核未通过”；
            # 门控词在“未通过”前后任一方向出现都算真正的审核终态。
            if match.group(0) == "未通过" and not re.search(
                r"审核|审议|股东会|董事会|重组|收购|验收|检查|批准|注册|许可|测试|试验",
                text[max(0, start - 12) : end]
                + text[end : end + 12],
            ):
                continue
            window = text[max(0, start - 60) : end + 40]
            if not _EVENT_KEYWORD_RE.search(window):
                continue
            if _HYPOTHETICAL_MARKERS.search(
                text[max(0, start - 40) : start]
            ):
                continue
            return True
    return False


def _is_periodic_report_title(title: str) -> bool:
    """定期报告（年报/半年报/季报及其摘要）标题识别。"""

    return bool(
        re.search(r"半年度报告|年度报告|季度报告", title or "")
    )


def _terminal_marker_in(section: str) -> bool:
    for match in _TERMINAL_RE.finditer(section):
        start, end = match.start(), match.end()
        # 紧跟“风险”的“终止/失败/取消”是风险提示，不是已发生的终态。
        if "风险" in section[end : end + 4]:
            continue
        prefix = section[max(0, start - 40) : start]
        if _HYPOTHETICAL_MARKERS.search(prefix):
            continue
        preceding = section[max(0, start - 3) : start]
        if _TERMINAL_NEGATION_RE.search(preceding):
            continue
        return True
    return False


def _materiality_from_ratio(ratio: float | None, *, buyback: bool = False) -> int | None:
    if ratio is None:
        return None
    if buyback:
        if ratio < 0.001:
            return 0
        if ratio < 0.005:
            return 1
        if ratio < 0.01:
            return 2
        if ratio < 0.03:
            return 3
        return 4
    if ratio < 0.01:
        return 0
    if ratio < 0.05:
        return 1
    if ratio < 0.15:
        return 2
    if ratio < 0.30:
        return 3
    return 4


def _qualitative_materiality(
    documents: list[SourceDocument],
    evidence_ids: tuple[str, ...],
    level: int,
) -> int:
    """Qualitative materiality 3-4 requires two independent evidence refs and
    at least one formal disclosure; otherwise the level is capped at 2."""

    if level <= 2:
        return level
    independent_evidence = len({item for item in evidence_ids if item})
    if independent_evidence < 2 or not any(is_formal_disclosure(doc) for doc in documents):
        return 2
    return level


def _scan_counter_evidence(
    document: SourceDocument,
    *,
    include_uncertainty: bool = True,
) -> tuple[tuple[dict[str, object], ...], tuple[EvidenceRef, ...]]:
    """Scan a document for plan.md 10.7 counter-evidence patterns.

    ``include_uncertainty=False`` keeps only the title/body conflict check and
    is used for periodic reports: their forward-looking risk boilerplate (lock-
    up/减持 commitments, contingent litigation notes) refers to other matters,
    while the reported results themselves are executed facts.
    """

    body = document.body_text or ""
    patterns: list[tuple[str, re.Pattern[str]]] = []
    if _POSITIVE_CUES.search(document.title or ""):
        patterns.append(("title_body_conflict", _TITLE_BODY_CONFLICT_CUES))
    if include_uncertainty:
        patterns.extend(
            (
                ("high_uncertainty", _HIGH_UNCERTAINTY_CUES),
                ("partial", _PARTIAL_OFFSET_CUES),
            )
        )
    result: list[dict[str, object]] = []
    refs: list[EvidenceRef] = []
    for kind, pattern in patterns:
        matches, _evidence = find_context_matches(document, pattern, kind)
        if not matches:
            # Title-only match: the headline itself contradicts the positive
            # body (e.g. "关于终止回购的公告"); keep it as counter-evidence.
            if pattern.search(document.title or ""):
                evidence = make_evidence(
                    document, kind, document.title or "", 0, len(document.title or "")
                )
                if evidence.evidence_id:
                    result.append(
                        {
                            "kind": kind,
                            "reason": f"标题出现{kind}信号",
                            "evidence_id": evidence.evidence_id,
                        }
                    )
                    refs.append(evidence)
            continue
        surviving: list[tuple[int, int]] = []
        for start, end, _text in matches:
            if kind in ("title_body_conflict", "high_uncertainty"):
                prefix = body[max(0, start - 40) : start]
                match_text = body[start:end]
                if _HYPOTHETICAL_MARKERS.search(prefix) or _HYPOTHETICAL_MARKERS.search(
                    match_text
                ):
                    continue
            surviving.append((start, end))
        if not surviving:
            continue
        start, end = surviving[0]
        context_start = max(0, start - 60)
        context_end = min(len(body), end + 60)
        evidence = make_evidence(document, kind, body, context_start, context_end)
        if not evidence.evidence_id:
            continue
        result.append(
            {
                "kind": kind,
                "reason": f"正文出现{kind}信号",
                "evidence_id": evidence.evidence_id,
            }
        )
        refs.append(evidence)
    return tuple(result), tuple(refs)


def _certainty(text: str) -> tuple[str, float]:
    if re.search(
        r"已(?:到账|实施|执行|完成|取得|获批|落地|回购|增持|投产|量产|过户)|"
        r"正式(?:投产|量产)|(?:投产|量产)(?:完成|成功)|"
        r"顺利(?:投产|量产)|建成(?:投产|达产)|"
        r"已[^。；;]{0,12}?(?:投产|量产)|"
        r"收到[^。；;]{0,48}(?:批复|批文|注册证|许可证)|"
        r"(?:取得|获得)[^。；;]{0,24}(?:许可证|注册证|批件)", text
    ):
        return "executed", CERTAINTY_STAGES["executed"]
    if re.search(
        r"签订|签署|正式合同|董事会(?:决议|审议)?通过|股东大会通过|审议通过|已通过|"
        r"审核通过|过会|证监会(?:同意|核准|注册)",
        text,
    ):
        return "signed", CERTAINTY_STAGES["signed"]
    if re.search(r"中标|获选|评标|中选", text):
        return "awarded", CERTAINTY_STAGES["awarded"]
    if _RUMOR_CUES.search(text):
        return "rumor", CERTAINTY_STAGES["rumor"]
    if re.search(r"框架协议|合作意向|筹划|申请中|审批中|拟|计划|预计|待签|公示中", text):
        return "framework", CERTAINTY_STAGES["framework"]
    return "framework", CERTAINTY_STAGES["framework"]


def _unexpectedness(text: str, historical_similar_event_id: str | None) -> float:
    if _EXPECTATION_BEAT_CUES.search(text):
        return 100.0
    if _FIRST_TIME_CUES.search(text) or _BREAKTHROUGH_CUES.search(text):
        return 75.0
    if historical_similar_event_id is None:
        return 50.0
    if _PROGRESS_CUES.search(text):
        return 25.0
    return 0.0


def _novelty(
    historical_similar_event_id: str | None,
    current_amounts: tuple[str, ...],
    historical_amounts: tuple[str, ...],
) -> float:
    if historical_similar_event_id is None:
        return 100.0
    if current_amounts and historical_amounts and current_amounts != historical_amounts:
        return 60.0
    return 30.0


def _canonical_amounts(text: str) -> tuple[str, ...]:
    """Canonicalized amounts for structured-fingerprint comparison."""

    result: list[str] = []
    for match in _AMOUNT_PATTERN.finditer(text):
        parsed = parse_amount(match.group(0))
        if parsed is not None:
            result.append(f"{parsed[0]:.0f}")
    return tuple(dict.fromkeys(result))


def canonical_amounts(text: str) -> tuple[str, ...]:
    """Public wrapper used by the clustering structured fingerprint."""

    return _canonical_amounts(text)


def _canonical_targets(text: str) -> tuple[str, ...]:
    """Key customer/target names for structured fingerprints (conservative)."""

    result: list[str] = []
    for pattern in (
        re.compile(r"(?:客户|供应商|合作方)[：:为是]?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,20})"),
        re.compile(r"(?:标的|收购|并购)[：:为是]?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,20})"),
    ):
        for match in pattern.finditer(text):
            result.append(match.group(1))
    return tuple(dict.fromkeys(result))


def canonical_targets(text: str) -> tuple[str, ...]:
    """Public wrapper used by the clustering structured fingerprint."""

    return _canonical_targets(text)


def _canonical_dates(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            match.group(0)
            for match in re.finditer(r"(?:20\d{2}年)?\d{1,2}月\d{1,2}日", text)
        )
    )


def canonical_dates(text: str) -> tuple[str, ...]:
    """Public wrapper used by the clustering structured fingerprint."""

    return _canonical_dates(text)


def _best_document(documents: list[SourceDocument]) -> SourceDocument:
    return sorted(documents, key=_provider_rank)[0]


def _provider_rank(document: SourceDocument) -> int:
    """Lower is more authoritative (used to pick the best document).

    辅助性文件（法律意见书、核查意见、专项/审计报告等）永远排在主公告之后，
    避免“增持完成公告 + 法律意见书”同簇时用意见书正文污染确定性/方向
    （万孚生物回归：意见书正文的“或存在”让确定性误判为 rumor）。
    """

    if document.provider_key == "cninfo":
        base = 0
    elif document.provider_key in {"sse", "irm"}:
        base = 1
    else:
        base = 2
    if re.search(
        r"法律意见书|核查意见|验资报告|专项报告|审计报告|独立财务顾问报告",
        document.title or "",
    ):
        base += 3
    return base


# ---------------------------------------------------------------------------
# Ten event-type detectors
# ---------------------------------------------------------------------------


def _headline_event_documents(
    documents: list[SourceDocument],
    pattern: re.Pattern[str],
    *,
    excluded_title: re.Pattern[str] | None = None,
) -> list[SourceDocument]:
    """Return documents whose title and body both describe the event.

    Formal disclosures routinely mention contracts, approvals, buybacks and
    acquisitions in risk sections, historical background or board agendas.
    Treating any body occurrence as the current company-level event produced
    false research signals.  Requiring a matching disclosure headline keeps
    the rule pipeline conservative while still allowing the body to provide
    the required evidence.
    """

    hits: list[SourceDocument] = []
    for document in documents:
        title = document.title or ""
        body = document.body_text or ""
        if excluded_title is not None and excluded_title.search(title):
            continue
        if pattern.search(title) and pattern.search(body):
            hits.append(document)
    return hits


# v2 里程碑 3：章节定位。句段事实优先从经营/财务章节提取，附注、风险提示、
# 备查文件等章节降权，避免“全文关键词首次命中”被脚注或风险提示误导。
_SECTION_PREFERRED_RE = re.compile(
    r"主要会计数据|主要财务数据|经营情况讨论与分析|管理层讨论与分析|"
    r"董事会报告|经营情况|主要经营情况|报告期内经营"
)
_SECTION_DEPRIORITIZED_RE = re.compile(
    r"财务报表附注|附注|风险提示|备查文件|释义|公司简介"
)


def _section_rank(body: str, position: int) -> int:
    """返回 position 所在章节的优先级：0=经营/财务章节，1=正文，2=附注/风险/备查。"""

    prefix = body[: max(0, position)]
    preferred = [
        match.start() for match in _SECTION_PREFERRED_RE.finditer(prefix)
    ]
    deprioritized = [
        match.start() for match in _SECTION_DEPRIORITIZED_RE.finditer(prefix)
    ]
    preferred_pos = max(preferred) if preferred else -1
    deprioritized_pos = max(deprioritized) if deprioritized else -1
    if preferred_pos >= 0 and preferred_pos >= deprioritized_pos:
        return 0
    if deprioritized_pos >= 0:
        return 2
    return 1


def _detect_earnings_upgrade(
    documents: list[SourceDocument],
) -> Detection | None:
    # plan.md 10.1 earnings_upgrade covers guidance (业绩预告/快报/上修) and
    # formally reported periodic results (半年度/年度/季度报告).  The headline
    # gate keeps body mentions in risk sections from becoming the current
    # company-level event; documents that merely reference a periodic report
    # (inquiry replies, delay notices) are excluded explicitly.
    pattern = re.compile(
        r"业绩预告|业绩快报|业绩上修|预计净利润|归母净利润|扣非净利润|"
        r"归属于上市公司股东的净利润|归属于上市公司股东的扣除非经常性损益的净利润|"
        r"半年度报告|年度报告|季度报告|"
        r"净利润.{0,40}(?:同比增长|较上年同期|增长|增加|预增|上修)|扭亏为盈|预盈"
    )
    excluded_title = re.compile(r"问询函|延期|无法按期|终止|取消")
    hits = _headline_event_documents(
        documents, pattern, excluded_title=excluded_title
    )
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    body = doc.body_text or ""
    matches = list(pattern.finditer(body))
    # A bare periodic-report term (e.g. “半年度报告” in the boilerplate header)
    # carries no figures; prefer the match that describes earnings itself, and
    # use 章节定位 to favour 经营/财务 sections over 附注/风险提示 (v2 里程碑 3).
    def _match_key(match: re.Match[str]) -> tuple[int, int]:
        return (_section_rank(body, match.start()), match.start())

    earnings_matches = [
        match
        for match in matches
        if re.search(r"净利润|归母|扣非|业绩|扭亏|预盈|预增", match.group(0))
    ]
    candidates = earnings_matches if earnings_matches else matches
    earnings_match = min(candidates, key=_match_key) if candidates else None
    if earnings_match is not None:
        evidence_start = max(0, earnings_match.start() - 60)
        evidence_end = min(len(body), earnings_match.end() + 60)
        evidence = make_evidence(
            doc,
            "earnings_upgrade",
            body,
            evidence_start,
            evidence_end,
        )
        ratio_context = body[earnings_match.start() : earnings_match.end() + 40]
    else:
        title_match = pattern.search(doc.title or "")
        excerpt = re.sub(r"\s+", " ", doc.title or "").strip()
        evidence = EvidenceRef(
            evidence_id=(
                hashlib.sha1(
                    f"{doc.document_id}:earnings_upgrade:{excerpt[:80]}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
                if excerpt
                else ""
            ),
            document_id=doc.document_id,
            start_offset=None,
            end_offset=None,
            excerpt=excerpt[:EXCERPT_MAX_CHARS],
            source_url=doc.source_url or doc.document_url or "",
        )
        ratio_context = body
    direction = _direction(text, doc.title)
    ratio = None
    ratio_match = re.search(
        r"(?:同比|较上年同期|同比增长|净利润增长)[^。；;]{0,20}?"
        r"([+-]?\d[\d,]*\.?\d*)\s*(?:%|个百分点)",
        ratio_context,
    )
    if ratio_match is not None:
        ratio = parse_percent(ratio_match.group(0))
    metrics: list[dict[str, object]] = []
    if ratio is not None:
        metrics.append(
            _metric(
                "归母净利润同比变动",
                ratio * 100,
                "%",
                "上年同期",
                ratio,
                evidence.evidence_id,
            )
        )
    is_periodic_report = bool(
        re.search(r"半年度报告|年度报告|季度报告", doc.title or "")
    )
    if is_periodic_report:
        amount = _net_profit_level_amount(body)
    else:
        amount = parse_amount(ratio_context)
    if amount is not None:
        metrics.append(
            _metric(
                "净利润" if is_periodic_report else "预计净利润",
                amount[2],
                amount[1],
                None,
                None,
                evidence.evidence_id,
            )
        )
    # v2 检测覆盖：方向以披露数值为准。定期报告披露的归母净利润为负
    # （亏损收窄、*ST/净资产为负都不是正向业绩事件）或同比比例下降时，
    # 不得判为业绩上修；“扣非净利润下降”保留为部分反证，不翻转整体方向。
    if is_periodic_report and amount is not None and amount[0] < 0:
        if not re.search(r"扭亏为盈|预盈", text):
            direction = "negative"
    if ratio is not None and ratio < 0:
        before = body[max(0, ratio_match.start() - 30) : ratio_match.start()]
        if not re.search(r"扣非", before + ratio_match.group(0)):
            direction = "negative"
    # 表格口径的同比数字（“净利润（元） N M -20.59%”没有“同比”提示词）：
    # 取净利润所在行（表行/段落）内的百分比；为负时方向为负
    # （大中矿业回归，5a44de6f）。只扫描本行，避免把扣非行/其他指标的
    # 百分比混入；行内出现“扣非”字样时不适用。
    if (
        direction != "negative"
        and ratio is None
        and earnings_match is not None
    ):
        row_end = body.find("\n", earnings_match.end())
        if row_end == -1:
            row_end = min(len(body), earnings_match.end() + 120)
        row_text = body[earnings_match.start() : row_end]
        if not re.search(r"扣非", row_text):
            row_pct = re.search(r"([+-]?\d[\d,]*\.?\d*)\s*%", row_text)
            if row_pct is not None and parse_percent(row_pct.group(0)) < 0:
                direction = "negative"
    materiality = _materiality_from_ratio(ratio)
    if materiality is None:
        if re.search(r"扭亏为盈|预盈|同比(?:增长|预增)", text):
            materiality = 2
        else:
            materiality = 1
    if is_periodic_report:
        # 定期报告披露的是已实现业绩，确定性按“已执行”口径。
        certainty_stage, certainty = "executed", CERTAINTY_STAGES["executed"]
    else:
        certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(
        doc, include_uncertainty=not is_periodic_report
    )
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "业绩方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "业绩增长或扭亏为盈改善盈利预期"
    return Detection(
        event_type="earnings_upgrade",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,  # filled by extractor
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_major_contract(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"重大合同|签订(?:了)?合同|签署(?:了)?合同|中标|订单|供货合同|销售合同|"
        r"合同金额|项目合同|协议金额"
    )
    hits = _headline_event_documents(documents, pattern)
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    if re.search(
        r"进入.{0,12}(?:供应链|供应体系)|客户突破|合格供应商|批量供货",
        text,
    ):
        # A customer-supply relationship is the primary event; let the
        # customer-breakthrough detector own it.
        return None
    matches, evidence = find_context_matches(doc, pattern, "major_contract")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    amount = parse_amount(doc.body_text or "")
    ratio = None
    if amount is not None:
        ratio_match = re.search(
            r"占[^。；;]{0,12}(?:营业)?收入[^。；;]{0,20}?"
            r"([+-]?\d[\d,]*\.?\d*)\s*(?:%|个百分点)",
            doc.body_text or "",
        )
        if ratio_match is not None:
            ratio = parse_percent(ratio_match.group(0))
        metrics.append(
            _metric(
                "合同金额",
                amount[2],
                amount[1],
                "最近一个会计年度营业收入" if ratio is not None else None,
                ratio,
                evidence.evidence_id,
            )
        )
    materiality = _materiality_from_ratio(ratio) if ratio is not None else None
    if materiality is None:
        materiality = 2 if re.search(r"战略合作|大型|巨额|大额", text) else 1
    certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "合同/订单方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "新增合同或订单预计增厚未来营业收入"
    return Detection(
        event_type="major_contract",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_price_increase(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"涨价|提价|上调(?:了)?(?:产品)?价格|价格上调|出厂价(?:上涨|上调)|"
        r"产品价格(?:上涨|上调|提高)"
    )
    hits = _headline_event_documents(documents, pattern)
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "price_increase")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    ratio_match = re.search(
        r"涨幅|上调(?:幅度)?[^。；;]{0,10}?([+-]?\d[\d,]*\.?\d*)\s*(?:%|个百分点)",
        doc.body_text or "",
    )
    ratio = parse_percent(ratio_match.group(0)) if ratio_match is not None else None
    if ratio is not None:
        metrics.append(
            _metric(
                "产品价格涨幅",
                ratio * 100,
                "%",
                "调整前价格",
                ratio,
                evidence.evidence_id,
            )
        )
    product_match = re.search(
        r"(?:对)?([\u4e00-\u9fa5A-Za-z0-9]{2,24})(?:产品|出厂价|价格)",
        doc.body_text or "",
    )
    if product_match is not None:
        metrics.append(
            _metric(
                "涉及产品",
                product_match.group(1),
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
    materiality = _materiality_from_ratio(ratio) if ratio is not None else None
    if materiality is None:
        materiality = 1
    certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "价格变动方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "产品价格上涨直接提升单位盈利能力"
    return Detection(
        event_type="price_increase",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_approval(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"获批|批准|注册证|批件|上市许可|药品注册|临床试验(?:批件|批准)|"
        r"通过(?:了)?.{0,12}(?:认证|审批|审核|备案)|"
        r"(?:取得|获得)(?:了)?.{0,12}(?:证书|批文|许可|许可证)"
    )
    intellectual_property = re.compile(
        r"专利|商标|软件著作权|知识产权|作品登记"
    )
    hits = _headline_event_documents(
        documents,
        pattern,
        excluded_title=intellectual_property,
    )
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "approval")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    product_match = re.search(
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,24})(?:产品|药品|疫苗|器械)?(?:获|取得|通过)",
        doc.body_text or "",
    )
    if product_match is not None:
        metrics.append(
            _metric(
                "获批产品",
                product_match.group(1),
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
    if re.search(r"海外|美国FDA|欧洲|欧盟|日本|国际", doc.body_text or ""):
        metrics.append(
            _metric(
                "目标市场",
                "海外",
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
    materiality = 2 if re.search(r"量产|商业化|上市销售", doc.body_text or "") else 1
    certainty_stage, certainty = _certainty(text)
    if re.search(r"获批|取得.{0,8}(?:批文|证书|注册证)", text):
        certainty_stage = "executed"
        certainty = CERTAINTY_STAGES["executed"]
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "获批/认证方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "获批/认证为相关产品打开准入或商业化空间"
    return Detection(
        event_type="approval",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_buyback_or_increase(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"回购|增持|股份回购|股东增持|增持计划|回购方案|回购股份"
    )
    non_return_cancellation = re.compile(
        r"(?:限制性股票|股权激励)[^。；;]{0,20}回购注销|"
        r"回购注销[^。；;]{0,20}(?:限制性股票|股权激励)|"
        # 行权价/回购价格调整是例行会计调整，不是回购增持事件（广合科技回归）。
        r"行权价格|回购价格|调整[^。；;]{0,12}?价格"
    )
    hits = _headline_event_documents(
        documents,
        pattern,
        excluded_title=non_return_cancellation,
    )
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "buyback_or_increase")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    amount = parse_amount(doc.body_text or "")
    ratio = None
    if amount is not None:
        ratio_match = re.search(
            r"占(?:总股本|公司总股本)[^。；;]{0,12}?([+-]?\d[\d,]*\.?\d*)\s*(?:%|个百分点)",
            doc.body_text or "",
        )
        ratio = parse_percent(ratio_match.group(0)) if ratio_match is not None else None
        metrics.append(
            _metric(
                "回购/增持金额",
                amount[2],
                amount[1],
                "公司总市值" if ratio is not None else None,
                ratio,
                evidence.evidence_id,
            )
        )
    price_match = re.search(
        r"(?:回购|增持)(?:价格)?(?:上限|不超过)[^。；;]{0,8}?([+-]?\d[\d,]*\.?\d*)\s*元",
        doc.body_text or "",
    )
    if price_match is not None:
        price = _clean_number(price_match.group(1))
        if price is not None:
            metrics.append(
                _metric(
                    "价格上限",
                    price,
                    "元/股",
                    None,
                    None,
                    evidence.evidence_id,
                )
            )
    materiality = _materiality_from_ratio(ratio, buyback=True) if ratio is not None else None
    if materiality is None:
        materiality = 1
    certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    if certainty_stage == "framework":
        counter_evidence = counter_evidence + (
            {
                "kind": "high_uncertainty",
                "reason": "回购/增持仅为方案，执行存在不确定性",
                "evidence_id": evidence.evidence_id,
            },
        )
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "回购/增持方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "回购或增持改善资本结构与股东回报预期"
    return Detection(
        event_type="buyback_or_increase",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_mna(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"并购(?!买)|重组|收购|重大资产重组|重大资产购买|"
        r"发行[^。；;]{0,24}?购买[^。；;]{0,20}?(?:资产|股权)|"
        r"吸收合并|控制权变更|股权收购|资产注入|借壳"
    )
    internal_reorganization = re.compile(
        r"子公司之间.{0,12}吸收合并|"
        r"全资子公司.{0,20}(?:吸收合并|内部重组)|"
        r"(?:吸收合并|内部重组).{0,20}全资子公司"
    )
    # 问询函回复/延期公告只是监管流程文书，不是并购事件的实质进展
    # （柳钢股份回归，a9a37455）。
    regulatory_process = re.compile(r"问询函|延期|无法按期|回复(?:公告)?")
    hits = _headline_event_documents(
        documents,
        pattern,
        excluded_title=re.compile(
            f"{internal_reorganization.pattern}|{regulatory_process.pattern}"
        ),
    )
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "mna")
    direction = _direction(text, doc.title)
    if re.search(r"出售|剥离|置出|转让|减持", text) and not re.search(
        r"收购|购买|注入|增持", text
    ):
        direction = "negative"
    if direction == "neutral" and re.search(
        r"收购|购买|注入|增持|优质资产|协同|增强盈利能力", text
    ):
        direction = "positive"
    metrics: list[dict[str, object]] = []
    evidence_refs: list[EvidenceRef] = [evidence]
    amount = parse_amount(doc.body_text or "")
    if amount is not None:
        amount_matches, amount_evidence = find_context_matches(
            doc, _AMOUNT_PATTERN, "mna_amount"
        )
        if amount_evidence.evidence_id:
            evidence_refs.append(amount_evidence)
        metrics.append(
            _metric(
                "交易金额",
                amount[2],
                amount[1],
                None,
                None,
                evidence.evidence_id,
            )
        )
    target_match = re.search(
        r"(?:收购|并购|重组|标的)(?:对象)?[：:为是]?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,24})",
        doc.body_text or "",
    )
    if target_match is not None:
        metrics.append(
            _metric(
                "交易标的",
                target_match.group(1),
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
    if re.search(r"重组|控制权变更|借壳|整体上市|资产注入", text):
        materiality = 4
    elif re.search(r"收购|并购|购买[^。；;]{0,20}?(?:股权|资产)", text):
        materiality = 3
    else:
        materiality = 2
    materiality = _qualitative_materiality(
        documents,
        tuple(item.evidence_id for item in evidence_refs),
        materiality,
    )
    certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "并购重组方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "并购重组可能增强资产或业务协同"
    return Detection(
        event_type="mna",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(*evidence_refs, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_capacity_launch(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"投产|达产|量产|新增产能|产能释放|试生产|首条产线|产线(?:建成|投产)"
    )
    hits = _headline_event_documents(documents, pattern)
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "capacity_launch")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    capacity_match = re.search(
        r"([+-]?\d[\d,]*\.?\d*)\s*(?:万吨|万吨\/年|GW|GWh|吨|万件|万台|万套|亿只|只)",
        doc.body_text or "",
    )
    if capacity_match is not None:
        metrics.append(
            _metric(
                "新增产能",
                capacity_match.group(0),
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
    materiality = 2 if re.search(r"新增产能|扩产|产能(?:翻|提升|释放)", text) else 1
    certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "产能投产方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "新增产能释放打开收入增长空间"
    return Detection(
        event_type="capacity_launch",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_direct_policy_benefit(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"政策(?:利好|受益|支持|补贴)|国补|以旧换新|政府采购|专项债|"
        r"税收优惠|退税|补贴政策|消费补贴"
    )
    hits = _headline_event_documents(documents, pattern)
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "direct_policy_benefit")
    direction = _direction(text, doc.title)
    if direction != "positive":
        return None
    metrics: list[dict[str, object]] = (
        _metric(
            "政策要点",
            re.sub(r"\s+", " ", (doc.body_text or "")[:80]).strip(),
            None,
            None,
            None,
            evidence.evidence_id,
        ),
    )
    materiality = 2 if re.search(r"直接受益|利好|明确|落地|实施", text) else 1
    certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    mechanism = "政策直接覆盖公司业务，可能带来需求或成本改善"
    return Detection(
        event_type="direct_policy_benefit",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=metrics,
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
    )


def _detect_customer_breakthrough(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"进入(?:了)?.{0,12}(?:供应链|供应体系|客户名单|供应商)|"
        r"获得(?:了)?.{0,12}客户|通过(?:了)?.{0,12}客户认证|"
        r"客户突破|大客户|合格供应商|定点"
    )
    hits = _headline_event_documents(documents, pattern)
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "customer_breakthrough")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    customer_match = re.search(
        r"(?:进入|获得|通过|成为|拿下|开拓)[^。；;]{0,20}?([\u4e00-\u9fa5A-Za-z0-9]{2,24})"
        r"(?:的)?(?:供应链|客户|供应商|认证)",
        doc.body_text or "",
    )
    if customer_match is not None:
        metrics.append(
            _metric(
                "客户",
                customer_match.group(1),
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
    materiality = 2 if re.search(r"量产|供货|订单|批量", text) else 1
    certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "客户突破方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "重要客户突破打开新增收入来源"
    return Detection(
        event_type="customer_breakthrough",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_subsidy_or_compensation(
    documents: list[SourceDocument],
) -> Detection | None:
    pattern = re.compile(
        r"政府补助|补贴|补偿|赔偿|获赔|补助(?:资金|款项)?|拆迁补偿"
    )
    hits = _headline_event_documents(documents, pattern)
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    # 定增“不存在/不提供…财务资助或补偿”是合规承诺，不是补贴赔偿事件
    # （63b76f6b/b0d59160 回归）。
    negated_subsidy = re.compile(
        r"不存在[^。；;]{0,32}?(?:补偿|补贴|资助)|"
        r"(?:不提供|未提供|未向|不得)[^。；;]{0,12}?(?:补偿|补贴|资助)"
    )
    if negated_subsidy.search(text):
        return None
    matches, evidence = find_context_matches(doc, pattern, "subsidy_or_compensation")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    amount = parse_amount(doc.body_text or "")
    ratio = None
    if amount is not None:
        ratio_match = re.search(
            r"占[^。；;]{0,15}(?:净利润|利润总额|归母净利润)[^。；;]{0,12}?"
            r"([+-]?\d[\d,]*\.?\d*)\s*(?:%|个百分点)",
            doc.body_text or "",
        )
        ratio = parse_percent(ratio_match.group(0)) if ratio_match is not None else None
        metrics.append(
            _metric(
                "补贴/赔偿金额",
                amount[2],
                amount[1],
                "最近一个会计年度净利润" if ratio is not None else None,
                ratio,
                evidence.evidence_id,
            )
        )
    if re.search(r"一次性|非经常性|与日常经营无关", text):
        metrics.append(
            _metric(
                "一次性属性",
                "一次性",
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
    materiality = _materiality_from_ratio(ratio) if ratio is not None else None
    if materiality is None:
        materiality = 1
    certainty_stage, certainty = _certainty(text)
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    if _ALREADY_CONFIRMED_CUES.search(text):
        counter_evidence = counter_evidence + (
            {
                "kind": "partial",
                "reason": "补贴可能已在上一年度确认",
                "evidence_id": evidence.evidence_id,
            },
        )
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "补贴/赔偿方向非正向"
        materiality = 0
    mechanism = None if direction != "positive" else "补贴或赔偿直接增厚当期利润"
    return Detection(
        event_type="subsidy_or_compensation",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


# ---------------------------------------------------------------------------
# v2 新增六类事件（plan.md 第三部分）：股东回报 / 研发里程碑 / 风险解除 /
# 股权激励 / 融资完成 / 资产处置
# ---------------------------------------------------------------------------


def _detect_shareholder_return(
    documents: list[SourceDocument],
) -> Detection | None:
    """现金分红、特别分红、已回购股份注销（必须有正式方案/实施状态与金额）。

    只送转股（股票股利）不是现金回报，不生成信号；方案被终止/取消时方向为负。
    """

    pattern = re.compile(
        r"现金分红|特别分红|分红方案|派息|分配预案|权益分派|利润分配预案|"
        r"分红实施|分红派发|已回购股份(?:注销|并注销)|"
        r"注销(?:所回购|已回购|回购)股份"
    )
    pure_stock_dividend = re.compile(
        r"送股|转增|送转"
    )
    hits = _headline_event_documents(
        documents, pattern, excluded_title=pure_stock_dividend
    )
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "shareholder_return")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    amount = parse_amount(doc.body_text or "")
    ratio = None
    if amount is not None:
        ratio_match = re.search(
            r"占[^。；;]{0,12}(?:净利润|归母净利润|公司总市值|总股本)"
            r"[^。；;]{0,20}?([+-]?\d[\d,]*\.?\d*)\s*(?:%|个百分点)",
            doc.body_text or "",
        )
        ratio = (
            parse_percent(ratio_match.group(0))
            if ratio_match is not None
            else None
        )
        metrics.append(
            _metric(
                "现金分红/注销金额",
                amount[2],
                amount[1],
                "最近一个会计年度净利润或总市值" if ratio is not None else None,
                ratio,
                evidence.evidence_id,
            )
        )
    if re.search(r"实施|已实施|实施完毕|已完成|已注销", text):
        certainty_stage, certainty = "executed", CERTAINTY_STAGES["executed"]
    elif re.search(r"预案|方案|通过|拟", text):
        certainty_stage, certainty = "signed", CERTAINTY_STAGES["signed"]
    else:
        certainty_stage, certainty = _certainty(text)
    materiality = _materiality_from_ratio(ratio) if ratio is not None else None
    if materiality is None:
        materiality = 1 if re.search(r"特别分红|高比例|大额", text) else 0
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "股东回报方向非正向（终止/取消）"
        materiality = 0
    mechanism = (
        None
        if direction != "positive"
        else "现金分红或回购注销提升股东现金回报"
    )
    return Detection(
        event_type="shareholder_return",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_rd_milestone(
    documents: list[SourceDocument],
) -> Detection | None:
    """关键临床终点、关键技术验证、注册申请受理（正式批文仍归 approval）。"""

    pattern = re.compile(
        r"临床(?:试验)?[^。；;]{0,24}(?:达到|达成|满足|实现)[^。；;]{0,24}"
        r"(?:主要(?:临床)?终点|临床终点)|"
        r"达到[^。；;]{0,12}(?:主要临床终点|临床终点)|"
        r"未达(?:到)?[^。；;]{0,12}(?:主要(?:临床)?终点|临床终点)|"
        r"注册申请(?:获|已)?受理|上市申请受理|"
        r"关键技术(?:验证|突破)|(?:完成|通过)[^。；;]{0,12}(?:技术验证|"
        r"临床前研究|临床(?:试验)?研究)|"
        r"达到[^。；;]{0,16}主要终点"
    )
    approval_title = re.compile(
        r"获批|批准|注册证|批件|上市许可"
    )
    hits = _headline_event_documents(
        documents, pattern, excluded_title=approval_title
    )
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "rd_milestone")
    direction = _direction(text, doc.title)
    # 里程碑达成（达到终点/完成验证/获受理）本身即为正向事实；
    # 终态（未达到/终止）已由 _direction 处理为负向。
    if direction == "neutral" and re.search(
        r"(?<!未)(?<!不)(?:达到|达成|满足|实现|完成|通过|受理)", text
    ):
        direction = "positive"
    metrics: list[dict[str, object]] = []
    if re.search(r"受理", text):
        metrics.append(
            _metric(
                "里程碑类型",
                "注册申请受理",
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
        certainty_stage, certainty = "framework", CERTAINTY_STAGES["framework"]
    else:
        metrics.append(
            _metric(
                "里程碑类型",
                "临床终点/技术验证",
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
        certainty_stage, certainty = "awarded", CERTAINTY_STAGES["awarded"]
    materiality = 2 if re.search(r"突破|首个|首次|关键", text) else 1
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "研发里程碑方向非正向（未达终点/终止）"
        materiality = 0
    mechanism = (
        None
        if direction != "positive"
        else "关键研发里程碑推进产品商业化进程"
    )
    return Detection(
        event_type="rd_milestone",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_risk_resolution(
    documents: list[SourceDocument],
) -> Detection | None:
    """风险警示撤销、重大诉讼/债务/担保/冻结或监管事项正式解除。"""

    pattern = re.compile(
        r"撤销[^。；;]{0,10}(?:风险警示|退市风险警示)|"
        r"(?:风险警示|退市风险警示)[^。；;]{0,20}(?:撤销|解除)|摘帽|"
        r"(?:诉讼|仲裁|债务|担保|冻结|查封|监管)[^。；;]{0,16}"
        r"(?:解除|撤销|终结|结案|和解|化解)"
    )
    hits = _headline_event_documents(documents, pattern)
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "risk_resolution")
    direction = _direction(text, doc.title)
    # 风险事项正式解除/撤销/结案本身即为正向事实（“撤销”不是负面终态）。
    if re.search(
        r"解除|撤销|终结|结案|和解|化解|摘帽", text
    ) and not re.search(r"被实施|新增|维持|继续", text):
        direction = "positive"
    if re.search(r"解除|撤销|终结|结案|和解|化解|摘帽", text):
        certainty_stage, certainty = "executed", CERTAINTY_STAGES["executed"]
    else:
        certainty_stage, certainty = _certainty(text)
    metrics: list[dict[str, object]] = []
    amount = parse_amount(doc.body_text or "")
    if amount is not None:
        metrics.append(
            _metric(
                "涉诉/涉保金额",
                amount[2],
                amount[1],
                None,
                None,
                evidence.evidence_id,
            )
        )
    materiality = 2 if amount is not None else 1
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "风险事项方向非正向（新增/继续）"
        materiality = 0
    mechanism = (
        None
        if direction != "positive"
        else "重大风险事项正式解除，消除不确定性"
    )
    return Detection(
        event_type="risk_resolution",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_equity_incentive(
    documents: list[SourceDocument],
) -> Detection | None:
    """股权激励：必须披露覆盖范围、授予规模与量化考核目标。

    只进入潜在催化（确定性按方案口径 0.45），不生成确定性利好。
    """

    pattern = re.compile(
        r"股权激励计划|限制性股票激励|限制性股票|股票期权激励|股权激励方案"
    )
    # 限制性股票回购注销不属于激励方案（股东回报口径由 shareholder_return
    # 独立处理，且以股东回报为目的的回购已由 buyback 排除）。
    cancelled = re.compile(r"回购注销|注销")
    hits = _headline_event_documents(
        documents, pattern, excluded_title=cancelled
    )
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "equity_incentive")
    direction = _direction(text, doc.title)
    # 三项必需披露：覆盖范围（对象/人数）、授予规模（数量/占比）、量化考核目标。
    has_scope = re.search(r"激励对象|激励人数|覆盖|人员", text) is not None
    has_size = re.search(r"授予(?:数量|规模|股份|股票|限制性股票)|万股|万股数", text) is not None
    has_target = re.search(
        r"考核(?:指标|目标)|业绩考核|营业收入.{0,12}考核|净利润.{0,12}考核",
        text,
    ) is not None
    if not (has_scope and has_size and has_target):
        return Detection(
            event_type="equity_incentive",
            direction=direction,
            positive_mechanism=None,
            metrics=(),
            certainty_stage="framework",
            certainty=CERTAINTY_STAGES["framework"],
            materiality_level=0,
            unexpectedness=0.0,
            counter_evidence=(),
            evidence_ids=(evidence.evidence_id,),
            evidence_refs=(evidence,),
            rejection_reason="股权激励缺少覆盖范围/授予规模/量化考核目标之一",
        )
    # 三项必需披露齐备时，激励计划为正向（绑定核心团队利益）。
    direction = "positive"
    metrics: list[dict[str, object]] = []
    size_match = re.search(
        r"授予(?:数量|规模)[^。；;]{0,12}?([\d,]+\.?\d*)\s*万股|"
        r"占(?:总股本|公司总股本)[^。；;]{0,12}?([+-]?\d[\d,]*\.?\d*)\s*%",
        text,
    )
    if size_match is not None:
        raw = size_match.group(1) or size_match.group(2)
        value = _clean_number(raw)
        if value is not None:
            metrics.append(
                _metric(
                    "授予规模",
                    value,
                    "万股" if size_match.group(1) else "%",
                    "公司总股本" if size_match.group(2) else None,
                    parse_percent(size_match.group(0))
                    if size_match.group(2)
                    else None,
                    evidence.evidence_id,
                )
            )
    # 方案阶段：只进入潜在催化。
    certainty_stage, certainty = "framework", CERTAINTY_STAGES["framework"]
    materiality = 2 if metrics else 1
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    if not counter_evidence:
        counter_evidence = (
            {
                "kind": "high_uncertainty",
                "reason": "股权激励为方案，业绩考核兑现存在不确定性",
                "evidence_id": evidence.evidence_id,
            },
        )
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "股权激励方向非正向（终止/取消）"
        materiality = 0
    mechanism = (
        None
        if direction != "positive"
        else "股权激励绑定核心团队利益，设量化考核目标"
    )
    return Detection(
        event_type="equity_incentive",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


def _detect_financing_completion(
    documents: list[SourceDocument],
) -> Detection | None:
    """融资完成且资金用途存在量化公司级正向机制；预案本身不入榜。"""

    pattern = re.compile(
        r"(?:非公开发行|定向发行|向特定对象发行)[^。；;]{0,20}(?:完成|"
        r"募集资金到位|已到账)|可转换公司债券[^。；;]{0,20}(?:完成发行|上市)|"
        r"发行完成|募集资金(?:到位|已到账)|配股[^。；;]{0,10}完成|"
        r"(?:发行境外上市股份|境外发行上市|发行H股)[^。；;]{0,24}"
        r"(?:备案|备案通知书)|"
        r"(?:完成|成功)[^。；;]{0,16}?(?:发行|交割)|"
        r"(?:发行|交割)[^。；;]{0,8}?完成"
    )
    plan_only = re.compile(r"预案|申请|获批|问询|回复")
    hits = _headline_event_documents(
        documents, pattern, excluded_title=plan_only
    )
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "financing_completion")
    direction = _direction(text, doc.title)
    if direction == "neutral" and re.search(
        r"发行完成|募集资金(?:到位|已到账)|已完成|成功发行|"
        r"完成[^。；;]{0,12}?交割|交割[^。；;]{0,8}?完成",
        text,
    ):
        direction = "positive"
    metrics: list[dict[str, object]] = []
    amount = parse_amount(doc.body_text or "")
    # 量化公司级正向机制：募集资金用于产能/项目投资且带金额或比例。
    use_match = re.search(
        r"募集资金[^。；;]{0,30}?用于[^。；;]{0,24}(?:项目|产能|建设|研发|投入)"
        r"[^。；;]{0,30}?([\d,]+\.?\d*)\s*亿元",
        doc.body_text or "",
    )
    if amount is not None:
        metrics.append(
            _metric(
                "募集资金总额",
                amount[2],
                amount[1],
                None,
                None,
                evidence.evidence_id,
            )
        )
    if use_match is not None:
        metrics.append(
            _metric(
                "资金用途量化",
                _clean_number(use_match.group(1)),
                "亿元",
                "项目投资金额",
                None,
                evidence.evidence_id,
            )
        )
    # v2 检测覆盖（中信证券回归）：融资已实际完成（交割/成功发行/到位）且披露
    # 量化募集资金总额时，视为“量化公司级正向机制”成立；预案/获批阶段仍要求
    # 显式“募集资金…用于…项目…亿元”用途表述，否则按原门控拒绝。
    completion_match = re.search(
        r"(?:已完成|完成|成功)[^。；;]{0,20}?(?:发行|交割|到账)|"
        r"(?:交割|到账)[^。；;]{0,10}?完成",
        text,
    )
    total_match = re.search(
        r"募集资金(?:总额)?(?:为)?[^。；;]{0,16}?([\d,]+\.?\d*)\s*亿元",
        doc.body_text or "",
    )
    has_quantified_use = bool(
        metrics
        and (
            use_match is not None
            or (
                completion_match is not None
                and total_match is not None
            )
        )
    )
    if direction != "positive" or not has_quantified_use:
        return Detection(
            event_type="financing_completion",
            direction=direction,
            positive_mechanism=None,
            metrics=tuple(metrics),
            certainty_stage="executed" if "到位" in text or "完成" in text else "framework",
            certainty=(
                CERTAINTY_STAGES["executed"]
                if "到位" in text or "完成" in text
                else CERTAINTY_STAGES["framework"]
            ),
            materiality_level=0,
            unexpectedness=0.0,
            counter_evidence=(),
            evidence_ids=(evidence.evidence_id,),
            evidence_refs=(evidence,),
            rejection_reason=(
                "融资方向非正向" if direction != "positive" else "资金用途缺少量化公司级正向机制"
            ),
        )
    materiality = _materiality_from_ratio(
        _ratio_amount_to_equity(amount[0]) if amount is not None else None
    )
    if materiality is None:
        materiality = 1
    certainty_stage, certainty = "executed", CERTAINTY_STAGES["executed"]
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    # 稀释与融资成本作为反证（部分抵消）。
    if re.search(r"稀释|摊薄即期回报|发行费用", text):
        counter_evidence = counter_evidence + (
            {
                "kind": "partial",
                "reason": "融资带来股本稀释或发行费用",
                "evidence_id": evidence.evidence_id,
            },
        )
    mechanism = "融资完成并投向量化产能/项目，支撑公司级正向机制"
    return Detection(
        event_type="financing_completion",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=None,
    )


def _ratio_amount_to_equity(amount_yuan: float) -> float | None:
    """融资规模相对净资产的粗略占比（缺少净资产时返回 None）。"""

    # 需要正文中的净资产/市值数据；这里不凭常识补全，返回 None 由调用方降级。
    return None


def _detect_asset_disposal(
    documents: list[SourceDocument],
) -> Detection | None:
    """资产处置：必须披露成交状态、现金回收或利润影响；普通出售不自动利好。"""

    pattern = re.compile(
        r"(?:出售|转让|处置)[^。；;]{0,12}(?:资产|股权|房产|土地|子公司)|"
        r"重大资产出售|交割|成交"
    )
    hits = _headline_event_documents(documents, pattern)
    if not hits:
        return None
    doc = _best_document(hits)
    text = f"{doc.title}\n{doc.body_text}"
    matches, evidence = find_context_matches(doc, pattern, "asset_disposal")
    direction = _direction(text, doc.title)
    metrics: list[dict[str, object]] = []
    amount = parse_amount(doc.body_text or "")
    has_status = re.search(r"已完成|已签署|交割|过户|成交", text) is not None
    has_cash_or_profit = re.search(
        r"现金|回收|回款|投资收益|影响利润|利润(?:增加|提升)|增值",
        text,
    ) is not None
    if amount is not None:
        metrics.append(
            _metric(
                "成交金额",
                amount[2],
                amount[1],
                None,
                None,
                evidence.evidence_id,
            )
        )
    if re.search(r"一次性|非经常性|资产处置收益", text):
        metrics.append(
            _metric(
                "一次性属性",
                "一次性",
                None,
                None,
                None,
                evidence.evidence_id,
            )
        )
    if not has_status or not has_cash_or_profit:
        return Detection(
            event_type="asset_disposal",
            direction=direction,
            positive_mechanism=None,
            metrics=tuple(metrics),
            certainty_stage="framework",
            certainty=CERTAINTY_STAGES["framework"],
            materiality_level=0,
            unexpectedness=0.0,
            counter_evidence=(),
            evidence_ids=(evidence.evidence_id,),
            evidence_refs=(evidence,),
            rejection_reason="资产处置缺少成交状态或现金回收/利润影响披露",
        )
    materiality = 2 if amount is not None else 1
    certainty_stage, certainty = "executed", CERTAINTY_STAGES["executed"]
    counter_evidence, counter_refs = _scan_counter_evidence(doc)
    if re.search(r"一次性|非经常性", text):
        counter_evidence = counter_evidence + (
            {
                "kind": "partial",
                "reason": "处置收益属一次性/非经常性损益",
                "evidence_id": evidence.evidence_id,
            },
        )
    rejection_reason = None
    if direction != "positive":
        rejection_reason = "资产处置方向非正向"
        materiality = 0
    mechanism = (
        None
        if direction != "positive"
        else "资产处置回收现金或确认收益（一次性属性已标记）"
    )
    return Detection(
        event_type="asset_disposal",
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=certainty_stage,
        certainty=certainty,
        materiality_level=materiality,
        unexpectedness=0.0,
        counter_evidence=counter_evidence,
        evidence_ids=(evidence.evidence_id,),
        evidence_refs=(evidence, *counter_refs),
        rejection_reason=rejection_reason,
    )


Detector = Callable[[list[SourceDocument]], Detection | None]

_DETECTORS: tuple[tuple[str, Detector], ...] = (
    ("earnings_upgrade", _detect_earnings_upgrade),
    ("major_contract", _detect_major_contract),
    ("price_increase", _detect_price_increase),
    ("approval", _detect_approval),
    ("buyback_or_increase", _detect_buyback_or_increase),
    ("mna", _detect_mna),
    ("capacity_launch", _detect_capacity_launch),
    ("direct_policy_benefit", _detect_direct_policy_benefit),
    ("customer_breakthrough", _detect_customer_breakthrough),
    ("subsidy_or_compensation", _detect_subsidy_or_compensation),
    ("shareholder_return", _detect_shareholder_return),
    ("rd_milestone", _detect_rd_milestone),
    ("risk_resolution", _detect_risk_resolution),
    ("equity_incentive", _detect_equity_incentive),
    ("financing_completion", _detect_financing_completion),
    ("asset_disposal", _detect_asset_disposal),
)


def event_type_hint(text: str) -> str:
    """Lightweight event-type hint used by the clustering fingerprint."""

    # 标题门控只应作用于真实标题；聚类指纹传入的是“标题\n正文”，
    # 取首行作为合成文档的标题，避免正文中的“回购价格/行权价格”等
    # 排除词污染标题级排除（688381 回购方案回归）。
    synthetic_title = (text.split("\n", 1)[0] if text else "")[:200]
    for event_type, detector in _DETECTORS:
        document = SourceDocument(
            document_id="__fingerprint__",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            source_url="",
            document_url=None,
            title=synthetic_title,
            published_at=datetime(2026, 1, 1, tzinfo=None),
            stock_codes=(),
            body_text=text,
            content_hash="",
            parse_status="parsed",
            parse_error=None,
        )
        if detector([document]) is not None:
            return event_type
    return ""


def detect_all_facts(
    documents: list[SourceDocument],
) -> list[Detection]:
    """v2 多事实管线：运行全部检测器收集一个文档组的所有候选事实。

    同一事件类型只保留证据引用更完整的一次检测；不同类型的事实各自保留，
    供“每股每事件只入榜一个门控最高事实、其余留明细”使用。
    """

    detections: list[Detection] = []
    by_type: dict[str, Detection] = {}
    for _event_type, detector in _DETECTORS:
        detection = detector(documents)
        if detection is None:
            continue
        existing = by_type.get(detection.event_type)
        if existing is None or len(detection.evidence_refs) > len(
            existing.evidence_refs
        ):
            if existing is not None:
                detections.remove(existing)
            by_type[detection.event_type] = detection
            detections.append(detection)
    return detections


def _detection_rank(detection: Detection) -> tuple[int, int, float]:
    """门控最高优先：正向 > 重大性 > 确定性。"""

    return (
        1 if detection.direction == "positive" else 0,
        detection.materiality_level,
        detection.certainty,
    )


# ---------------------------------------------------------------------------
# Rule-based extractor
# ---------------------------------------------------------------------------


class RuleBasedSignalExtractor:
    """Always-available rules-only extractor (default implementation)."""

    version = EXTRACTOR_VERSION

    def __init__(self, storage: object) -> None:
        self.storage = storage

    def extract_all(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> tuple[EventExtraction, ...]:
        extractions: list[EventExtraction] = []
        for stock_code in sorted(cluster.stock_codes):
            extraction = self.extract_for_stock(cluster, documents, stock_code)
            if extraction is not None:
                extractions.append(extraction)
        return tuple(extractions)

    def extract(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> EventExtraction:
        """Protocol entry point; falls back to the first stock."""

        stock_code = cluster.stock_codes[0] if cluster.stock_codes else ""
        extraction = self.extract_for_stock(cluster, documents, stock_code)
        if extraction is None:
            return self._no_signal(cluster, stock_code, "无有效信号")
        return extraction

    def extract_for_stock(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
        stock_code: str,
    ) -> EventExtraction | None:
        relevant = [
            doc
            for doc in documents
            if stock_code in doc.stock_codes
            and (doc.body_text or "").strip()
            and doc.parse_status == "parsed"
        ]
        if not relevant:
            return None
        detections = detect_all_facts(relevant)
        if not detections:
            return self._no_signal(cluster, stock_code, "未识别为十六类事件之一")
        # v2 多事实管线：门控最高（正向 > 重大性 > 确定性）的事实进入榜单。
        best = max(detections, key=_detection_rank)
        return self._build_extraction(cluster, relevant, stock_code, best)

    def alternate_facts(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
        stock_code: str,
        selected_event_type: str,
    ) -> list[Detection]:
        """返回同一文档组中未入选榜单的候选事实（v2 留明细）。

        其余事实的证据引用一并持久化，供 ``event_claims`` 明细与人工/AI
        复核使用；它们不生成第二条信号。
        """

        relevant = [
            doc
            for doc in documents
            if stock_code in doc.stock_codes
            and (doc.body_text or "").strip()
            and doc.parse_status == "parsed"
        ]
        if not relevant:
            return []
        result: list[Detection] = []
        for detection in detect_all_facts(relevant):
            if detection.event_type == selected_event_type:
                continue
            for evidence in detection.evidence_refs:
                if evidence.evidence_id:
                    self.storage.upsert_evidence_ref(evidence)
            result.append(detection)
        return result

    def _build_extraction(
        self,
        cluster: EventCluster,
        relevant: list[SourceDocument],
        stock_code: str,
        detection: Detection,
    ) -> EventExtraction:
        evidence_ids = list(detection.evidence_ids)
        for evidence in detection.evidence_refs:
            if not evidence.evidence_id:
                continue
            self.storage.upsert_evidence_ref(evidence)
            if evidence.evidence_id not in evidence_ids:
                evidence_ids.append(evidence.evidence_id)
        historical = self._historical_amounts(cluster)
        current = _canonical_amounts(
            " ".join(doc.body_text for doc in relevant)
        )
        novelty = _novelty(
            cluster.historical_similar_event_id,
            current,
            historical,
        )
        unexpectedness = _unexpectedness(
            " ".join(f"{doc.title}\n{doc.body_text}" for doc in relevant),
            cluster.historical_similar_event_id,
        )
        no_valid_signal = (
            detection.rejection_reason is not None
            or detection.positive_mechanism is None
        )
        return EventExtraction(
            event_id=cluster.event_id,
            stock_code=stock_code,
            event_type=detection.event_type,
            direction=detection.direction,
            positive_mechanism=detection.positive_mechanism,
            metrics=detection.metrics,
            certainty_stage=detection.certainty_stage,
            certainty=detection.certainty,
            novelty=novelty,
            unexpectedness=unexpectedness,
            materiality_level=detection.materiality_level,
            counter_evidence=detection.counter_evidence,
            evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            no_valid_signal=no_valid_signal,
            extractor_kind="rules",
            extractor_version=self.version,
        )

    def _historical_amounts(self, cluster: EventCluster) -> tuple[str, ...]:
        if not cluster.historical_similar_event_id:
            return ()
        historical = self.storage.get_event_cluster(
            cluster.historical_similar_event_id
        )
        if historical is None:
            return ()
        documents = self.storage.get_source_documents_between(
            historical.first_seen_at - timedelta(days=1),
            historical.last_seen_at + timedelta(days=1),
        )
        ids = set(historical.document_ids)
        return _canonical_amounts(
            " ".join(
                doc.body_text
                for doc in documents
                if doc.document_id in ids and doc.body_text
            )
        )

    @staticmethod
    def _no_signal(
        cluster: EventCluster, stock_code: str, reason: str
    ) -> EventExtraction:
        return EventExtraction(
            event_id=cluster.event_id,
            stock_code=stock_code,
            event_type=UNSUPPORTED_EVENT_TYPE,
            direction="neutral",
            positive_mechanism=None,
            metrics=(),
            certainty_stage="framework",
            certainty=0.0,
            novelty=0.0,
            unexpectedness=0.0,
            materiality_level=0,
            counter_evidence=(),
            evidence_ids=(),
            no_valid_signal=True,
            extractor_kind="rules",
            extractor_version=EXTRACTOR_VERSION,
        )
