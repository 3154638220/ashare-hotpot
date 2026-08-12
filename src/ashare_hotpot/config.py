from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


APP_NAME = "A股热度"
APP_SLUG = "AshareHotPot"
APP_VERSION = "1.3.0"
PROJECT_URL = "https://github.com/3154638220/ashare-hotpot"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def release_url(version: str = APP_VERSION) -> str:
    """Return the GitHub release page for an application version."""

    return f"{PROJECT_URL}/releases/tag/v{version}"


@dataclass(frozen=True, slots=True)
class SourceConfig:
    key: str
    name: str
    base_url: str
    adapter: str = "ths_list"
    provider_key: str = "ths"
    provider_name: str = "同花顺"
    # When True, successfully parsed articles of this source are also
    # persisted as ``SourceDocument(kind="news")`` so the short-term signal
    # pipeline (clustering -> rules extraction -> scoring) can consume the
    # company-level statements they carry.  The source keeps its existing
    # news-board role; the signal pipeline treats it with the configured
    # 0.60 media-confidence tier and never invents evidence.
    signal_feed: bool = False
    # Research/announcement adapters only:
    column: str = ""
    tab_name: str = "fulltext"
    category: str = ""
    kind: str = "announcement"


@dataclass(frozen=True, slots=True)
class PolicySourceConfig:
    """One fixed national policy source (政策只进 policy_documents)。"""

    key: str
    name: str
    list_url: str
    # 分页模板：``{n}`` 替换为页码-1 生成第 N 页 URL；None 表示只枚举首页。
    pagination_template: str | None = None


DEFAULT_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig("companynews", "公司资讯", "https://stock.10jqka.com.cn/companynews_list/"),
    SourceConfig("stock_focus", "个股聚焦", "https://stock.10jqka.com.cn/ggjj_list/"),
    SourceConfig("company_research", "公司研究", "https://stock.10jqka.com.cn/gegudp_list/"),
    SourceConfig("industry_research", "行业研究", "https://stock.10jqka.com.cn/bkfy_list/"),
    SourceConfig("market_news", "证券市场新闻", "https://stock.10jqka.com.cn/stocknews_list/"),
    SourceConfig(
        "company_interaction",
        "独家公司互动",
        "https://yuanchuang.10jqka.com.cn/djgshd_list/",
        signal_feed=True,
    ),
    SourceConfig("announcement", "个股公告", "https://stock.10jqka.com.cn/gegugg_list/"),
)


INTERACTION_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(
        "irm",
        "深交所互动易",
        "http://irm.cninfo.com.cn/newircs/index/search",
        adapter="irm",
        provider_key="irm",
        provider_name="深交所互动易",
    ),
    SourceConfig(
        "sse",
        "上证e互动",
        "https://sns.sseinfo.com/ajax/feeds.do",
        adapter="sse",
        provider_key="sse",
        provider_name="上证e互动",
    ),
)


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_DISCLOSURE_URL = "https://www.cninfo.com.cn/new/disclosure"
SSE_FEEDS_URL = "https://sns.sseinfo.com/ajax/feeds.do"


RESEARCH_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(
        "cninfo_announcement",
        "巨潮资讯公告",
        CNINFO_QUERY_URL,
        adapter="cninfo",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        column="szse",
        tab_name="fulltext",
        kind="announcement",
    ),
    SourceConfig(
        "cninfo_research",
        "巨潮资讯调研",
        CNINFO_DISCLOSURE_URL,
        adapter="cninfo",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        column="szse_relation",
        tab_name="relation",
        kind="research_activity",
    ),
    SourceConfig(
        "sse_publish",
        "上证e互动发布",
        SSE_FEEDS_URL,
        adapter="sse_publish",
        provider_key="sse",
        provider_name="上证e互动",
        kind="research_activity",
    ),
    SourceConfig(
        "irm_ircs",
        "互动易投资者关系",
        "https://irm.cninfo.com.cn/newircs/index/search",
        adapter="irm_ircs",
        provider_key="irm",
        provider_name="深交所互动易",
        kind="research_activity",
    ),
    SourceConfig(
        "sse_announcement",
        "上交所公司公告",
        "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do",
        adapter="sse_announcement",
        provider_key="sse",
        provider_name="上海证券交易所",
        kind="announcement",
    ),
    SourceConfig(
        "bse_announcement",
        "北交所公司公告",
        "https://www.bse.cn/disclosureInfoController/initDisclosureList.do",
        adapter="bse_announcement",
        provider_key="bse",
        provider_name="北京证券交易所",
        kind="announcement",
    ),
    SourceConfig(
        "bse_performance",
        "北交所业绩说明会",
        "https://www.bse.cn/performanceController/list.do",
        adapter="bse_performance",
        provider_key="bse",
        provider_name="北京证券交易所",
        kind="research_activity",
    ),
)


# v1.2/v2：固定十个国家级政策源（政策只进 policy_documents + 每日 manifest，
# 绝不进入 source_documents 信号管线）。分页模板 ``{n}`` = 页码-1；为 None 的
# 来源只枚举首页（覆盖为部分覆盖）；WAF/JS 壳来源由解析层失败关闭并显示缺口。
POLICY_SOURCES: tuple[PolicySourceConfig, ...] = (
    PolicySourceConfig(
        "state_council",
        "国务院政策文件库",
        "https://www.gov.cn/zhengce/",
    ),
    PolicySourceConfig(
        "ndrc",
        "国家发展改革委政策发布",
        "https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/",
        pagination_template="https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/index_{n}.html",
    ),
    PolicySourceConfig(
        "miit",
        "工业和信息化部政策文件",
        "https://www.miit.gov.cn/zwgk/zcwj/",
    ),
    PolicySourceConfig(
        "mof",
        "财政部政策发布",
        "http://www.mof.gov.cn/zhengwuxinxi/zhengcefabu/",
        pagination_template="http://www.mof.gov.cn/zhengwuxinxi/zhengcefabu/index_{n}.htm",
    ),
    PolicySourceConfig(
        "mofcom",
        "商务部政策发布",
        "https://www.mofcom.gov.cn/zcfb/index.html",
    ),
    PolicySourceConfig(
        "nmpa",
        "国家药监局法规文件",
        "https://www.nmpa.gov.cn/xxgk/fgwj/",
    ),
    PolicySourceConfig(
        "nea",
        "国家能源局政策文件",
        "https://www.nea.gov.cn/policy/zxwj.htm",
    ),
    PolicySourceConfig(
        "samr",
        "市场监管总局法规文件",
        "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/",
    ),
    PolicySourceConfig(
        "mee",
        "生态环境部法规标准",
        "https://www.mee.gov.cn/xxgk2018/xxgk/xxgk06/",
        pagination_template="https://www.mee.gov.cn/xxgk2018/xxgk/xxgk06/index_{n}.html",
    ),
    PolicySourceConfig(
        "csrc",
        "证监会政府信息公开",
        "https://www.csrc.gov.cn/csrc/c101803/zfxxgk_zdgk.shtml",
    ),
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
    interaction_sources: tuple[SourceConfig, ...] = INTERACTION_SOURCES
    # Legacy research source metadata remains available to compatibility
    # readers and historical coverage diagnostics.  RefreshService filters
    # every ``research_activity`` source from active collection/metrics.
    research_sources: tuple[SourceConfig, ...] = RESEARCH_SOURCES
    policy_sources: tuple[PolicySourceConfig, ...] = POLICY_SOURCES
    window_hours: int = 24
    max_pages_per_source: int = 50
    backfill_days: int = 200
    # v2 里程碑 5 灰度切换：机构解析管线版本。默认 "v2"（名单章节定位 +
    # 种子归一 + 组织分类，plan.md 第三部分 里程碑 4）；"v1" 为发布前的
    # 整篇正文行级提取兼容口径，仅用于回退与并行比较。切换后需 550 天
    # 机构活动基线重算才生效（批次原子发布，失败保留上一批已发布指标）。
    research_pipeline_version: str = "v2"
    # Institution metric/UI gray switch.  ``warming_v2`` is the released
    # default; ``z20_legacy`` keeps one version-cycle rollback capability.
    institution_metric_version: str = "warming_v2"
    research_max_pages_per_run: int = 40
    research_max_pdfs_per_run: int = 100
    detail_workers: int = 4
    request_timeout_seconds: float = 15.0
    minimum_request_interval_seconds: float = 0.25
    request_retries: int = 3
    retention_days: int = 7
    popularity_cache_minutes: int = 10
    interaction_cache_minutes: int = 10
    # Optional AI enhancement (plan.md section 11).  Off by default; the API
    # key lives in a separate DPAPI-encrypted file, never in SQLite/QSettings.
    ai_enabled: bool = False
    ai_base_url: str = ""
    ai_model: str = ""
    ai_timeout_seconds: float = 30.0
    ai_prompt_schema_version: str = "ai-v1"

    @property
    def data_dir(self) -> Path:
        return self.app_root / "data"

    @property
    def log_dir(self) -> Path:
        return self.app_root / "logs"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "hotpot.db"

    @property
    def pdf_temp_dir(self) -> Path:
        """Cleanable temp directory for raw attachment downloads (never kept)."""

        return self.app_root / "pdf_tmp"

    @property
    def ai_credentials_path(self) -> Path:
        """DPAPI-encrypted AI API key file (separate from the database)."""

        return self.app_root / "ai_credentials.bin"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_temp_dir.mkdir(parents=True, exist_ok=True)
