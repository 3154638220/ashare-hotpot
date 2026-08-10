"""v1.2/v2 政策源采集层：十个政策源 fixture 契约 + 失败关闭 + 清单对账。

覆盖：服务器端渲染列表页的共享启发式解析（国务院/发改委/工信部/财政部/
商务部/生态环境部）、分页（index_N 后缀）、WAF/JS 壳来源失败关闭（药监局/
能源局/市场监管总局/证监会）、PolicySyncService 持久化与每日 manifest。
政策文档绝不出个股信号（结构测试见 test_v2_m2_sources.py）。
"""

from __future__ import annotations

import threading
from datetime import date, datetime
from pathlib import Path

import pytest

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.coverage import (
    COVERAGE_STATUS_PARTIAL,
    COVERAGE_STATUS_REALTIME_PROVISIONAL,
    COVERAGE_STATUS_UNAVAILABLE,
)
from ashare_hotpot.policy_sources import (
    PolicySource,
    PolicySyncService,
    parse_policy_list_page,
)
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=SHANGHAI_TZ)
FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _config(key: str) -> object:
    settings = AppSettings()
    return next(
        source for source in settings.policy_sources if source.key == key
    )


class PolicyStubClient:
    def __init__(self, text_map: dict[str, str]) -> None:
        self.text_map = text_map
        self.calls: list[str] = []

    def get_text(self, url: str, *, accept: str = "", headers=None) -> str:
        self.calls.append(url)
        for template, value in self.text_map.items():
            if template.startswith(("http://", "https://")):
                # 完整 URL 模板：精确匹配（避免 "zhengcefabu/" 误吞分页请求）。
                if url == template:
                    return value
            elif template in url:
                return value
        raise RuntimeError(f"未配置响应：{url}")


def test_policy_sources_registered_ten_sources() -> None:
    settings = AppSettings()
    keys = [source.key for source in settings.policy_sources]
    assert keys == [
        "state_council",
        "ndrc",
        "miit",
        "mof",
        "mofcom",
        "nmpa",
        "nea",
        "samr",
        "mee",
        "csrc",
    ]


@pytest.mark.parametrize(
    ("key", "fixture", "expected_first"),
    [
        ("state_council", "policy_state_council_page1.html", "集成电路"),
        ("ndrc", "policy_ndrc_page1.html", "国家发展改革委"),
        ("miit", "policy_miit_page1.html", "工业和信息化部"),
        ("mof", "policy_mof_page1.html", "企业会计准则"),
        ("mofcom", "policy_mofcom_page1.html", "商务部"),
        ("mee", "policy_mee_page1.html", "中华人民共和国"),
    ],
)
def test_list_mode_sources_parse_real_fixture(
    key: str, fixture: str, expected_first: str
) -> None:
    config = _config(key)
    items = parse_policy_list_page(
        _load(fixture),
        source_key=key,
        list_url=config.list_url,
        now=NOW,
    )
    assert len(items) >= 5
    first = items[0]
    assert expected_first in first.title
    assert first.url.startswith(("http://", "https://"))
    assert first.document_id.startswith("policy:")
    assert 2020 <= first.published_at.year <= 2030


@pytest.mark.parametrize(
    "fixture",
    [
        "policy_nmpa_page1.html",  # WAF 412 挑战页
        "policy_nea_page1.html",  # JS 框架页
        "policy_samr_page1.html",  # JS 壳
        "policy_csrc_page1.html",  # 栏目为年报，非政策列表（启发式 0 条）
    ],
)
def test_fail_closed_sources_raise_on_unusable_pages(fixture: str) -> None:
    with pytest.raises(RuntimeError, match="空列表或结构异常"):
        parse_policy_list_page(
            _load(fixture),
            source_key="x",
            list_url="https://example.test/",
            now=NOW,
        )


@pytest.mark.parametrize(
    ("key", "page2_fixture"),
    [
        ("ndrc", "policy_ndrc_page2.html"),
        ("mof", "policy_mof_page2.html"),
    ],
)
def test_index_n_pagination_contract(
    key: str, page2_fixture: str
) -> None:
    config = _config(key)
    client = PolicyStubClient(
        {
            "index_1": _load(page2_fixture),
        }
    )
    source = PolicySource(config, client)
    result = source.fetch_page(2, NOW)
    assert len(result.items) >= 1
    assert "index_1" in client.calls[0]


def test_mee_page_two_structure_change_fails_closed() -> None:
    """生态环境部 index_1.html 实测 200 但无列表（结构变化）：失败关闭。"""

    config = _config("mee")
    client = PolicyStubClient(
        {"index_1": _load("policy_mee_page2.html")}
    )
    source = PolicySource(config, client)
    with pytest.raises(RuntimeError, match="空列表或结构异常"):
        source.fetch_page(2, NOW)


def test_no_pagination_source_stops_after_first_page() -> None:
    config = _config("miit")
    client = PolicyStubClient(
        {config.list_url: _load("policy_miit_page1.html")}
    )
    source = PolicySource(config, client)
    assert source.fetch_page(1, NOW).items
    second = source.fetch_page(2, NOW)
    assert second.items == ()
    assert second.exhausted is True


def test_policy_sync_persists_documents_and_manifest(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    settings = AppSettings()
    settings.policy_sources = (_config("mof"),)
    mof = _config("mof")
    client = PolicyStubClient(
        {
            mof.list_url: _load("policy_mof_page1.html"),
            mof.pagination_template.format(n=1): _load("policy_mof_page2.html"),
        }
    )
    service = PolicySyncService(settings, storage)
    result = service.sync_once(
        now=NOW, cancel=threading.Event(), client=client, max_pages_per_source=2
    )
    assert result.documents_added >= 40
    assert result.failure_sources == ()
    assert result.pages_consumed >= 2

    documents = storage.get_policy_documents("mof")
    assert len(documents) == result.documents_added
    assert all(doc.source_key == "mof" for doc in documents)
    assert all(doc.body_status == "metadata_only" for doc in documents)

    manifests = storage.get_source_manifests("mof")
    assert manifests
    manifest = manifests[0]
    count, digest = storage.summarize_policy_day(
        "mof", manifest.manifest_date
    )
    assert count == manifest.document_id_count
    assert digest == manifest.document_id_set_hash

    # 政策文档绝不出现在 source_documents（信号管线不可见）。
    source_docs = storage.get_source_documents_between(
        NOW.replace(year=2020), NOW
    )
    assert all(
        doc.document_id not in {d.document_id for d in documents}
        for doc in source_docs
    )


def test_policy_sync_structure_change_keeps_page_one_and_marks_gap(
    tmp_path: Path,
) -> None:
    """生态环境部第 2 页结构变化：第 1 页文档保留，来源标记不可用。"""

    storage = Storage(tmp_path / "hotpot.db")
    settings = AppSettings()
    mee = _config("mee")
    settings.policy_sources = (mee,)
    client = PolicyStubClient(
        {
            mee.list_url: _load("policy_mee_page1.html"),
            mee.pagination_template.format(n=1): _load("policy_mee_page2.html"),
        }
    )
    service = PolicySyncService(settings, storage)
    result = service.sync_once(
        now=NOW, cancel=threading.Event(), client=client, max_pages_per_source=3
    )
    assert result.failure_sources == ("mee",)
    assert result.documents_added > 50  # 第 1 页文档已持久化
    assert result.coverages[0].status == COVERAGE_STATUS_UNAVAILABLE
    assert len(storage.get_policy_documents("mee")) == result.documents_added


def test_policy_sync_fail_closed_source_records_gap(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    settings = AppSettings()
    settings.policy_sources = (_config("nmpa"),)
    client = PolicyStubClient(
        {"fgwj": _load("policy_nmpa_page1.html")}
    )
    service = PolicySyncService(settings, storage)
    result = service.sync_once(
        now=NOW, cancel=threading.Event(), client=client, max_pages_per_source=2
    )
    assert result.failure_sources == ("nmpa",)
    assert result.documents_added == 0
    coverage = result.coverages[0]
    assert coverage.status == COVERAGE_STATUS_UNAVAILABLE
    assert coverage.error

    manifests = storage.get_source_manifests("nmpa")
    assert manifests
    assert manifests[0].failure_intervals
    assert manifests[0].failure_intervals[-1].ended_at is None


def test_policy_sync_partial_coverage_without_pagination(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    settings = AppSettings()
    settings.policy_sources = (_config("miit"),)
    client = PolicyStubClient(
        {"zwgk/zcwj/": _load("policy_miit_page1.html")}
    )
    service = PolicySyncService(settings, storage)
    result = service.sync_once(
        now=NOW, cancel=threading.Event(), client=client, max_pages_per_source=3
    )
    assert result.failure_sources == ()
    assert result.documents_added > 20
    assert result.coverages[0].status == COVERAGE_STATUS_PARTIAL
    assert result.coverages[0].reached_cutoff is True
