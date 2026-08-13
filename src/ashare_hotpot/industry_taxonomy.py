"""Conservative, auditable industry taxonomy for the industry-heat board.

The Eastmoney ``EM2016`` endpoint currently returns a stable set of broad
primary labels (for example ``电子设备`` and ``信息技术``).  Tonghuashun
articles, however, usually expose narrower concepts such as ``消费电子`` or
``AI ASIC``.  This module is deliberately pure and keeps the bridge between
those two vocabularies fixed, reviewable and testable.

Generic cross-industry words such as ``AI`` and ``新能源`` are intentionally
absent.  A text rule must name a sufficiently specific mechanism or segment;
one article may map to more than one primary industry when the text itself
does so explicitly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


EM2016_INDUSTRIES: frozenset[str] = frozenset(
    {
        "农林牧渔",
        "基础化工",
        "钢铁",
        "有色金属",
        "电子设备",
        "交运设备",
        "信息技术",
        "医药生物",
        "公用事业",
        "交通运输",
        "房地产",
        "商贸零售",
        "食品饮料",
        "轻工制造",
        "金融",
        "建筑",
        "休闲、生活及专业服务",
        "互联网",
        "文化传媒",
        "化石能源",
        "国防与装备",
        "建材",
        "纺织服装",
        "家电",
        "机械设备",
        "电气设备",
        "综合",
    }
)

# Persisted on article-cache rows.  Bump only when the DOM/text attribution
# contract changes and legacy cached detail pages must be fetched once again.
INDUSTRY_ATTRIBUTION_VERSION = 1


def _compact(value: str) -> str:
    return re.sub(r"[\s·・_\-—–（）()]+", "", value.strip())


_ALIASES: dict[str, str] = {
    **{_compact(name): name for name in EM2016_INDUSTRIES},
    "化工": "基础化工",
    "农林牧副渔": "农林牧渔",
    "农牧业": "农林牧渔",
    "有色": "有色金属",
    "半导体": "电子设备",
    "集成电路": "电子设备",
    "电子": "电子设备",
    "消费电子": "电子设备",
    "光芯片": "电子设备",
    "ASIC": "电子设备",
    "AIASIC": "电子设备",
    "GPU": "电子设备",
    "MLCC": "电子设备",
    "PCB": "电子设备",
    "存储芯片": "电子设备",
    "存储产业": "电子设备",
    "汽车": "交运设备",
    "汽车零部件": "交运设备",
    "计算机": "信息技术",
    "软件": "信息技术",
    "软件行业": "信息技术",
    "FDE": "信息技术",
    "医药": "医药生物",
    "电力": "公用事业",
    "运输": "交通运输",
    "地产": "房地产",
    "房企": "房地产",
    "内房股": "房地产",
    "楼市": "房地产",
    "零售": "商贸零售",
    "证券": "金融",
    "券商": "金融",
    "银行": "金融",
    "非银金融": "金融",
    "建筑装饰": "建筑",
    "建筑材料": "建材",
    "军工": "国防与装备",
    "国防军工": "国防与装备",
    "传媒": "文化传媒",
    "短剧": "文化传媒",
    "AI短剧": "文化传媒",
    "石油石化": "化石能源",
    "煤炭": "化石能源",
    "纺织服饰": "纺织服装",
    "家用电器": "家电",
    "电力设备": "电气设备",
    "专用设备": "机械设备",
    "机器人": "机械设备",
    "人形机器人": "机械设备",
    "陪伴机器人": "机械设备",
    "具身智能": "机械设备",
    "储能": "电气设备",
    "美容护理": "休闲、生活及专业服务",
    "社会服务": "休闲、生活及专业服务",
}


def map_industry_alias(value: str | None) -> str | None:
    """Map one explicit source label or concept to an EM2016 primary label."""

    if not value:
        return None
    return _ALIASES.get(_compact(value))


# Pattern order is significant only for the evidence labels returned to the
# UI.  Canonical industries are deduplicated later by the heat builder.
_TEXT_CONCEPT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"内房股|房企|楼市|房地产|地产(?:板块|行业|企业|政策|新政)"), "房地产"),
    (re.compile(r"AI\s*短剧|微短剧|短剧(?:行业|产业|平台)"), "AI短剧"),
    (re.compile(r"光芯片"), "光芯片"),
    (re.compile(r"AI\s*ASIC|\bASIC\b", re.IGNORECASE), "AI ASIC"),
    (re.compile(r"\bGPU\b", re.IGNORECASE), "GPU"),
    (re.compile(r"\bMLCC\b", re.IGNORECASE), "MLCC"),
    (re.compile(r"\bPCB\b|印制电路板", re.IGNORECASE), "PCB"),
    (re.compile(r"存储(?:产业|芯片|器|行业)"), "存储产业"),
    (re.compile(r"半导体|集成电路|算力芯片"), "半导体"),
    (re.compile(r"软件行业|企业软件|AI应用层|\bFDE\b", re.IGNORECASE), "软件行业"),
    (re.compile(r"陪伴机器人"), "陪伴机器人"),
    (re.compile(r"人形机器人"), "人形机器人"),
    (re.compile(r"具身智能"), "具身智能"),
    (re.compile(r"机器人(?:行业|产业|赛道)"), "机器人"),
    (re.compile(r"液冷|真空设备"), "机械设备"),
    (re.compile(r"特种气体|电子特气"), "基础化工"),
    (re.compile(r"AIDC配储|储能(?:行业|产业|板块|系统|需求)"), "储能"),
    (re.compile(r"白酒(?:行业|板块|股)?"), "食品饮料"),
)


def infer_industry_concepts(*texts: str) -> tuple[str, ...]:
    """Return fixed high-confidence concept evidence found in article text.

    The return values are evidence labels, not generated prose.  Callers map
    them through :func:`map_industry_alias`, making every inferred industry
    traceable to one frozen rule.
    """

    combined = " ".join(text for text in texts if text)
    found: list[str] = []
    for pattern, concept in _TEXT_CONCEPT_RULES:
        if pattern.search(combined) and concept not in found:
            found.append(concept)
    return tuple(found)


def merge_industry_concepts(*groups: Iterable[str]) -> tuple[str, ...]:
    """Stable, order-preserving merge used by DOM and text extraction."""

    return tuple(dict.fromkeys(item.strip() for group in groups for item in group if item.strip()))


__all__ = [
    "EM2016_INDUSTRIES",
    "INDUSTRY_ATTRIBUTION_VERSION",
    "infer_industry_concepts",
    "map_industry_alias",
    "merge_industry_concepts",
]
