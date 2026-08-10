from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.discovery import (
    QUEUE_STATUS_AWAITING_REVIEW,
    QUEUE_STATUS_EMPTY_TEXT,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_PENDING_ATTACHMENT,
)
from ashare_hotpot.models import (
    DiscoveryCandidate,
    EventCluster,
    EventSignal,
    RankingRow,
    Snapshot,
    SourceDocument,
    SyncCursor,
)
from ashare_hotpot.storage import (
    BACKUP_NAME,
    BACKUP_NAME_111,
    SCHEMA_VERSION,
    Storage,
)

from test_storage_v110 import _now


EXPECTED_DISCOVERY_TABLES = {"discovery_candidates"}
EXPECTED_DISCOVERY_INDEXES = {
    "idx_discovery_candidates_queue",
    "idx_discovery_candidates_source",
    "idx_discovery_candidates_published",
}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _index_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _sample_document(
    document_id: str,
    *,
    title: str,
    kind: str = "announcement",
    parse_status: str = "metadata_only",
    document_url: str | None = None,
    published_at: datetime | None = None,
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind=kind,
        source_url="https://example.test/list",
        document_url=document_url,
        title=title,
        published_at=published_at or _now(),
        stock_codes=("600390",),
        body_text="",
        content_hash=f"hash-{document_id}",
        parse_status=parse_status,
        parse_error=None,
    )


def _cursor() -> SyncCursor:
    return SyncCursor(
        source_key="cninfo_announcement",
        sync_kind="announcement",
        cursor={"page": 1},
        target_start=_now().date(),
        covered_start=_now().date(),
        last_success_at=_now(),
        last_error=None,
        updated_at=_now(),
    )


def _snapshot() -> Snapshot:
    return Snapshot(
        snapshot_id=None,
        window_start=_now() - timedelta(hours=1),
        window_end=_now(),
        created_at=_now(),
        partial=False,
        coverages=[],
        rankings=[
            RankingRow(
                rank=1,
                code="600390",
                name="五矿资本",
                event_count=1,
                raw_article_count=1,
                latest_mention=_now(),
                event_ids=("e1",),
            )
        ],
        events=[],
        stats={},
    )


def _create_110_database(path) -> Storage:
    """Create a database that looks like a 110 build (no discovery layer)."""

    storage = Storage(path)
    storage.upsert_source_document(
        _sample_document(
            "cninfo:110-1",
            title="关于拟签订重大合同的公告",
            document_url="https://static.cninfo.com.cn/finalpage/x.PDF",
        ),
        _now(),
    )
    storage.upsert_source_document(
        _sample_document(
            "cninfo:110-2",
            title="2026年半年度报告摘要",
            parse_status="parsed",
        ),
        _now(),
    )
    storage.upsert_source_document(
        _sample_document(
            "cninfo:110-3",
            title="2026年半年度报告摘要",
            parse_status="failed",
        ),
        _now(),
    )
    with storage._connect() as connection:
        connection.execute("DROP TABLE IF EXISTS discovery_candidates")
        connection.execute("PRAGMA user_version = 110")
    return storage


# ---------------------------------------------------------------------------
# 新库 / 升级 / 幂等 / 回滚
# ---------------------------------------------------------------------------


def test_fresh_database_reports_120_and_discovery_schema(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 121
        assert EXPECTED_DISCOVERY_TABLES <= _table_names(connection)
        assert EXPECTED_DISCOVERY_INDEXES <= _index_names(connection)
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(discovery_candidates)"
            ).fetchall()
        }
        assert {"signal_priority", "queue_status", "enqueued_ts"} <= columns


def test_110_database_upgrades_to_120_with_backup_once_and_backfill(
    tmp_path,
) -> None:
    path = tmp_path / "hotpot.db"
    _create_110_database(path)

    storage = Storage(path)

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 121
        assert EXPECTED_DISCOVERY_TABLES <= _table_names(connection)
    # 迁移回填：附件文档进入待解析队列，已解析文档进入待核验，失败文档保持失败。
    statuses = {
        candidate.document_id: candidate.queue_status
        for candidate in storage.get_discovery_candidates()
    }
    assert statuses["cninfo:110-1"] == QUEUE_STATUS_PENDING_ATTACHMENT
    assert statuses["cninfo:110-2"] == QUEUE_STATUS_AWAITING_REVIEW
    assert statuses["cninfo:110-3"] == QUEUE_STATUS_FAILED
    assert storage.get_discovery_candidates()[0].source_key == "cninfo_announcement"

    # pre-111 备份只创建一次；重复初始化不覆盖备份、不重复入队。
    backup_path = path.with_name(BACKUP_NAME_111)
    assert backup_path.exists()
    backup_before = backup_path.read_bytes()
    storage.initialize()
    assert backup_path.read_bytes() == backup_before
    assert len(storage.get_discovery_candidates()) == 3
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 121


def test_111_migration_rolls_back_and_keeps_110_database(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "hotpot.db"
    _create_110_database(path)

    monkeypatch.setattr(
        "ashare_hotpot.storage.DISCOVERY_TABLE_STATEMENTS", ("THIS IS NOT SQL",)
    )

    with pytest.raises(sqlite3.OperationalError):
        Storage(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 110
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 3
    finally:
        connection.close()
    assert path.with_name(BACKUP_NAME_111).exists()
    # 修复后重试可升级。
    monkeypatch.undo()
    storage = Storage(path)
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 121


def test_v0_migration_chain_creates_all_backups(tmp_path) -> None:
    """版本 0 数据库仍走 pre-110 备份 + 逐级升级到 120（不再停留在 110/111）。"""

    from ashare_hotpot.storage import LEGACY_SCHEMA

    path = tmp_path / "hotpot.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    connection.commit()
    connection.close()

    storage = Storage(path)
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 121
        assert EXPECTED_DISCOVERY_TABLES <= _table_names(connection)
    assert path.with_name(BACKUP_NAME).exists()
    assert path.with_name("hotpot.db.pre-120.bak").exists() and path.with_name("hotpot.db.pre-121.bak").exists()


# ---------------------------------------------------------------------------
# 队列状态、最早待处理时间、统计与 promoted 判定
# ---------------------------------------------------------------------------


def _candidate(
    document_id: str,
    *,
    status: str,
    source_key: str = "cninfo_announcement",
    title: str = "关于拟签订重大合同的公告",
    document_url: str | None = "https://static.cninfo.com.cn/finalpage/x.PDF",
    signal_priority: bool = False,
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        document_id=document_id,
        source_key=source_key,
        source_name="巨潮资讯公告",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        stock_codes=("600390",),
        title=title,
        published_at=_now(),
        discovery_type="contract_order",
        trigger_reason="标题含“重大合同”",
        queue_status=status,
        attachment_type="PDF",
        document_url=document_url,
        enqueued_at=_now() if status == QUEUE_STATUS_PENDING_ATTACHMENT else None,
        updated_at=_now(),
        signal_priority=signal_priority,
    )


def test_queue_status_roundtrip_and_earliest_enqueue_is_preserved(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(
        _sample_document(
            "doc-1",
            title="关于拟签订重大合同的公告",
            document_url="https://static.cninfo.com.cn/finalpage/x.PDF",
        ),
        _now(),
    )
    first = _candidate("doc-1", status=QUEUE_STATUS_PENDING_ATTACHMENT)
    storage.save_research_batch(
        [_sample_document("doc-1", title="关于拟签订重大合同的公告", document_url="https://static.cninfo.com.cn/finalpage/x.PDF")],
        [first],
        _cursor(),
        _now(),
    )
    candidate = storage.get_discovery_candidates()[0]
    assert candidate.queue_status == QUEUE_STATUS_PENDING_ATTACHMENT

    # 重新入队不刷新最早待处理时间；离开队列后清空 enqueued_ts。
    later = _now() + timedelta(hours=2)
    reenqueue = _candidate(
        "doc-1",
        status=QUEUE_STATUS_PENDING_ATTACHMENT,
        signal_priority=True,
    )
    storage.save_research_batch(
        [_sample_document("doc-1", title="关于拟签订重大合同的公告", document_url="https://static.cninfo.com.cn/finalpage/x.PDF")],
        [reenqueue],
        _cursor(),
        later,
    )
    candidate = storage.get_discovery_candidates()[0]
    assert candidate.enqueued_at == _now()

    storage.set_discovery_queue_status("doc-1", QUEUE_STATUS_AWAITING_REVIEW, later)
    candidate = storage.get_discovery_candidates()[0]
    assert candidate.queue_status == QUEUE_STATUS_AWAITING_REVIEW
    assert candidate.enqueued_at is None


def test_discovery_stats_per_source_and_earliest_pending(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.save_research_batch(
        [
            _sample_document("doc-1", title="a", document_url="u1"),
            _sample_document("doc-2", title="b", document_url="u2"),
            _sample_document("doc-3", title="c"),
        ],
        [
            _candidate("doc-1", status=QUEUE_STATUS_PENDING_ATTACHMENT),
            _candidate("doc-2", status=QUEUE_STATUS_FAILED),
            _candidate("doc-3", status=QUEUE_STATUS_AWAITING_REVIEW, document_url=None),
        ],
        _cursor(),
        _now(),
    )
    stats = storage.get_discovery_stats()
    assert len(stats) == 1
    row = stats[0]
    assert row["discovered"] == 3
    assert row["pending"] == 1
    assert row["awaiting"] == 1
    assert row["empty_text"] == 0
    assert row["failed"] == 1
    assert row["earliest_pending_ts"] == int(_now().timestamp())


def test_promoted_document_ids_join_with_signals(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    storage.upsert_source_document(
        _sample_document("doc-sig", title="回购报告书", parse_status="parsed"), now
    )
    cluster = EventCluster(
        event_id="event-1",
        stock_codes=("600390",),
        canonical_title="回购报告书",
        first_seen_at=now,
        last_seen_at=now,
        representative_document_id="doc-sig",
        document_ids=["doc-sig"],
        historical_similar_event_id=None,
    )
    storage.upsert_event_cluster(cluster)
    storage.link_event_document("event-1", "doc-sig")
    storage.upsert_event_signal(
        EventSignal(
            event_id="event-1",
            stock_code="600390",
            board="confirmed_positive",
            score=80.0,
            source_confidence=0.9,
            materiality_level=2,
            certainty=0.9,
            unexpectedness=0.5,
            novelty=0.5,
            timeliness=0.8,
            penalty=0.0,
            provisional=False,
        ),
        created_at=now,
    )
    assert storage.get_promoted_document_ids() == {"doc-sig"}


# ---------------------------------------------------------------------------
# 清理 / clear_all / 统计
# ---------------------------------------------------------------------------


def test_retention_purge_removes_discovery_candidates_with_documents(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    old = _now() - timedelta(days=500)
    storage.upsert_source_document(
        _sample_document(
            "old-doc", title="旧公告", document_url="u", published_at=old
        ),
        old,
    )
    storage.save_research_batch(
        [
            _sample_document(
                "old-doc", title="旧公告", document_url="u", published_at=old
            )
        ],
        [_candidate("old-doc", status=QUEUE_STATUS_PENDING_ATTACHMENT)],
        _cursor(),
        old,
    )
    storage.upsert_source_document(
        _sample_document("new-doc", title="新公告", document_url="u2"), _now()
    )
    storage.save_research_batch(
        [_sample_document("new-doc", title="新公告", document_url="u2")],
        [_candidate("new-doc", status=QUEUE_STATUS_PENDING_ATTACHMENT)],
        _cursor(),
        _now(),
    )

    storage.purge_research_retention(_now())

    remaining = {candidate.document_id for candidate in storage.get_discovery_candidates()}
    assert remaining == {"new-doc"}


def test_clear_all_removes_discovery_candidates_and_stats_include_count(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.save_research_batch(
        [_sample_document("doc-1", title="a", document_url="u")],
        [_candidate("doc-1", status=QUEUE_STATUS_PENDING_ATTACHMENT)],
        _cursor(),
        _now(),
    )
    assert storage.get_storage_stats().discovery_candidate_count == 1

    storage.clear_all()

    assert storage.get_discovery_candidates() == []
    assert storage.get_storage_stats().discovery_candidate_count == 0


def test_old_snapshot_reads_after_111_upgrade(tmp_path) -> None:
    """110 时代保存的快照（只含原四榜）在 111 上仍可读取。"""

    path = tmp_path / "hotpot.db"
    storage = _create_110_database(path)
    storage.save_snapshot(_snapshot())
    storage = Storage(path)

    snapshot = storage.load_latest_snapshot()
    assert snapshot is not None
    assert snapshot.rankings  # 原四榜仍可读
