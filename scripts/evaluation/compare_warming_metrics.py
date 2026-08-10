"""Read-only legacy/v2 institution-metric comparison for warming-v2 release.

Reports only research-behaviour data quality and ranking changes.  It does not
join prices, returns, holdings or fund-flow data and performs no backtest.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path


def _connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _latest_metrics(
    connection: sqlite3.Connection, window_kind: str
) -> dict[str, dict[str, object]]:
    row = connection.execute(
        "SELECT MAX(snapshot_ts) AS snapshot_ts "
        "FROM institution_metric_snapshots WHERE window_kind=?",
        (window_kind,),
    ).fetchone()
    if row is None or row["snapshot_ts"] is None:
        return {}
    rows = connection.execute(
        "SELECT stock_code, metrics_json FROM institution_metric_snapshots "
        "WHERE window_kind=? AND snapshot_ts=? ORDER BY stock_code",
        (window_kind, int(row["snapshot_ts"])),
    ).fetchall()
    return {
        str(item["stock_code"]): dict(json.loads(item["metrics_json"] or "{}"))
        for item in rows
    }


def _legacy_top20(rows: dict[str, dict[str, object]]) -> list[str]:
    return sorted(
        rows,
        key=lambda code: (
            -float(rows[code].get("z20") or -1e18),
            -int(rows[code].get("new_groups") or 0),
            code,
        ),
    )[:20]


def _warming_top20(rows: dict[str, dict[str, object]]) -> list[str]:
    level = {"full": 0, "provisional": 1, "raw_only": 2}

    def recent_rank(value: object) -> int:
        try:
            return -date.fromisoformat(str(value)).toordinal()
        except ValueError:
            return 0

    return sorted(
        rows,
        key=lambda code: (
            level.get(str(rows[code].get("coverage_level") or "raw_only"), 3),
            -float(rows[code].get("warming_score") or -1e18),
            -int(rows[code].get("unseen_100d_groups") or 0),
            -int(rows[code].get("active_days") or 0),
            -int(rows[code].get("current_unique_groups") or 0),
            recent_rank(rows[code].get("recent_activity")),
            code,
        ),
    )[:20]


def build_report(db_path: Path) -> dict[str, object]:
    connection = _connect_readonly(db_path)
    try:
        schema_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        legacy = _latest_metrics(connection, "z20")
        warming = _latest_metrics(connection, "warming_20")
        legacy_top20 = _legacy_top20(legacy)
        warming_top20 = _warming_top20(warming)
        participant_counts = connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN research_eligible=0 THEN 1 ELSE 0 END) AS excluded "
            "FROM research_participant_occurrences"
        ).fetchone()
        total_participants = int(participant_counts["total"] or 0)
        excluded_participants = int(participant_counts["excluded"] or 0)

        legacy_dates: dict[str, set[str]] = {}
        for row in connection.execute(
            "SELECT activity_id, activity_date FROM research_activity_dates"
        ):
            legacy_dates.setdefault(str(row["activity_id"]), set()).add(
                str(row["activity_date"])
            )
        occurrence_dates: dict[str, set[str]] = {}
        for row in connection.execute(
            "SELECT activity_id, occurred_on FROM activity_occurrences "
            "WHERE metric_eligible=1 AND occurred_on IS NOT NULL"
        ):
            occurrence_dates.setdefault(str(row["activity_id"]), set()).add(
                str(row["occurred_on"])
            )
        comparable_activities = sorted(set(legacy_dates) & set(occurrence_dates))
        date_corrections = sum(
            legacy_dates[key] != occurrence_dates[key]
            for key in comparable_activities
        )
        single_day_count = sum(bool(item.get("single_day")) for item in warming.values())
        return {
            "schema_version": schema_version,
            "database": str(db_path),
            "generated_at": datetime.now().astimezone().isoformat(),
            "top20": {
                "legacy": legacy_top20,
                "warming_v2": warming_top20,
                "overlap_count": len(set(legacy_top20) & set(warming_top20)),
                "added": sorted(set(warming_top20) - set(legacy_top20)),
                "removed": sorted(set(legacy_top20) - set(warming_top20)),
            },
            "non_research_organization_pollution": {
                "excluded_occurrences": excluded_participants,
                "all_participant_occurrences": total_participants,
                "rate": (
                    excluded_participants / total_participants
                    if total_participants
                    else None
                ),
            },
            "date_corrections": {
                "corrected_activities": date_corrections,
                "comparable_activities": len(comparable_activities),
            },
            "single_day_concentration": {
                "single_day_rows": single_day_count,
                "warming_rows": len(warming),
                "share": single_day_count / len(warming) if warming else None,
            },
            "note": (
                "只比较公开披露的机构研究行为与数据质量；"
                "未使用股价、收益、持仓或资金流数据，未做回测。"
            ),
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.db.is_file():
        raise SystemExit(f"database not found: {args.db}")
    report = build_report(args.db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
