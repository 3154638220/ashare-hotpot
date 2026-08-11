from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import (
    EventCluster,
    EventExtraction,
    EventSignal,
    EvidenceRef,
    Institution,
    InstitutionAlias,
    ParsedArticle,
    ResearchActivity,
    ResearchParticipant,
    Snapshot,
    SourceDocument,
    SyncCursor,
)
from ashare_hotpot.storage import (
    BACKUP_NAME,
    LEGACY_SCHEMA,
    SCHEMA_VERSION,
    Storage,
)


EXPECTED_RESEARCH_TABLES = {
    "source_documents",
    "source_document_stocks",
    "evidence_refs",
    "event_clusters",
    "event_cluster_stocks",
    "event_cluster_documents",
    "event_extractions",
    "event_signals",
    "institutions",
    "institution_aliases",
    "research_activities",
    "research_activity_dates",
    "research_participants",
    "institution_metric_snapshots",
    "source_sync_state",
    "trading_days",
}

EXPECTED_RESEARCH_INDEXES = {
    "idx_source_documents_published",
    "idx_source_documents_hash",
    "idx_source_document_stocks_code",
    "idx_evidence_refs_document",
    "idx_event_clusters_first_seen",
    "idx_event_clusters_last_seen",
    "idx_event_cluster_stocks_code",
    "idx_event_cluster_documents_doc",
    "idx_event_signals_board_score",
    "idx_institutions_group",
    "idx_institution_aliases_inst",
    "idx_research_activities_stock",
    "idx_research_activity_dates_date",
    "idx_research_participants_inst",
    "idx_metric_snapshots_window",
    "idx_metric_snapshots_stock_window",
    "idx_trading_days_year",
}


# Exact v0.2.0 table contract.  That release had no ``interactions`` table,
# which is the historical upgrade path covered by the v1.2.2 repair.
V020_SCHEMA = """
CREATE TABLE articles (
    url TEXT PRIMARY KEY,
    seq TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_ts INTEGER NOT NULL,
    channel_key TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    source_name TEXT NOT NULL,
    stocks_json TEXT NOT NULL,
    filtered_reason TEXT,
    fetch_error TEXT,
    fetched_ts INTEGER NOT NULL
);
CREATE INDEX idx_articles_published ON articles(published_ts);

CREATE TABLE refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts INTEGER NOT NULL,
    finished_ts INTEGER,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts INTEGER NOT NULL,
    window_start_ts INTEGER NOT NULL,
    window_end_ts INTEGER NOT NULL,
    partial INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX idx_snapshots_created ON snapshots(created_ts DESC);

CREATE TABLE stock_industries (
    code TEXT PRIMARY KEY,
    industry TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE TABLE guba_stock_catalog (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE TABLE guba_posts (
    post_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_ts INTEGER NOT NULL,
    author TEXT NOT NULL,
    comment_count INTEGER NOT NULL,
    fetched_ts INTEGER NOT NULL
);
CREATE INDEX idx_guba_posts_published ON guba_posts(published_ts);
CREATE INDEX idx_guba_posts_code_published ON guba_posts(code, published_ts DESC);

CREATE TABLE guba_scan_state (
    code TEXT PRIMARY KEY,
    scanned_ts INTEGER NOT NULL,
    pages_scanned INTEGER NOT NULL,
    reached_cutoff INTEGER NOT NULL,
    error TEXT
);

CREATE TABLE app_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);
"""


def _now() -> datetime:
    return datetime(2026, 8, 6, 10, 0, tzinfo=SHANGHAI_TZ)


def _create_legacy_database(path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    connection.execute(
        "INSERT INTO articles("
        "url, seq, title, summary, published_ts, channel_key, channel_name, "
        "source_name, provider_key, provider_name, content_type, stocks_json, fetched_ts"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "https://example.test/legacy",
            "1",
            "旧新闻",
            "",
            int(_now().timestamp()),
            "companynews",
            "公司资讯",
            "同花顺财经",
            "ths",
            "同花顺",
            "新闻",
            "[]",
            int(_now().timestamp()),
        ),
    )
    connection.commit()
    connection.close()


def _create_v020_database(path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(V020_SCHEMA)
    connection.execute(
        "INSERT INTO articles("
        "url, seq, title, summary, published_ts, channel_key, channel_name, "
        "source_name, stocks_json, fetched_ts"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "https://example.test/v020",
            "2",
            "v0.2 新闻",
            "",
            int(_now().timestamp()),
            "companynews",
            "公司资讯",
            "同花顺财经",
            "[]",
            int(_now().timestamp()),
        ),
    )
    connection.commit()
    connection.close()


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _index_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def test_fresh_database_reports_110_and_repeated_init_is_idempotent(tmp_path) -> None:
    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_RESEARCH_TABLES <= _table_names(connection)
        assert EXPECTED_RESEARCH_INDEXES <= _index_names(connection)

    now = _now()
    storage.upsert_article(
        ParsedArticle(
            "1",
            "https://example.test/fresh",
            "新库新闻",
            "",
            now,
            "companynews",
            "公司资讯",
            "同花顺财经",
        ),
        now,
    )
    storage.initialize()
    storage.initialize()

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    assert not (tmp_path / BACKUP_NAME).exists()


def test_legacy_database_migrates_in_place_with_backup_created_once(tmp_path) -> None:
    path = tmp_path / "hotpot.db"
    _create_legacy_database(path)

    storage = Storage(path)

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert EXPECTED_RESEARCH_TABLES <= _table_names(connection)
        source_document_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_documents)"
            ).fetchall()
        }
        assert "page_count" in source_document_columns
        source_document_stock_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_document_stocks)"
            ).fetchall()
        }
        assert "stock_name" in source_document_stock_columns
        assert (
            connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
        )
    assert storage.get_articles_between(
        _now() - timedelta(hours=1), _now() + timedelta(hours=1)
    )[0].title == "旧新闻"

    backup_path = path.with_name(BACKUP_NAME)
    assert backup_path.exists()
    backup_before = backup_path.read_bytes()

    # Initializing again must not create a second backup or drop data.
    storage.initialize()
    assert backup_path.read_bytes() == backup_before
    with storage._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v020_database_upgrade_creates_interaction_cache_for_research_views(tmp_path) -> None:
    """A real v0.2 database must not crash stock-name lookup after upgrading."""

    path = tmp_path / "hotpot.db"
    _create_v020_database(path)

    storage = Storage(path)

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "interactions" in _table_names(connection)
        assert "idx_interactions_question_time" in _index_names(connection)
        assert "idx_interactions_code" in _index_names(connection)
    assert storage.get_stock_names({"000001"}) == {"000001": "000001"}
    assert storage.get_articles_between(
        _now() - timedelta(hours=1), _now() + timedelta(hours=1)
    )[0].title == "v0.2 新闻"
    assert path.with_name(BACKUP_NAME).exists()


def test_current_schema_repairs_missing_interaction_cache_without_reset(tmp_path) -> None:
    """Repair databases incorrectly marked as current by the original v1.1.1 migration."""

    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    now = _now()
    storage.upsert_article(
        ParsedArticle(
            "3",
            "https://example.test/retained",
            "保留的缓存文章",
            "",
            now,
            "companynews",
            "公司资讯",
            "同花顺财经",
        ),
        now,
    )
    with storage._connect() as connection:
        connection.execute("DROP TABLE interactions")

    storage.initialize()

    with storage._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "interactions" in _table_names(connection)
    assert storage.get_stock_names({"000001"}) == {"000001": "000001"}
    assert storage.get_articles_between(now - timedelta(hours=1), now + timedelta(hours=1))[0].title == (
        "保留的缓存文章"
    )


def test_failed_migration_rolls_back_and_keeps_legacy_database(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "hotpot.db"
    _create_legacy_database(path)

    monkeypatch.setattr(
        "ashare_hotpot.storage.RESEARCH_TABLE_STATEMENTS", ("THIS IS NOT SQL",)
    )

    with pytest.raises(sqlite3.OperationalError):
        Storage(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    finally:
        connection.close()
    # The one-time backup is created before migration, so a retry can restore.
    assert path.with_name(BACKUP_NAME).exists()


def test_already_110_database_without_page_count_gets_column(tmp_path) -> None:
    """An earlier 110 development build may lack ``page_count``; re-initializing
    must add the column idempotently without touching existing data."""

    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    now = _now()
    storage.upsert_source_document(
        SourceDocument(
            document_id="doc-old",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            source_url="https://example.test/list",
            document_url=None,
            title="旧公告",
            published_at=now,
            stock_codes=("000001",),
            body_text="",
            content_hash="old-hash",
            parse_status="metadata_only",
            parse_error=None,
        ),
        now,
    )
    with storage._connect() as connection:
        connection.execute(
            "ALTER TABLE source_documents DROP COLUMN page_count"
        )

    storage.initialize()

    with storage._connect() as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_documents)"
            ).fetchall()
        }
        assert "page_count" in columns
    restored = storage.get_source_document("doc-old")
    assert restored is not None
    assert restored.title == "旧公告"
    assert restored.page_count is None


def test_already_110_database_without_activity_columns_gets_them(tmp_path) -> None:
    """An earlier 110 development build may lack milestone-4 columns on
    ``research_activities``; re-initializing must add them idempotently
    without touching existing rows."""

    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    now = _now()
    storage.upsert_source_document(
        SourceDocument(
            document_id="doc-old",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="research_activity",
            source_url="https://example.test/list",
            document_url=None,
            title="投资者关系活动记录表",
            published_at=now,
            stock_codes=("000001",),
            body_text="",
            content_hash="old-hash",
            parse_status="metadata_only",
            parse_error=None,
        ),
        now,
    )
    activity = ResearchActivity(
        activity_id="activity-old",
        stock_code="000001",
        source_document_id="doc-old",
        activity_dates=(date(2026, 8, 5),),
        activity_type="survey",
        reported_participant_count=None,
        named_participant_count=1,
        question_count=3,
        high_depth_question_count=1,
        topic_counts={"customers": 1},
    )
    storage.upsert_research_activity(activity, now)
    with storage._connect() as connection:
        connection.execute(
            "ALTER TABLE research_activities DROP COLUMN depth_counts_json"
        )
        connection.execute(
            "ALTER TABLE research_activities DROP COLUMN date_precision"
        )

    storage.initialize()
    storage.initialize()

    with storage._connect() as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(research_activities)"
            ).fetchall()
        }
        assert "depth_counts_json" in columns
        assert "date_precision" in columns
    restored = storage.get_research_activity("activity-old")
    assert restored is not None
    assert restored.activity_type == "survey"
    assert restored.depth_counts == {}
    assert restored.date_precision == "explicit"


def test_already_110_database_without_stock_name_column_gets_it(tmp_path) -> None:
    """An earlier 110 development build may lack
    ``source_document_stocks.stock_name``; re-initializing must add the column
    idempotently without touching existing rows."""

    path = tmp_path / "hotpot.db"
    storage = Storage(path)
    now = _now()
    storage.upsert_source_document(
        SourceDocument(
            document_id="doc-old",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            source_url="https://example.test/list",
            document_url=None,
            title="旧公告",
            published_at=now,
            stock_codes=("000001",),
            body_text="",
            content_hash="old-hash",
            parse_status="metadata_only",
            parse_error=None,
        ),
        now,
    )
    with storage._connect() as connection:
        connection.execute(
            "ALTER TABLE source_document_stocks DROP COLUMN stock_name"
        )

    storage.initialize()
    storage.initialize()

    with storage._connect() as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(source_document_stocks)"
            ).fetchall()
        }
        assert "stock_name" in columns
    restored = storage.get_source_document("doc-old")
    assert restored is not None
    assert restored.stock_codes == ("000001",)
    assert restored.stock_names == {}


def test_source_document_stock_names_feed_get_stock_names(tmp_path) -> None:
    """Research-document names must reach the display lookup even when no news
    article or Q&A record exists for the stock (the reported code-instead-of-
    name bug)."""

    storage = Storage(tmp_path / "hotpot.db")
    now = _now()

    document = SourceDocument(
        document_id="doc-1",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url="https://example.test/list",
        document_url=None,
        title="投资者关系活动记录表",
        published_at=now,
        stock_codes=("300423",),
        body_text="",
        content_hash="hash-1",
        parse_status="metadata_only",
        parse_error=None,
        stock_names={"300423": "昇辉科技"},
    )
    storage.upsert_source_document(document, now)
    restored = storage.get_source_document("doc-1")
    assert restored is not None
    assert restored.stock_names == {"300423": "昇辉科技"}
    assert storage.get_source_documents_by_ids(["doc-1"])[0].stock_names == {
        "300423": "昇辉科技"
    }
    assert storage.get_source_documents_by_stock("300423")[0].stock_names == {
        "300423": "昇辉科技"
    }

    # No news article or interaction record exists for this code; the
    # research-document name must still be the displayed name.
    assert storage.get_stock_names({"300423"}) == {"300423": "昇辉科技"}
    assert storage.get_stock_names({"300423", "600999"}) == {
        "300423": "昇辉科技",
        "600999": "600999",
    }

    # Documents persisted before names existed can be backfilled without
    # touching the document row, and code-only values are never stored.
    legacy = SourceDocument(
        document_id="doc-2",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url="https://example.test/list",
        document_url=None,
        title="控股股东股份被轮候冻结的公告",
        published_at=now,
        stock_codes=("600180",),
        body_text="",
        content_hash="hash-2",
        parse_status="metadata_only",
        parse_error=None,
    )
    storage.upsert_source_document(legacy, now)
    assert storage.get_source_document("doc-2").stock_names == {}
    storage.upsert_source_document_stock_names("doc-2", {"600180": "*ST瑞茂"})
    storage.upsert_source_document_stock_names("doc-2", {"600180": "600180"})
    assert storage.get_source_document("doc-2").stock_names == {"600180": "*ST瑞茂"}
    assert storage.get_stock_names({"600180"})["600180"] == "*ST瑞茂"


def test_research_crud_roundtrips(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()

    document = SourceDocument(
        document_id="doc-1",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url="https://example.test/list",
        document_url="https://example.test/pdf",
        title="投资者关系活动记录表",
        published_at=now,
        stock_codes=("000001", "600519"),
        body_text="正文",
        content_hash="hash-1",
        parse_status="parsed",
        parse_error=None,
        page_count=2,
    )
    storage.upsert_source_document(document, now)
    assert storage.get_source_document("doc-1") == document
    assert storage.get_source_document("doc-1").page_count == 2
    assert [
        item.document_id
        for item in storage.get_source_documents_by_stock("000001")
    ] == ["doc-1"]
    assert storage.get_source_documents_by_stock("600000") == []
    assert [
        item.document_id
        for item in storage.get_source_documents_between(
            now - timedelta(hours=1), now + timedelta(hours=1)
        )
    ] == ["doc-1"]

    evidence = EvidenceRef(
        evidence_id="ev-1",
        document_id="doc-1",
        start_offset=10,
        end_offset=30,
        excerpt="摘录",
        source_url="https://example.test/pdf",
    )
    storage.upsert_evidence_ref(evidence)
    assert storage.get_evidence_refs_for_document("doc-1") == [evidence]

    cluster = EventCluster(
        event_id="event-1",
        stock_codes=("000001",),
        canonical_title="平安银行重大订单",
        first_seen_at=now,
        last_seen_at=now,
        representative_document_id="doc-1",
        document_ids=["doc-1"],
        historical_similar_event_id=None,
    )
    storage.upsert_event_cluster(cluster)
    loaded = storage.get_event_cluster("event-1")
    assert loaded is not None
    assert loaded.stock_codes == ("000001",)
    assert loaded.document_ids == ["doc-1"]
    assert [
        item.event_id
        for item in storage.find_event_cluster_candidates(
            {"000001"}, now - timedelta(hours=72)
        )
    ] == ["event-1"]
    assert (
        storage.find_event_cluster_candidates({"600000"}, now - timedelta(hours=72))
        == []
    )
    storage.set_event_historical_similar("event-1", "event-0")
    assert storage.get_event_cluster("event-1").historical_similar_event_id == "event-0"

    extraction = EventExtraction(
        event_id="event-1",
        stock_code="000001",
        event_type="major_contract",
        direction="positive",
        positive_mechanism="按合同确认收入",
        metrics=({"name": "合同金额", "value": 100, "unit": "万元"},),
        certainty_stage="signed",
        certainty=0.9,
        novelty=0.8,
        unexpectedness=0.7,
        materiality_level=3,
        counter_evidence=(),
        evidence_ids=("ev-1",),
        no_valid_signal=False,
        extractor_kind="rules",
        extractor_version="1.0",
    )
    storage.upsert_event_extraction(extraction, now)
    assert storage.get_event_extraction("event-1", "000001") == extraction

    signal = EventSignal(
        event_id="event-1",
        stock_code="000001",
        board="confirmed_positive",
        score=85.0,
        source_confidence=0.9,
        materiality_level=3,
        certainty=0.9,
        unexpectedness=0.7,
        novelty=0.8,
        timeliness=0.6,
        penalty=0.0,
        provisional=False,
    )
    storage.upsert_event_signal(signal, snapshot_id=7, created_at=now)
    assert storage.get_event_signals("confirmed_positive") == [signal]
    assert storage.get_event_signals("potential_catalyst") == []

    institution = Institution(
        institution_id="inst-1",
        canonical_name="某基金管理有限公司",
        group_id="group-1",
        institution_type="public_fund",
        verification_status="normalized",
    )
    storage.upsert_institution(institution, now)
    assert storage.get_institution("inst-1") == institution
    storage.upsert_institution_alias(
        InstitutionAlias("某基金", "inst-1", "exact_rule")
    )
    assert storage.resolve_institution_alias("某基金").institution_id == "inst-1"
    assert [
        alias.normalized_alias for alias in storage.get_institution_aliases("inst-1")
    ] == ["某基金"]

    activity = ResearchActivity(
        activity_id="activity-1",
        stock_code="000001",
        source_document_id="doc-1",
        activity_dates=(date(2026, 8, 4), date(2026, 8, 5)),
        activity_type="investor_relations",
        reported_participant_count=12,
        named_participant_count=3,
        question_count=20,
        high_depth_question_count=5,
        topic_counts={"growth": 8},
        depth_counts={"low": 5, "medium": 10, "high": 5},
        date_precision="explicit",
    )
    storage.upsert_research_activity(activity, now)
    assert storage.get_research_activity("activity-1") == activity
    assert storage.get_research_activity("activity-1").depth_counts == {
        "low": 5,
        "medium": 10,
        "high": 5,
    }
    assert [
        item.activity_id
        for item in storage.get_research_activities_between(
            date(2026, 8, 1), date(2026, 8, 31)
        )
    ] == ["activity-1"]
    storage.add_research_participant(
        ResearchParticipant("activity-1", "inst-1", "张三", "ev-1")
    )
    assert storage.get_research_participants("activity-1")[0].analyst_name == "张三"
    storage.upsert_institution(
        Institution(
            institution_id="inst-stale",
            canonical_name="旧解析残留机构",
            group_id="group-stale",
            institution_type="other",
            verification_status="needs_review",
        ),
        now,
    )
    storage.add_research_participant(
        ResearchParticipant("activity-1", "inst-stale", "旧解析残留", "ev-stale")
    )
    assert len(storage.get_research_participants("activity-1")) == 2
    storage.replace_research_participants(
        "activity-1",
        [ResearchParticipant("activity-1", "inst-1", "张三", "ev-1")],
    )
    participants = storage.get_research_participants("activity-1")
    assert [item.institution_id for item in participants] == ["inst-1"]

    snapshot_id = storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="z20",
        metrics={"z20": 1.2},
        window_start=None,
        window_end=None,
        snapshot_at=now,
    )
    assert snapshot_id >= 1
    snapshots = storage.get_institution_metric_snapshots("000001", "z20")
    assert snapshots[0][1] == {"z20": 1.2}


def test_sync_state_resumable_and_survives_purges(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    cursor = SyncCursor(
        source_key="cninfo",
        sync_kind="announcement_list",
        cursor={"page": 3},
        target_start=date(2026, 1, 1),
        covered_start=date(2026, 3, 1),
        last_success_at=now,
        last_error=None,
        updated_at=now,
    )
    storage.save_sync_state(cursor)
    assert storage.get_sync_state("cninfo", "announcement_list") == cursor
    assert [item.source_key for item in storage.list_sync_states()] == ["cninfo"]

    storage.purge_older_than(now)
    storage.purge_research_retention(now + timedelta(days=1000))
    assert storage.get_sync_state("cninfo", "announcement_list") == cursor

    storage.clear_all()
    assert storage.get_sync_state("cninfo", "announcement_list") is None
    assert storage.list_sync_states() == []


def test_metric_board_reads_one_completed_global_batch(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    first = _now()
    second = first + timedelta(hours=1)
    third = second + timedelta(hours=1)

    for code in ("000001", "000002"):
        storage.upsert_institution_metric_snapshot(
            stock_code=code,
            window_kind="z20",
            metrics={"z20": 1.0},
            window_start=None,
            window_end=first,
            snapshot_at=first,
            publish=False,
        )
    storage.mark_institution_metric_batch(first)
    assert sorted(storage.get_latest_institution_metric_snapshots("z20")) == [
        "000001",
        "000002",
    ]

    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="z20",
        metrics={"z20": 2.0},
        window_start=None,
        window_end=second,
        snapshot_at=second,
        publish=False,
    )
    # Staged rows are invisible until the completed batch marker advances.
    assert sorted(storage.get_latest_institution_metric_snapshots("z20")) == [
        "000001",
        "000002",
    ]
    storage.mark_institution_metric_batch(second)
    latest = storage.get_latest_institution_metric_snapshots("z20")
    assert list(latest) == ["000001"]
    assert latest["000001"][1]["z20"] == 2.0

    # A completed run with no rows must publish an empty board.
    storage.mark_institution_metric_batch(third)
    assert storage.get_latest_institution_metric_snapshots("z20") == {}


def test_snapshot_research_board_publish_rolls_back_atomically(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    document = SourceDocument(
        document_id="doc-old-signal",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url="https://example.test/old",
        document_url=None,
        title="旧信号",
        published_at=now,
        stock_codes=("000001",),
        body_text="旧信号正文",
        content_hash="old-signal-hash",
        parse_status="parsed",
        parse_error=None,
    )
    storage.upsert_source_document(document, now)
    storage.upsert_event_cluster(
        EventCluster(
            event_id="event-old-signal",
            stock_codes=("000001",),
            canonical_title="旧信号",
            first_seen_at=now,
            last_seen_at=now,
            representative_document_id=document.document_id,
            document_ids=[document.document_id],
            historical_similar_event_id=None,
        )
    )
    old_signal = EventSignal(
        event_id="event-old-signal",
        stock_code="000001",
        board="confirmed_positive",
        score=80.0,
        source_confidence=1.0,
        materiality_level=3,
        certainty=1.0,
        unexpectedness=50.0,
        novelty=100.0,
        timeliness=100.0,
        penalty=0.0,
        provisional=False,
    )
    storage.upsert_event_signal(old_signal, created_at=now)
    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="z20",
        metrics={"z20": 1.0},
        window_start=None,
        window_end=now,
        snapshot_at=now,
    )

    invalid_signal = EventSignal(
        event_id="missing-event",
        stock_code="000002",
        board="confirmed_positive",
        score=90.0,
        source_confidence=1.0,
        materiality_level=4,
        certainty=1.0,
        unexpectedness=100.0,
        novelty=100.0,
        timeliness=100.0,
        penalty=0.0,
        provisional=False,
    )
    snapshot = Snapshot(
        snapshot_id=None,
        window_start=now - timedelta(hours=24),
        window_end=now + timedelta(hours=1),
        created_at=now + timedelta(hours=1),
        partial=False,
        coverages=[],
        rankings=[],
        events=[],
        stats={},
    )

    with pytest.raises(sqlite3.IntegrityError):
        storage.save_snapshot(
            snapshot,
            event_signals=(invalid_signal,),
            institution_metric_batch_at=now + timedelta(hours=1),
        )

    assert storage.load_latest_snapshot() is None
    assert storage.get_event_signals() == [old_signal]
    latest_metrics = storage.get_latest_institution_metric_snapshots("z20")
    assert list(latest_metrics) == ["000001"]
    assert latest_metrics["000001"][0] == now


def test_trading_days_storage_roundtrip_and_replace(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6)]
    storage.replace_trading_days(2026, days, source="sse", updated_at=now)

    assert storage.get_trading_days_between(date(2026, 8, 1), date(2026, 8, 31)) == days
    assert storage.trading_day_count_between(date(2026, 8, 1), date(2026, 8, 31)) == 4
    assert storage.is_trading_day(date(2026, 8, 4)) is True
    assert storage.is_trading_day(date(2026, 8, 7)) is False
    assert storage.get_trading_day_source(2026) == "sse"

    storage.replace_trading_days(
        2026, [date(2026, 8, 10)], source="fallback", updated_at=now
    )
    assert storage.get_trading_day_source(2026) == "fallback"
    assert storage.trading_day_count_between(date(2026, 8, 1), date(2026, 8, 31)) == 1


def test_retention_purge_uses_domain_periods_and_keeps_calendar_and_sync(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()

    old_news = SourceDocument(
        "doc-old-news", "ths", "同花顺", "news", "https://example.test",
        None, "旧新闻", now - timedelta(days=60), ("000001",), "正文", "h1",
        "parsed", None,
    )
    recent_news = SourceDocument(
        "doc-recent-news", "ths", "同花顺", "news", "https://example.test",
        None, "新新闻", now - timedelta(days=1), ("000001",), "正文", "h2",
        "parsed", None,
    )
    old_announcement = SourceDocument(
        "doc-old-ann", "cninfo", "巨潮资讯", "announcement", "https://example.test",
        None, "旧公告", now - timedelta(days=500), ("000001",), "正文", "h3",
        "parsed", None,
    )
    recent_activity_doc = SourceDocument(
        "doc-recent-act", "irm", "深交所互动易", "research_activity", "https://example.test",
        None, "新调研记录", now - timedelta(days=1), ("000001",), "正文", "h4",
        "parsed", None,
    )
    for document in (old_news, recent_news, old_announcement, recent_activity_doc):
        storage.upsert_source_document(document, now)

    old_event = EventCluster(
        "event-old", ("000001",), "旧事件", now - timedelta(days=300),
        now - timedelta(days=300), "doc-old-news", ["doc-old-news"], None,
    )
    recent_event = EventCluster(
        "event-recent", ("000001",), "新事件", now - timedelta(days=30),
        now - timedelta(days=30), "doc-recent-news", ["doc-recent-news"], None,
    )
    storage.upsert_event_cluster(old_event)
    storage.upsert_event_cluster(recent_event)

    old_activity = ResearchActivity(
        "activity-old", "000001", "doc-recent-act", (date(2025, 1, 1),),
        "investor_relations", None, 0, 0, 0, {},
    )
    recent_activity = ResearchActivity(
        "activity-recent", "000001", "doc-recent-act", (date(2026, 8, 5),),
        "investor_relations", None, 0, 0, 0, {},
    )
    storage.upsert_research_activity(old_activity, now - timedelta(days=500))
    storage.upsert_research_activity(recent_activity, now)

    old_institution = Institution("inst-old", "旧机构", "g-old", "other", "normalized")
    recent_institution = Institution("inst-new", "新机构", "g-new", "other", "normalized")
    storage.upsert_institution(old_institution, now - timedelta(days=500))
    storage.upsert_institution(recent_institution, now)

    storage.upsert_institution_metric_snapshot(
        stock_code="000001", window_kind="z20", metrics={"z20": 0.5},
        window_start=None, window_end=None, snapshot_at=now - timedelta(days=500),
    )
    storage.upsert_institution_metric_snapshot(
        stock_code="000001", window_kind="z20", metrics={"z20": 1.5},
        window_start=None, window_end=None, snapshot_at=now,
    )
    storage.replace_trading_days(2026, [date(2026, 8, 6)], source="sse", updated_at=now)
    storage.save_sync_state(
        SyncCursor("cninfo", "list", {"page": 1}, None, None, now, None, now)
    )

    storage.purge_research_retention(now)

    assert storage.get_source_document("doc-old-news") is None
    assert storage.get_source_document("doc-recent-news") is not None
    assert storage.get_source_document("doc-old-ann") is None
    assert storage.get_source_document("doc-recent-act") is not None
    assert storage.get_event_cluster("event-old") is None
    assert storage.get_event_cluster("event-recent") is not None
    assert storage.get_research_activity("activity-old") is None
    assert storage.get_research_activity("activity-recent") is not None
    assert storage.get_institution("inst-old") is None
    assert storage.get_institution("inst-new") is not None
    snapshots = storage.get_institution_metric_snapshots("000001", "z20")
    assert [item[1]["z20"] for item in snapshots] == [1.5]
    assert storage.get_trading_day_source(2026) == "sse"
    assert storage.get_sync_state("cninfo", "list") is not None


def test_clear_all_removes_research_tables(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    document = SourceDocument(
        "doc-1", "cninfo", "巨潮资讯", "announcement", "https://example.test",
        None, "公告", now, ("000001",), "正文", "h1", "parsed", None,
    )
    storage.upsert_source_document(document, now)
    storage.upsert_event_cluster(
        EventCluster("event-1", ("000001",), "事件", now, now, "doc-1", ["doc-1"], None)
    )
    storage.upsert_institution(
        Institution("inst-1", "机构", "g-1", "other", "normalized"), now
    )
    storage.upsert_research_activity(
        ResearchActivity("activity-1", "000001", "doc-1", (date(2026, 8, 5),), "irm", None, 0, 0, 0, {}),
        now,
    )
    storage.save_sync_state(SyncCursor("cninfo", "list", None, None, None, now, None, now))
    storage.replace_trading_days(2026, [date(2026, 8, 6)], source="sse", updated_at=now)

    storage.clear_all()

    assert storage.get_source_document("doc-1") is None
    assert storage.get_event_cluster("event-1") is None
    assert storage.get_institution("inst-1") is None
    assert storage.get_research_activity("activity-1") is None
    assert storage.get_sync_state("cninfo", "list") is None
    assert storage.get_trading_day_source(2026) is None


def test_storage_stats_include_research_counts(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    now = _now()
    document = SourceDocument(
        "doc-1", "cninfo", "巨潮资讯", "announcement", "https://example.test",
        None, "公告", now, ("000001",), "正文", "h1", "parsed", None,
    )
    storage.upsert_source_document(document, now)
    storage.upsert_event_cluster(
        EventCluster("event-1", ("000001",), "事件", now, now, "doc-1", ["doc-1"], None)
    )
    storage.upsert_institution(
        Institution("inst-1", "机构", "g-1", "other", "normalized"), now
    )
    storage.upsert_research_activity(
        ResearchActivity("activity-1", "000001", "doc-1", (date(2026, 8, 5),), "irm", None, 0, 0, 0, {}),
        now,
    )
    storage.replace_trading_days(2026, [date(2026, 8, 6)], source="sse", updated_at=now)

    stats = storage.get_storage_stats()
    assert stats.source_document_count == 1
    assert stats.event_cluster_count == 1
    assert stats.institution_count == 1
    assert stats.research_activity_count == 1
    assert stats.trading_day_count == 1
