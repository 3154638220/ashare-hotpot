"""v2 优化计划里程碑 1 固定回归集：同股同事件重复聚类（plan.md 第三部分）。

覆盖 688381、001389、600196、600581、603001 五只股票的真实样本缺陷：
半年报与摘要、同标题药品批文、同次回购文件（方案/核查意见）本应合并为
一个事件簇，正文金额差异不得把同一事件拆成重复事件。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from ashare_hotpot.clustering import PersistentEventClusterer
from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import SourceDocument
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 8, 0, 0, 0, tzinfo=SHANGHAI_TZ)


def _doc(
    document_id: str,
    title: str,
    body: str,
    *,
    codes: tuple[str, ...],
    published_at: datetime = NOW,
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url="https://www.cninfo.com.cn/new/hisAnnouncement/query",
        document_url=None,
        title=title,
        published_at=published_at,
        stock_codes=codes,
        body_text=body,
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def _run(storage: Storage, *documents: SourceDocument) -> list:
    for document in documents:
        storage.upsert_source_document(document, NOW)
    clusterer = PersistentEventClusterer(storage)
    clusterer.process_window(
        NOW - timedelta(days=10), NOW + timedelta(days=1), NOW
    )
    return storage.get_event_clusters_active(
        NOW - timedelta(days=10), NOW + timedelta(days=1)
    )


def _cluster_docs(storage: Storage, event_id: str) -> set[str]:
    return set(storage.get_event_cluster(event_id).document_ids)


def test_001389_half_year_report_and_summary_merge(tmp_path: Path) -> None:
    """半年报正文与摘要同一事件：正文金额差异不得拆成两个簇。"""

    storage = Storage(tmp_path / "hotpot.db")
    clusters = _run(
        storage,
        _doc(
            "doc-001389-report",
            "2026年半年度报告",
            "公司2026年上半年归母净利润9.56亿元，同比增长94.39%，上年同期4.92亿元。",
            codes=("001389",),
        ),
        _doc(
            "doc-001389-summary",
            "2026年半年度报告摘要",
            "公司2026年上半年实现营业收入增长，归母净利润同比大幅增长。",
            codes=("001389",),
        ),
    )
    assert len(clusters) == 1
    assert _cluster_docs(storage, clusters[0].event_id) == {
        "doc-001389-report",
        "doc-001389-summary",
    }


def test_600581_half_year_report_and_summary_merge(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    clusters = _run(
        storage,
        _doc(
            "doc-600581-report",
            "八一钢铁2026年半年度报告",
            "公司2026年上半年实现营业收入增长，归母净利润同比增加。",
            codes=("600581",),
        ),
        _doc(
            "doc-600581-summary",
            "八一钢铁2026年半年度报告摘要",
            "公司2026年上半年归母净利润同比增加。",
            codes=("600581",),
        ),
    )
    assert len(clusters) == 1


def test_603001_half_year_report_and_summary_merge(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    clusters = _run(
        storage,
        _doc(
            "doc-603001-report",
            "2026年半年度报告",
            "公司2026年上半年归母净利润同比增长。",
            codes=("603001",),
        ),
        _doc(
            "doc-603001-summary",
            "2026年半年度报告摘要",
            "公司2026年上半年实现营业收入增长。",
            codes=("603001",),
        ),
    )
    assert len(clusters) == 1


def test_600196_identical_title_approval_merges(tmp_path: Path) -> None:
    """同股票同标题药品批文（复星医药）即使正文金额不同也合并。"""

    storage = Storage(tmp_path / "hotpot.db")
    clusters = _run(
        storage,
        _doc(
            "doc-600196-a",
            "复星医药关于控股子公司药品获注册批准的公告",
            "近日，公司控股子公司收到国家药品监督管理局核准签发的《药品注册证书》。",
            codes=("600196",),
        ),
        _doc(
            "doc-600196-b",
            "复星医药关于控股子公司药品获注册批准的公告",
            "公司控股子公司产品获得药品注册批准，可开展相关商业化活动。",
            codes=("600196",),
        ),
    )
    assert len(clusters) == 1
    assert _cluster_docs(storage, clusters[0].event_id) == {
        "doc-600196-a",
        "doc-600196-b",
    }


def test_688381_buyback_family_merges_and_director_election_stays_separate(
    tmp_path: Path,
) -> None:
    """同次回购文件（方案+核查意见）合并；无关董事选举公告不得并入回购簇。"""

    storage = Storage(tmp_path / "hotpot.db")
    clusters = _run(
        storage,
        _doc(
            "doc-688381-plan",
            "关于以集中竞价交易方式回购股份方案的公告",
            "公司拟以集中竞价交易方式回购公司股份，回购资金总额不低于3000万元，"
            "不超过6305万元，回购价格上限50元/股。",
            codes=("688381",),
        ),
        _doc(
            "doc-688381-opinion",
            "中信建投证券股份有限公司关于江苏帝奥微电子股份有限公司"
            "使用部分超募资金回购股份的核查意见",
            "公司本次回购股份资金总额不超过6305万元，使用部分超募资金回购。",
            codes=("688381",),
        ),
        _doc(
            "doc-688381-director",
            "关于选举第三届董事会职工代表董事的公告",
            "公司第三届董事会职工代表董事选举产生。",
            codes=("688381",),
        ),
    )
    # 回购方案与核查意见合并为一簇；董事选举独立成簇。
    assert len(clusters) == 2
    buyback_clusters = [
        cluster
        for cluster in clusters
        if "回购" in cluster.canonical_title
    ]
    assert len(buyback_clusters) == 1
    assert _cluster_docs(storage, buyback_clusters[0].event_id) == {
        "doc-688381-plan",
        "doc-688381-opinion",
    }
    director_clusters = [
        cluster
        for cluster in clusters
        if "董事" in cluster.canonical_title
    ]
    assert len(director_clusters) == 1
    assert _cluster_docs(storage, director_clusters[0].event_id) == {
        "doc-688381-director"
    }


def test_reprocess_merges_cross_cluster_same_day_family(tmp_path: Path) -> None:
    """重跑时跨簇合并：同股票同日公告族即使已分属两个簇也收敛为单簇。"""

    storage = Storage(tmp_path / "hotpot.db")
    docs = [
        _doc(
            "doc-600196-a",
            "复星医药关于控股子公司药品获注册批准的公告",
            "近日，公司控股子公司收到国家药品监督管理局核准签发的《药品注册证书》。",
            codes=("600196",),
        ),
        _doc(
            "doc-600196-b",
            "复星医药关于控股子公司药品获注册批准的公告",
            "公司控股子公司产品获得药品注册批准，可开展相关商业化活动。",
            codes=("600196",),
        ),
    ]
    for document in docs:
        storage.upsert_source_document(document, NOW)
    # 第一轮只处理第一份文档（模拟历史运行）。
    PersistentEventClusterer(storage).process_documents(docs[:1], NOW)
    # 第二轮处理第二份文档（模拟增量刷新）。
    PersistentEventClusterer(storage).process_documents(docs[1:], NOW)
    clusters = storage.get_event_clusters_active(
        NOW - timedelta(days=1), NOW + timedelta(days=1)
    )
    assert len(clusters) == 1
    assert _cluster_docs(storage, clusters[0].event_id) == {
        "doc-600196-a",
        "doc-600196-b",
    }
