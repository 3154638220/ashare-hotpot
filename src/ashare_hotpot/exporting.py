from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import (
    DiscoveryViewRow,
    IndustryHeatRow,
    InstitutionZ20ViewRow,
    InteractionRankingRow,
    PersistenceViewRow,
    PopularityRankRow,
    RankingRow,
    ShortTermViewRow,
)
from .research_views import TOPIC_LABELS


SOURCE_LABELS = {
    "news": "基本面消息",
    "interaction": "基本面互动",
    "pop": "综合人气",
    "surge": "飙升榜",
    "confirm": "确定性利好",
    "catalyst": "潜在催化",
    "z20": "20日机构升温",
    "persist60": "60日持续关注",
    "persist120": "120日持续关注",
    "discovery": "待核验",
    "industry": "行业热度",
}

CSV_HEADERS = {
    "news": ("排名", "股票名称", "代码", "所属行业", "有效事件", "原始篇数", "最近事件", "来源", "数据源", "内容类型"),
    "interaction": ("排名", "股票名称", "代码", "所属行业", "有效提问", "已回复", "回复率", "最近回复", "平台"),
    "pop": ("排名", "股票名称", "代码", "现价", "涨跌幅"),
    "surge": ("排名", "股票名称", "代码", "较昨日变动", "现价", "涨跌幅"),
    "confirm": ("排名", "股票名称", "代码", "事件类型", "正向机制", "重大性", "关键相对量", "确定性", "主要反证/落地风险", "事件时间", "质量状态"),
    "catalyst": ("排名", "股票名称", "代码", "事件类型", "正向机制", "重大性", "关键相对量", "确定性", "主要反证/落地风险", "事件时间", "质量状态"),
    "z20": ("排名", "股票名称", "代码", "行业", "z20", "机构集团数", "新增机构集团", "分析师数", "高深度占比", "最近活动", "覆盖状态"),
    "z20_v2": ("排名", "股票名称", "代码", "行业", "标准化升温值（描述性）", "绝对增量", "当前集团数", "100 日未见集团", "活跃日期数", "单日集中度", "单日集中", "最近活动", "指标版本", "来源 cohort", "日期质量", "排除组织数", "暂定原因", "覆盖状态"),
    "persist60": ("排名", "股票名称", "代码", "窗口", "持续关注分", "活跃周数/比例", "机构集团数", "重复跟进比例", "研究深度", "单日集中度", "主要关注主题", "覆盖状态"),
    "persist120": ("排名", "股票名称", "代码", "窗口", "持续关注分", "活跃周数/比例", "机构集团数", "重复跟进比例", "研究深度", "单日集中度", "主要关注主题", "覆盖状态"),
    "persistence_v2": ("排名", "股票名称", "代码", "窗口", "持续关注规则指数", "活跃周数/比例", "机构集团数", "重复跟进比例", "研究深度", "单日集中度", "主要关注主题", "指标版本", "来源 cohort", "日期质量", "排除组织数", "暂定原因", "覆盖状态"),
    "discovery": ("排名", "股票名称", "代码", "发现类型", "原始标题", "触发原因", "正文状态", "发布时间", "来源", "质量状态"),
    "industry": ("排名", "行业", "热度", "A", "A分位", "B", "B分位", "映射/来源状态"),
}

QUALITY_LABELS = {
    "ok": "正常",
    "partial": "部分覆盖",
    "cold_start": "冷启动",
    "provisional": "暂定",
    "error": "来源失败",
}

WINDOW_LABELS = {
    "persistence_60": "60 日",
    "persistence_120": "120 日",
}


def default_export_name(source_key: str, created_at: datetime | None) -> str:
    timestamp = (created_at or datetime.now()).strftime("%Y%m%d_%H%M")
    return f"A股热度_{SOURCE_LABELS.get(source_key, source_key)}_{timestamp}.csv"


def row_values(
    source_key: str,
    row: RankingRow | PopularityRankRow | InteractionRankingRow | IndustryHeatRow | ShortTermViewRow | InstitutionZ20ViewRow | PersistenceViewRow | DiscoveryViewRow,
) -> tuple[object, ...]:
    if isinstance(row, IndustryHeatRow):
        return (
            row.rank, row.industry, f"{row.heat:.2f}", row.a,
            f"{row.a_percentile:.2f}", row.b, f"{row.b_percentile:.2f}",
            f"{row.mapping_status}/{row.source_status}",
        )
    if isinstance(row, DiscoveryViewRow):
        return (
            row.rank,
            row.stock_name,
            row.stock_code,
            row.discovery_type_label,
            row.title,
            row.trigger_reason,
            row.parse_status_label,
            row.published_at.strftime("%Y-%m-%d %H:%M:%S")
            if row.published_at
            else "",
            row.source_name,
            QUALITY_LABELS.get(row.quality_state, row.quality_state),
        )
    if isinstance(row, ShortTermViewRow):
        return (
            row.rank,
            row.stock_name,
            row.stock_code,
            row.event_type,
            row.positive_mechanism or "",
            f"L{row.materiality_level}",
            row.key_metric or "",
            f"{row.certainty * 100:.0f}%",
            row.counter_evidence or ("尚未落地" if row.board == "potential_catalyst" else ""),
            row.event_time.strftime("%Y-%m-%d %H:%M:%S") if row.event_time else "",
            QUALITY_LABELS.get(row.quality_state, row.quality_state),
        )
    if isinstance(row, InstitutionZ20ViewRow):
        if row.metric_version == "warming_v2":
            return (
                row.rank, row.stock_name, row.stock_code,
                row.industry or "未标注",
                "" if row.z20 is None else f"{row.z20:.2f}",
                "" if row.absolute_change is None else f"{row.absolute_change:+.2f}",
                row.current_unique_groups,
                "" if row.unseen_100d_groups is None else row.unseen_100d_groups,
                row.active_days,
                f"{row.single_day_concentration * 100:.1f}%",
                "是" if row.single_day else "否",
                row.recent_activity.isoformat() if row.recent_activity else "",
                row.metric_version, row.source_cohort_id, row.date_quality,
                row.excluded_organization_count, row.provisional_reason or "",
                QUALITY_LABELS.get(row.coverage_state, row.coverage_state),
            )
        return (
            row.rank,
            row.stock_name,
            row.stock_code,
            row.industry or "未标注",
            "" if row.z20 is None else f"{row.z20:.2f}",
            row.current_unique_groups,
            row.new_groups,
            row.analyst_count,
            f"{row.high_depth_ratio * 100:.1f}%" if row.high_depth_ratio else "0.0%",
            row.recent_activity.isoformat() if row.recent_activity else "",
            QUALITY_LABELS.get(row.coverage_state, row.coverage_state),
        )
    if isinstance(row, PersistenceViewRow):
        topics = " / ".join(
            f"{TOPIC_LABELS.get(topic, topic)} {count}"
            for topic, count in sorted(row.topics.items(), key=lambda item: (-item[1], item[0]))
        )
        if row.metric_version == "persistence_rules_v2":
            return (
                row.rank, row.stock_name, row.stock_code,
                WINDOW_LABELS.get(row.window_kind.replace("_v2", ""), row.window_kind),
                "" if row.persistence_score is None else f"{row.persistence_score:.1f}",
                f"{row.active_weeks}/{row.active_week_ratio * 100:.1f}%",
                row.unique_groups,
                f"{row.repeat_followup_ratio * 100:.1f}%",
                "" if row.depth_score is None else f"{row.depth_score * 100:.1f}%",
                f"{row.single_day_concentration * 100:.1f}%", topics,
                row.metric_version, row.source_cohort_id, row.date_quality,
                row.excluded_organization_count, row.provisional_reason or "",
                QUALITY_LABELS.get(row.coverage_state, row.coverage_state),
            )
        return (
            row.rank,
            row.stock_name,
            row.stock_code,
            WINDOW_LABELS.get(row.window_kind, row.window_kind),
            "" if row.persistence_score is None else f"{row.persistence_score:.1f}",
            f"{row.active_weeks}/{row.active_week_ratio * 100:.1f}%",
            row.unique_groups,
            f"{row.repeat_followup_ratio * 100:.1f}%",
            "" if row.depth_score is None else f"{row.depth_score * 100:.1f}%",
            f"{row.single_day_concentration * 100:.1f}%",
            topics,
            QUALITY_LABELS.get(row.coverage_state, row.coverage_state),
        )
    if isinstance(row, RankingRow):
        return (
            row.rank,
            row.name,
            row.code,
            "、".join(row.industry_tags) if row.industry_tags else "未标注",
            row.event_count,
            row.raw_article_count,
            row.latest_mention.strftime("%Y-%m-%d %H:%M:%S"),
            "/".join(row.channels) if row.channels else "",
            "/".join(row.sources) if row.sources else "",
            "/".join(row.content_types) if row.content_types else "",
        )
    if isinstance(row, InteractionRankingRow):
        return (
            row.rank,
            row.name,
            row.code,
            "、".join(row.industry_tags) if row.industry_tags else "未标注",
            row.question_count,
            row.replied_count,
            f"{row.reply_rate * 100:.1f}%" if row.question_count else "—",
            row.latest_reply.strftime("%Y-%m-%d %H:%M:%S"),
            "/".join(row.platforms) if row.platforms else "",
        )
    if source_key == "surge":
        return (
            row.rank,
            row.name,
            row.code,
            "" if row.change is None else row.change,
            "" if row.current_price is None else f"{row.current_price:.2f}",
            "" if row.change_percent is None else f"{row.change_percent:+.2f}%",
        )
    return (
        row.rank,
        row.name,
        row.code,
        "" if row.current_price is None else f"{row.current_price:.2f}",
        "" if row.change_percent is None else f"{row.change_percent:+.2f}%",
    )


def export_csv(
    path: Path,
    source_key: str,
    rows: Iterable[RankingRow | PopularityRankRow | InteractionRankingRow | IndustryHeatRow | ShortTermViewRow | InstitutionZ20ViewRow | PersistenceViewRow | DiscoveryViewRow],
) -> int:
    """Export rows in the caller-provided visible order for Excel-friendly use."""

    materialized = list(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        header_key = source_key
        if source_key == "z20" and any(
            isinstance(row, InstitutionZ20ViewRow)
            and row.metric_version == "warming_v2"
            for row in materialized
        ):
            header_key = "z20_v2"
        elif source_key in {"persist60", "persist120"} and any(
            isinstance(row, PersistenceViewRow)
            and row.metric_version == "persistence_rules_v2"
            for row in materialized
        ):
            header_key = "persistence_v2"
        writer.writerow(CSV_HEADERS[header_key])
        writer.writerows(row_values(source_key, row) for row in materialized)
    return len(materialized)


def tab_separated_row(
    source_key: str,
    row: RankingRow | PopularityRankRow | InteractionRankingRow | ShortTermViewRow | InstitutionZ20ViewRow | PersistenceViewRow | DiscoveryViewRow,
) -> str:
    return "\t".join(str(value) for value in row_values(source_key, row))
