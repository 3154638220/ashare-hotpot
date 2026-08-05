from __future__ import annotations

import httpx

from ashare_hotpot.updates import (
    UpdateCheckResult,
    check_for_updates,
    fetch_latest_release,
    latest_release_api_url,
    repo_from_url,
    version_key,
)


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: object | None = None,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error:
            raise ValueError("bad json")
        return self._payload


def test_version_key_parses_and_compares() -> None:
    assert version_key("v0.1.0") == version_key("0.1.0") == (0, 1, 0)
    assert version_key("v0.2.0") > version_key("0.1.9")
    assert version_key("1.2") == version_key("1.2.0")
    assert version_key("v1.0.0-beta") < version_key("v1.0.0")
    assert version_key("v1.0.0-rc.1") < version_key("v1.0.0")
    assert version_key("") == (0, 0, 0)


def test_repo_from_url_extracts_owner_and_repo() -> None:
    assert repo_from_url("https://github.com/3154638220/ashare-hotpot") == (
        "3154638220",
        "ashare-hotpot",
    )
    assert repo_from_url("https://github.com/3154638220/ashare-hotpot/") == (
        "3154638220",
        "ashare-hotpot",
    )
    assert latest_release_api_url("https://github.com/3154638220/ashare-hotpot") == (
        "https://api.github.com/repos/3154638220/ashare-hotpot/releases/latest"
    )


def test_repo_from_url_rejects_invalid_address() -> None:
    try:
        repo_from_url("https://example.com/not-github")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid project URL should raise ValueError")


def test_check_for_updates_finds_newer_release(monkeypatch) -> None:
    payload = {
        "tag_name": "v0.2.0",
        "html_url": "https://github.com/3154638220/ashare-hotpot/releases/tag/v0.2.0",
        "name": "0.2.0",
        "published_at": "2026-08-01T00:00:00Z",
        "body": "新功能",
    }
    monkeypatch.setattr(
        "ashare_hotpot.updates.httpx.get",
        lambda *_args, **_kwargs: FakeResponse(payload=payload),
    )

    result = check_for_updates("https://github.com/3154638220/ashare-hotpot", "0.1.0")

    assert result.has_update
    assert result.error is None
    assert result.latest is not None
    assert result.latest.tag_name == "v0.2.0"
    assert result.latest.html_url.endswith("/releases/tag/v0.2.0")
    assert result.latest.body == "新功能"


def test_check_for_updates_reports_no_update(monkeypatch) -> None:
    monkeypatch.setattr(
        "ashare_hotpot.updates.httpx.get",
        lambda *_args, **_kwargs: FakeResponse(payload={"tag_name": "v0.1.0"}),
    )

    result = check_for_updates("https://github.com/3154638220/ashare-hotpot", "0.1.0")

    assert not result.has_update
    assert result.latest is None
    assert result.error is None


def test_check_for_updates_returns_error_on_http_failure(monkeypatch) -> None:
    def raise_error(*_args, **_kwargs):
        raise httpx.ConnectError(
            "unreachable",
            request=httpx.Request("GET", "https://api.github.com/repos/o/r/releases/latest"),
        )

    monkeypatch.setattr("ashare_hotpot.updates.httpx.get", raise_error)

    result = check_for_updates("https://github.com/3154638220/ashare-hotpot", "0.1.0")

    assert not result.has_update
    assert result.latest is None
    assert "无法连接 GitHub 服务" in result.error


def test_check_for_updates_returns_error_on_http_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "ashare_hotpot.updates.httpx.get",
        lambda *_args, **_kwargs: FakeResponse(status_code=403),
    )

    result = check_for_updates("https://github.com/3154638220/ashare-hotpot", "0.1.0")

    assert not result.has_update
    assert "限流" in result.error


def test_check_for_updates_returns_error_on_bad_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "ashare_hotpot.updates.httpx.get",
        lambda *_args, **_kwargs: FakeResponse(payload={"odd": "shape"}),
    )

    result = check_for_updates("https://github.com/3154638220/ashare-hotpot", "0.1.0")

    assert not result.has_update
    assert "格式异常" in result.error


def test_fetch_latest_release_uses_expected_request(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(
            payload={
                "tag_name": "v0.3.0",
                "html_url": "https://github.com/3154638220/ashare-hotpot/releases/tag/v0.3.0",
            }
        )

    monkeypatch.setattr("ashare_hotpot.updates.httpx.get", fake_get)

    release = fetch_latest_release("https://github.com/3154638220/ashare-hotpot")

    assert release.tag_name == "v0.3.0"
    assert calls[0]["url"].endswith("/releases/latest")
    assert calls[0]["kwargs"]["timeout"] > 0
    assert "User-Agent" in calls[0]["kwargs"]["headers"]


def test_update_check_result_has_update_only_when_latest_present() -> None:
    assert UpdateCheckResult(latest=None).has_update is False
    assert UpdateCheckResult(latest=None, error="boom").has_update is False
