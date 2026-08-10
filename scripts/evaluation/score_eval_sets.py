"""Score frozen evaluation sets against plan.md 17.2 gates.

This script never generates labels; it only checks that every required label
is present in the frozen JSON (labels are produced by human review, or since
2026-08-08 by LLM annotation under explicit user authorization) and computes
the release gates:

- short-term (v2 强化门槛): Precision@10 >= 0.90; top-20 irrelevant <= 5%;
  top-20 duplicate = 0; must-hit recall >= 85%;
- institution (v2 强化门槛): entity precision >= 92%; group dedup precision
  >= 95%; named-institution recall >= 92% (原文明确列名机构).

v2 优化计划（plan.md 第三部分 里程碑 1）废弃“由现有 event_extractions 反向
生成必达集”的自证口径：must-hit 召回只对标注环节独立给出的
``must_hit_candidate=True`` 事件计算；旧文件里由引擎生成的 ``must_hit`` 列表
不再参与评分。评分同时汇总错误账本（``error_types``：类型/方向/重大性/
重复聚类错误）并输出分层统计。

Exit code 0 means every applicable gate passes; 1 means at least one gate
fails or required labels are missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ashare_hotpot.institutions import (  # noqa: E402
    SEED_ENTITIES,
    normalize_institution_name,
)
from ashare_hotpot.research_activities import (  # noqa: E402
    RESEARCH_INSTITUTION_TYPES,
)

EVAL_SCHEMA_VERSION = 1
SHORT_TERM_SCHEMA_VERSION = 2
MIN_EVENT_CLUSTERS = 300
MIN_INSTITUTION_RECORDS = 100
MIN_DISCOVERY_SAMPLES = 300
# v2 优化计划（plan.md 第三部分“v2 测试与验收”强化门槛，2026-08-09）：
# 严格榜 Precision@10 >= 90%；Top20 无关 <= 5%；Top20 重复 = 0；
# 独立标注必达召回 >= 85%；机构实体精确率 >= 92%；列名机构召回 >= 92%；
# 集团去重精确率 >= 95%；候选发现召回 >= 95%。
PRECISION_AT_10_MIN = 0.90
TOP20_IRRELEVANT_MAX = 0.05
TOP20_DUPLICATE_MAX = 0.0
MUST_HIT_RECALL_MIN = 0.85
ENTITY_PRECISION_MIN = 0.92
GROUP_PRECISION_MIN = 0.95
NAMED_INSTITUTION_RECALL_MIN = 0.92
ALL_ORG_PRECISION_MIN = 0.90
ALL_ORG_NAMED_RECALL_MIN = 0.90
DISCOVERY_RECALL_MIN = 0.95


class LabelError(ValueError):
    """A required label is missing or not parseable."""


def parse_label(value: object, field_name: str, location: str) -> bool:
    """Parse a boolean label; ``None``/missing values raise LabelError."""

    if value is None:
        raise LabelError(f"missing label {field_name!r} at {location}")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "relevant", "ok"}:
            return True
        if normalized in {"false", "0", "no", "irrelevant", "bad"}:
            return False
    raise LabelError(
        f"unparseable label {field_name!r}={value!r} at {location}"
    )


def parse_named_institutions(value: object, location: str) -> list[str]:
    """Parse the ``named_institutions`` label of one activity record.

    The value is the annotator's list of institutions explicitly named in the
    original record text (plan.md 里程碑 7 recall gate).  ``None``/missing
    means the record was not annotated; a non-list or empty-string entry is a
    labeling error, not an empty list.
    """

    if value is None:
        raise LabelError(f"missing label 'named_institutions' at {location}")
    if not isinstance(value, list):
        raise LabelError(
            f"unparseable label 'named_institutions'={value!r} at {location}"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise LabelError(
                f"unparseable named institution {item!r} at {location}"
            )
        result.append(item.strip())
    return result


def parse_named_institution_types(
    value: object, named: list[str], location: str
) -> list[str] | None:
    """Parse the parallel ``named_institution_types`` label.

    ``None``/missing falls back to the conservative all-named denominator
    (pre-type-annotation evaluation files).  A malformed array raises
    :class:`LabelError` instead of guessing.
    """

    if value is None:
        return None
    if not isinstance(value, list) or len(value) != len(named):
        raise LabelError(
            f"named_institution_types must parallel named_institutions "
            f"at {location}"
        )
    result: list[str] = []
    for item in value:
        if item not in ("research", "other"):
            raise LabelError(
                f"unparseable named institution type {item!r} at {location}"
            )
        result.append(str(item))
    return result


def _seed_short_name_aliases() -> dict[str, set[str]]:
    """Normalized seed short names per canonical name.

    Lets the recall matcher pair a brand-level mention from the original text
    (e.g. “国泰君安”) with the persisted canonical entity
    (“国泰君安证券股份有限公司”) without inventing fuzzy merges.
    """

    aliases: dict[str, set[str]] = {}
    for entity in SEED_ENTITIES:
        canonical = normalize_institution_name(entity.canonical_name)
        bucket = aliases.setdefault(canonical, set())
        bucket.add(canonical)
        for short in entity.short_names:
            bucket.add(normalize_institution_name(short))
    return aliases


_SEED_ALIASES = _seed_short_name_aliases()


_LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "集团有限公司",
    "集团股份有限公司",
    "有限公司",
    "股份公司",
    "集团公司",
    "公司",
    "集团",
)


def _strip_legal_suffix(value: str) -> str:
    """Remove non-distinctive legal-form suffixes (plan.md 12.1)."""

    for suffix in _LEGAL_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _institution_names_match(named: str, canonical: str) -> bool:
    """True when a text mention and a persisted canonical name are the same
    institution under the pipeline's own conservative normalization.

    Exact normalized equality covers full legal names and legal-suffix
    variants (“中信证券” vs “中信证券股份有限公司”); seed short names cover
    brand-level mentions (“国泰君安” vs “国泰君安证券股份有限公司”).  No
    fuzzy similarity is used, so distinct institutions (“华夏基金” vs
    “华夏银行”) never match.
    """

    named_norm = normalize_institution_name(named)
    canonical_norm = normalize_institution_name(canonical)
    if named_norm == canonical_norm:
        return True
    if named_norm in _SEED_ALIASES.get(canonical_norm, ()):
        return True
    # 无辨识意义的法定后缀剥离后相等（“国元证券股份有限公司” vs “国元证券”）。
    # 只剥离后缀、不剥离前缀/品牌词，避免误合并（“工商银行” vs
    # “中国工商银行”不匹配）。
    return _strip_legal_suffix(named_norm) == _strip_legal_suffix(canonical_norm)


def _top_n(rows: list[dict], n: int) -> list[dict]:
    return rows[:n]


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    value: float | None
    required: float | None
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ShortTermScore:
    event_cluster_count: int
    board_row_count: int
    precision_at_10: float | None
    top20_irrelevant_ratio: float | None
    top20_duplicate_ratio: float | None
    must_hit_recall: float | None
    must_hit_count: int
    unparsed_must_hit_count: int
    error_ledger: dict[str, object]
    gates: list[Gate] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(gate.passed for gate in self.gates)


@dataclass(frozen=True, slots=True)
class InstitutionScore:
    record_count: int
    labeled_entity_count: int
    entity_precision: float | None
    labeled_group_count: int
    group_precision: float | None
    named_institution_count: int
    named_institution_recall: float | None
    # v2 计划按“研究机构实体”与“全部组织提及”分口径统计（plan v2 测试与验收）：
    # 研究机构主指标 ≥92%（券商/基金/保险/资管/私募/境外投资机构），
    # 全部组织提及 ≥90%（含产业公司/律所/咨询等，保留在明细不计入主榜）。
    research_entity_precision: float | None = None
    research_named_recall: float | None = None
    all_org_entity_precision: float | None = None
    all_org_named_recall: float | None = None
    gates: list[Gate] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(gate.passed for gate in self.gates)


@dataclass(frozen=True, slots=True)
class DiscoveryScore:
    sample_count: int
    should_discover_count: int
    candidate_recall: float | None
    fixed_case_count: int
    fixed_case_misses: tuple[str, ...]
    gates: list[Gate] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(gate.passed for gate in self.gates)


def score_short_term(data: dict) -> ShortTermScore:
    """Score one frozen short-term evaluation set."""

    if data.get("kind") != "short_term_events":
        raise LabelError(f"not a short-term set: {data.get('kind')!r}")
    if data.get("schema_version") != SHORT_TERM_SCHEMA_VERSION:
        raise LabelError(
            f"unsupported schema_version {data.get('schema_version')!r}"
        )
    events = list(data.get("events") or [])
    board = list(data.get("board") or [])
    gates: list[Gate] = []

    # Every board row must carry a human label before scoring is allowed.
    for row in board:
        location = (
            f"board[{row.get('rank')}] "
            f"{row.get('event_id')}/{row.get('stock_code')}"
        )
        parse_label(row.get("relevant"), "relevant", location)
        parse_label(row.get("duplicate"), "duplicate", location)

    top10 = _top_n(board, 10)
    top20 = _top_n(board, 20)
    precision_at_10 = (
        _ratio(
            sum(1 for row in top10 if parse_label(row["relevant"], "relevant", "")),
            len(top10),
        )
        if len(top10) >= 10
        else None
    )
    top20_irrelevant = (
        _ratio(
            sum(
                1
                for row in top20
                if not parse_label(row["relevant"], "relevant", "")
            ),
            len(top20),
        )
        if len(top20) >= 20
        else None
    )
    top20_duplicate = (
        _ratio(
            sum(1 for row in top20 if parse_label(row["duplicate"], "duplicate", "")),
            len(top20),
        )
        if len(top20) >= 20
        else None
    )

    # v2 证据规则（plan.md §6）：parse_status 不是 parsed 的事件没有可用正文，
    # 标题本身不足以支撑证据，属于 M2 覆盖/附件队列缺口，不进入必达召回分母；
    # 只对“有正文可评估”的事件计算独立标注必达召回（解析失败层单独跟踪）。
    # 兼容旧导出：representative 缺失或 parse_status 为空时按可评估处理。
    _UNPARSED_STATUSES = {"metadata_only", "empty_text", "failed"}

    def _parsed_representative(event: dict) -> bool:
        status = str((event.get("representative") or {}).get("parse_status") or "")
        return status not in _UNPARSED_STATUSES

    must_hit = [
        event
        for event in events
        if parse_label(
            event.get("must_hit_candidate"),
            "must_hit_candidate",
            f"events/{event.get('event_id')}",
        )
        and _parsed_representative(event)
    ]
    unparsed_must_hit = [
        event
        for event in events
        if parse_label(
            event.get("must_hit_candidate"),
            "must_hit_candidate",
            f"events/{event.get('event_id')}",
        )
        and not _parsed_representative(event)
    ]
    must_hit_total = len(must_hit)
    must_hit_matched = 0
    board_event_ids = {str(row.get("event_id")) for row in board}
    for item in must_hit:
        if str(item.get("event_id")) in board_event_ids:
            must_hit_matched += 1
    must_hit_recall = (
        _ratio(must_hit_matched, must_hit_total) if must_hit_total else None
    )
    error_ledger = _build_error_ledger(events)

    gates.append(
        Gate(
            "event_cluster_set_size",
            float(len(events)),
            float(MIN_EVENT_CLUSTERS),
            len(events) >= MIN_EVENT_CLUSTERS,
            f"{len(events)} >= {MIN_EVENT_CLUSTERS}",
        )
    )
    gates.append(
        Gate(
            "precision_at_10",
            precision_at_10,
            PRECISION_AT_10_MIN,
            precision_at_10 is not None and precision_at_10 >= PRECISION_AT_10_MIN,
            (
                "needs >= 10 board rows"
                if precision_at_10 is None
                else f"{precision_at_10:.3f} >= {PRECISION_AT_10_MIN}"
            ),
        )
    )
    gates.append(
        Gate(
            "top20_irrelevant",
            top20_irrelevant,
            TOP20_IRRELEVANT_MAX,
            top20_irrelevant is not None and top20_irrelevant <= TOP20_IRRELEVANT_MAX,
            (
                "needs >= 20 board rows"
                if top20_irrelevant is None
                else f"{top20_irrelevant:.3f} <= {TOP20_IRRELEVANT_MAX}"
            ),
        )
    )
    gates.append(
        Gate(
            "top20_duplicate",
            top20_duplicate,
            TOP20_DUPLICATE_MAX,
            top20_duplicate is not None and top20_duplicate <= TOP20_DUPLICATE_MAX,
            (
                "needs >= 20 board rows"
                if top20_duplicate is None
                else f"{top20_duplicate:.3f} <= {TOP20_DUPLICATE_MAX}"
            ),
        )
    )
    gates.append(
        Gate(
            "must_hit_recall",
            must_hit_recall,
            MUST_HIT_RECALL_MIN,
            must_hit_recall is not None and must_hit_recall >= MUST_HIT_RECALL_MIN,
            (
                "no independently labeled must-hit events"
                if must_hit_recall is None
                else f"{must_hit_recall:.3f} >= {MUST_HIT_RECALL_MIN}"
            ),
        )
    )
    return ShortTermScore(
        event_cluster_count=len(events),
        board_row_count=len(board),
        precision_at_10=precision_at_10,
        top20_irrelevant_ratio=top20_irrelevant,
        top20_duplicate_ratio=top20_duplicate,
        must_hit_recall=must_hit_recall,
        must_hit_count=must_hit_total,
        unparsed_must_hit_count=len(unparsed_must_hit),
        error_ledger=error_ledger,
        gates=gates,
    )


EVENT_ERROR_TYPES = (
    "type_error",
    "direction_error",
    "materiality_error",
    "duplicate_clustering",
)


def _build_error_ledger(events: list[dict]) -> dict[str, object]:
    """v2 里程碑 1 错误账本：汇总每类错误及代表性样本。"""

    counts: dict[str, int] = {error: 0 for error in EVENT_ERROR_TYPES}
    samples: list[dict[str, str]] = []
    for event in events:
        value = event.get("error_types")
        if value is None:
            raise LabelError(
                f"missing label 'error_types' at events/{event.get('event_id')}"
            )
        if not isinstance(value, list) or not all(
            isinstance(error, str) and error in EVENT_ERROR_TYPES
            for error in value
        ):
            raise LabelError(
                f"unparseable label 'error_types'={value!r} at "
                f"events/{event.get('event_id')}"
            )
        for error in dict.fromkeys(value):
            counts[error] += 1
            samples.append(
                {
                    "event_id": str(event.get("event_id") or ""),
                    "error_type": error,
                    "canonical_title": str(event.get("canonical_title") or "")[:80],
                }
            )
    return {
        "counts": counts,
        "total_events": len(events),
        "samples": samples,
    }


def score_institution(data: dict) -> InstitutionScore:
    """Score one frozen institution-record evaluation set."""

    if data.get("kind") != "institution_records":
        raise LabelError(f"not an institution set: {data.get('kind')!r}")
    if data.get("schema_version") != EVAL_SCHEMA_VERSION:
        raise LabelError(
            f"unsupported schema_version {data.get('schema_version')!r}"
        )
    records = list(data.get("records") or [])

    entity_total = 0
    entity_ok = 0
    research_entity_total = 0
    research_entity_ok = 0
    group_total = 0
    group_ok = 0
    named_total = 0
    named_matched = 0
    research_named_total = 0
    research_named_matched = 0
    for record in records:
        location = f"records/{record.get('activity_id')}"
        named = parse_named_institutions(
            record.get("named_institutions"), f"{location}/named_institutions"
        )
        named_types = parse_named_institution_types(
            record.get("named_institution_types"),
            named,
            f"{location}/named_institution_types",
        )
        participants = record.get("participants") or []
        system_entities = []
        research_system_entities = []
        for participant in participants:
            canonical = str(participant.get("canonical_name") or "").strip()
            if not parse_label(
                participant.get("entity_ok"), "entity_ok", location
            ) or not canonical:
                continue
            system_entities.append(canonical)
            if str(participant.get("institution_type") or "") in (
                RESEARCH_INSTITUTION_TYPES
            ):
                research_system_entities.append(canonical)
        for participant in record.get("participants") or []:
            entity_ok_value = parse_label(
                participant.get("entity_ok"), "entity_ok", location
            )
            group_ok_value = parse_label(
                participant.get("group_ok"), "group_ok", location
            )
            entity_total += 1
            group_total += 1
            if entity_ok_value:
                entity_ok += 1
            if group_ok_value:
                group_ok += 1
            if str(participant.get("institution_type") or "") in (
                RESEARCH_INSTITUTION_TYPES
            ):
                research_entity_total += 1
                if entity_ok_value:
                    research_entity_ok += 1
        for index, named_name in enumerate(named):
            named_total += 1
            is_research_named = (
                named_types[index] == "research"
                if named_types is not None
                else True  # 旧文件无类型标注：按保守口径全部计入分母
            )
            if any(
                _institution_names_match(named_name, canonical)
                for canonical in system_entities
            ):
                named_matched += 1
            if is_research_named:
                research_named_total += 1
                if any(
                    _institution_names_match(named_name, canonical)
                    for canonical in research_system_entities
                ):
                    research_named_matched += 1

    entity_precision = _ratio(entity_ok, entity_total) if entity_total else None
    research_entity_precision = (
        _ratio(research_entity_ok, research_entity_total)
        if research_entity_total
        else None
    )
    group_precision = _ratio(group_ok, group_total) if group_total else None
    named_recall = _ratio(named_matched, named_total) if named_total else None
    research_named_recall = (
        _ratio(research_named_matched, research_named_total)
        if research_named_total
        else None
    )
    gates = [
        Gate(
            "institution_record_set_size",
            float(len(records)),
            float(MIN_INSTITUTION_RECORDS),
            len(records) >= MIN_INSTITUTION_RECORDS,
            f"{len(records)} >= {MIN_INSTITUTION_RECORDS}",
        ),
        Gate(
            "entity_precision",
            research_entity_precision,
            ENTITY_PRECISION_MIN,
            research_entity_precision is not None
            and research_entity_precision >= ENTITY_PRECISION_MIN,
            (
                "no labeled participants"
                if research_entity_precision is None
                else f"{research_entity_precision:.3f} >= {ENTITY_PRECISION_MIN}"
            ),
        ),
        Gate(
            "group_precision",
            group_precision,
            GROUP_PRECISION_MIN,
            group_precision is not None and group_precision >= GROUP_PRECISION_MIN,
            (
                "no labeled participants"
                if group_precision is None
                else f"{group_precision:.3f} >= {GROUP_PRECISION_MIN}"
            ),
        ),
        Gate(
            "named_institution_recall",
            research_named_recall,
            NAMED_INSTITUTION_RECALL_MIN,
            research_named_recall is not None
            and research_named_recall >= NAMED_INSTITUTION_RECALL_MIN,
            (
                "no named institutions in the sample (no body text?)"
                if research_named_recall is None
                else f"{research_named_recall:.3f} >= {NAMED_INSTITUTION_RECALL_MIN}"
            ),
        ),
        Gate(
            "all_org_entity_precision",
            entity_precision,
            ALL_ORG_PRECISION_MIN,
            entity_precision is not None
            and entity_precision >= ALL_ORG_PRECISION_MIN,
            (
                "no labeled participants"
                if entity_precision is None
                else f"{entity_precision:.3f} >= {ALL_ORG_PRECISION_MIN}"
            ),
        ),
        Gate(
            "all_org_named_recall",
            named_recall,
            ALL_ORG_NAMED_RECALL_MIN,
            named_recall is not None
            and named_recall >= ALL_ORG_NAMED_RECALL_MIN,
            (
                "no named institutions in the sample (no body text?)"
                if named_recall is None
                else f"{named_recall:.3f} >= {ALL_ORG_NAMED_RECALL_MIN}"
            ),
        ),
    ]
    return InstitutionScore(
        record_count=len(records),
        labeled_entity_count=entity_total,
        entity_precision=research_entity_precision,
        labeled_group_count=group_total,
        group_precision=group_precision,
        named_institution_count=named_total,
        named_institution_recall=research_named_recall,
        research_entity_precision=research_entity_precision,
        research_named_recall=research_named_recall,
        all_org_entity_precision=entity_precision,
        all_org_named_recall=named_recall,
        gates=gates,
    )


def _discovery_label(value: object, location: str) -> bool:
    """Discovery labels are ``should_discover`` / ``not_discover``."""

    if value is None:
        raise LabelError(f"missing label 'label' at {location}")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"should_discover", "true", "1", "yes"}:
            return True
        if normalized in {"not_discover", "false", "0", "no"}:
            return False
    raise LabelError(f"unparseable label 'label'={value!r} at {location}")


def score_discovery(data: dict) -> DiscoveryScore:
    """Score one frozen discovery-candidate set (plan.md 里程碑 7).

    Gates: candidate recall >= 95% over ``should_discover`` items, and zero
    fixed-case misses (本机公开样本必须进入发现层).
    """

    if data.get("kind") != "discovery_candidates":
        raise LabelError(f"not a discovery set: {data.get('kind')!r}")
    if data.get("schema_version") != EVAL_SCHEMA_VERSION:
        raise LabelError(
            f"unsupported schema_version {data.get('schema_version')!r}"
        )
    items = list(data.get("items") or [])
    should_discover: list[dict] = []
    for item in items:
        location = f"items/{item.get('document_id')}"
        if _discovery_label(item.get("label"), location):
            should_discover.append(item)
    if not should_discover:
        raise LabelError("no should_discover labels in the discovery set")
    candidate_recall = _ratio(
        sum(1 for item in should_discover if item.get("in_discovery_layer")),
        len(should_discover),
    )
    fixed_items = [item for item in items if item.get("fixed_case")]
    fixed_misses: list[str] = [
        str(item.get("title") or item.get("document_id") or "?")
        for item in fixed_items
        if not item.get("in_discovery_layer")
    ]
    if not fixed_items:
        fixed_misses = ["样本中无固定案例（数据库缺少本机公开样本）"]
    gates = [
        Gate(
            "discovery_set_size",
            float(len(items)),
            float(MIN_DISCOVERY_SAMPLES),
            len(items) >= MIN_DISCOVERY_SAMPLES,
            f"{len(items)} >= {MIN_DISCOVERY_SAMPLES}",
        ),
        Gate(
            "candidate_recall",
            candidate_recall,
            DISCOVERY_RECALL_MIN,
            candidate_recall >= DISCOVERY_RECALL_MIN,
            f"{candidate_recall:.3f} >= {DISCOVERY_RECALL_MIN}",
        ),
        Gate(
            "fixed_case_zero_miss",
            float(len(fixed_misses)),
            0.0,
            not fixed_misses,
            "零遗漏" if not fixed_misses else f"遗漏：{fixed_misses}",
        ),
    ]
    return DiscoveryScore(
        sample_count=len(items),
        should_discover_count=len(should_discover),
        candidate_recall=candidate_recall,
        fixed_case_count=len(fixed_items),
        fixed_case_misses=tuple(fixed_misses),
        gates=gates,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--short-term", type=Path)
    parser.add_argument("--institution", type=Path)
    parser.add_argument("--discovery", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.short_term and not args.institution and not args.discovery:
        parser.error(
            "provide --short-term and/or --institution and/or --discovery"
        )

    results: dict[str, object] = {}
    failed = False
    try:
        if args.short_term:
            data = json.loads(args.short_term.read_text(encoding="utf-8"))
            score = score_short_term(data)
            results["short_term"] = asdict(score)
            failed = failed or not score.passed
        if args.institution:
            data = json.loads(args.institution.read_text(encoding="utf-8"))
            score = score_institution(data)
            results["institution"] = asdict(score)
            failed = failed or not score.passed
        if args.discovery:
            data = json.loads(args.discovery.read_text(encoding="utf-8"))
            score = score_discovery(data)
            results["discovery"] = asdict(score)
            failed = failed or not score.passed
    except (LabelError, json.JSONDecodeError, OSError) as error:
        print(f"error: {error}")
        return 1

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
