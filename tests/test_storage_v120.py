"""v1.2/v2 schema 111→120→121 迁移与覆盖层存储 (plan.md 第二、三部分)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.coverage import (
    COVERAGE_STATUS_LIST_RECONCILED,
    COVERAGE_STATUS_REALTIME_PROVISIONAL,
    OCR_PAGE_STATUS_LOW_CONFIDENCE,
    OCR_STATUS_DONE,
    POLICY_LINK_NAMED_COMPANY,
    summarize_document_ids,
)
from ashare_hotpot.models import (
    CoverageSnapshot,
    FailureInterval,
    Institution,
    OcrPageResult,
    PolicyDocument,
    PolicyLink,
    RankingRow,
    Snapshot,
    SourceDocument,
    SourceManifest,
    SyncCursor,
)
from ashare_hotpot.storage import (
    BACKUP_NAME,
    BACKUP_NAME_111,
    BACKUP_NAME_120,
    BACKUP_NAME_121,
    SCHEMA_VERSION,
    Storage,
)

from test_storage_v110 import _now


EXPECTED_V120_TABLES = {
    "source_manifests",
    "policy_documents",
    "policy_links",
    "ocr_pages",
    "coverage_snapshots",
}
EXPECTED_V120_INDEXES = {
    "idx_source_manifests_date",
    "idx_source_manifests_status",
    "idx_policy_documents_source",
    "idx_policy_links_policy",
    "idx_policy_links_stock",
    "idx_ocr_pages_status",
    "idx_coverage_snapshots_ts",
}
EXPECTED_V121_TABLES = {
    "event_claims",
    "research_participant_mentions",
    "reported_participant_counts",
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


def _create_111_database(path) -> Storage:
    """Create a database that looks like a 111 build (no coverage layer)."""

    storage = Storage(path)
    storage.upsert_source_document(
        _sample_document("sse:111-1", title="投资者关系活动记录表"),
        _now(),
    )
    storage.save_snapshot(_snapshot())
    with storage._connect() as connection:
        connection.execute("DROP TABLE IF EXISTS coverage_snapshots")
        connection.execute("DROP TABLE IF EXISTS source_manifests")
        connection.execute("DROP TABLE IF EXISTS policy_links")
        connection.execute("DROP TABLE IF EXISTS policy_documents")
        connection.execute("DROP TABLE IF EXISTS ocr_pages")
        connection.execute("PRAGMA user_version = 111")
    return storage


def _sample_document(
    document_id: str,
    *,
    title: str,
    parse_status: str = "metadata_only",
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="sse",
        provider_name="上交所",
        kind="announcement",
        source_url="https://example.test/list",
        document_url=None,
        title=title,
        published_at=_now(),
        stock_codes=("600390",),
        body_text="",
        content_hash=f"hash-{document_id}",
        parse_status=parse_status,
        parse_error=None,
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


def _manifest(
    source_key: str = "sse_announcement",
    *,
    manifest_date: date | None = None,
    updated_at: datetime | None = None,
    total_count: int = 5,
    document_ids: tuple[str, ...] = ("a", "b", "c"),
    coverage_status: str = COVERAGE_STATUS_LIST_RECONCILED,
) -> SourceManifest:
    count, digest = summarize_document_ids(document_ids)
    return SourceManifest(
        source_key=source_key,
        manifest_date=manifest_date or _now().date(),
        total_count=total_count,
        document_id_count=count,
        document_id_set_hash=digest,
        watermark={"page": 1},
        failure_intervals=(),
        ocr_status=OCR_STATUS_DONE,
        scheduled_task_result={"triggered": True},
        coverage_status=coverage_status,
        updated_at=updated_at or _now(),
    )


def _policy_document(
    document_id: str,
    *,
    source_key: str = "policy_miit",
    published_at: datetime | None = None,
) -> PolicyDocument:
    return PolicyDocument(
        document_id=document_id,
        source_key=source_key,
        title="关于推动产业高质量发展的意见",
        published_at=published_at or _now(),
        source_url="https://example.test/policy",
        document_url=None,
        body_text="正文",
        body_hash="body-hash",
        body_status="parsed",
        body_error=None,
        content_hash=f"content-{document_id}",
        updated_at=_now(),
    )


# ---------------------------------------------------------------------------
# 新库 / 升级 / 幂等 / 回滚
# ---------------------------------------------------------------------------


def test_fresh_database_reports_121_and_coverage_schema(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V120_TABLES <= _table_names(connection)
        assert EXPECTED_V121_TABLES <= _table_names(connection)
        assert EXPECTED_V120_INDEXES <= _index_names(connection)
        manifest_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_manifests)"
            ).fetchall()
        }
        assert {
            "total_count",
            "document_id_count",
            "document_id_set_hash",
            "watermark_json",
            "failure_intervals_json",
            "ocr_status",
            "scheduled_task_result_json",
            "coverage_status",
        } <= manifest_columns


def test_111_database_upgrades_to_121_with_backup_once_and_stays_idempotent(
    tmp_path,
) -> None:
    path = tmp_path / "hotpot.db"
    _create_111_database(path)

    storage = Storage(path)

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V120_TABLES <= _table_names(connection)
        assert EXPECTED_V121_TABLES <= _table_names(connection)

    # pre-120/pre-121 备份只创建一次；重复初始化不覆盖备份、不重复建表。
    backup_path = path.with_name(BACKUP_NAME_120)
    assert backup_path.exists()
    backup_before = backup_path.read_bytes()
    assert path.with_name(BACKUP_NAME_121).exists()
    storage.initialize()
    assert backup_path.read_bytes() == backup_before
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_110_database_walks_111_then_120_then_121_with_backups(tmp_path) -> None:
    """110 数据库沿 111 → 120 → 121 链路升级，各级备份各创建一次。"""

    from test_storage_v111 import _create_110_database

    path = tmp_path / "hotpot.db"
    _create_110_database(path)

    storage = Storage(path)

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V120_TABLES <= _table_names(connection)
        assert EXPECTED_V121_TABLES <= _table_names(connection)
    assert path.with_name(BACKUP_NAME_111).exists()
    assert path.with_name(BACKUP_NAME_120).exists()
    assert path.with_name(BACKUP_NAME_121).exists()
    # 110 时代的文档仍保留并进入待核验队列。
    assert len(storage.get_discovery_candidates()) == 3


def test_v0_migration_chain_creates_all_four_backups(tmp_path) -> None:
    from ashare_hotpot.storage import LEGACY_SCHEMA

    path = tmp_path / "hotpot.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    connection.commit()
    connection.close()

    storage = Storage(path)
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V120_TABLES <= _table_names(connection)
        assert EXPECTED_V121_TABLES <= _table_names(connection)
    assert path.with_name(BACKUP_NAME).exists()
    assert path.with_name(BACKUP_NAME_111).exists()
    assert path.with_name(BACKUP_NAME_120).exists()
    assert path.with_name(BACKUP_NAME_121).exists()


def test_120_migration_rolls_back_and_keeps_111_database(tmp_path, monkeypatch) -> None:
    path = tmp_path / "hotpot.db"
    _create_111_database(path)

    monkeypatch.setattr(
        "ashare_hotpot.storage.V120_TABLE_STATEMENTS", ("THIS IS NOT SQL",)
    )

    with pytest.raises(sqlite3.OperationalError):
        Storage(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 111
        assert "source_manifests" not in _table_names(connection)
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 1
    finally:
        connection.close()
    assert path.with_name(BACKUP_NAME_120).exists()

    # 修复后重试可升级到 121。
    monkeypatch.undo()
    storage = Storage(path)
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_V120_TABLES <= _table_names(connection)
        assert EXPECTED_V121_TABLES <= _table_names(connection)


def test_old_111_snapshot_reads_after_121_upgrade(tmp_path) -> None:
    """111 时代保存的快照（只含原四榜）在 121 上仍可读取。"""

    path = tmp_path / "hotpot.db"
    storage = _create_111_database(path)
    storage = Storage(path)

    snapshot = storage.load_latest_snapshot()
    assert snapshot is not None
    assert snapshot.rankings  # 原四榜仍可读


# ---------------------------------------------------------------------------
# 覆盖层 CRUD 往返
# ---------------------------------------------------------------------------


def test_source_manifest_upsert_roundtrip_and_digest(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_manifest(_manifest())
    storage.upsert_source_manifest(
        _manifest(
            source_key="policy_gov",
            coverage_status=COVERAGE_STATUS_REALTIME_PROVISIONAL,
        )
    )

    manifests = storage.get_source_manifests()
    assert len(manifests) == 2
    assert manifests[0].source_key == "policy_gov"  # 同日按 source_key 排序
    assert manifests[1].coverage_status == COVERAGE_STATUS_LIST_RECONCILED

    count, digest = storage.get_manifest_digest(
        "sse_announcement", _now().date()
    )
    assert count == 3
    assert digest == summarize_document_ids(("a", "b", "c"))[1]

    # upsert 覆盖同一天同一来源。
    storage.upsert_source_manifest(
        _manifest(document_ids=("a", "b", "c", "d"), total_count=6)
    )
    assert storage.get_manifest_digest("sse_announcement", _now().date()) == (
        4,
        summarize_document_ids(("a", "b", "c", "d"))[1],
    )
    manifests = storage.get_source_manifests(source_key="sse_announcement")
    assert manifests[0].total_count == 6
    assert manifests[0].document_id_count == 4


def test_manifest_failure_intervals_and_scheduled_task_roundtrip(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    manifest = SourceManifest(
        source_key="bse_announcement",
        manifest_date=now.date(),
        total_count=10,
        document_id_count=10,
        document_id_set_hash="h",
        watermark={"cursor": {"page": 2}},
        failure_intervals=(
            FailureInterval(started_at=now, ended_at=None, reason="登录页"),
        ),
        ocr_status="pending",
        scheduled_task_result={"exit_code": 2, "message": "网络不可用"},
        coverage_status="unavailable",
        updated_at=now,
    )
    storage.upsert_source_manifest(manifest)

    restored = storage.get_source_manifests("bse_announcement")[0]
    assert restored.failure_intervals[0].reason == "登录页"
    assert restored.failure_intervals[0].ended_at is None
    assert restored.scheduled_task_result == {"exit_code": 2, "message": "网络不可用"}
    assert restored.coverage_status == "unavailable"


def test_policy_document_and_link_roundtrip_with_cascade(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    document = _policy_document("pol-1")
    storage.upsert_policy_document(document)
    storage.upsert_policy_document(
        _policy_document("pol-2", source_key="policy_gov", published_at=_now() - timedelta(days=2))
    )

    documents = storage.get_policy_documents()
    assert [doc.document_id for doc in documents] == ["pol-1", "pol-2"]
    assert storage.get_policy_documents(source_key="policy_gov")[0].document_id == "pol-2"

    link = PolicyLink(
        link_id="link-1",
        policy_document_id="pol-1",
        target_document_id="ann-1",
        stock_code="600390",
        link_kind=POLICY_LINK_NAMED_COMPANY,
        evidence_excerpt="政策点名",
        evidence_id="ev-1",
        created_at=_now(),
    )
    storage.upsert_policy_link(link)
    assert storage.get_policy_links(stock_code="600390")[0].evidence_excerpt == "政策点名"
    assert storage.get_policy_links(policy_document_id="pol-1")[0].link_id == "link-1"

    # 政策文档删除时链接级联清理。
    storage.upsert_policy_document(
        _policy_document("pol-1", published_at=_now() - timedelta(days=500))
    )
    storage.purge_coverage_retention(_now())
    assert storage.get_policy_links() == []


def test_ocr_page_roundtrip_requires_existing_document(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(
        _sample_document("doc-1", title="扫描版公告"),
        _now(),
    )
    storage.save_ocr_page(
        OcrPageResult(
            document_id="doc-1",
            page_index=0,
            confidence=0.71,
            text="低置信页",
            model_version="ppocr-v4",
            evidence_url="https://example.test/1.pdf#page=1",
            status=OCR_PAGE_STATUS_LOW_CONFIDENCE,
            error=None,
            updated_at=_now(),
        )
    )
    storage.save_ocr_page(
        OcrPageResult(
            document_id="doc-1",
            page_index=1,
            confidence=0.95,
            text="高置信页",
            model_version="ppocr-v4",
            evidence_url="https://example.test/1.pdf#page=2",
            status="ok",
            error=None,
            updated_at=_now(),
        )
    )

    pages = storage.get_ocr_pages("doc-1")
    assert [page.page_index for page in pages] == [0, 1]
    assert pages[0].status == OCR_PAGE_STATUS_LOW_CONFIDENCE
    assert pages[1].confidence == 0.95

    # 未知文档的 OCR 页被外键拒绝（不伪造证据）。
    with pytest.raises(sqlite3.IntegrityError):
        storage.save_ocr_page(
            OcrPageResult(
                document_id="missing",
                page_index=0,
                confidence=0.9,
                text="",
                model_version="ppocr-v4",
                evidence_url=None,
                status="ok",
                error=None,
                updated_at=_now(),
            )
        )


def test_coverage_snapshot_save_and_latest(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    storage.save_coverage_snapshot(
        CoverageSnapshot(
            snapshot_id="snap-1",
            snapshot_ts=now,
            statuses={"sse_announcement": COVERAGE_STATUS_LIST_RECONCILED},
            manifest_count=1,
            policy_document_count=0,
            ocr_pending_count=0,
            provisional=False,
            error=None,
        )
    )
    storage.save_coverage_snapshot(
        CoverageSnapshot(
            snapshot_id="snap-2",
            snapshot_ts=now + timedelta(minutes=5),
            statuses={"sse_announcement": COVERAGE_STATUS_REALTIME_PROVISIONAL},
            manifest_count=1,
            policy_document_count=1,
            ocr_pending_count=2,
            provisional=True,
            error="bse 不可用",
        )
    )

    latest = storage.get_latest_coverage_snapshot()
    assert latest.snapshot_id == "snap-2"
    assert latest.provisional is True
    assert latest.ocr_pending_count == 2
    assert latest.statuses["sse_announcement"] == COVERAGE_STATUS_REALTIME_PROVISIONAL


# ---------------------------------------------------------------------------
# 清理 / 统计 / 保留周期
# ---------------------------------------------------------------------------


def test_clear_all_removes_coverage_tables_and_stats_include_counts(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(_sample_document("doc-1", title="公告"), _now())
    storage.upsert_source_manifest(_manifest())
    storage.upsert_policy_document(_policy_document("pol-1"))
    storage.save_ocr_page(
        OcrPageResult(
            document_id="doc-1",
            page_index=0,
            confidence=0.9,
            text="",
            model_version="ppocr-v4",
            evidence_url=None,
            status="ok",
            error=None,
            updated_at=_now(),
        )
    )
    storage.save_coverage_snapshot(
        CoverageSnapshot(
            snapshot_id="snap-1",
            snapshot_ts=_now(),
            statuses={},
            manifest_count=0,
            policy_document_count=0,
            ocr_pending_count=0,
            provisional=False,
            error=None,
        )
    )

    stats = storage.get_storage_stats()
    assert stats.source_manifest_count == 1
    assert stats.policy_document_count == 1
    assert stats.ocr_page_count == 1
    assert stats.coverage_snapshot_count == 1

    storage.clear_all()

    assert storage.get_source_manifests() == []
    assert storage.get_policy_documents() == []
    assert storage.get_ocr_pages("doc-1") == []
    assert storage.get_latest_coverage_snapshot() is None
    stats = storage.get_storage_stats()
    assert stats.source_manifest_count == 0
    assert stats.policy_document_count == 0
    assert stats.ocr_page_count == 0
    assert stats.coverage_snapshot_count == 0


def test_retention_purge_keeps_30_day_manifests_and_400_day_policies(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    old_manifest = _manifest(
        source_key="sse_announcement",
        manifest_date=(now - timedelta(days=40)).date(),
        updated_at=now - timedelta(days=40),
    )
    recent_manifest = _manifest(
        source_key="bse_announcement",
        manifest_date=now.date(),
        updated_at=now,
    )
    storage.upsert_source_manifest(old_manifest)
    storage.upsert_source_manifest(recent_manifest)
    storage.upsert_policy_document(
        _policy_document("pol-old", published_at=now - timedelta(days=500))
    )
    storage.upsert_policy_document(
        _policy_document("pol-new", published_at=now - timedelta(days=100))
    )
    storage.upsert_policy_link(
        PolicyLink(
            link_id="link-old",
            policy_document_id="pol-old",
            target_document_id=None,
            stock_code=None,
            link_kind="industry_watch",
            evidence_excerpt="行业观察",
            evidence_id=None,
            created_at=now,
        )
    )

    storage.purge_coverage_retention(now)

    assert [m.source_key for m in storage.get_source_manifests()] == [
        "bse_announcement"
    ]
    assert [d.document_id for d in storage.get_policy_documents()] == ["pol-new"]
    assert storage.get_policy_links() == []


def test_purge_coverage_retention_never_touches_institutions_or_cursors(
    tmp_path,
) -> None:
    """覆盖层保留周期不得误删机构历史或同步游标。"""

    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    storage.save_sync_state(
        SyncCursor(
            source_key="sse_announcement",
            sync_kind="announcement",
            cursor={"page": 1},
            target_start=now.date(),
            covered_start=now.date(),
            last_success_at=now,
            last_error=None,
            updated_at=now,
        )
    )
    storage.upsert_institution(
        Institution(
            institution_id="inst-1",
            canonical_name="某基金",
            group_id="inst-1",
            institution_type="fund",
            verification_status="verified",
        ),
        created_at=now - timedelta(days=500),
    )

    storage.purge_coverage_retention(now)

    assert storage.list_sync_states()  # 游标保留
    assert storage.get_institution("inst-1") is not None  # 机构保留
