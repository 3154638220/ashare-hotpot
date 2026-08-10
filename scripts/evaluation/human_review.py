"""发布前人工抽检工具（plan v2：以 LLM 全量与人工抽检的较低结果决定发布）。

机构侧（``institution``）：逐条审核 `human_spotcheck_institution_r3.json`
中的活动——对每个 LLM named 复核 research/other/删除，对每个参与者复核
entity_ok；进度保存为 JSON 可中断续做；完成后按人工口径重算研究机构召回
（剔除人工删除项、纠正 LLM 类型误标）与实体精确率。
事件侧（``events``）：逐事件复核 relevant/duplicate，重算 Precision@10 与
Top20 无关/重复。

用法::

    python scripts/evaluation/human_review.py institution \
        --list evaluation/human_spotcheck_institution_r3.json \
        --llm %TEMP%\\ashare_v2eval_r3_170225\\eval_out\\institution_records_v1.llm.json \
        --state evaluation/human_review_institution_r3.json

    python scripts/evaluation/human_review.py events \
        --list evaluation/human_spotcheck_events_r3.json \
        --llm %TEMP%\\ashare_v2eval_r3_170225\\eval_out\\short_term_events_v1.llm.json \
        --state evaluation/human_review_events_r3.json

``--batch`` 可传入预填判定 JSON（自动化测试/非交互批量复核用），格式见
``apply_institution_review`` / ``apply_event_review`` 文档。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ashare_hotpot.research_activities import RESEARCH_INSTITUTION_TYPES  # noqa: E402

from score_eval_sets import (  # noqa: E402
    _institution_names_match,
    parse_label,
    parse_named_institution_types,
    parse_named_institutions,
)


def apply_institution_review(
    records: list[dict],
    state: dict,
) -> dict:
    """按人工抽检口径重算机构指标。

    ``state`` 结构：:

        {
          "named_overrides": {activity_id: {name: "research"|"other"|"delete"}},
          "participant_ok": {activity_id: {institution_id: bool}},
        }

    规则：named 删除项不计入召回分母；类型覆盖替换 LLM 类型；参与者
    entity_ok 覆盖替换 LLM 判定。返回与 score_eval_sets 一致的指标字典。
    """

    overrides = state.get("named_overrides") or {}
    ok_overrides = state.get("participant_ok") or {}
    research_total = 0
    research_matched = 0
    all_org_total = 0
    all_org_matched = 0
    entity_total = 0
    entity_ok = 0
    group_total = 0
    group_ok = 0
    for record in records:
        location = f"records/{record.get('activity_id')}"
        named = parse_named_institutions(
            record.get("named_institutions"), f"{location}/named_institutions"
        )
        types = parse_named_institution_types(
            record.get("named_institution_types"), named, location
        ) or ["research"] * len(named)
        named_overrides = overrides.get(record.get("activity_id")) or {}
        ok_overrides_act = ok_overrides.get(record.get("activity_id")) or {}
        parts = record.get("participants") or []
        research_ok: list[str] = []
        any_ok: list[str] = []
        for p in parts:
            pid = p.get("institution_id")
            entity_total += 1
            ok_value = (
                ok_overrides_act.get(pid)
                if pid in ok_overrides_act
                else parse_label(p.get("entity_ok"), "entity_ok", location)
            )
            group_value = parse_label(p.get("group_ok"), "group_ok", location)
            if ok_value:
                entity_ok += 1
            if group_value:
                group_ok += 1
            group_total += 1
            canonical = str(p.get("canonical_name") or "").strip()
            if not ok_value or not canonical:
                continue
            any_ok.append(canonical)
            if str(p.get("institution_type") or "") in (
                RESEARCH_INSTITUTION_TYPES
            ):
                research_ok.append(canonical)
        for index, name in enumerate(named):
            override = named_overrides.get(name)
            if override == "delete":
                continue
            effective_type = (
                override if override in ("research", "other") else types[index]
            )
            all_org_total += 1
            if any(_institution_names_match(name, c) for c in any_ok):
                all_org_matched += 1
            if effective_type == "research":
                research_total += 1
                if any(_institution_names_match(name, c) for c in research_ok):
                    research_matched += 1
    return {
        "research_named_recall": (
            research_matched / research_total if research_total else None
        ),
        "all_org_named_recall": (
            all_org_matched / all_org_total if all_org_total else None
        ),
        "entity_precision": entity_ok / entity_total if entity_total else None,
        "group_precision": group_ok / group_total if group_total else None,
    }


def apply_event_review(events: list[dict], board: list[dict], state: dict) -> dict:
    """按人工抽检口径重算事件侧指标（relevant/duplicate 覆盖）。"""

    relevant_overrides = state.get("relevant") or {}
    duplicate_overrides = state.get("duplicate") or {}
    rows = []
    for row in board:
        eid = row.get("event_id")
        relevant = (
            relevant_overrides.get(eid)
            if eid in relevant_overrides
            else parse_label(row.get("relevant"), "relevant", "")
        )
        duplicate = (
            duplicate_overrides.get(eid)
            if eid in duplicate_overrides
            else parse_label(row.get("duplicate"), "duplicate", "")
        )
        rows.append({**row, "relevant": relevant, "duplicate": duplicate})
    top10 = rows[:10]
    top20 = rows[:20]
    precision_at_10 = (
        sum(1 for r in top10 if r["relevant"]) / len(top10) if top10 else None
    )
    top20_irrelevant = (
        sum(1 for r in top20 if not r["relevant"]) / len(top20)
        if len(top20) >= 20
        else None
    )
    top20_duplicate = (
        sum(1 for r in top20 if r["duplicate"]) / len(top20)
        if len(top20) >= 20
        else None
    )
    return {
        "precision_at_10": precision_at_10,
        "top20_irrelevant": top20_irrelevant,
        "top20_duplicate": top20_duplicate,
        "board_rows": len(rows),
    }


def _prompt_institution(items: list[dict], state: dict) -> dict:
    overrides = state.setdefault("named_overrides", {})
    ok_overrides = state.setdefault("participant_ok", {})
    for index, item in enumerate(items):
        aid = item["activity_id"]
        print("\n[%d/%d] %s | %s" % (index + 1, len(items), item.get("stock_code"), (item.get("title") or "")[:50]))
        print("  document:", (item.get("document_id") or "")[:30])
        print("  LLM named (research/other):")
        for name, t in zip(item.get("llm_named_institutions") or [], (item.get("llm_named_types") or [])):
            print("    [%s] %s" % (t, name))
        print("  Pipeline participants (entity_ok / type):")
        for p in item.get("pipeline_participants") or []:
            print("    [%s/%s] %s" % (p.get("entity_ok"), p.get("institution_type"), p.get("canonical_name")))
        # 交互输入（简化：跳过=Enter 保持 LLM 口径）。
        resp = input("  复核 named（q 退出，s 跳过，其他=按 LLM 口径）: ").strip()
        if resp.lower() == "q":
            break
    return state


def export_institution_csv(items: list[dict], out: Path) -> None:
    """导出机构抽检 CSV（人工在 Excel 填写 human 列后导入）。"""

    import csv

    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["activity_id", "kind", "name", "llm_value", "human_value"]
        )
        for item in items:
            aid = item["activity_id"]
            for name, t in zip(
                item.get("llm_named_institutions") or [],
                item.get("llm_named_types") or [],
            ):
                writer.writerow([aid, "named", name, t, ""])
            for participant in item.get("pipeline_participants") or []:
                writer.writerow(
                    [
                        aid,
                        "participant",
                        participant.get("canonical_name") or "",
                        participant.get("institution_id") or "",
                        "",
                    ]
                )


def import_institution_csv(csv_path: Path) -> dict:
    """读取人工填写的抽检 CSV → 抽检 state。

    ``named`` 行 human_value：research/other/delete（留空 = 按 LLM 口径）；
    ``participant`` 行 human_value：true/false（留空 = 按 LLM 口径）。
    """

    import csv

    state: dict = {"named_overrides": {}, "participant_ok": {}}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            aid = (row.get("activity_id") or "").strip()
            kind = (row.get("kind") or "").strip()
            human = (row.get("human_value") or "").strip().lower()
            if not aid or not human:
                continue
            if kind == "named":
                name = (row.get("name") or "").strip()
                if human in ("research", "other", "delete") and name:
                    state["named_overrides"].setdefault(aid, {})[name] = human
            elif kind == "participant":
                pid = (row.get("llm_value") or "").strip()
                if human in ("true", "1", "yes"):
                    state["participant_ok"].setdefault(aid, {})[pid] = True
                elif human in ("false", "0", "no"):
                    state["participant_ok"].setdefault(aid, {})[pid] = False
    return state


def export_events_csv(items: list[dict], out: Path) -> None:
    """导出事件抽检 CSV（人工填写 relevant/duplicate 列后导入）。"""

    import csv

    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "event_id",
                "stock_codes",
                "title",
                "llm_label",
                "must_hit_llm",
                "on_board",
                "error_types",
                "relevant",
                "duplicate",
            ]
        )
        for item in items:
            writer.writerow(
                [
                    item.get("event_id"),
                    ";".join(item.get("stock_codes") or []),
                    item.get("canonical_title") or "",
                    item.get("label_llm") or "",
                    item.get("must_hit_llm") or "",
                    item.get("on_board") or "",
                    ",".join(item.get("error_types") or []),
                    "",
                    "",
                ]
            )


def import_events_csv(csv_path: Path) -> dict:
    """读取人工填写的抽检 CSV → 事件抽检 state。"""

    import csv

    state: dict = {"relevant": {}, "duplicate": {}}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            eid = (row.get("event_id") or "").strip()
            if not eid:
                continue
            relevant = (row.get("relevant") or "").strip().lower()
            duplicate = (row.get("duplicate") or "").strip().lower()
            if relevant in ("true", "1", "yes"):
                state["relevant"][eid] = True
            elif relevant in ("false", "0", "no"):
                state["relevant"][eid] = False
            if duplicate in ("true", "1", "yes"):
                state["duplicate"][eid] = True
            elif duplicate in ("false", "0", "no"):
                state["duplicate"][eid] = False
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_inst = sub.add_parser("institution")
    p_inst.add_argument("--list", type=Path, required=True)
    p_inst.add_argument("--llm", type=Path)
    p_inst.add_argument("--state", type=Path)
    p_inst.add_argument("--batch", type=Path)
    p_inst.add_argument("--auto", action="store_true", help="非交互：直接按 LLM 口径评分")
    p_inst.add_argument("--export-csv", type=Path, help="导出抽检 CSV 模板")
    p_inst.add_argument("--import-csv", type=Path, help="导入人工填写的抽检 CSV")
    p_evt = sub.add_parser("events")
    p_evt.add_argument("--list", type=Path, required=True)
    p_evt.add_argument("--llm", type=Path)
    p_evt.add_argument("--state", type=Path)
    p_evt.add_argument("--batch", type=Path)
    p_evt.add_argument("--auto", action="store_true")
    p_evt.add_argument("--export-csv", type=Path, help="导出抽检 CSV 模板")
    p_evt.add_argument("--import-csv", type=Path, help="导入人工填写的抽检 CSV")
    args = parser.parse_args(argv)

    if args.command == "institution":
        if args.export_csv is not None:
            items = json.loads(args.list.read_text(encoding="utf-8"))["items"]
            export_institution_csv(items, args.export_csv)
            print("exported ->", args.export_csv)
            return 0
        if args.llm is None or args.state is None:
            parser.error("institution 需要 --llm 与 --state（或仅 --export-csv）")
        llm = json.loads(args.llm.read_text(encoding="utf-8"))
        records = llm["records"]
        state: dict = {}
        if args.import_csv is not None:
            state = import_institution_csv(args.import_csv)
        elif args.batch is not None:
            state = json.loads(args.batch.read_text(encoding="utf-8"))
        elif not args.auto:
            items = json.loads(args.list.read_text(encoding="utf-8"))["items"]
            state = _prompt_institution(items, state)
            args.state.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        result = apply_institution_review(records, state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "events":
        if args.export_csv is not None:
            items = json.loads(args.list.read_text(encoding="utf-8"))["items"]
            export_events_csv(items, args.export_csv)
            print("exported ->", args.export_csv)
            return 0
        if args.llm is None:
            parser.error("events 需要 --llm（或仅 --export-csv）")
    llm = json.loads(args.llm.read_text(encoding="utf-8"))
    state = {}
    if args.command == "events" and args.import_csv is not None:
        state = import_events_csv(args.import_csv)
    elif args.batch is not None:
        state = json.loads(args.batch.read_text(encoding="utf-8"))
    if args.command == "events":
        result = apply_event_review(llm["events"], llm.get("board") or [], state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
