"""Run the 550-day warming-v2 staging recompute on a database copy only."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ  # noqa: E402
from ashare_hotpot.institution_metrics import ResearchBoardService  # noqa: E402
from ashare_hotpot.storage import Storage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.db.is_file():
        raise SystemExit(f"database copy not found: {args.db}")
    storage = Storage(args.db)
    storage.initialize()
    now = datetime.now(SHANGHAI_TZ)
    result = ResearchBoardService(
        AppSettings(app_root=args.db.parent), storage
    ).run(
        now=now,
        backfill_days=550,
        pipeline_version="v2",
    )
    report = {
        "database_copy": str(args.db),
        "run_at": now.isoformat(),
        "pipeline_version": result.pipeline_version,
        "documents_scanned": result.documents_scanned,
        "activities_persisted": result.activities_persisted,
        "participants_added": result.participants_added,
        "warming_rows": len(result.warming_v2_rows),
        "persistence_60_rows": len(result.persistence_rule_60_rows),
        "persistence_120_rows": len(result.persistence_rule_120_rows),
        "errors": list(result.errors),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
