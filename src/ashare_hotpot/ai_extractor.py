from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import AppSettings
from .extraction import (
    CERTAINTY_STAGES,
    COUNTER_EVIDENCE_KINDS,
    DIRECTIONS,
    EVENT_TYPES,
    UNSUPPORTED_EVENT_TYPE,
    RuleBasedSignalExtractor,
)
from .models import (
    EventCluster,
    EventExtraction,
    EvidenceRef,
    SourceDocument,
)


logger = logging.getLogger(__name__)

AI_EXTRACTOR_VERSION = "ai-v1"
AI_FALLBACK_VERSION = "rules-fallback-v1"
AI_PROMPT_SCHEMA_VERSION = "ai-v1"
AI_REQUEST_TIMEOUT_SECONDS = 30.0
AI_MAX_RETRIES = 1
EXCERPT_LIMIT = 240


# ---------------------------------------------------------------------------
# Windows DPAPI credential storage (never written to SQLite or logs)
# ---------------------------------------------------------------------------


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.c_void_p),
    ]


def dpapi_protect(plaintext: str) -> bytes:
    """Encrypt ``plaintext`` with Windows DPAPI for the current user."""

    if sys.platform != "win32":
        raise RuntimeError("DPAPI 仅支持 Windows")
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    data = plaintext.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(data)
    blob_in = _DataBlob(len(data), ctypes.addressof(buffer))
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise RuntimeError("DPAPI 加密失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def dpapi_unprotect(blob: bytes) -> str:
    """Decrypt a Windows DPAPI blob for the current user."""

    if sys.platform != "win32":
        raise RuntimeError("DPAPI 仅支持 Windows")
    buffer = ctypes.create_string_buffer(blob)
    blob_in = _DataBlob(len(blob), ctypes.addressof(buffer))
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise RuntimeError("DPAPI 解密失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-16-le")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


class AiCredentialStore:
    """DPAPI-encrypted API key stored in its own file, never in SQLite."""

    def __init__(self, app_root: Path) -> None:
        self.path = app_root / "ai_credentials.bin"

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            return dpapi_unprotect(self.path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - corrupt blob degrades to None
            logger.warning("AI credential load failed: %s", exc)
            return None

    def save(self, api_key: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(dpapi_protect(api_key))

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


# ---------------------------------------------------------------------------
# OpenAI-compatible client
# ---------------------------------------------------------------------------


class OpenAiClient:
    """Minimal OpenAI-compatible chat-completions client with one retry."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = AI_REQUEST_TIMEOUT_SECONDS,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._http_client = http_client

    def chat_completion(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(AI_MAX_RETRIES + 1):
            if attempt:
                time.sleep(1.0 * attempt)
            try:
                if self._http_client is not None:
                    response = self._http_client.post(
                        url, json=payload, headers=headers, timeout=self.timeout
                    )
                else:
                    with httpx.Client(timeout=self.timeout) as client:
                        response = client.post(
                            url, json=payload, headers=headers
                        )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"AI 请求失败: {exc}") from exc
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt < AI_MAX_RETRIES:
                    continue
                raise RuntimeError(f"AI 请求限流/服务端错误: HTTP {response.status_code}")
            if response.status_code != 200:
                raise RuntimeError(f"AI 请求失败: HTTP {response.status_code}")
            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("AI 响应缺少 choices/message/content") from exc
            return _parse_strict_json(content)
        raise RuntimeError("AI 请求重试耗尽")


def _parse_strict_json(content: str) -> dict[str, Any]:
    """Accept a single strict JSON object; reject markdown wrapping."""

    text = content.strip()
    if text.startswith("```"):
        raise RuntimeError("AI 响应被 Markdown 包裹")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AI 响应非法 JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("AI 响应不是 JSON 对象")
    return payload


# ---------------------------------------------------------------------------
# Validation of AI extractions (strict; failures degrade to rules)
# ---------------------------------------------------------------------------


def validate_extraction_payload(
    payload: dict[str, Any],
    *,
    cluster: EventCluster,
    stock_code: str,
) -> EventExtraction | None:
    event_type = str(payload.get("event_type") or "")
    if event_type not in EVENT_TYPES and event_type != UNSUPPORTED_EVENT_TYPE:
        return None
    direction = str(payload.get("direction") or "")
    if direction not in DIRECTIONS:
        return None
    certainty = payload.get("certainty")
    if not isinstance(certainty, (int, float)) or not 0.0 <= float(certainty) <= 1.0:
        return None
    materiality = payload.get("materiality_level")
    if not isinstance(materiality, int) or not 0 <= materiality <= 4:
        return None
    for field in ("unexpectedness", "novelty"):
        value = payload.get(field)
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 100.0:
            return None
    metrics_raw = payload.get("metrics")
    if not isinstance(metrics_raw, list):
        return None
    metrics: list[dict[str, object]] = []
    for item in metrics_raw:
        if not isinstance(item, dict):
            return None
        if not all(key in item for key in ("name", "value", "unit", "comparison_basis", "comparison_ratio", "evidence_id")):
            return None
        metrics.append({str(key): value for key, value in item.items()})
    counter_raw = payload.get("counter_evidence")
    if not isinstance(counter_raw, list):
        return None
    counter: list[dict[str, object]] = []
    for item in counter_raw:
        if not isinstance(item, dict):
            return None
        if str(item.get("kind") or "") not in COUNTER_EVIDENCE_KINDS:
            return None
        counter.append({str(key): value for key, value in item.items()})
    evidence_ids = payload.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not all(
        isinstance(item, str) and item for item in evidence_ids
    ):
        return None
    no_valid_signal = bool(payload.get("no_valid_signal", False))
    mechanism = payload.get("positive_mechanism")
    if mechanism is not None and not isinstance(mechanism, str):
        return None
    return EventExtraction(
        event_id=cluster.event_id,
        stock_code=stock_code,
        event_type=event_type,
        direction=direction,
        positive_mechanism=mechanism,
        metrics=tuple(metrics),
        certainty_stage=str(payload.get("certainty_stage") or _stage_for(certainty)),
        certainty=float(certainty),
        novelty=float(payload["novelty"]),
        unexpectedness=float(payload["unexpectedness"]),
        materiality_level=materiality,
        counter_evidence=tuple(counter),
        evidence_ids=tuple(evidence_ids),
        no_valid_signal=no_valid_signal,
        extractor_kind="llm",
        extractor_version=AI_EXTRACTOR_VERSION,
    )


def _stage_for(certainty: float) -> str:
    best = "framework"
    best_diff = 1.0
    for stage, value in CERTAINTY_STAGES.items():
        diff = abs(value - certainty)
        if diff < best_diff:
            best = stage
            best_diff = diff
    return best


# ---------------------------------------------------------------------------
# OpenAI-compatible extractor with cache
# ---------------------------------------------------------------------------


class SignalExtractor(Protocol):
    def extract(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> EventExtraction: ...

    def extract_for_stock(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
        stock_code: str,
    ) -> EventExtraction: ...

    def extract_all(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> tuple[EventExtraction, ...]: ...


_SYSTEM_PROMPT = (
    "你是 A 股公告与新闻的结构化抽取器。只输出一个严格 JSON 对象，"
    "不得使用 Markdown 代码块。事件类型只能从以下枚举中选择："
    + "、".join(EVENT_TYPES)
    + " 或 unsupported_event_type。方向只能是 positive/negative/neutral。"
    "certainty 为 0-1 小数；materiality_level 为 0-4 整数；"
    "unexpectedness/novelty 为 0-100 数值；metrics 每一项必须包含 "
    "name/value/unit/comparison_basis/comparison_ratio/evidence_id；"
    "counter_evidence 每一项必须包含 kind/reason/evidence_id，"
    "kind 只能是 none/partial/high_uncertainty/title_body_conflict。"
    "没有正文依据的字段必须为空或 null，不得编造金额、客户或状态。"
)


class OpenAICompatibleSignalExtractor:
    """Optional AI enhancement; outputs go through strict validation."""

    def __init__(
        self,
        settings: AppSettings,
        storage: object,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.http_client = http_client
        self._client: OpenAiClient | None = None

    def _ensure_client(self) -> OpenAiClient:
        if self._client is None:
            store = AiCredentialStore(self.settings.app_root)
            api_key = store.load()
            if not api_key or not self.settings.ai_base_url or not self.settings.ai_model:
                raise RuntimeError("AI 未配置")
            self._client = OpenAiClient(
                base_url=self.settings.ai_base_url,
                model=self.settings.ai_model,
                api_key=api_key,
                timeout=self.settings.ai_timeout_seconds,
                http_client=self.http_client,
            )
        return self._client

    def extract(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> EventExtraction:
        stock_code = cluster.stock_codes[0] if cluster.stock_codes else ""
        return self._extract_for_stock(cluster, documents, stock_code)

    def extract_for_stock(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
        stock_code: str,
    ) -> EventExtraction:
        return self._extract_for_stock(cluster, documents, stock_code)

    def extract_all(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> tuple[EventExtraction, ...]:
        extractions: list[EventExtraction] = []
        for stock_code in sorted(cluster.stock_codes):
            extractions.append(
                self._extract_for_stock(cluster, documents, stock_code)
            )
        return tuple(extractions)

    def _extract_for_stock(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
        stock_code: str,
    ) -> EventExtraction:
        representative = self._representative_document(cluster, documents)
        cache = self._cached_response(representative)
        if cache is None:
            prompt = self._build_prompt(cluster, representative, stock_code)
            client = self._ensure_client()
            payload = client.chat_completion(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )
            self._save_cache(representative, payload)
        else:
            payload = cache
        extraction = validate_extraction_payload(
            payload,
            cluster=cluster,
            stock_code=stock_code,
        )
        if extraction is None:
            raise RuntimeError("AI 输出未通过结构校验")
        return self._persist_llm_evidence(extraction, documents)

    def _representative_document(
        self, cluster: EventCluster, documents: tuple[SourceDocument, ...]
    ) -> SourceDocument:
        for document in documents:
            if document.document_id == cluster.representative_document_id:
                return document
        parsed = [
            doc
            for doc in documents
            if (doc.body_text or "").strip() and doc.parse_status == "parsed"
        ]
        return parsed[0] if parsed else documents[0]

    @staticmethod
    def _build_prompt(
        cluster: EventCluster,
        document: SourceDocument,
        stock_code: str,
    ) -> str:
        body = (document.body_text or "")[:4000]
        return (
            f"股票代码：{stock_code}\n"
            f"标题：{document.title}\n"
            f"正文：\n{body}"
        )

    def _cache_key(self, document: SourceDocument) -> str:
        return (
            f"{document.document_id}:{document.content_hash}:"
            f"{self.settings.ai_model}:{self.settings.ai_prompt_schema_version}"
        )

    def _cached_response(
        self, document: SourceDocument
    ) -> dict[str, Any] | None:
        payload = self.storage.get_llm_extraction_cache(
            document.document_id,
            self.settings.ai_model,
            self.settings.ai_prompt_schema_version,
        )
        if payload is None:
            return None
        if str(payload.get("content_hash")) != document.content_hash:
            return None
        return dict(payload.get("extraction") or {})

    def _save_cache(
        self, document: SourceDocument, payload: dict[str, Any]
    ) -> None:
        self.storage.save_llm_extraction_cache(
            document.document_id,
            self.settings.ai_model,
            self.settings.ai_prompt_schema_version,
            {"content_hash": document.content_hash, "extraction": payload},
            datetime.now(),
        )

    def _persist_llm_evidence(
        self,
        extraction: EventExtraction,
        documents: tuple[SourceDocument, ...],
    ) -> EventExtraction:
        by_id = {doc.document_id: doc for doc in documents}
        remap: dict[str, str] = {}
        for evidence_id in extraction.evidence_ids:
            if evidence_id.startswith("llm:"):
                remap[evidence_id] = evidence_id
                continue
            # Evidence ids returned by the model must map to a known document;
            # otherwise the extraction fails closed.
            document_id = evidence_id.split(":", 1)[0]
            document = by_id.get(document_id)
            if document is None:
                raise RuntimeError("AI 证据引用了未知文档")
            llm_evidence_id = "llm:" + hashlib.sha1(
                f"{document_id}:{extraction.event_id}:{extraction.stock_code}".encode("utf-8")
            ).hexdigest()[:16]
            self.storage.upsert_evidence_ref(
                EvidenceRef(
                    evidence_id=llm_evidence_id,
                    document_id=document.document_id,
                    start_offset=None,
                    end_offset=None,
                    excerpt=(
                        re.sub(r"\s+", " ", str(evidence_id))[:EXCERPT_LIMIT]
                    ),
                    source_url=document.source_url or document.document_url or "",
                )
            )
            remap[evidence_id] = llm_evidence_id
        if not remap:
            return extraction
        from dataclasses import replace

        metrics = tuple(
            {
                str(key): (
                    remap.get(str(value), value)
                    if key == "evidence_id"
                    else value
                )
                for key, value in metric.items()
            }
            for metric in extraction.metrics
        )
        counter = tuple(
            {
                str(key): (
                    remap.get(str(value), value)
                    if key == "evidence_id"
                    else value
                )
                for key, value in item.items()
            }
            for item in extraction.counter_evidence
        )
        return replace(
            extraction,
            evidence_ids=tuple(remap.get(item, item) for item in extraction.evidence_ids),
            metrics=metrics,
            counter_evidence=counter,
        )


class FallbackSignalExtractor:
    """Primary AI extractor with rules fallback per stock."""

    def __init__(
        self,
        primary: SignalExtractor | None,
        fallback: RuleBasedSignalExtractor,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def extract(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> EventExtraction:
        stock_code = cluster.stock_codes[0] if cluster.stock_codes else ""
        if self.primary is None:
            return self.fallback.extract(cluster, documents)
        try:
            return self.primary.extract_for_stock(
                cluster, documents, stock_code
            )
        except Exception as exc:  # noqa: BLE001 - degrade per event
            logger.warning("AI extraction failed, falling back: %s", exc)
            result = self.fallback.extract_for_stock(cluster, documents, stock_code)
            if result is None:
                return self.fallback._no_signal(cluster, stock_code, "AI 降级且规则未识别")
            from dataclasses import replace

            return replace(
                result,
                extractor_kind="rules_fallback",
                extractor_version=AI_FALLBACK_VERSION,
            )

    def extract_all(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> tuple[EventExtraction, ...]:
        if self.primary is None:
            return self.fallback.extract_all(cluster, documents)
        extractions: list[EventExtraction] = []
        for stock_code in sorted(cluster.stock_codes):
            try:
                extraction = self.primary.extract_for_stock(
                    cluster, documents, stock_code
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "AI extraction failed for %s/%s, falling back: %s",
                    cluster.event_id,
                    stock_code,
                    exc,
                )
                result = self.fallback.extract_for_stock(
                    cluster, documents, stock_code
                )
                if result is None:
                    continue
                from dataclasses import replace

                extraction = replace(
                    result,
                    extractor_kind="rules_fallback",
                    extractor_version=AI_FALLBACK_VERSION,
                )
            extractions.append(extraction)
        return tuple(extractions)


def build_signal_extractor(settings: AppSettings, storage: object) -> SignalExtractor:
    """Rules by default; optional AI with per-stock rules fallback."""

    rules = RuleBasedSignalExtractor(storage)
    if not settings.ai_enabled:
        return rules
    store = AiCredentialStore(settings.app_root)
    if not store.load() or not settings.ai_base_url or not settings.ai_model:
        return rules
    return FallbackSignalExtractor(
        OpenAICompatibleSignalExtractor(settings, storage),
        rules,
    )
