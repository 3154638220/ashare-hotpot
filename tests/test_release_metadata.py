from __future__ import annotations

import tomllib
from pathlib import Path

from ashare_hotpot import __version__
from ashare_hotpot.config import APP_VERSION, release_url


EXPECTED_RELEASE_VERSION = "1.4.1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_v141_release_metadata_is_consistent() -> None:
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    installer = (PROJECT_ROOT / "installer" / "AshareHotPot.iss").read_text(
        encoding="utf-8"
    )
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert project["project"]["version"] == EXPECTED_RELEASE_VERSION
    assert APP_VERSION == EXPECTED_RELEASE_VERSION
    assert __version__ == EXPECTED_RELEASE_VERSION
    assert f'#define MyAppVersion "{EXPECTED_RELEASE_VERSION}"' in installer
    assert release_url() == (
        "https://github.com/3154638220/ashare-hotpot/releases/tag/v1.4.1"
    )
    assert "AshareHotPot-Setup-1.4.1-x64.exe" in readme
