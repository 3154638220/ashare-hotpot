"""Pure computation for the independent industry-heat board.

This module intentionally has no network, storage, UI, or ranking side
effects.  Callers provide the already fetched Eastmoney Top100 rows, parsed
industry-research articles, and the cached stock-to-EM2016 mapping.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable, Mapping

from .models import IndustryHeatRow, IndustryHeatSnapshot, ParsedArticle, PopularityRankRow


# EM2016's primary industry labels are the canonical values.  Aliases are
# deliberately explicit: no substring/fuzzy matching or model inference is
# allowed to decide an industry.
EM2016_INDUSTRIES: frozenset[str] = frozenset(
    {
        "农林牧渔",
        "基础化工",
        "钢铁",
        "有色金属",
        "电子",
        "汽车",
        "家用电器",
        "食品饮料",
        "纺织服饰",
        "轻工制造",
        "医药生物",
        "公用事业",
        "交通运输",
        "房地产",
        "商贸零售",
        "社会服务",
        "金融",
        "银行",
        "非银金融",
        "建筑材料",
        "建筑装饰",
        "电力设备",
        "国防军工",
        "计算机",
        "传媒",
        "通信",
        "煤炭",
        "石油石化",
        "环保",
        "美容护理",
        "机械设备",
        "综合",
    }
)


def _compact(value: str) -> str:
    return re.sub(r"[\s·・_\-—–（）()]+", "", value.strip())


# Keep this table small and reviewable.  Canonical EM2016 labels are included
# so parsing a value from the public stock endpoint never depends on a branch.
INDUSTRY_ALIASES: dict[str, str] = {
    **{_compact(name): name for name in EM2016_INDUSTRIES},
    "基础化工": "基础化工",
    "化工": "基础化工",
    "农林牧副渔": "农林牧渔",
    "农牧业": "农林牧渔",
    "有色": "有色金属",
    "有色金属冶炼": "有色金属",
    "半导体": "电子",
    "集成电路": "电子",
    "家电": "家用电器",
    "纺织": "纺织服饰",
    "服装家纺": "纺织服饰",
    "轻工": "轻工制造",
    "医药": "医药生物",
    "电力": "公用事业",
    "公用": "公用事业",
    "运输": "交通运输",
    "地产": "房地产",
    "零售": "商贸零售",
    "社会服务业": "社会服务",
    "银行": "金融",
    "金融业": "金融",
    "证券": "非银金融",
    "券商": "非银金融",
    "建材": "建筑材料",
    "建筑": "建筑装饰",
    "电气设备": "电力设备",
    "军工": "国防军工",
    "计算机应用": "计算机",
    "传媒娱乐": "传媒",
    "石油天然气": "石油石化",
    "环保工程": "环保",
    "专用设备": "机械设备",
}


def map_industry_alias(value: str | None) -> str | None:
    """Map one explicit label to an EM2016 primary industry.

    Matching is normalized only for whitespace and fixed punctuation.  A
    value not present in :data:`INDUSTRY_ALIASES` is intentionally rejected.
    """

    if not value:
        return None
    return INDUSTRY_ALIASES.get(_compact(value))


def _article_key(article: ParsedArticle) -> str:
    url = article.url.strip()
    if url:
        return f"url:{url}"
    if article.seq.strip():
        return f"seq:{article.seq.strip()}"
    return f"fallback:{article.title.strip()}|{article.published_at.isoformat()}"


def _in_window(article: ParsedArticle, start: datetime, end: datetime) -> bool:
    # All production datetimes are Asia/Shanghai-aware.  Keeping the check
    # simple and inclusive makes the exact 24-hour boundary testable.
    return start <= article.published_at <= end


def _article_industries(
    article: ParsedArticle,
    stock_industries: Mapping[str, str],
) -> tuple[str, ...]:
    explicit_tags = tuple(
        dict.fromkeys(
            mapped
            for tag in article.industry_tags
            if (mapped := map_industry_alias(tag)) is not None
        )
    )
    if article.industry_tags:
        # An explicit but unknown label is not silently replaced by a stock
        # guess; the source must be corrected or a later rule can be added.
        return explicit_tags
    return tuple(
        sorted(
            {
                mapped
                for stock in article.stocks
                if (mapped := map_industry_alias(stock_industries.get(stock.code)))
                is not None
            }
        )
    )


def _average_rank_percentiles(values: Mapping[str, float]) -> dict[str, float]:
    """Return ascending average-rank percentiles in the inclusive 0..100 range."""

    if not values:
        return {}
    ordered = sorted(values.values())
    count = len(ordered)
    result: dict[str, float] = {}
    for key, value in values.items():
        first = next(index for index, item in enumerate(ordered, start=1) if item == value)
        last = count - next(
            index for index, item in enumerate(reversed(ordered), start=1) if item == value
        ) + 1
        average_rank = (first + last) / 2
        percentile = 100.0 if count == 1 else (average_rank - 1) / (count - 1) * 100.0
        result[key] = round(percentile, 2)
    return result


def build_industry_heat_snapshot(
    popularity: Iterable[PopularityRankRow],
    research_articles: Iterable[ParsedArticle],
    stock_industries: Mapping[str, str],
    *,
    window_end: datetime,
    source_complete: bool = True,
    source_error: str | None = None,
    popularity_available: bool = True,
    popularity_stale: bool = False,
) -> IndustryHeatSnapshot:
    """Build an industry snapshot from already parsed inputs.

    ``research_articles`` are deduplicated by URL (or stable source sequence)
    and counted only inside the fixed 24-hour window ending at ``window_end``.
    The popularity iterable is treated as the comprehensive Top100 input; its
    distinct stock codes are the A denominator.
    """

    popularity_rows = list(popularity)
    top100_codes = {row.code for row in popularity_rows if row.code.strip()}
    top100_by_industry: dict[str, set[str]] = defaultdict(set)
    for row in popularity_rows:
        industry = map_industry_alias(stock_industries.get(row.code))
        if industry:
            top100_by_industry[industry].add(row.code)

    unique_articles: dict[str, ParsedArticle] = {}
    start = window_end - timedelta(hours=24)
    for article in research_articles:
        if _in_window(article, start, window_end):
            unique_articles.setdefault(_article_key(article), article)

    article_industries: dict[str, tuple[str, ...]] = {
        key: _article_industries(article, stock_industries)
        for key, article in unique_articles.items()
    }
    articles_by_industry: dict[str, set[str]] = defaultdict(set)
    article_urls_by_industry: dict[str, set[str]] = defaultdict(set)
    for key, industries in article_industries.items():
        article = unique_articles[key]
        for industry in industries:
            articles_by_industry[industry].add(key)
            article_urls_by_industry[industry].add(article.url or article.seq)

    industries = sorted(top100_by_industry)
    a_values = {industry: float(len(top100_by_industry[industry])) for industry in industries}
    b_values = {
        industry: math.log1p(len(articles_by_industry.get(industry, set())))
        for industry in industries
    }
    a_percentiles = _average_rank_percentiles(a_values)
    b_percentiles = _average_rank_percentiles(b_values)

    rows_without_rank = [
        IndustryHeatRow(
            rank=0,
            industry=industry,
            heat=round(0.5 * a_percentiles[industry] + 0.5 * b_percentiles[industry], 2),
            a=int(a_values[industry]),
            a_percentile=a_percentiles[industry],
            b=len(articles_by_industry.get(industry, set())),
            b_percentile=b_percentiles[industry],
            mapping_status="mapped",
            source_status="complete" if source_complete else "partial",
            article_urls=tuple(sorted(article_urls_by_industry.get(industry, set()))),
        )
        for industry in industries
    ]
    ordered_rows = sorted(
        rows_without_rank,
        key=lambda row: (-row.heat, -row.a, -row.b, row.industry),
    )
    rows = [
        IndustryHeatRow(
            rank=index,
            industry=row.industry,
            heat=row.heat,
            a=row.a,
            a_percentile=row.a_percentile,
            b=row.b,
            b_percentile=row.b_percentile,
            mapping_status=row.mapping_status,
            source_status=row.source_status,
            article_urls=row.article_urls,
        )
        for index, row in enumerate(ordered_rows, start=1)
    ]
    mapped_article_count = sum(bool(items) for items in article_industries.values())
    top100_mapped = sum(len(codes) for codes in top100_by_industry.values())
    total = len(top100_codes)
    if not popularity_available:
        source_status = "unavailable"
    elif popularity_stale:
        source_status = "stale"
    elif not source_complete:
        source_status = "failed" if source_error else "partial"
    else:
        source_status = "complete"
    row_source_status = source_status
    rows = [
        IndustryHeatRow(
            rank=row.rank,
            industry=row.industry,
            heat=row.heat,
            a=row.a,
            a_percentile=row.a_percentile,
            b=row.b,
            b_percentile=row.b_percentile,
            mapping_status=row.mapping_status,
            source_status=row_source_status,
            article_urls=row.article_urls,
        )
        for row in rows
    ]
    return IndustryHeatSnapshot(
        snapshot_at=window_end,
        window_start=start,
        window_end=window_end,
        rows=rows,
        top100_total=total,
        top100_mapped=top100_mapped,
        mapping_coverage=round(top100_mapped / total, 4) if total else 0.0,
        research_article_total=len(unique_articles),
        research_article_mapped=mapped_article_count,
        unmapped_article_count=len(unique_articles) - mapped_article_count,
        mapping_status="complete" if top100_mapped == total else "partial",
        source_status=source_status,
        source_error=source_error,
    )


__all__ = [
    "EM2016_INDUSTRIES",
    "INDUSTRY_ALIASES",
    "build_industry_heat_snapshot",
    "map_industry_alias",
]
