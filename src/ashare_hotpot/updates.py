from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


GITHUB_API_URL = "https://api.github.com"
USER_AGENT = "AshareHotPot/0.1 (+personal desktop news index; contact via project README)"
CHECK_TIMEOUT_SECONDS = 10.0

_VERSION_RE = re.compile(
    r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-_.]?(alpha|beta|rc|pre|dev))?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """The latest published GitHub release of the project."""

    tag_name: str
    html_url: str
    name: str | None = None
    published_at: str | None = None
    body: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    """Outcome of a release check; either a newer release or an error message."""

    latest: ReleaseInfo | None
    error: str | None = None

    @property
    def has_update(self) -> bool:
        return self.error is None and self.latest is not None


def version_key(text: str) -> tuple[int, ...]:
    """Parse a version string into a comparable numeric key.

    ``v0.1.0`` and ``0.1.0`` compare equal, and pre-release suffixes such as
    ``-beta`` sort strictly below their release counterparts.
    """

    match = _VERSION_RE.search(text or "")
    if not match:
        return (0, 0, 0)
    parts = [int(group) for group in match.groups()[:3] if group is not None]
    parts = (parts + [0, 0, 0])[:3]
    if match.group(4):
        parts[-1] -= 1
    return tuple(parts)


def repo_from_url(project_url: str) -> tuple[str, str]:
    """Extract the ``(owner, repo)`` pair from a GitHub project URL."""

    path = urlparse(project_url).path.strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"无法从项目地址解析仓库：{project_url}")
    return parts[0], parts[1]


def latest_release_api_url(project_url: str) -> str:
    owner, repo = repo_from_url(project_url)
    return f"{GITHUB_API_URL}/repos/{owner}/{repo}/releases/latest"


def fetch_latest_release(project_url: str, *, timeout: float = CHECK_TIMEOUT_SECONDS) -> ReleaseInfo:
    """Query the GitHub API for the latest published release.

    Raises :class:`RuntimeError` with a user-facing message when the request
    fails or the payload is unexpected.
    """

    owner, repo = repo_from_url(project_url)
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/releases/latest"
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    except (httpx.HTTPError, OSError) as exc:
        raise RuntimeError(f"无法连接 GitHub 服务（{exc.__class__.__name__}）") from exc
    if response.status_code == 404:
        raise RuntimeError("仓库暂无发布版本")
    if response.status_code == 403:
        raise RuntimeError("GitHub 接口访问受限（可能触发限流），请稍后再试")
    if response.status_code == 401:
        raise RuntimeError("GitHub 接口拒绝访问")
    if response.status_code != 200:
        raise RuntimeError(f"GitHub 接口返回异常状态 {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("GitHub 返回的不是有效 JSON") from exc
    if not isinstance(payload, dict) or not payload.get("tag_name"):
        raise RuntimeError("GitHub 返回的发布信息格式异常")
    return ReleaseInfo(
        tag_name=str(payload["tag_name"]),
        html_url=str(payload.get("html_url") or f"https://github.com/{owner}/{repo}/releases"),
        name=str(payload["name"]) if payload.get("name") else None,
        published_at=str(payload["published_at"]) if payload.get("published_at") else None,
        body=str(payload["body"]) if payload.get("body") else None,
    )


def check_for_updates(
    project_url: str,
    current_version: str,
    *,
    timeout: float = CHECK_TIMEOUT_SECONDS,
) -> UpdateCheckResult:
    """Return a newer release when one exists; never raises."""

    try:
        latest = fetch_latest_release(project_url, timeout=timeout)
    except Exception as exc:
        return UpdateCheckResult(latest=None, error=str(exc))
    if version_key(latest.tag_name) > version_key(current_version):
        return UpdateCheckResult(latest=latest)
    return UpdateCheckResult(latest=None)
