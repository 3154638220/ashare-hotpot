"""Compare v1 vs v2 research pipelines on a database copy (v2 里程碑 5 灰度切换).

plan.md 第三部分 里程碑 5：“在生产数据库副本上并行比较 v1/v2；通过门槛后
原子切换，保留一个版本周期的 v1 回退能力。” 本脚本在只读打开的数据库副本上
对每一份研究活动文档并行运行冻结的 v1 兼容管线（整篇正文行级提取）与 v2
管线（名单章节定位 + 种子归一 + 组织分类），输出逐活动参与者差异与汇总，
供原子切换前的并行比较核验使用。脚本不修改主库。

用法::

    python scripts/evaluation/compare_pipeline_versions.py \
        --db %TEMP%\\ashare_v2eval_r3_170225\\hotpot.db \
        --out evaluation/compare_pipeline_v1_v2.json \
        --limit 50

退出码 0 表示比较完成；1 表示参数/数据库问题。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ashare_hotpot.config import SHANGHAI_TZ  # noqa: E402
from ashare_hotpot.institutions import InstitutionRegistry  # noqa: E402
from ashare_hotpot.research_activities import (  # noqa: E402
    parse_research_activity,
)
from ashare_hotpot.storage import SCHEMA_VERSION, Storage  # noqa: E402


def _connect_readonly(db_path: Path):
    import sqlite3

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.text_factory = lambda b: b.decode("utf-8", "replace")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != SCHEMA_VERSION:
        raise SystemExit(
            f"expected database schema version {SCHEMA_VERSION}, "
            f"found {version} in {db_path}"
        )
    return connection


def _activity_names(storage: Storage, result) -> set[str]:
    if result is None:
        return set()
    return {
        storage.get_institution(p.institution_id).canonical_name
        for p in result.participants
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("evaluation"))
    parser.add_argument(
        "--limit", type=int, default=0, help="0 = all research activities"
    )
    parser.add_argument(
        "--window-days", type=int, default=550,
        help="document window (days) before now",
    )
    args = parser.parse_args(argv)

    if not args.db.is_file():
        raise SystemExit(f"database not found: {args.db}")
    connection = _connect_readonly(args.db)
    cur = connection.cursor()
    window_start = datetime.now(SHANGHAI_TZ) - timedelta(
        days=args.window_days
    )
    rows = cur.execute(
        "SELECT document_id FROM source_documents "
        "WHERE kind='research_activity' "
        "AND parse_status='parsed' "
        "AND length(body_text) > 0 "
        "ORDER BY document_id ASC"
    ).fetchall()
    document_ids = [str(row[0]) for row in rows]
    if args.limit > 0:
        document_ids = document_ids[: args.limit]
    cur.close()
    connection.close()

    tmp_root = Path(tempfile.mkdtemp(prefix="ashare_pipeline_cmp_"))
    storage_v1 = Storage(tmp_root / "v1.db")
    storage_v2 = Storage(tmp_root / "v2.db")
    registry_v1 = InstitutionRegistry(storage_v1)
    registry_v2 = InstitutionRegistry(storage_v2)

    total = 0
    common_total = 0
    v1_only_total = 0
    v2_only_total = 0
    per_activity: list[dict[str, object]] = []
    connection = _connect_readonly(args.db)
    cur = connection.cursor()
    for document_id in document_ids:
        row = cur.execute(
            "SELECT document_id, provider_key, provider_name, kind, "
            "source_url, document_url, title, published_ts, body_text, "
            "parse_status FROM source_documents WHERE document_id=?",
            (document_id,),
        ).fetchone()
        if row is None:
            continue
        stocks = [
            str(r[0])
            for r in cur.execute(
                "SELECT stock_code FROM source_document_stocks "
                "WHERE document_id=?",
                (document_id,),
            ).fetchall()
        ]
        from ashare_hotpot.models import SourceDocument

        document = SourceDocument(
            document_id=str(row[0]),
            provider_key=str(row[1]),
            provider_name=str(row[2]),
            kind=str(row[3]),
            source_url=str(row[4] or ""),
            document_url=str(row[5] or ""),
            title=str(row[6] or ""),
            published_at=datetime.fromtimestamp(
                int(row[7]), tz=SHANGHAI_TZ
            ),
            stock_codes=tuple(stocks),
            body_text=str(row[8] or ""),
            content_hash="",
            parse_status=str(row[9] or "parsed"),
            parse_error=None,
        )
        try:
            result_v1 = parse_research_activity(
                document, registry_v1, pipeline_version="v1"
            )
            result_v2 = parse_research_activity(
                document, registry_v2, pipeline_version="v2"
            )
        except Exception as exc:  # noqa: BLE001 - one document only
            per_activity.append(
                {
                    "document_id": document_id,
                    "error": str(exc)[:200],
                }
            )
            continue
        names_v1 = _activity_names(storage_v1, result_v1)
        names_v2 = _activity_names(storage_v2, result_v2)
        common = names_v1 & names_v2
        v1_only = names_v1 - names_v2
        v2_only = names_v2 - names_v1
        total += 1
        common_total += len(common)
        v1_only_total += len(v1_only)
        v2_only_total += len(v2_only)
        per_activity.append(
            {
                "document_id": document_id,
                "title": str(row[6] or "")[:60],
                "stock_codes": stocks,
                "v1_count": len(names_v1),
                "v2_count": len(names_v2),
                "common": sorted(common)[:20],
                "v1_only": sorted(v1_only)[:20],
                "v2_only": sorted(v2_only)[:20],
            }
        )
    cur.close()
    connection.close()
    shutil.rmtree(tmp_root, ignore_errors=True)

    report = {
        "schema_version": SCHEMA_VERSION,
        "database": str(args.db),
        "activities_compared": total,
        "participant_totals": {
            "common": common_total,
            "v1_only": v1_only_total,
            "v2_only": v2_only_total,
        },
        "note": (
            "v1 = 发布前整篇正文行级提取（冻结快照）；"
            "v2 = 名单章节定位 + 种子归一 + 组织分类。"
            "差异样本见 per_activity（每项截取前 20 条）。"
        ),
        "per_activity": per_activity,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "activities_compared=%d participants: common=%d v1_only=%d v2_only=%d"
        % (total, common_total, v1_only_total, v2_only_total)
    )
    print("report -> %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
