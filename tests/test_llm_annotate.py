"""Offline tests for the LLM evaluation-set annotator.

All tests use stub ``call`` functions / monkeypatched transport; no real API
key or network access is used (project rule: fake keys and stubs only).
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from scripts.evaluation import llm_annotate as ann


def _event(event_id: str, label: object = None) -> dict:
    return {
        "event_id": event_id,
        "stock_codes": ["000001"],
        "canonical_title": "标题",
        "first_seen_at": "2026-08-08T00:00:00+08:00",
        "last_seen_at": "2026-08-08T00:00:00+08:00",
        "historical_similar_event_id": None,
        "representative": {
            "document_id": "doc1",
            "title": "标题",
            "source_url": "http://x",
            "document_url": None,
            "published_at": "2026-08-08T00:00:00+08:00",
            "parse_status": "parsed",
        },
        "document_count": 1,
        "evidence": [],
        "label": label,
        "must_hit_candidate": None,
        "error_types": None,
    }


def _board_row(event_id: str, stock_code: str = "000001", **overrides) -> dict:
    row = {
        "rank": 1,
        "event_id": event_id,
        "stock_code": stock_code,
        "board": "confirmed_positive",
        "score": 80.0,
        "materiality_level": 3,
        "certainty": 0.9,
        "provisional": False,
        "event_type": "major_contract",
        "extractor_kind": "rules",
        "relevant": None,
        "duplicate": None,
    }
    row.update(overrides)
    return row


def _participant(institution_id: str, name: str = "中信证券股份有限公司") -> dict:
    return {
        "institution_id": institution_id,
        "canonical_name": name,
        "group_id": "group:citic",
        "institution_type": "brokerage",
        "verification_status": "verified",
        "analyst_name": None,
        "evidence_id": "evidence:x",
        "entity_ok": None,
        "group_ok": None,
    }


def _discovery_item(document_id: str, label: object = None) -> dict:
    return {
        "document_id": document_id,
        "stratum": "cninfo_announcement",
        "stock_code": "600390",
        "stock_name": "五矿资本",
        "title": "关于拟签订重大合同的公告",
        "published_at": "2026-08-08T00:00:00+08:00",
        "parse_status": "metadata_only",
        "discovery_type": "contract_order",
        "queue_status": "pending_attachment",
        "in_discovery_layer": True,
        "promoted_to_board": False,
        "fixed_case": False,
        "label": label,
    }


def _institution_record(activity_id: str, participants: list[dict]) -> dict:
    return {
        "activity_id": activity_id,
        "stock_code": "000001",
        "stock_name": "平安银行",
        "activity_type": "survey",
        "activity_dates": ["2026-08-01"],
        "body_text": "参与单位：中信证券股份有限公司、易方达基金",
        "reported_participant_count": None,
        "named_participant_count": len(participants),
        "question_count": 5,
        "high_depth_question_count": 0,
        "date_precision": "explicit",
        "source": {
            "document_id": "doc1",
            "title": "投资者关系活动记录表",
            "source_url": "http://x",
            "document_url": None,
            "published_at": "2026-08-02T00:00:00+08:00",
            "parse_status": "parsed",
            "provider_key": "cninfo",
        },
        "stratum": "cninfo_research",
        "named_institutions": None,
        "participants": participants,
    }


class TestEventAnnotation:
    def test_labels_events_and_preserves_existing(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "short_term_events",
            "meta": {},
            "board": [],
            "events": [
                _event("e1"),
                _event("e2", label="not_signal"),
            ],
            "must_hit": [],
        }

        def call(**kwargs):
            assert "api_key" not in kwargs
            return {
                "items": [
                    {
                        "event_id": "e1",
                        "label": "positive_signal",
                        "must_hit_candidate": True,
                        "error_types": [],
                        "rationale": "重大合同",
                    }
                ]
            }

        ann.run_short_term(
            data,
            call=call,
            batch_size=20,
            max_workers=2,
            model="stub",
        )
        assert data["events"][0]["label"] == "positive_signal"
        assert data["events"][0]["must_hit_candidate"] is True
        assert data["events"][0]["error_types"] == []
        assert data["events"][1]["label"] == "not_signal"  # preserved
        assert data["annotation"]["needs_human"] == []

    def test_invalid_error_types_marks_needs_human(self) -> None:
        data = {
            "schema_version": 2,
            "kind": "short_term_events",
            "meta": {},
            "board": [],
            "events": [_event("e1")],
            "must_hit": [],
        }

        def call(**kwargs):
            return {
                "items": [
                    {
                        "event_id": "e1",
                        "label": "positive_signal",
                        "must_hit_candidate": True,
                        "error_types": ["not_an_error"],
                        "rationale": "重大合同",
                    }
                ]
            }

        ann.run_short_term(
            data, call=call, batch_size=20, max_workers=1, model="stub"
        )
        assert data["events"][0]["label"] is None
        assert data["annotation"]["needs_human"] == ["event:e1"]

    def test_invalid_label_marks_needs_human(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "short_term_events",
            "meta": {},
            "board": [],
            "events": [_event("e1")],
            "must_hit": [],
        }

        def call(**kwargs):
            return {"items": [{"event_id": "e1", "label": "buy"}]}

        ann.run_short_term(
            data, call=call, batch_size=20, max_workers=1, model="stub"
        )
        assert data["events"][0]["label"] is None
        assert data["annotation"]["needs_human"] == ["event:e1"]
        assert data["annotation"]["errors"]

    def test_missing_event_id_marks_needs_human(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "short_term_events",
            "meta": {},
            "board": [],
            "events": [_event("e1")],
            "must_hit": [],
        }

        def call(**kwargs):
            return {"items": [{"event_id": "other", "label": "neutral"}]}

        ann.run_short_term(
            data, call=call, batch_size=20, max_workers=1, model="stub"
        )
        assert data["events"][0]["label"] is None
        assert data["annotation"]["needs_human"] == ["event:e1"]


class TestBoardAnnotation:
    def test_labels_board_rows(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "short_term_events",
            "meta": {},
            "board": [
                _board_row("e1", "000001", rank=1),
                _board_row("e2", "000002", rank=2),
            ],
            "events": [],
            "must_hit": [],
        }

        def call(**kwargs):
            return {
                "items": [
                    {
                        "event_id": "e1",
                        "relevant": True,
                        "duplicate": False,
                        "rationale": "ok",
                    },
                    {
                        "event_id": "e2",
                        "relevant": False,
                        "duplicate": True,
                        "rationale": "重复",
                    },
                ]
            }

        ann.run_short_term(
            data, call=call, batch_size=20, max_workers=1, model="stub"
        )
        assert data["board"][0]["relevant"] is True
        assert data["board"][0]["duplicate"] is False
        assert data["board"][1]["relevant"] is False
        assert data["board"][1]["duplicate"] is True
        assert data["annotation"]["needs_human"] == []

    def test_board_missing_row_marks_needs_human(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "short_term_events",
            "meta": {},
            "board": [
                _board_row("e1", "000001", rank=1),
                _board_row("e2", "000002", rank=2),
            ],
            "events": [],
            "must_hit": [],
        }

        def call(**kwargs):
            return {
                "items": [
                    {
                        "event_id": "e1",
                        "relevant": True,
                        "duplicate": False,
                    }
                ]
            }

        ann.run_short_term(
            data, call=call, batch_size=20, max_workers=1, model="stub"
        )
        assert data["board"][0]["relevant"] is True
        assert data["board"][1]["relevant"] is None
        assert "board:e2/000002" in data["annotation"]["needs_human"]


class TestInstitutionAnnotation:
    def test_labels_participants(self) -> None:
        p1 = _participant("inst:1")
        p2 = _participant("inst:2", name="某媒体")
        data = {
            "schema_version": 1,
            "kind": "institution_records",
            "meta": {},
            "records": [_institution_record("a1", [p1, p2])],
        }

        def call(**kwargs):
            return {
                "items": [
                    {
                        "institution_id": "inst:1",
                        "entity_ok": True,
                        "group_ok": True,
                        "rationale": "正确",
                    },
                    {
                        "institution_id": "inst:2",
                        "entity_ok": False,
                        "group_ok": False,
                        "rationale": "媒体不计入",
                    },
                ],
                "named_institutions": ["中信证券股份有限公司"],
            }

        ann.run_institution(data, call=call, max_workers=1, model="stub")
        assert data["records"][0]["participants"][0]["entity_ok"] is True
        assert data["records"][0]["participants"][1]["entity_ok"] is False
        assert data["records"][0]["named_institutions"] == [
            "中信证券股份有限公司"
        ]
        assert data["annotation"]["needs_human"] == []

    def test_missing_participant_marks_needs_human(self) -> None:
        p1 = _participant("inst:1")
        p2 = _participant("inst:2")
        data = {
            "schema_version": 1,
            "kind": "institution_records",
            "meta": {},
            "records": [_institution_record("a1", [p1, p2])],
        }

        def call(**kwargs):
            return {
                "items": [
                    {
                        "institution_id": "inst:1",
                        "entity_ok": True,
                        "group_ok": True,
                    }
                ],
                "named_institutions": ["中信证券股份有限公司"],
            }

        ann.run_institution(data, call=call, max_workers=1, model="stub")
        assert data["records"][0]["participants"][0]["entity_ok"] is True
        assert data["records"][0]["participants"][1]["entity_ok"] is None
        assert "participant:a1/inst:2" in data["annotation"]["needs_human"]

    def test_missing_named_institutions_marks_needs_human(self) -> None:
        p1 = _participant("inst:1")
        data = {
            "schema_version": 1,
            "kind": "institution_records",
            "meta": {},
            "records": [_institution_record("a1", [p1])],
        }

        def call(**kwargs):
            return {
                "items": [
                    {
                        "institution_id": "inst:1",
                        "entity_ok": True,
                        "group_ok": True,
                    }
                ]
            }

        ann.run_institution(data, call=call, max_workers=1, model="stub")
        assert data["records"][0]["participants"][0]["entity_ok"] is True
        assert data["records"][0]["named_institutions"] is None
        assert "named:a1" in data["annotation"]["needs_human"]

    def test_invalid_named_institutions_marks_needs_human(self) -> None:
        p1 = _participant("inst:1")
        data = {
            "schema_version": 1,
            "kind": "institution_records",
            "meta": {},
            "records": [_institution_record("a1", [p1])],
        }

        def call(**kwargs):
            return {
                "items": [
                    {
                        "institution_id": "inst:1",
                        "entity_ok": True,
                        "group_ok": True,
                    }
                ],
                "named_institutions": "中信证券",
            }

        ann.run_institution(data, call=call, max_workers=1, model="stub")
        assert data["records"][0]["named_institutions"] is None
        assert "named:a1" in data["annotation"]["needs_human"]

    def test_zero_participant_record_still_annotates_named(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "institution_records",
            "meta": {},
            "records": [_institution_record("a0", [])],
        }

        def call(**kwargs):
            return {
                "items": [],
                "named_institutions": ["易方达基金"],
            }

        ann.run_institution(data, call=call, max_workers=1, model="stub")
        assert data["records"][0]["named_institutions"] == ["易方达基金"]
        assert data["annotation"]["needs_human"] == []


class TestChatJson:
    def test_parses_markdown_wrapped_json(self) -> None:
        assert ann._parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_accepts_top_level_array(self) -> None:
        assert ann._parse_json_object('[{"a": 1}]') == {"items": [{"a": 1}]}

    def test_ignores_trailing_text_after_json(self) -> None:
        assert ann._parse_json_object('{"a": 1}\n以上是标注结果') == {"a": 1}

    def test_finds_results_in_keyed_dict(self) -> None:
        output = {
            "e1": {
                "label": "positive_signal",
                "must_hit_candidate": True,
                "error_types": ["direction_error"],
                "rationale": "重大合同",
            }
        }
        result = ann.annotate_event_batch(
            [_event("e1")],
            call=lambda **kwargs: output,
        )
        assert result["e1"]["label"] == "positive_signal"
        assert result["e1"]["must_hit_candidate"] is True
        assert result["e1"]["error_types"] == ["direction_error"]

    def test_finds_results_in_flat_single_item(self) -> None:
        output = {
            "institution_id": "inst:1",
            "entity_ok": True,
            "group_ok": True,
            "rationale": "正确",
            "named_institutions": ["中信证券股份有限公司"],
        }
        data = {
            "schema_version": 1,
            "kind": "institution_records",
            "meta": {},
            "records": [
                _institution_record("a1", [_participant("inst:1")])
            ],
        }
        ann.run_institution(
            data, call=lambda **kwargs: output, max_workers=1, model="stub"
        )
        assert data["records"][0]["participants"][0]["entity_ok"] is True
        assert data["records"][0]["named_institutions"] == [
            "中信证券股份有限公司"
        ]
        assert data["annotation"]["needs_human"] == []

    def test_rejects_invalid_json(self) -> None:
        with pytest.raises(ValueError):
            ann._parse_json_object("not json")

    def test_retries_on_http_500_then_succeeds(self, monkeypatch) -> None:
        calls = {"count": 0}

        class FakeResponse:
            def __init__(self, payload: bytes):
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self._payload

        def fake_urlopen(request, timeout=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(
                    request.full_url, 500, "server error", {}, None
                )
            payload = json.dumps(
                {"choices": [{"message": {"content": '{"ok": true}'}}]}
            ).encode("utf-8")
            return FakeResponse(payload)

        monkeypatch.setattr(ann.urllib.request, "urlopen", fake_urlopen)
        result = ann.chat_json(
            api_key="fake-key",
            base_url="https://example.invalid",
            model="stub",
            system="s",
            user="u",
            timeout=5,
            retries=1,
        )
        assert result == {"ok": True}
        assert calls["count"] == 2

    def test_raises_on_persistent_http_error(self, monkeypatch) -> None:
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 500, "server error", {}, None
            )

        monkeypatch.setattr(ann.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(urllib.error.HTTPError):
            ann.chat_json(
                api_key="fake-key",
                base_url="https://example.invalid",
                model="stub",
                system="s",
                user="u",
                timeout=5,
                retries=1,
            )


def test_read_key_requires_env(monkeypatch) -> None:
    monkeypatch.delenv("LLM_ANNOTATE_TEST_KEY", raising=False)
    with pytest.raises(SystemExit):
        ann._read_key("LLM_ANNOTATE_TEST_KEY")
    monkeypatch.setenv("LLM_ANNOTATE_TEST_KEY", "fake-key")
    assert ann._read_key("LLM_ANNOTATE_TEST_KEY") == "fake-key"


class TestDiscoveryAnnotation:
    def test_labels_discovery_items_and_preserves_existing(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "discovery_candidates",
            "meta": {},
            "items": [
                _discovery_item("d1"),
                _discovery_item("d2", label="should_discover"),
            ],
        }

        def call(**kwargs):
            assert "api_key" not in kwargs
            return {
                "label": "should_discover",
                "rationale": "公开披露，应进入发现层",
            }

        ann.run_discovery(
            data,
            call=call,
            max_workers=2,
            model="stub",
        )
        assert data["items"][0]["label"] == "should_discover"
        assert "llm_rationale" in data["items"][0]
        assert data["items"][1]["label"] == "should_discover"  # preserved
        assert data["annotation"]["needs_human"] == []

    def test_invalid_label_marks_needs_human(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "discovery_candidates",
            "meta": {},
            "items": [_discovery_item("d1")],
        }

        def call(**kwargs):
            return {"label": "buy"}

        ann.run_discovery(
            data,
            call=call,
            max_workers=1,
            model="stub",
        )
        assert data["items"][0]["label"] is None
        assert data["annotation"]["needs_human"] == ["d1"]
        assert data["annotation"]["errors"]

    def test_missing_item_marks_needs_human(self) -> None:
        data = {
            "schema_version": 1,
            "kind": "discovery_candidates",
            "meta": {},
            "items": [_discovery_item("d1")],
        }

        def call(**kwargs):
            return {"label": "not_discover", "rationale": "非公开披露"}

        def failing(*_args, **_kwargs):
            raise RuntimeError("模型不可用")

        ann.run_discovery(
            data,
            call=failing,
            max_workers=1,
            model="stub",
        )
        assert data["items"][0]["label"] is None
        assert data["annotation"]["needs_human"] == ["d1"]
        assert data["annotation"]["errors"]

    def test_main_cli_discovery_does_not_duplicate_max_workers(
        self, monkeypatch, tmp_path
    ) -> None:
        """Regression: main() passed max_workers twice for --discovery."""

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key-for-tests")
        source = {
            "schema_version": 1,
            "kind": "discovery_candidates",
            "meta": {},
            "items": [_discovery_item("d1"), _discovery_item("d2")],
        }
        in_path = tmp_path / "discovery_candidates_v1.json"
        in_path.write_text(
            json.dumps(source, ensure_ascii=False), encoding="utf-8"
        )

        calls = {"n": 0}

        def fake_chat_json(**kwargs):
            calls["n"] += 1
            return {"label": "should_discover", "rationale": "公开披露"}

        monkeypatch.setattr(ann, "chat_json", fake_chat_json)
        code = ann.main(
            ["--discovery", str(in_path), "--out", str(tmp_path), "--max-workers", "1"]
        )
        assert code == 0
        out_path = tmp_path / "discovery_candidates_v1.llm.json"
        labeled = json.loads(out_path.read_text(encoding="utf-8"))
        assert calls["n"] == 2
        assert all(item["label"] == "should_discover" for item in labeled["items"])
        assert labeled["annotation"]["needs_human"] == []
        assert labeled["annotation"]["errors"] == []
