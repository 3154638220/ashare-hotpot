"""Annotate candidate evaluation sets with an OpenAI-compatible LLM.

Milestone 6 / plan.md 17.2: since 2026-08-08 the user explicitly authorized
LLM annotation for the evaluation sets (DeepSeek API).  This script:

- preserves any existing labels (only fills ``null`` fields);
- calls the LLM in batches with strict JSON validation;
- marks every item that failed validation or the API as ``needs_human``
  (kept ``null``) instead of guessing;
- never logs or persists the API key (read from an environment variable).

Usage::

    python scripts/evaluation/llm_annotate.py \
        --short-term evaluation/candidate_20260808/short_term_events_v1.json \
        --institution evaluation/candidate_20260808/institution_records_v1.json \
        --out evaluation/candidate_20260808

Environment: ``DEEPSEEK_API_KEY`` (override with ``--key-env``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ashare_hotpot.config import SHANGHAI_TZ  # noqa: E402

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_KEY_ENV = "DEEPSEEK_API_KEY"
EVENT_LABELS = {"positive_signal", "neutral", "not_signal", "duplicate"}
EVENT_ERROR_TYPES = (
    "type_error",
    "direction_error",
    "materiality_error",
    "duplicate_clustering",
)
MAX_RETRIES = 1


def _read_key(env_name: str) -> str:
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise SystemExit(
            f"API key environment variable {env_name!r} is empty or unset"
        )
    return key


def chat_json(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: float,
    retries: int = MAX_RETRIES,
) -> dict[str, Any]:
    """POST a chat completion and return the parsed JSON object.

    Raises ``ValueError`` on invalid JSON and ``urllib.error.HTTPError`` /
    ``TimeoutError`` on transport errors; the caller decides what is fatal.
    """

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(2 * attempt)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < retries:
                last_error = error
                continue
            raise
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt < retries:
                last_error = error
                continue
            raise
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as error:
            raise ValueError(f"malformed API response: {error}") from error
        return _parse_json_object(content)
    raise RuntimeError(f"API request failed after retries: {last_error}")


def _parse_json_object(content: str) -> dict[str, Any]:
    """Strictly parse a JSON object from the model output."""

    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = min(
        [index for index in (cleaned.find("{"), cleaned.find("[")) if index >= 0],
        default=-1,
    )
    if start > 0:
        cleaned = cleaned[start:]
    try:
        decoder = json.JSONDecoder()
        value, _ = decoder.raw_decode(cleaned)
    except json.JSONDecodeError as error:
        raise ValueError(f"model output is not valid JSON: {error}") from error
    if isinstance(value, list):
        # The model sometimes returns the items array directly.
        value = {"items": value}
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


def _split_array(value: object, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(i, dict) for i in value):
        raise ValueError(f"field {field!r} must be a JSON array of objects")
    return value


def _get_bool(item: dict[str, Any], field: str) -> bool:
    value = item.get(field)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "0", "no"}:
        return False
    raise ValueError(f"field {field!r}={value!r} is not a boolean")


def _get_label(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or value not in EVENT_LABELS:
        raise ValueError(f"field {field!r}={value!r} is not a valid event label")
    return value


def _get_error_types(item: dict[str, Any]) -> list[str]:
    """严格校验事件级错误账本标签（v2 里程碑 1）。"""

    value = item.get("error_types")
    if value is None:
        raise ValueError("field 'error_types' is missing")
    if not isinstance(value, list) or not all(
        isinstance(error, str) and error in EVENT_ERROR_TYPES for error in value
    ):
        raise ValueError(f"field 'error_types'={value!r} is not a valid error list")
    return list(dict.fromkeys(value))


def _truncate(text: str, limit: int = 240) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


EVENT_SYSTEM_PROMPT = (
    "你是 A 股公开披露事件的评估标注器，只输出合法 JSON 对象，不要输出任何其他文字。\n"
    "输出结构固定为 {\"items\": [每项一个对象]}，items 必须覆盖输入中全部 event_id，"
    "一个都不能少。\n"
    "对每个事件给出 label（四选一）：\n"
    '- "positive_signal"：确属真实重大公司事件且有明确正向机制（如业绩上修、重大合同、'
    "产品涨价、获批认证、回购增持、并购重组、产能投产、直接政策受益、重要客户突破、"
    "补贴赔偿、股东回报（现金分红/特别分红/已回购股份注销）、研发里程碑（关键临床终点/"
    "技术验证/注册申请受理）、风险解除（风险警示撤销/重大诉讼债务担保冻结正式解除）、"
    "股权激励（须披露覆盖范围/授予规模/量化考核目标）、融资完成（须资金用途存在量化"
    "公司级正向机制）、资产处置（须披露成交状态/现金回收或利润影响）等）。\n"
    '- "neutral"：真实公司事件但中性（例行披露、问询函回复、董事会议案、分红等）。\n'
    '- "not_signal"：不是事件（独立董事声明、高管辞职、无实质内容的常规文件等）。\n'
    '- "duplicate"：与本批其他事件重复。\n'
    "同时给出 must_hit_candidate（布尔）：是否属于本引擎应当上榜的重大必达事件。"
    "判定标准：事件必须是十六类事件之一（业绩上修、重大合同、产品涨价、获批认证、"
    "回购增持、并购重组、产能投产、直接政策受益、重要客户突破、补贴赔偿、股东回报、"
    "研发里程碑、风险解除、股权激励、融资完成、资产处置）；有明确"
    "正向机制；且按入榜门槛应达到潜在催化（重大性≥1、确定性≥0.40、无标题正文冲突）"
    "或确定性利好（重大性≥2、确定性≥0.70）。已获批/已审核通过/已投产/已完成等实质进展"
    "明确的框架事件可算；仅方案/意向（含无考核目标的股权激励、无量化资金用途的融资预案）、"
    "重大性不足（如回购占比<0.1%）、或非十六类事件"
    "（定增预案、投资者关系活动记录、IPO 发行公告、竞得土地、法律意见书等）一律 false。\n"
    "标注必达必须依据披露正文核对阶段与方向（plan §6/§10.6）：\n"
    '- parse_status 为 "metadata_only"/"empty_text"/"failed" 的文档没有可用正文，'
    "标题本身不足以支撑证据（无正文时最多保留候选和元数据），must_hit_candidate=false；\n"
    "- 融资完成类必须实际完成（募集资金到位/发行完成/到账）；注册批复、备案、核准、"
    "批文只是审批/备案阶段，不等于融资完成，且必须存在量化资金用途才可算潜在催化；\n"
    "- 拟签订/尚需股东会审议/公开挂牌未成交/设立子公司投资建设/框架协议等未落地阶段，"
    "不是已签约、已处置或已投产；\n"
    "- 定期报告：归母净利润为负（亏损扩大或减亏）不是正向业绩事件，*ST/净资产为负尤其"
    "不是；扭亏为盈才可作为业绩上修；\n"
    "- 股权激励的量化考核目标必须出现在该披露正文中（仅提及《考核管理办法》名称不算）；\n"
    "- 投资者关系活动记录中的回顾性讨论不构成新事件披露，不得仅因其中提到中标/合同而"
    "标为必达；限制性股票回购注销属行政管理事项，不是股东回报。\n"
    "注意：真实发生但属负面的事件（业绩下滑、诉讼败诉、监管处罚等）标为 neutral"
    "（真实事件但非利好信号），不要发明枚举之外的标签。\n"
    "同时给出 error_types（字符串数组）：根据事件正文与引擎抽取快照（engine 字段），"
    "判断本引擎对该事件的抽取是否存在以下错误（v2 里程碑 1 错误账本）：\n"
    '- "type_error"：事件类型判断错误；\n'
    '- "direction_error"：方向判断错误（如“终止重大资产重组”被当成正向）；\n'
    '- "materiality_error"：重大性等级明显错误；\n'
    '- "duplicate_clustering"：本事件与同股票其他事件本应合并（重复聚类）；\n'
    "没有错误时输出 []。\n"
    "每项输出 {\"event_id\": str, \"label\": str, \"must_hit_candidate\": bool, "
    "\"error_types\": [...], \"rationale\": str}，rationale 不超过 60 字。"
)


BOARD_SYSTEM_PROMPT = (
    "你是 A 股短期研究榜的评估标注器，只输出合法 JSON 对象，不要输出任何其他文字。\n"
    "输出结构固定为 {\"items\": [每行一个对象]}，items 必须覆盖输入中全部行，"
    "一行都不能少。\n"
    "对榜单每一行判断：\n"
    "- relevant（布尔）：该行信号是否确属有依据的『确定性利好』或『潜在催化』"
    "（依据事件本身判断，不要看分数高低）。\n"
    "- duplicate（布尔）：该行是否与前 20 名中其他行内容重复（同一事件/同一股票/"
    "同一实质内容）。\n"
    "每项输出 {\"event_id\": str, \"stock_code\": str, \"relevant\": bool, "
    "\"duplicate\": bool, \"rationale\": str}，rationale 不超过 60 字。"
)


INSTITUTION_SYSTEM_PROMPT = (
    "你是 A 股调研活动机构实体的评估标注器，只输出合法 JSON 对象，不要输出任何其他文字。\n"
    "输出结构固定为 {\"items\": [每个参与者一个对象], \"named_institutions\": [...], "
    "\"named_institution_types\": [...]}，items 必须覆盖输入 participants 中全部 "
    "institution_id，一个都不能少。\n"
    "对每个参与者判断：\n"
    "- entity_ok（布尔）：canonical_name 是否是一个真实机构且名称归一正确；"
    "若明显不是机构（媒体、个人、非金融机构被误认）或名称/类型明显错误则为 false。\n"
    "- group_ok（布尔）：group_id 的集团归并是否合理（同一集团/同一法人合并为同一"
    " group 为 true；把不同机构错误归并、或同名不同机构为 false）。\n"
    "另外给出 named_institutions（字符串数组）：根据活动记录正文，列出原文中明确列名的"
    "机构名称（按原文原样、去重）。只包括明确列名的机构实体；排除上市公司自身、个人、"
    "媒体、描述性短语以及“约N家机构”这类模糊总数。若正文不可用（parse_status 不是 "
    "parsed 或正文为空）则输出 []。\n"
    "范围口径（v2 里程碑 4 名单章节定位，plan §12.2）：只列出“参与单位/参会机构/"
    "投资者名单/附件清单/参会人员名单”等名单章节中明确列名的参与机构；正文叙述中提到"
    "的机构（如“公司参与XX策略会”“接待了XX”“走访XX”）、平台/主办方名称（如全景网、"
    "互动易）、内部部门（如证券事务部）以及“共同基金/机构投资者/个人投资者”等泛指不列入。\n"
    "同时给出 named_institution_types（字符串数组，与 named_institutions 一一对应）："
    "每项为 \"research\"（券商/公募基金/保险/资管/私募/信托/境外投资机构等研究机构）"
    "或 \"other\"（产业公司、银行总行部门、律所、咨询、媒体等非研究机构）。\n"
    "每项输出 {\"institution_id\": str, \"entity_ok\": bool, \"group_ok\": bool, "
    "\"rationale\": str}，rationale 不超过 50 字。"
)


INSTITUTION_ITEMS_SYSTEM_PROMPT = (
    "你是 A 股调研活动机构实体的评估标注器，只输出合法 JSON 对象，不要输出任何其他文字。\n"
    "输出结构固定为 {\"items\": [每个参与者一个对象]}，items 必须覆盖输入 participants 中"
    "全部 institution_id，一个都不能少。本轮只判断参与者实体，不要输出 "
    "named_institutions 或 named_institution_types。\n"
    "对每个参与者判断：\n"
    "- entity_ok（布尔）：canonical_name 是否是一个真实机构且名称归一正确；"
    "若明显不是机构（媒体、个人、非金融机构被误认）或名称/类型明显错误则为 false。\n"
    "- group_ok（布尔）：group_id 的集团归并是否合理（同一集团/同一法人合并为同一"
    " group 为 true；把不同机构错误归并、或同名不同机构为 false）。\n"
    "每项输出 {\"institution_id\": str, \"entity_ok\": bool, \"group_ok\": bool, "
    "\"rationale\": str}，rationale 不超过 50 字。"
)


INSTITUTION_NAMED_SYSTEM_PROMPT = (
    "你是 A 股调研活动机构实体的评估标注器，只输出合法 JSON 对象，不要输出任何其他文字。\n"
    "输出结构固定为 {\"named_institutions\": [...]}。本轮只列出原文明确列名的参与机构"
    "名称（按原文原样、去重），不要输出 items 或 named_institution_types。\n"
    "范围口径（v2 里程碑 4 名单章节定位，plan §12.2）：只列出“参与单位/参会机构/"
    "投资者名单/附件清单/参会人员名单”等名单章节中明确列名的参与机构；排除上市公司自身、"
    "个人、媒体、描述性短语、“约N家机构”等模糊总数，以及正文叙述中提到（“参与XX策略会”"
    "“接待了XX”）与平台/主办方名称。若正文不可用或没有列名机构则输出 []。\n"
    "每项为字符串，非空。"
)


def _institution_types_prompt(record: dict[str, Any], named: list[str]) -> str:
    """Prompt that only asks for the parallel research/other type array."""

    source = record.get("source") or {}
    body = _truncate(
        str(record.get("body_text") or source.get("body_text") or ""), 12000
    )
    lines = [
        "以下是调研活动正文与已列名机构（JSON）。请输出 "
        "named_institution_types：与 named_institutions 一一对应的字符串数组，"
        "每项为 \"research\"（券商/公募基金/保险/资管/私募/信托/境外投资机构等"
        "研究机构）或 \"other\"（产业公司、银行总行部门、律所、咨询、媒体等）。\n"
    ]
    lines.append(
        json.dumps(
            {
                "activity_id": record["activity_id"],
                "stock_code": record["stock_code"],
                "stock_name": record.get("stock_name", ""),
                "body_text": body,
                "named_institutions": named,
            },
            ensure_ascii=False,
        )
    )
    return "\n".join(lines)


INSTITUTION_TYPES_SYSTEM_PROMPT = (
    "你是 A 股调研活动机构实体的评估标注器，只输出合法 JSON 对象，不要输出任何其他文字。\n"
    "输出结构固定为 {\"named_institution_types\": [...]}，不要输出任何其他字段。\n"
)


def _event_batch_prompt(batch: list[dict[str, Any]]) -> str:
    lines = ["以下是事件列表（JSON 数组），请按每项 event_id 输出标签："]
    lines.append("[")
    for event in batch:
        representative = event.get("representative") or {}
        evidence = event.get("evidence") or []
        evidence_text = "; ".join(
            _truncate(item.get("excerpt") or "", 240)
            for item in evidence[:2]
            if (item.get("excerpt") or "").strip()
        )
        lines.append(
            json.dumps(
                {
                    "event_id": event["event_id"],
                    "stock_codes": event.get("stock_codes", []),
                    "canonical_title": _truncate(event.get("canonical_title") or "", 120),
                    "representative_title": _truncate(
                        representative.get("title") or "", 120
                    ),
                    "parse_status": representative.get("parse_status") or "",
                    "document_count": event.get("document_count", 1),
                    "historical_similar_event_id": event.get(
                        "historical_similar_event_id"
                    ),
                    "stratum": event.get("stratum", ""),
                    "layout": event.get("layout", ""),
                    "engine": event.get("engine"),
                    "evidence_excerpts": evidence_text,
                },
                ensure_ascii=False,
            )
        )
    lines.append("]")
    return "\n".join(lines)


def _board_prompt(rows: list[dict[str, Any]]) -> str:
    lines = ["以下是研究榜全部行（JSON 数组），请按 event_id+stock_code 输出判断："]
    lines.append("[")
    for row in rows:
        lines.append(
            json.dumps(
                {
                    "rank": row["rank"],
                    "event_id": row["event_id"],
                    "stock_code": row["stock_code"],
                    "board": row["board"],
                    "score": row["score"],
                    "materiality_level": row["materiality_level"],
                    "certainty": row["certainty"],
                    "event_type": row["event_type"],
                    "extractor_kind": row["extractor_kind"],
                },
                ensure_ascii=False,
            )
        )
    lines.append("]")
    return "\n".join(lines)


def _institution_record_prompt(
    record: dict[str, Any], participants: list[dict[str, Any]]
) -> str:
    source = record.get("source") or {}
    body = _truncate(
        str(record.get("body_text") or source.get("body_text") or ""), 12000
    )
    lines = ["以下是调研活动与参与者列表（JSON），请对 participants 中每个 institution_id 输出判断："]
    lines.append(
        json.dumps(
            {
                "activity_id": record["activity_id"],
                "stock_code": record["stock_code"],
                "stock_name": record.get("stock_name", ""),
                "activity_type": record.get("activity_type", ""),
                "activity_dates": record.get("activity_dates", []),
                "date_precision": record.get("date_precision", ""),
                "question_count": record.get("question_count", 0),
                "source_title": _truncate(source.get("title") or "", 120),
                "source_parse_status": source.get("parse_status") or "",
                "body_text": body,
                "participants": [
                    {
                        "institution_id": p["institution_id"],
                        "canonical_name": p["canonical_name"],
                        "group_id": p["group_id"],
                        "institution_type": p["institution_type"],
                        "verification_status": p["verification_status"],
                        "analyst_name": p.get("analyst_name"),
                    }
                    for p in participants
                ],
            },
            ensure_ascii=False,
        )
    )
    return "\n".join(lines)


def _get_named_institutions(output: dict[str, Any]) -> list[str]:
    """Strictly validate the record-level ``named_institutions`` label."""

    value = output.get("named_institutions")
    if value is None:
        raise ValueError("field 'named_institutions' is missing")
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(
            "field 'named_institutions' must be an array of non-empty strings"
        )
    return [str(item).strip() for item in value]


def _get_named_institution_types(
    output: dict[str, Any],
    named: list[str],
) -> list[str] | None:
    """Strictly validate the record-level ``named_institution_types`` label.

    A parallel array (same order as ``named_institutions``) of
    ``"research"`` / ``"other"``; missing or malformed values return ``None``
    so the scorer falls back to the conservative all-named denominator.
    """

    value = output.get("named_institution_types")
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != len(named):
        raise ValueError(
            "field 'named_institution_types' must be a parallel array of "
            "the same length as named_institutions"
        )
    result: list[str] = []
    for item in value:
        if item not in ("research", "other"):
            raise ValueError(
                "field 'named_institution_types' entries must be "
                "'research' or 'other'"
            )
        result.append(str(item))
    return result


def _find_results(
    output: dict[str, Any],
    *,
    key_field: str,
    expected_keys: set[str],
) -> dict[str, dict[str, Any]]:
    for field in ("items", "results", "labels", "judgments"):
        if field in output:
            rows = _split_array(output[field], field)
            break
    else:
        # The model sometimes wraps the array directly in the object, or
        # returns a dict keyed by the id field itself.
        rows = None
        arrays = [
            value
            for value in output.values()
            if isinstance(value, list) and all(isinstance(i, dict) for i in value)
        ]
        if len(arrays) == 1:
            rows = arrays[0]
        else:
            keyed = [
                {"_key": str(key), **value}
                for key, value in output.items()
                if isinstance(value, dict)
            ]
            if keyed:
                rows = keyed
            elif len(expected_keys) == 1 and key_field in output:
                # Single-item responses are sometimes returned as a flat object.
                rows = [output]
        if rows is None:
            raise ValueError("no items/results array found in model output")
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(key_field) or row.get("_key") or "")
        if key and key in expected_keys:
            by_key[key] = row
    return by_key


def annotate_event_batch(
    batch: list[dict[str, Any]],
    *,
    call: Callable[..., dict[str, Any]],
    **kwargs: Any,
) -> dict[str, dict[str, Any]]:
    """Return {event_id: {label, must_hit_candidate, rationale}} or {}."""

    output = call(
        system=EVENT_SYSTEM_PROMPT,
        user=_event_batch_prompt(batch),
        **kwargs,
    )
    expected = {event["event_id"] for event in batch}
    by_key = _find_results(output, key_field="event_id", expected_keys=expected)
    result: dict[str, dict[str, Any]] = {}
    for event_id, row in by_key.items():
        label = _get_label(row, "label")
        must_hit = _get_bool(row, "must_hit_candidate")
        error_types = _get_error_types(row)
        rationale = _truncate(str(row.get("rationale") or ""), 60)
        result[event_id] = {
            "label": label,
            "must_hit_candidate": must_hit,
            "error_types": error_types,
            "rationale": rationale,
        }
    return result


def annotate_board(
    rows: list[dict[str, Any]],
    *,
    call: Callable[..., dict[str, Any]],
    **kwargs: Any,
) -> dict[tuple[str, str], dict[str, Any]]:
    output = call(system=BOARD_SYSTEM_PROMPT, user=_board_prompt(rows), **kwargs)
    expected = {(row["event_id"], row["stock_code"]) for row in rows}
    expected_ids = {row["event_id"] for row in rows}
    by_key = _find_results(output, key_field="event_id", expected_keys=expected_ids)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        event_id = row["event_id"]
        stock_code = row["stock_code"]
        item = by_key.get(event_id)
        if item is None:
            continue
        relevant = _get_bool(item, "relevant")
        duplicate = _get_bool(item, "duplicate")
        result[(event_id, stock_code)] = {
            "relevant": relevant,
            "duplicate": duplicate,
            "rationale": _truncate(str(item.get("rationale") or ""), 60),
        }
    return result


def annotate_institution_record(
    record: dict[str, Any],
    *,
    call: Callable[..., dict[str, Any]],
    **kwargs: Any,
) -> tuple[dict[str, dict[str, Any]], list[str] | None, list[str] | None]:
    participants = [p for p in record.get("participants") or []]
    expected = {p["institution_id"] for p in participants}
    result: dict[str, dict[str, Any]] = {}
    named_institutions: list[str] | None = None
    named_institution_types: list[str] | None = None
    # Split large participant lists into chunks to avoid output truncation
    # (the record-level named_institution_types makes each chunk heavier).
    chunks = (
        [participants[start : start + 6] for start in range(0, len(participants), 6)]
        if participants
        else [[]]
    )
    for chunk in chunks:
        chunk_ids = {p["institution_id"] for p in chunk}
        output = call(
            system=INSTITUTION_ITEMS_SYSTEM_PROMPT,
            user=_institution_record_prompt(record, chunk),
            **kwargs,
        )
        by_key = _find_results(
            output, key_field="institution_id", expected_keys=chunk_ids
        )
        for institution_id, item in by_key.items():
            result[institution_id] = {
                "entity_ok": _get_bool(item, "entity_ok"),
                "group_ok": _get_bool(item, "group_ok"),
                "rationale": _truncate(str(item.get("rationale") or ""), 50),
            }
    # 名单与类型独立请求（大名单记录避免输出超长数组截断）：
    # named（仅名字）→ types（与 named 同序的 research/other）。
    if named_institutions is None:
        try:
            output = call(
                system=INSTITUTION_NAMED_SYSTEM_PROMPT,
                user=_institution_record_prompt(record, []),
                **kwargs,
            )
            named_institutions = _get_named_institutions(output)
        except ValueError:
            named_institutions = None
    if named_institutions is not None and named_institution_types is None:
        try:
            output = call(
                system=INSTITUTION_TYPES_SYSTEM_PROMPT,
                user=_institution_types_prompt(record, named_institutions),
                **kwargs,
            )
            named_institution_types = _get_named_institution_types(
                output, named_institutions
            )
        except ValueError:
            named_institution_types = None
    return result, named_institutions, named_institution_types


def _api_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "api_key": _read_key(args.key_env),
        "base_url": args.base_url,
        "model": args.model,
        "timeout": args.timeout,
    }


def run_short_term(
    data: dict[str, Any],
    *,
    call: Callable[..., dict[str, Any]],
    batch_size: int,
    max_workers: int,
    **kwargs: Any,
) -> dict[str, Any]:
    board = list(data.get("board") or [])
    events = list(data.get("events") or [])
    board_needs = [
        row
        for row in board
        if row.get("relevant") is None or row.get("duplicate") is None
    ]
    event_needs = [event for event in events if event.get("label") is None]

    board_results: dict[tuple[str, str], dict[str, Any]] = {}
    board_errors: list[str] = []
    if board_needs:
        try:
            board_results = annotate_board(board_needs, call=call, **kwargs)
        except Exception as error:  # noqa: BLE001 - per-item degradation
            board_errors.append(f"board: {error}")

    event_results: dict[str, dict[str, Any]] = {}
    event_errors: list[str] = []
    if event_needs:
        batches = [
            event_needs[i : i + batch_size]
            for i in range(0, len(event_needs), batch_size)
        ]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    annotate_event_batch, batch, call=call, **kwargs
                ): batch
                for batch in batches
            }
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    event_results.update(future.result())
                except Exception as error:  # noqa: BLE001
                    event_errors.append(
                        f"events batch starting with "
                        f"{batch[0]['event_id']}: {error}"
                    )

    events_by_id = {event["event_id"]: event for event in events}
    board_rows_by_key = {(row["event_id"], row["stock_code"]): row for row in board}
    needs_human: list[str] = []
    for event in events:
        if event["label"] is None and event["event_id"] in event_results:
            event["label"] = event_results[event["event_id"]]["label"]
            event["must_hit_candidate"] = event_results[event["event_id"]][
                "must_hit_candidate"
            ]
            event["error_types"] = event_results[event["event_id"]]["error_types"]
            event["llm_rationale"] = event_results[event["event_id"]]["rationale"]
        elif event["label"] is None:
            needs_human.append(f"event:{event['event_id']}")
    for row in board:
        if row.get("relevant") is None or row.get("duplicate") is None:
            key = (row["event_id"], row["stock_code"])
            if key in board_results:
                row["relevant"] = board_results[key]["relevant"]
                row["duplicate"] = board_results[key]["duplicate"]
                row["llm_rationale"] = board_results[key]["rationale"]
            else:
                needs_human.append(
                    f"board:{row['event_id']}/{row['stock_code']}"
                )

    data["annotation"] = {
        "mode": "llm",
        "model": kwargs.get("model", ""),
        "annotated_at": datetime.now(SHANGHAI_TZ).isoformat(),
        "needs_human": sorted(needs_human),
        "errors": event_errors + board_errors,
    }
    return data


def run_institution(
    data: dict[str, Any],
    *,
    call: Callable[..., dict[str, Any]],
    max_workers: int,
    **kwargs: Any,
) -> dict[str, Any]:
    records = list(data.get("records") or [])
    needs: list[dict[str, Any]] = [
        record
        for record in records
        if record.get("named_institutions") is None
        or any(
            p.get("entity_ok") is None or p.get("group_ok") is None
            for p in record.get("participants") or []
        )
    ]
    results: dict[str, dict[str, dict[str, Any]]] = {}
    named_results: dict[str, list[str]] = {}
    named_type_results: dict[str, list[str]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                annotate_institution_record, record, call=call, **kwargs
            ): record
            for record in needs
        }
        for future in as_completed(futures):
            record = futures[future]
            try:
                participant_labels, named, named_types = future.result()
                results[record["activity_id"]] = participant_labels
                if named is not None:
                    named_results[record["activity_id"]] = named
                if named_types is not None:
                    named_type_results[record["activity_id"]] = named_types
            except Exception as error:  # noqa: BLE001
                errors.append(f"{record['activity_id']}: {error}")

    needs_human: list[str] = []
    for record in records:
        if record.get("named_institutions") is None:
            named = named_results.get(record["activity_id"])
            if named is None:
                needs_human.append(f"named:{record['activity_id']}")
            else:
                record["named_institutions"] = named
                if named_type_results.get(record["activity_id"]) is not None:
                    record["named_institution_types"] = (
                        named_type_results[record["activity_id"]]
                    )
        for participant in record.get("participants") or []:
            if (
                participant.get("entity_ok") is not None
                and participant.get("group_ok") is not None
            ):
                continue
            item = (
                results.get(record["activity_id"]) or {}
            ).get(participant["institution_id"])
            if item is None:
                needs_human.append(
                    f"participant:{record['activity_id']}/"
                    f"{participant['institution_id']}"
                )
                continue
            participant["entity_ok"] = item["entity_ok"]
            participant["group_ok"] = item["group_ok"]
            participant["llm_rationale"] = item["rationale"]

    data["annotation"] = {
        "mode": "llm",
        "model": kwargs.get("model", ""),
        "annotated_at": datetime.now(SHANGHAI_TZ).isoformat(),
        "needs_human": sorted(needs_human),
        "errors": errors,
    }
    return data


def _discovery_item_prompt(item: dict[str, Any]) -> str:
    lines = [
        "以下是一条上市公司公开披露/列表项（JSON）。请判断它是否应进入"
        "“待核验事件”发现层：只要属于公开披露或投资者关系活动记录，就应进入"
        "（发现层是防漏层，不是利好判断）。只输出 label："
        "should_discover 或 not_discover，并给出简短 rationale。"
    ]
    lines.append(
        json.dumps(
            {
                "document_id": item["document_id"],
                "stock_code": item.get("stock_code", ""),
                "stock_name": item.get("stock_name", ""),
                "title": _truncate(item.get("title") or "", 200),
                "published_at": item.get("published_at", ""),
                "parse_status": item.get("parse_status", ""),
                "discovery_type": item.get("discovery_type"),
                "queue_status": item.get("queue_status"),
                "stratum": item.get("stratum", ""),
                "promoted_to_board": item.get("promoted_to_board", False),
            },
            ensure_ascii=False,
        )
    )
    return "\n".join(lines)


DISCOVERY_SYSTEM_PROMPT = (
    "你是 A 股公开披露“待核验事件发现层”的评估标注器，只输出合法 JSON 对象，"
    "不要输出任何其他文字。输出结构固定为 {\"label\": ..., \"rationale\": ...}。"
    "label 二选一：\"should_discover\"（该公开列表项应进入发现层）或 "
    "\"not_discover\"（不属于公开披露/投资者关系活动记录）。"
    "发现层是防漏层，不是利好判断，不得因标题含风险词而判 not_discover。"
)


def annotate_discovery_item(
    item: dict[str, Any],
    *,
    call: Callable[..., dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Label one discovery item; strict JSON validation, no guessing."""

    output = call(
        system=DISCOVERY_SYSTEM_PROMPT,
        user=_discovery_item_prompt(item),
        **kwargs,
    )
    label = output.get("label")
    if not isinstance(label, str) or label not in {
        "should_discover",
        "not_discover",
    }:
        raise ValueError(f"label={label!r} is not a valid discovery label")
    return {
        "label": label,
        "rationale": _truncate(str(output.get("rationale") or ""), 240),
    }


def run_discovery(
    data: dict[str, Any],
    *,
    call: Callable[..., dict[str, Any]],
    max_workers: int,
    **kwargs: Any,
) -> dict[str, Any]:
    items = list(data.get("items") or [])
    needs = [item for item in items if item.get("label") is None]
    results: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(annotate_discovery_item, item, call=call, **kwargs): item
            for item in needs
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                results[item["document_id"]] = future.result()
            except Exception as error:  # noqa: BLE001
                errors.append(f"{item['document_id']}: {error}")
    needs_human: list[str] = []
    for item in items:
        if item.get("label") is not None:
            continue
        result = results.get(item["document_id"])
        if result is None:
            needs_human.append(item["document_id"])
            continue
        item["label"] = result["label"]
        item["llm_rationale"] = result["rationale"]
    data["annotation"] = {
        "mode": "llm",
        "model": kwargs.get("model", ""),
        "annotated_at": datetime.now(SHANGHAI_TZ).isoformat(),
        "needs_human": sorted(needs_human),
        "errors": errors,
    }
    return data


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--short-term", type=Path)
    parser.add_argument("--institution", type=Path)
    parser.add_argument("--discovery", type=Path)
    parser.add_argument("--out", type=Path, default=Path("evaluation"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--key-env", default=DEFAULT_KEY_ENV)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--event-batch-size", type=int, default=20)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args(argv)
    if not args.short_term and not args.institution and not args.discovery:
        parser.error("provide --short-term and/or --institution and/or --discovery")

    kwargs = _api_kwargs(args)
    kwargs["max_workers"] = args.max_workers
    if args.short_term:
        data = json.loads(args.short_term.read_text(encoding="utf-8"))
        run_short_term(
            data,
            call=chat_json,
            batch_size=args.event_batch_size,
            **kwargs,
        )
        out_path = args.out / f"{args.short_term.stem}.llm.json"
        _save(out_path, data)
        annotation = data["annotation"]
        print(
            f"short-term: events labeled "
            f"{sum(1 for e in data['events'] if e.get('label') is not None)}/"
            f"{len(data['events'])}, board labeled "
            f"{sum(1 for r in data['board'] if r.get('relevant') is not None)}/"
            f"{len(data['board'])}, needs_human "
            f"{len(annotation['needs_human'])}, errors {len(annotation['errors'])}"
            f" -> {out_path}"
        )
        if annotation["needs_human"]:
            print("  needs_human:", annotation["needs_human"][:10])
    if args.institution:
        data = json.loads(args.institution.read_text(encoding="utf-8"))
        run_institution(data, call=chat_json, **kwargs)
        out_path = args.out / f"{args.institution.stem}.llm.json"
        _save(out_path, data)
        annotation = data["annotation"]
        total = sum(len(r.get("participants") or []) for r in data["records"])
        labeled = sum(
            1
            for r in data["records"]
            for p in r.get("participants") or []
            if p.get("entity_ok") is not None and p.get("group_ok") is not None
        )
        print(
            f"institution: participants labeled {labeled}/{total}, "
            f"needs_human {len(annotation['needs_human'])}, "
            f"errors {len(annotation['errors'])} -> {out_path}"
        )
        if annotation["needs_human"]:
            print("  needs_human:", annotation["needs_human"][:10])
    if args.discovery:
        data = json.loads(args.discovery.read_text(encoding="utf-8"))
        run_discovery(
            data,
            call=chat_json,
            **kwargs,
        )
        out_path = args.out / f"{args.discovery.stem}.llm.json"
        _save(out_path, data)
        annotation = data["annotation"]
        labeled = sum(1 for item in data["items"] if item.get("label") is not None)
        print(
            f"discovery: items labeled {labeled}/{len(data['items'])}, "
            f"needs_human {len(annotation['needs_human'])}, "
            f"errors {len(annotation['errors'])} -> {out_path}"
        )
        if annotation["needs_human"]:
            print("  needs_human:", annotation["needs_human"][:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
