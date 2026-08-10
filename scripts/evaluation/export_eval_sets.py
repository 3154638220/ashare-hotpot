"""Export candidate (unlabeled) evaluation sets from a 1.1.0 database.

Milestone 6 of ``plan.md`` requires frozen, human-verified evaluation sets:

- at least 300 event clusters for the short-term research boards;
- at least 100 research records for institution entity evaluation.

This script only *exports* candidate JSON with ``null`` label fields; it never
generates labels.  Labels are produced by a later annotation step (default:
human review; since 2026-08-08 also LLM annotation under explicit user
authorization, plan.md 17.2).  The exporter opens the database strictly
read-only and never modifies app data.

Usage::

    python scripts/evaluation/export_eval_sets.py [--db PATH] [--out DIR]
        [--seed N] [--short-term-size N] [--institution-size N]
        [--discovery-size N]

v2 优化计划（plan.md 第三部分 里程碑 1）废弃“由现有 event_extractions 反向
生成必达集”的自证口径：本导出器永远不填充 ``must_hit``；必达候选由标注环节
（LLM/人工）对每个事件独立给出 ``must_hit_candidate`` 标签。事件样本同时带
来源分层（``stratum``）、版式（``layout``）、解析状态、引擎抽取快照
（``engine``）与错误账本标签位（``error_types``）。
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ashare_hotpot.config import SHANGHAI_TZ  # noqa: E402

from ashare_hotpot.storage import SCHEMA_VERSION
EVAL_SCHEMA_VERSION = 1
SHORT_TERM_SCHEMA_VERSION = 2
DEFAULT_DB = (
    Path.home() / "AppData" / "Local" / "AshareHotPot" / "data" / "hotpot.db"
)
SHORT_TERM_FILE = "short_term_events_v1.json"
INSTITUTION_FILE = "institution_records_v1.json"
DISCOVERY_FILE = "discovery_candidates_v1.json"

BOARD_ORDER_SQL = """
    SELECT es.*, ec.last_seen_ts AS last_seen_ts
    FROM event_signals es
    JOIN event_clusters ec ON ec.event_id = es.event_id
    ORDER BY es.board, es.score DESC, es.materiality_level DESC,
             es.certainty DESC, ec.last_seen_ts DESC, es.stock_code ASC
"""

EVENT_SAMPLE_SQL = """
    SELECT ec.*
    FROM event_clusters ec
    ORDER BY ec.event_id ASC
"""

EVIDENCE_SQL = """
    SELECT er.evidence_id, er.source_url, er.excerpt
    FROM evidence_refs er
    JOIN event_cluster_documents ecd ON ecd.document_id = er.document_id
    WHERE ecd.event_id = ?
    ORDER BY er.evidence_id ASC
"""

ACTIVITY_SQL = """
    SELECT ra.*
    FROM research_activities ra
    ORDER BY ra.activity_id ASC
"""

ACTIVITY_DATES_SQL = """
    SELECT activity_date FROM research_activity_dates
    WHERE activity_id = ? ORDER BY activity_date ASC
"""

PARTICIPANTS_SQL = """
    SELECT rp.analyst_name, rp.evidence_id, i.*
    FROM research_participants rp
    JOIN institutions i ON i.institution_id = rp.institution_id
    WHERE rp.activity_id = ?
    ORDER BY i.institution_id ASC
"""

DOCUMENT_SQL = """
    SELECT document_id, title, source_url, document_url, published_ts,
           parse_status, provider_key
    FROM source_documents
    WHERE document_id = ?
"""

ACTIVITY_DOCUMENT_SQL = """
    SELECT document_id, title, source_url, document_url, published_ts,
           parse_status, provider_key, body_text
    FROM source_documents
    WHERE document_id = ?
"""

MAX_BODY_TEXT_CHARS = 100_000

# 里程碑 7 固定案例（本机公开样本）：必须出现在发现层（plan.md 里程碑 7）。
FIXED_DISCOVERY_CASES = (
    "五矿资本股份有限公司关于公司拟签订重大合同暨关联交易的公告",
    "西安炬光科技股份有限公司2026年半年度报告摘要",
    "关于以集中竞价交易方式回购公司股份用于注销并减少注册资本报告书",
    "关于股份回购实施结果暨股份变动的公告",
)

DISCOVERY_DOC_SQL = """
    SELECT sd.document_id, sd.provider_key, sd.provider_name, sd.kind,
           sd.title, sd.published_ts, sd.parse_status, sd.document_url,
           sds.stock_code
    FROM source_documents sd
    LEFT JOIN source_document_stocks sds ON sds.document_id = sd.document_id
    WHERE sd.kind IN ('announcement', 'research_activity')
    ORDER BY sd.document_id ASC
"""

def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version != SCHEMA_VERSION:
        raise SystemExit(
            f"expected database schema version {SCHEMA_VERSION}, "
            f"found {version} in {db_path}"
        )
    return connection


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, SHANGHAI_TZ).isoformat()


def _sample_ids(rows: list[sqlite3.Row], size: int, seed: int) -> list[sqlite3.Row]:
    """Deterministic, insertion-order independent sample of row ids."""

    items = sorted((str(row["event_id"]) for row in rows))
    rng = random.Random(seed)
    rng.shuffle(items)
    chosen = set(items[:size])
    return [row for row in rows if str(row["event_id"]) in chosen]


def _document_meta(connection: sqlite3.Connection, document_id: str | None) -> dict:
    if not document_id:
        return {}
    row = connection.execute(DOCUMENT_SQL, (document_id,)).fetchone()
    if row is None:
        return {"document_id": document_id}
    return {
        "document_id": str(row["document_id"]),
        "title": str(row["title"] or ""),
        "source_url": str(row["source_url"] or ""),
        "document_url": str(row["document_url"] or ""),
        "published_at": _iso(int(row["published_ts"])),
        "parse_status": str(row["parse_status"] or ""),
        "provider_key": str(row["provider_key"] or ""),
    }


def _activity_document_meta(
    connection: sqlite3.Connection, document_id: str | None
) -> dict:
    """Document meta for an activity record, including the extracted body.

    The full body text is required by the “原文明确列名机构” recall gate
    (plan.md 里程碑 7): annotators must be able to verify which institutions
    are explicitly named in the original record.  The body is capped only to
    guard against pathological files; real research records are a few KB.
    """

    if not document_id:
        return {}
    row = connection.execute(ACTIVITY_DOCUMENT_SQL, (document_id,)).fetchone()
    if row is None:
        return {"document_id": document_id}
    body = str(row["body_text"] or "")
    return {
        "document_id": str(row["document_id"]),
        "title": str(row["title"] or ""),
        "source_url": str(row["source_url"] or ""),
        "document_url": str(row["document_url"] or ""),
        "published_at": _iso(int(row["published_ts"])),
        "parse_status": str(row["parse_status"] or ""),
        "provider_key": str(row["provider_key"] or ""),
        "body_text": body[:MAX_BODY_TEXT_CHARS],
        "body_truncated": len(body) > MAX_BODY_TEXT_CHARS,
    }


def _stock_name(connection: sqlite3.Connection, stock_code: str) -> str:
    """Best-effort name lookup mirroring ``Storage.get_stock_names``.

    There is no central stock table; names are recovered from cached news
    articles and official Q&A records, falling back to the code itself.
    """

    for row in connection.execute("SELECT stocks_json FROM articles").fetchall():
        for item in json.loads(row["stocks_json"] or "[]"):
            if str(item.get("code") or "") == stock_code:
                name = str(item.get("name") or "")
                if name and name != stock_code:
                    return name
    row = connection.execute(
        "SELECT stock_name FROM interactions WHERE code=? AND stock_name<>? "
        "LIMIT 1",
        (stock_code, stock_code),
    ).fetchone()
    if row and row["stock_name"]:
        return str(row["stock_name"])
    return stock_code


def _discovery_stratum(provider_key: str, kind: str) -> str:
    """来源分层：巨潮公告 / 巨潮调研 / 上证发布 / 互动易投资者关系。"""

    if provider_key == "irm":
        return "irm_ircs"
    if provider_key == "sse":
        return "sse_publish"
    if kind == "research_activity":
        return "cninfo_research"
    return "cninfo_announcement"


def _document_layout(url: str | None) -> str:
    """版式分层：按附件扩展名推断 pdf/word，其余按 html/unknown。"""

    if not url:
        return "unknown"
    lower = str(url).lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith((".doc", ".docx")):
        return "word"
    return "html"


def _engine_snapshot(
    connection: sqlite3.Connection, event_id: str
) -> dict[str, object] | None:
    """引擎抽取快照（标注错误账本用）：事件类型/方向/重大性/确定性。"""

    row = connection.execute(
        "SELECT event_type, direction, materiality_level, certainty "
        "FROM event_extractions WHERE event_id=? ORDER BY stock_code ASC LIMIT 1",
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "event_type": str(row["event_type"] or ""),
        "direction": str(row["direction"] or ""),
        "materiality_level": int(row["materiality_level"] or 0),
        "certainty": float(row["certainty"] or 0.0),
    }


def export_short_term(
    db_path: Path,
    out_path: Path,
    *,
    seed: int,
    max_events: int,
) -> dict:
    """Write the candidate short-term event evaluation set; return its dict."""

    with _connect_readonly(db_path) as connection:
        board_rows = connection.execute(BOARD_ORDER_SQL).fetchall()
        event_rows = connection.execute(EVENT_SAMPLE_SQL).fetchall()
        sampled = _sample_ids(event_rows, max_events, seed)

        board: list[dict] = []
        for index, row in enumerate(board_rows, start=1):
            extraction = connection.execute(
                "SELECT event_type, extractor_kind FROM event_extractions "
                "WHERE event_id=? AND stock_code=?",
                (str(row["event_id"]), str(row["stock_code"])),
            ).fetchone()
            board.append(
                {
                    "rank": index,
                    "event_id": str(row["event_id"]),
                    "stock_code": str(row["stock_code"]),
                    "board": str(row["board"]),
                    "score": float(row["score"]),
                    "materiality_level": int(row["materiality_level"]),
                    "certainty": float(row["certainty"]),
                    "provisional": bool(row["provisional"]),
                    "event_type": str(extraction["event_type"]) if extraction else "",
                    "extractor_kind": (
                        str(extraction["extractor_kind"]) if extraction else "rules"
                    ),
                    # Annotation step fills these later; must stay null here.
                    "relevant": None,
                    "duplicate": None,
                }
            )

        events: list[dict] = []
        for row in sampled:
            evidence = [
                {
                    "evidence_id": str(item["evidence_id"]),
                    "source_url": str(item["source_url"] or ""),
                    "excerpt": str(item["excerpt"] or ""),
                }
                for item in connection.execute(EVIDENCE_SQL, (str(row["event_id"]),))
            ][:2]
            stock_rows = connection.execute(
                "SELECT stock_code FROM event_cluster_stocks "
                "WHERE event_id=? ORDER BY stock_code ASC",
                (str(row["event_id"]),),
            ).fetchall()
            representative = _document_meta(
                connection, str(row["representative_document_id"] or "")
            )
            events.append(
                {
                    "event_id": str(row["event_id"]),
                    "stock_codes": [str(item["stock_code"]) for item in stock_rows],
                    "canonical_title": str(row["canonical_title"] or ""),
                    "first_seen_at": _iso(int(row["first_seen_ts"])),
                    "last_seen_at": _iso(int(row["last_seen_ts"])),
                    "historical_similar_event_id": row[
                        "historical_similar_event_id"
                    ],
                    "representative": representative,
                    # v2 分层：来源、版式、正文状态（plan.md 第三部分 里程碑 1）。
                    "stratum": _discovery_stratum(
                        str(representative.get("provider_key") or ""),
                        "announcement",
                    ),
                    "layout": _document_layout(
                        str(representative.get("document_url") or "")
                    ),
                    "document_count": int(
                        connection.execute(
                            "SELECT COUNT(*) FROM event_cluster_documents "
                            "WHERE event_id=?",
                            (str(row["event_id"]),),
                        ).fetchone()[0]
                    ),
                    "evidence": evidence,
                    "engine": _engine_snapshot(
                        connection, str(row["event_id"])
                    ),
                    # Annotation step fills this later; must stay null here.
                    "label": None,
                    "must_hit_candidate": None,
                    "error_types": None,
                }
            )

        data = {
            "schema_version": SHORT_TERM_SCHEMA_VERSION,
            "kind": "short_term_events",
            "meta": {
                "exported_at": datetime.now(SHANGHAI_TZ).isoformat(),
                "database": db_path.name,
                "seed": seed,
                "event_cluster_count_total": len(event_rows),
                "event_cluster_count_sampled": len(events),
                "signal_count": len(board),
                "min_event_clusters": 300,
            },
            "board": board,
            "events": events,
            # v2 里程碑 1：必达集由独立标注（must_hit_candidate）决定，
            # 导出器永不从 event_extractions 反向生成（废弃自证口径）。
            "must_hit": [],
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def export_institution(
    db_path: Path,
    out_path: Path,
    *,
    seed: int,
    max_records: int,
) -> dict:
    """Write the candidate institution-record evaluation set; return its dict."""

    with _connect_readonly(db_path) as connection:
        activity_rows = connection.execute(ACTIVITY_SQL).fetchall()
        items = sorted((str(row["activity_id"]) for row in activity_rows))
        rng = random.Random(seed)
        rng.shuffle(items)
        chosen = set(items[:max_records])
        sampled = [
            row for row in activity_rows if str(row["activity_id"]) in chosen
        ]

        records: list[dict] = []
        for row in sampled:
            activity_id = str(row["activity_id"])
            participants = []
            for participant in connection.execute(PARTICIPANTS_SQL, (activity_id,)):
                participants.append(
                    {
                        "institution_id": str(participant["institution_id"]),
                        "canonical_name": str(participant["canonical_name"] or ""),
                        "group_id": str(participant["group_id"] or ""),
                        "institution_type": str(participant["institution_type"] or ""),
                        "verification_status": str(
                            participant["verification_status"] or ""
                        ),
                        "analyst_name": participant["analyst_name"],
                        "evidence_id": str(participant["evidence_id"] or ""),
                        # Annotation step fills these later; must stay null here.
                        "entity_ok": None,
                        "group_ok": None,
                    }
                )
            source_meta = _activity_document_meta(
                connection, str(row["source_document_id"] or "")
            )
            records.append(
                {
                    "activity_id": activity_id,
                    "stock_code": str(row["stock_code"]),
                    "stock_name": _stock_name(connection, str(row["stock_code"])),
                    "activity_type": str(row["activity_type"] or ""),
                    "activity_dates": [
                        str(item["activity_date"])
                        for item in connection.execute(
                            ACTIVITY_DATES_SQL, (activity_id,)
                        )
                    ],
                    "reported_participant_count": row[
                        "reported_participant_count"
                    ],
                    "named_participant_count": int(
                        row["named_participant_count"] or 0
                    ),
                    "question_count": int(row["question_count"] or 0),
                    "high_depth_question_count": int(
                        row["high_depth_question_count"] or 0
                    ),
                    "date_precision": str(row["date_precision"] or "explicit"),
                    "source": source_meta,
                    "stratum": _discovery_stratum(
                        str(source_meta.get("provider_key") or ""),
                        "research_activity",
                    ),
                    "participants": participants,
                }
            )

        data = {
            "schema_version": EVAL_SCHEMA_VERSION,
            "kind": "institution_records",
            "meta": {
                "exported_at": datetime.now(SHANGHAI_TZ).isoformat(),
                "database": db_path.name,
                "seed": seed,
                "activity_count_total": len(activity_rows),
                "activity_count_sampled": len(records),
                "min_records": 100,
            },
            "records": records,
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def export_discovery(
    db_path: Path,
    out_path: Path,
    *,
    seed: int,
    max_candidates: int,
) -> dict:
    """Write the source-tiered discovery-candidate evaluation set.

    Samples ``source_documents`` per stratum (巨潮公告/巨潮调研/上证发布/
    互动易投资者关系) so the set covers 已解析、元数据待解析、解析失败、
    严格榜命中与新增来源；固定案例强制包含。标签字段保持 ``null``，
    由后续人工/LLM 标注（对外声明必须注明“LLM 标注口径”）。
    """

    with _connect_readonly(db_path) as connection:
        rows = connection.execute(DISCOVERY_DOC_SQL).fetchall()
        strata: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            key = _discovery_stratum(
                str(row["provider_key"] or ""), str(row["kind"] or "")
            )
            strata.setdefault(key, []).append(row)
        rng = random.Random(seed)
        per_stratum = max(1, max_candidates // max(1, len(strata)))
        fixed_ids: set[str] = set()
        sampled: list[sqlite3.Row] = []
        for key, group in sorted(strata.items()):
            ids = sorted({str(row["document_id"]) for row in group})
            rng.shuffle(ids)
            chosen = set(ids[:per_stratum])
            for row in group:
                if str(row["title"] or "") in FIXED_DISCOVERY_CASES:
                    chosen.add(str(row["document_id"]))
                    fixed_ids.add(str(row["document_id"]))
            sampled.extend(
                row for row in group if str(row["document_id"]) in chosen
            )
        sampled.sort(key=lambda row: str(row["document_id"]))
        promoted = {
            str(item["document_id"])
            for item in connection.execute(
                "SELECT DISTINCT ecd.document_id "
                "FROM event_cluster_documents ecd "
                "JOIN event_signals es ON es.event_id = ecd.event_id"
            )
        }
        candidates = {
            str(item["document_id"]): dict(item)
            for item in connection.execute(
                "SELECT document_id, discovery_type, trigger_reason, "
                "queue_status FROM discovery_candidates"
            )
        }
        items: list[dict] = []
        for row in sampled:
            document_id = str(row["document_id"])
            candidate = candidates.get(document_id)
            items.append(
                {
                    "document_id": document_id,
                    "stratum": _discovery_stratum(
                        str(row["provider_key"] or ""), str(row["kind"] or "")
                    ),
                    "stock_code": (
                        str(row["stock_code"]) if row["stock_code"] else ""
                    ),
                    "stock_name": _stock_name(
                        connection, str(row["stock_code"] or "")
                    ),
                    "title": str(row["title"] or ""),
                    "published_at": _iso(int(row["published_ts"])),
                    "parse_status": str(row["parse_status"] or ""),
                    "document_url": str(row["document_url"] or ""),
                    "discovery_type": (
                        str(candidate["discovery_type"]) if candidate else None
                    ),
                    "trigger_reason": (
                        str(candidate["trigger_reason"]) if candidate else None
                    ),
                    "queue_status": (
                        str(candidate["queue_status"]) if candidate else None
                    ),
                    "in_discovery_layer": candidate is not None,
                    "promoted_to_board": document_id in promoted,
                    "fixed_case": str(row["title"] or "") in FIXED_DISCOVERY_CASES,
                    # Annotation step fills this later; must stay null here.
                    "label": None,
                }
            )
        data = {
            "schema_version": EVAL_SCHEMA_VERSION,
            "kind": "discovery_candidates",
            "meta": {
                "exported_at": datetime.now(SHANGHAI_TZ).isoformat(),
                "database": db_path.name,
                "seed": seed,
                "document_count_total": len(rows),
                "document_count_sampled": len(items),
                "strata": {
                    key: len(group) for key, group in sorted(strata.items())
                },
                "min_discovery_samples": 300,
                "fixed_case_count": len(fixed_ids),
                "fixed_case_titles": list(FIXED_DISCOVERY_CASES),
            },
            "items": items,
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="path to the 1.1.0 SQLite database (read-only access)",
    )
    parser.add_argument("--out", type=Path, default=Path("evaluation"))
    parser.add_argument("--seed", type=int, default=20260806)
    parser.add_argument("--short-term-size", type=int, default=300)
    parser.add_argument("--institution-size", type=int, default=100)
    parser.add_argument("--discovery-size", type=int, default=300)
    args = parser.parse_args()

    short_term = export_short_term(
        args.db,
        args.out / SHORT_TERM_FILE,
        seed=args.seed,
        max_events=args.short_term_size,
    )
    institution = export_institution(
        args.db,
        args.out / INSTITUTION_FILE,
        seed=args.seed,
        max_records=args.institution_size,
    )
    discovery = export_discovery(
        args.db,
        args.out / DISCOVERY_FILE,
        seed=args.seed,
        max_candidates=args.discovery_size,
    )
    print(
        f"exported {short_term['meta']['event_cluster_count_sampled']} event "
        f"clusters (total {short_term['meta']['event_cluster_count_total']}) "
        f"-> {args.out / SHORT_TERM_FILE}"
    )
    print(
        f"exported {institution['meta']['activity_count_sampled']} research "
        f"records (total {institution['meta']['activity_count_total']}) "
        f"-> {args.out / INSTITUTION_FILE}"
    )
    print(
        f"exported {discovery['meta']['document_count_sampled']} discovery "
        f"candidates (total {discovery['meta']['document_count_total']}) "
        f"-> {args.out / DISCOVERY_FILE}"
    )
    if short_term["meta"]["event_cluster_count_sampled"] < 300:
        print(
            "WARNING: short-term set is below the 300-cluster minimum; "
            "labels cannot pass plan.md 17.2 with this sample."
        )
    if institution["meta"]["activity_count_sampled"] < 100:
        print(
            "WARNING: institution set is below the 100-record minimum; "
            "labels cannot pass plan.md 17.2 with this sample."
        )
    if discovery["meta"]["document_count_sampled"] < 300:
        print(
            "WARNING: discovery set is below the 300-sample minimum; "
            "candidate recall cannot pass the milestone-7 gate with this sample."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
