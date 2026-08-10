from __future__ import annotations

import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from ashare_hotpot.ai_extractor import (
    AiCredentialStore,
    FallbackSignalExtractor,
    OpenAICompatibleSignalExtractor,
    _parse_strict_json,
    build_signal_extractor,
    dpapi_protect,
    dpapi_unprotect,
    validate_extraction_payload,
)
from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.extraction import RuleBasedSignalExtractor
from ashare_hotpot.models import EventCluster, SourceDocument
from ashare_hotpot.storage import Storage


win32 = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 仅支持 Windows")

NOW = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)


def _doc(document_id: str = "doc-1") -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url=f"https://example.test/{document_id}",
        document_url=None,
        title="公司签订重大合同公告",
        published_at=NOW - timedelta(hours=2),
        stock_codes=("000001",),
        body_text="公司近日与客户签订重大合同，合同金额1.2亿元，占营业收入的10%。",
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def _cluster() -> EventCluster:
    return EventCluster(
        event_id="event-1",
        stock_codes=("000001",),
        canonical_title="公司签订重大合同公告",
        first_seen_at=NOW - timedelta(hours=3),
        last_seen_at=NOW - timedelta(hours=2),
        representative_document_id="doc-1",
        document_ids=["doc-1"],
        historical_similar_event_id=None,
    )


VALID_PAYLOAD: dict[str, object] = {
    "event_type": "major_contract",
    "direction": "positive",
    "positive_mechanism": "新增合同或订单预计增厚未来营业收入",
    "metrics": [
        {
            "name": "合同金额",
            "value": 1.2,
            "unit": "亿元",
            "comparison_basis": None,
            "comparison_ratio": None,
            "evidence_id": "doc-1",
        }
    ],
    "certainty_stage": "signed",
    "certainty": 0.9,
    "novelty": 100,
    "unexpectedness": 50,
    "materiality_level": 2,
    "counter_evidence": [],
    "evidence_ids": ["doc-1"],
    "no_valid_signal": False,
}


class StubHttp:
    def __init__(self, responses: list[dict[str, object]] | dict[str, object]) -> None:
        self.responses = responses if isinstance(responses, list) else [responses]
        self.calls: list[tuple[str, dict[str, object], dict[str, object]]] = []

    def post(
        self,
        url: str,
        json: dict[str, object] | None = None,
        headers: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> SimpleNamespace:
        self.calls.append((url, json or {}, headers or {}))
        response = self.responses.pop(0)
        return SimpleNamespace(
            status_code=response["status"],
            json=lambda: response.get("body", {}),
        )


def _settings(tmp_path, **overrides) -> AppSettings:
    defaults = dict(
        app_root=tmp_path,
        ai_enabled=True,
        ai_base_url="https://api.example.test/v1",
        ai_model="test-model",
        ai_timeout_seconds=5.0,
        ai_prompt_schema_version="ai-v1",
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


@win32
def test_dpapi_roundtrip_hides_plaintext() -> None:
    key = "sk-test-123456"
    blob = dpapi_protect(key)
    assert blob != key.encode("utf-8")
    assert dpapi_unprotect(blob) == key


@win32
def test_credential_store_save_load_clear(tmp_path) -> None:
    store = AiCredentialStore(tmp_path)
    assert store.load() is None
    store.save("sk-secret")
    assert tmp_path.joinpath("ai_credentials.bin").exists()
    raw = tmp_path.joinpath("ai_credentials.bin").read_bytes()
    assert b"sk-secret" not in raw
    assert store.load() == "sk-secret"
    store.clear()
    assert not tmp_path.joinpath("ai_credentials.bin").exists()
    assert store.load() is None


def test_parse_strict_json_rejects_markdown_and_multiple_objects() -> None:
    assert _parse_strict_json('{"a": 1}') == {"a": 1}
    with pytest.raises(RuntimeError):
        _parse_strict_json('```json\n{"a": 1}\n```')
    with pytest.raises(RuntimeError):
        _parse_strict_json('{"a": 1} {"b": 2}')
    with pytest.raises(RuntimeError):
        _parse_strict_json("not json")


def test_validate_payload_rejects_unknown_enum_and_bad_fields() -> None:
    cluster = _cluster()
    good = validate_extraction_payload(VALID_PAYLOAD, cluster=cluster, stock_code="000001")
    assert good is not None
    assert good.extractor_kind == "llm"
    assert good.event_id == "event-1"

    bad_type = dict(VALID_PAYLOAD, event_type="mystery_event")
    assert validate_extraction_payload(bad_type, cluster=cluster, stock_code="000001") is None

    bad_direction = dict(VALID_PAYLOAD, direction="bullish")
    assert validate_extraction_payload(bad_direction, cluster=cluster, stock_code="000001") is None

    bad_certainty = dict(VALID_PAYLOAD, certainty=1.5)
    assert validate_extraction_payload(bad_certainty, cluster=cluster, stock_code="000001") is None

    bad_materiality = dict(VALID_PAYLOAD, materiality_level=9)
    assert validate_extraction_payload(bad_materiality, cluster=cluster, stock_code="000001") is None

    missing_metric_key = dict(
        VALID_PAYLOAD,
        metrics=[{"name": "合同金额", "value": 1.2}],
    )
    assert validate_extraction_payload(missing_metric_key, cluster=cluster, stock_code="000001") is None

    bad_counter_kind = dict(
        VALID_PAYLOAD,
        counter_evidence=[{"kind": "unknown", "reason": "x", "evidence_id": "doc-1"}],
    )
    assert validate_extraction_payload(bad_counter_kind, cluster=cluster, stock_code="000001") is None

    bad_evidence = dict(VALID_PAYLOAD, evidence_ids=["doc-1", 123])
    assert validate_extraction_payload(bad_evidence, cluster=cluster, stock_code="000001") is None


@win32
def test_openai_extractor_success_and_cache_hit(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_doc(), NOW)
    # First request returns a valid payload.
    stub = StubHttp(
        [
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": _json(VALID_PAYLOAD)}}]
                },
            }
        ]
    )
    settings = _settings(tmp_path)
    AiCredentialStore(settings.app_root).save("sk-test")
    extractor = OpenAICompatibleSignalExtractor(settings, storage, http_client=stub)

    extraction = extractor.extract_all(_cluster(), (_doc(),))
    assert len(extraction) == 1
    assert extraction[0].extractor_kind == "llm"
    assert extraction[0].event_type == "major_contract"
    assert extraction[0].evidence_ids and extraction[0].evidence_ids[0].startswith("llm:")
    assert extraction[0].metrics[0]["evidence_id"].startswith("llm:")
    assert len(stub.calls) == 1
    assert "sk-test" in stub.calls[0][2].get("Authorization", "")

    # Second call hits the cache: no additional request.
    again = extractor.extract_all(_cluster(), (_doc(),))
    assert len(again) == 1
    assert len(stub.calls) == 1


@win32
def test_cache_misses_when_model_changes(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_doc(), NOW)
    settings = _settings(tmp_path)
    AiCredentialStore(settings.app_root).save("sk-test")
    stub = StubHttp(
        [
            {"status": 200, "body": {"choices": [{"message": {"content": _json(VALID_PAYLOAD)}}]}},
            {"status": 200, "body": {"choices": [{"message": {"content": _json(VALID_PAYLOAD)}}]}},
        ]
    )
    first = OpenAICompatibleSignalExtractor(settings, storage, http_client=stub)
    first.extract_all(_cluster(), (_doc(),))
    assert len(stub.calls) == 1

    second = OpenAICompatibleSignalExtractor(
        _settings(tmp_path, ai_model="other-model"),
        storage,
        http_client=stub,
    )
    second.extract_all(_cluster(), (_doc(),))
    assert len(stub.calls) == 2


@win32
def test_ai_failure_falls_back_to_rules(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_doc(), NOW)
    settings = _settings(tmp_path)
    AiCredentialStore(settings.app_root).save("sk-test")

    def failing_post(*_args, **_kwargs):
        raise RuntimeError("timeout")

    stub = StubHttp([])
    stub.post = failing_post  # type: ignore[method-assign]
    primary = OpenAICompatibleSignalExtractor(settings, storage, http_client=stub)
    fallback = FallbackSignalExtractor(primary, RuleBasedSignalExtractor(storage))
    extractions = fallback.extract_all(_cluster(), (_doc(),))
    assert len(extractions) == 1
    assert extractions[0].extractor_kind == "rules_fallback"
    assert extractions[0].extractor_version == "rules-fallback-v1"
    assert extractions[0].event_type == "major_contract"


@win32
def test_http_429_retries_then_falls_back(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_doc(), NOW)
    settings = _settings(tmp_path)
    AiCredentialStore(settings.app_root).save("sk-test")
    stub = StubHttp(
        [
            {"status": 429, "body": {}},
            {"status": 429, "body": {}},
        ]
    )
    primary = OpenAICompatibleSignalExtractor(settings, storage, http_client=stub)
    fallback = FallbackSignalExtractor(primary, RuleBasedSignalExtractor(storage))
    extractions = fallback.extract_all(_cluster(), (_doc(),))
    assert extractions[0].extractor_kind == "rules_fallback"
    assert len(stub.calls) == 2  # one retry


@win32
def test_http_5xx_falls_back(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_doc(), NOW)
    settings = _settings(tmp_path)
    AiCredentialStore(settings.app_root).save("sk-test")
    stub = StubHttp([{"status": 500, "body": {}}])
    primary = OpenAICompatibleSignalExtractor(settings, storage, http_client=stub)
    fallback = FallbackSignalExtractor(primary, RuleBasedSignalExtractor(storage))
    extractions = fallback.extract_all(_cluster(), (_doc(),))
    assert extractions[0].extractor_kind == "rules_fallback"


@win32
def test_markdown_wrapped_response_falls_back(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_doc(), NOW)
    settings = _settings(tmp_path)
    AiCredentialStore(settings.app_root).save("sk-test")
    stub = StubHttp(
        {
            "status": 200,
            "body": {
                "choices": [
                    {"message": {"content": "```json\n%s\n```" % _json(VALID_PAYLOAD)}}
                ]
            },
        }
    )
    primary = OpenAICompatibleSignalExtractor(settings, storage, http_client=stub)
    fallback = FallbackSignalExtractor(primary, RuleBasedSignalExtractor(storage))
    extractions = fallback.extract_all(_cluster(), (_doc(),))
    assert extractions[0].extractor_kind == "rules_fallback"


@win32
def test_unknown_enum_response_falls_back(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_doc(), NOW)
    settings = _settings(tmp_path)
    AiCredentialStore(settings.app_root).save("sk-test")
    bad_payload = dict(VALID_PAYLOAD, event_type="mystery_event")
    stub = StubHttp(
        {
            "status": 200,
            "body": {"choices": [{"message": {"content": _json(bad_payload)}}]},
        }
    )
    primary = OpenAICompatibleSignalExtractor(settings, storage, http_client=stub)
    fallback = FallbackSignalExtractor(primary, RuleBasedSignalExtractor(storage))
    extractions = fallback.extract_all(_cluster(), (_doc(),))
    assert extractions[0].extractor_kind == "rules_fallback"


def test_disabled_ai_returns_rules_extractor(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    settings = _settings(tmp_path, ai_enabled=False)
    extractor = build_signal_extractor(settings, storage)
    assert isinstance(extractor, RuleBasedSignalExtractor)


def _json(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)
