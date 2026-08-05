from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import PopularityRankRow, RankingRow


SOURCE_LABELS = {
    "ths": "新闻热度",
    "pop": "综合人气",
    "surge": "飙升榜",
}

CSV_HEADERS = {
    "ths": ("排名", "股票名称", "代码", "所属行业", "有效提及", "原始篇数", "最近提及"),
    "pop": ("排名", "股票名称", "代码", "现价", "涨跌幅"),
    "surge": ("排名", "股票名称", "代码", "较昨日变动", "现价", "涨跌幅"),
}


def default_export_name(source_key: str, created_at: datetime | None) -> str:
    timestamp = (created_at or datetime.now()).strftime("%Y%m%d_%H%M")
    return f"A股热度_{SOURCE_LABELS.get(source_key, source_key)}_{timestamp}.csv"


def row_values(source_key: str, row: RankingRow | PopularityRankRow) -> tuple[object, ...]:
    if isinstance(row, RankingRow):
        return (
            row.rank,
            row.name,
            row.code,
            "、".join(row.industry_tags) if row.industry_tags else "未标注",
            row.event_count,
            row.raw_article_count,
            row.latest_mention.strftime("%Y-%m-%d %H:%M:%S"),
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
    rows: Iterable[RankingRow | PopularityRankRow],
) -> int:
    """Export rows in the caller-provided visible order for Excel-friendly use."""

    materialized = list(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADERS[source_key])
        writer.writerows(row_values(source_key, row) for row in materialized)
    return len(materialized)


def tab_separated_row(source_key: str, row: RankingRow | PopularityRankRow) -> str:
    return "\t".join(str(value) for value in row_values(source_key, row))
