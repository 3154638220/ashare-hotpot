from __future__ import annotations

from datetime import datetime, timedelta

from ashare_hotpot.clustering import PersistentEventClusterer
from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import EventCluster, SourceDocument
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)


def _doc(
    document_id: str,
    title: str,
    body: str,
    *,
    codes: tuple[str, ...] = ("000001",),
    published_at: datetime = NOW,
    provider_key: str = "cninfo",
    provider_name: str = "巨潮资讯",
    url: str | None = None,
    content_hash: str = "",
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key=provider_key,
        provider_name=provider_name,
        kind="announcement",
        source_url=url or f"https://example.test/{document_id}",
        document_url=url,
        title=title,
        published_at=published_at,
        stock_codes=codes,
        body_text=body,
        content_hash=content_hash or f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def _run(storage: Storage, *documents: SourceDocument) -> None:
    for document in documents:
        storage.upsert_source_document(document, NOW)
    clusterer = PersistentEventClusterer(storage)
    clusterer.process_window(NOW - timedelta(days=200), NOW + timedelta(days=1), NOW)


def _link_into_second_cluster(
    storage: Storage, document_id: str, stock_code: str
) -> str:
    """Simulate the historical cross-link bug: same doc in a second cluster."""

    event_id = "duplicate-cluster-id"
    storage.upsert_event_cluster(
        EventCluster(
            event_id=event_id,
            stock_codes=(stock_code,),
            canonical_title="重复簇标题",
            first_seen_at=NOW,
            last_seen_at=NOW,
            representative_document_id=document_id,
            document_ids=[document_id],
            historical_similar_event_id=None,
        )
    )
    return event_id


def test_cross_linked_document_consolidates_clusters(tmp_path) -> None:
    """A document linked into a second cluster heals into a single event."""

    storage = Storage(tmp_path / "hotpot.db")
    body = "公司签订重大合同，合同金额1.2亿元。"
    first = _doc("doc-1", "公司签订重大合同公告", body, content_hash="h1")
    second = _doc("doc-2", "公司签订重大合同公告", body, content_hash="h2")
    _run(storage, first, second)
    assert len(
        storage.get_event_clusters_active(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    ) == 1

    duplicate_id = _link_into_second_cluster(storage, "doc-1", "000001")
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=1), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 1
    survivor = clusters[0]
    assert set(survivor.document_ids) == {"doc-1", "doc-2"}
    assert storage.get_event_cluster(duplicate_id) is None


def test_reprocessing_same_document_does_not_duplicate_link(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    doc = _doc("doc-1", "公司签订重大合同公告", "公司签订重大合同，合同金额1.2亿元。")
    _run(storage, doc)
    _run(storage, doc)
    _run(storage, doc)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=1), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 1
    assert clusters[0].document_ids == ["doc-1"]


def test_conflicting_amounts_do_not_churn_clusters(tmp_path) -> None:
    """Already-clustered docs never create new clusters on re-runs."""

    storage = Storage(tmp_path / "hotpot.db")
    first = _doc(
        "doc-1",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.0亿元，占上年营收10%。",
        content_hash="h1",
    )
    second = _doc(
        "doc-2",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额2.0亿元，占上年营收20%。",
        content_hash="h2",
    )
    _run(storage, first, second)
    assert len(
        storage.get_event_clusters_active(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    ) == 2

    _run(storage, first, second)
    _run(storage, first, second)
    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=1), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 2
    by_doc = {doc: None for doc in ("doc-1", "doc-2")}
    for cluster in clusters:
        assert len(cluster.document_ids) == 1
        by_doc[cluster.document_ids[0]] = cluster.event_id
    assert by_doc["doc-1"] != by_doc["doc-2"]


def test_identical_url_merges_into_one_event(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    url = "https://static.cninfo.com.cn/finalpage/2026-08-06/1.PDF"
    first = _doc("doc-1", "公司签订重大合同公告", "公司签订重大合同，合同金额1.2亿元。", url=url)
    second = _doc("doc-2", "公司签订重大合同公告", "公司签订重大合同，合同金额1.2亿元。", url=url)
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert len(clusters) == 1
    assert set(clusters[0].document_ids) == {"doc-1", "doc-2"}


def test_identical_content_hash_merges(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    body = "公司签订重大合同，合同金额1.2亿元，占上年营收10%。"
    first = _doc(
        "doc-1", "标题一", body, url="https://a.test/1", content_hash="same-hash"
    )
    second = _doc(
        "doc-2", "标题二", body, url="https://b.test/2", content_hash="same-hash"
    )
    _run(storage, first, second)
    clusters = storage.get_event_clusters_active(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert len(clusters) == 1
    assert set(clusters[0].document_ids) == {"doc-1", "doc-2"}


def test_cross_source_near_identical_title_merges(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc(
        "doc-1",
        "公司签订重大合同公告",
        "公司近日签订重大合同。",
        provider_key="cninfo",
    )
    second = _doc(
        "doc-2",
        "公司关于签订重大合同的公告",
        "公司近日签订重大合同。",
        provider_key="ths",
        provider_name="同花顺",
    )
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert len(clusters) == 1
    # 巨潮正式披露优先成为代表文档。
    assert clusters[0].representative_document_id == "doc-1"


def test_trigram_similarity_merges_different_wording(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    body = (
        "公司预计2026年上半年归母净利润同比增长30%至40%，"
        "主要原因为产品销量提升及成本改善。"
    )
    first = _doc(
        "doc-1",
        "公司2026年半年度业绩预告",
        body,
        provider_key="cninfo",
    )
    second = _doc(
        "doc-2",
        "公司发布业绩预增公告",
        body,
        provider_key="ths",
        provider_name="同花顺",
    )
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert len(clusters) == 1
    assert set(clusters[0].document_ids) == {"doc-1", "doc-2"}


def test_structured_fingerprint_merges_same_type_and_amount(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc(
        "doc-1",
        "签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
    )
    second = _doc(
        "doc-2",
        "重大合同进展公告",
        "公司重大合同最新进展：合同金额1.2亿元。",
        published_at=NOW - timedelta(hours=1),
    )
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(NOW - timedelta(hours=2), NOW + timedelta(hours=1))
    assert len(clusters) == 1


def test_conflicting_amount_blocks_merge_even_with_similar_title(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc(
        "doc-1",
        "公司签订重大合同金额1.2亿元公告",
        "公司签订重大合同，合同金额1.2亿元，占上年营收10%。",
    )
    second = _doc(
        "doc-2",
        "公司签订重大合同金额3.5亿元公告",
        "公司签订重大合同，合同金额3.5亿元，占上年营收25%。",
        published_at=NOW - timedelta(hours=1),
    )
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(NOW - timedelta(hours=2), NOW + timedelta(hours=1))
    assert len(clusters) == 2


def test_72_hour_boundary_blocks_merge(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc(
        "doc-1",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        published_at=NOW - timedelta(hours=71),
    )
    second = _doc(
        "doc-2",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
    )
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=72), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 1
    assert set(clusters[0].document_ids) == {"doc-1", "doc-2"}

    storage2 = Storage(tmp_path / "hotpot2.db")
    old = _doc(
        "doc-3",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        published_at=NOW - timedelta(hours=73),
    )
    new = _doc(
        "doc-4",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
    )
    _run(storage2, old, new)
    clusters2 = storage2.get_event_clusters_active(
        NOW - timedelta(hours=74), NOW + timedelta(hours=1)
    )
    assert len(clusters2) == 2


def test_73_hour_gap_creates_separate_clusters(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    old = _doc(
        "doc-3",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        published_at=NOW - timedelta(hours=73),
    )
    new = _doc(
        "doc-4",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
    )
    _run(storage, old, new)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=74), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 2


def test_historical_similar_link_within_180_days(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc(
        "doc-1",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
    )
    later = _doc(
        "doc-2",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        published_at=NOW - timedelta(days=10),
    )
    _run(storage, first, later)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(days=20), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 2
    older = next(cluster for cluster in clusters if cluster.event_id != clusters[0].event_id)
    newer = next(cluster for cluster in clusters if cluster.event_id == clusters[0].event_id)
    assert newer.historical_similar_event_id == older.event_id


def test_beyond_180_days_gets_no_historical_link(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc(
        "doc-1",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
    )
    later = _doc(
        "doc-2",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        published_at=NOW - timedelta(days=200),
    )
    _run(storage, first, later)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(days=250), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 2
    assert all(cluster.historical_similar_event_id is None for cluster in clusters)


def test_event_id_is_stable_when_new_source_joins(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc("doc-1", "公司签订重大合同公告", "公司签订重大合同，合同金额1.2亿元。")
    _run(storage, first)
    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=1), NOW + timedelta(hours=1)
    )
    event_id = clusters[0].event_id

    second = _doc(
        "doc-2",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        provider_key="ths",
        provider_name="同花顺",
        published_at=NOW - timedelta(hours=2),
    )
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=3), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 1
    assert clusters[0].event_id == event_id
    assert set(clusters[0].document_ids) == {"doc-1", "doc-2"}


def test_multi_stock_cluster_unions_stock_codes(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc(
        "doc-1",
        "公司与合作方签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        codes=("000001",),
    )
    second = _doc(
        "doc-2",
        "公司与合作方签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        codes=("000001", "600519"),
        published_at=NOW - timedelta(hours=1),
    )
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=2), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 1
    assert set(clusters[0].stock_codes) == {"000001", "600519"}


def test_reprocess_is_idempotent(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _doc("doc-1", "公司签订重大合同公告", "公司签订重大合同，合同金额1.2亿元。")
    second = _doc(
        "doc-2",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        published_at=NOW - timedelta(hours=1),
    )
    _run(storage, first, second)
    _run(storage, first, second)

    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=2), NOW + timedelta(hours=1)
    )
    assert len(clusters) == 1
    assert set(clusters[0].document_ids) == {"doc-1", "doc-2"}
