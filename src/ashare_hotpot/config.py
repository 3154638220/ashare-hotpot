from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


APP_NAME = "A股新闻热度"
APP_SLUG = "AshareHotPot"
APP_VERSION = "0.1.0"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class SourceConfig:
    key: str
    name: str
    base_url: str


DEFAULT_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig("companynews", "公司资讯", "https://stock.10jqka.com.cn/companynews_list/"),
    SourceConfig("stock_focus", "个股聚焦", "https://stock.10jqka.com.cn/ggjj_list/"),
    SourceConfig("company_research", "公司研究", "https://stock.10jqka.com.cn/gegudp_list/"),
    SourceConfig("industry_research", "行业研究", "https://stock.10jqka.com.cn/bkfy_list/"),
    SourceConfig("market_news", "证券市场新闻", "https://stock.10jqka.com.cn/stocknews_list/"),
)


def default_app_root() -> Path:
    override = os.environ.get("ASHARE_HOTPOT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_SLUG
    return Path.home() / ".ashare-hotpot"


@dataclass(slots=True)
class AppSettings:
    app_root: Path = field(default_factory=default_app_root)
    sources: tuple[SourceConfig, ...] = DEFAULT_SOURCES
    window_hours: int = 24
    max_pages_per_source: int = 20
    detail_workers: int = 4
    request_timeout_seconds: float = 15.0
    minimum_request_interval_seconds: float = 0.25
    request_retries: int = 3
    retention_days: int = 7

    @property
    def data_dir(self) -> Path:
        return self.app_root / "data"

    @property
    def log_dir(self) -> Path:
        return self.app_root / "logs"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "hotpot.db"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

