from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import SHANGHAI_TZ
from .coverage import summarize_document_ids
from .discovery import (
    QUEUE_STATUS_AWAITING_REVIEW,
    QUEUE_STATUS_PENDING_ATTACHMENT,
    classify_discovery,
)
from .extraction import event_type_hint
from .models import (
    ACTIVITY_DATE_PRECISION_EXPLICIT_DAY,
    ACTIVITY_DATE_PRECISIONS,
    ActivityOccurrence,
    CoverageSnapshot,
    DiscoveryCandidate,
    EventCluster,
    EventClaim,
    EventExtraction,
    EventSignal,
    EvidenceRef,
    FailureInterval,
    IndustryHeatRow,
    IndustryHeatSnapshot,
    Institution,
    InstitutionAlias,
    InstitutionMetricSnapshotRecord,
    InteractionRecord,
    OcrPageResult,
    OfficialPopularitySnapshot,
    ParsedArticle,
    PolicyDocument,
    PolicyLink,
    ResearchActivity,
    ResearchParticipant,
    ResearchParticipantMention,
    ResearchParticipantOccurrence,
    ReportedParticipantCount,
    Snapshot,
    SourceManifest,
    SourceDocument,
    SourceWindowCoverage,
    SyncCursor,
)


SCHEMA_VERSION = 124
BACKUP_NAME = "hotpot.db.pre-110.bak"
BACKUP_NAME_111 = "hotpot.db.pre-111.bak"
BACKUP_NAME_120 = "hotpot.db.pre-120.bak"
BACKUP_NAME_121 = "hotpot.db.pre-121.bak"
BACKUP_NAME_122 = "hotpot.db.pre-122.bak"
BACKUP_NAME_123 = "hotpot.db.pre-123.bak"
BACKUP_NAME_124 = "hotpot.db.pre-124.bak"

# Retention periods per plan.md section 7.4.  The ordinary article/interaction
# cache purge keeps its own 7-day window; research data uses these cutoffs.
NEWS_BODY_RETENTION_DAYS = 30
EVENT_RETENTION_DAYS = 180
RESEARCH_RETENTION_DAYS = 400
# v1.2 覆盖层保留周期 (plan.md 第二部分): 每日 manifest 重对账近 30 天；
# 政策文档与公告基线一致保留 400 天。机构/活动历史仍由研究保留周期管理，
# 不得用本窗口误删。
MANIFEST_RETENTION_DAYS = 30
POLICY_RETENTION_DAYS = 400

INSTITUTION_METRIC_BATCH_STATE_KEY = "research:institution_metric_batch"

LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    url TEXT PRIMARY KEY,
    seq TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_ts INTEGER NOT NULL,
    channel_key TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    source_name TEXT NOT NULL,
    provider_key TEXT NOT NULL DEFAULT 'ths',
    provider_name TEXT NOT NULL DEFAULT '同花顺',
    content_type TEXT NOT NULL DEFAULT '新闻',
    stocks_json TEXT NOT NULL,
    industry_tags_json TEXT NOT NULL DEFAULT '[]',
    filtered_reason TEXT,
    fetch_error TEXT,
    fetched_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_ts);

CREATE TABLE IF NOT EXISTS refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts INTEGER NOT NULL,
    finished_ts INTEGER,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts INTEGER NOT NULL,
    window_start_ts INTEGER NOT NULL,
    window_end_ts INTEGER NOT NULL,
    partial INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_ts DESC);

CREATE TABLE IF NOT EXISTS stock_industries (
    code TEXT PRIMARY KEY,
    industry TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS guba_stock_catalog (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS guba_posts (
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
CREATE INDEX IF NOT EXISTS idx_guba_posts_published ON guba_posts(published_ts);
CREATE INDEX IF NOT EXISTS idx_guba_posts_code_published ON guba_posts(code, published_ts DESC);

CREATE TABLE IF NOT EXISTS guba_scan_state (
    code TEXT PRIMARY KEY,
    scanned_ts INTEGER NOT NULL,
    pages_scanned INTEGER NOT NULL,
    reached_cutoff INTEGER NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions (
    record_id TEXT PRIMARY KEY,
    platform_key TEXT NOT NULL,
    platform_name TEXT NOT NULL,
    code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    question TEXT NOT NULL,
    question_time_ts INTEGER NOT NULL,
    question_url TEXT NOT NULL,
    reply TEXT,
    reply_time_ts INTEGER,
    replied INTEGER NOT NULL DEFAULT 0,
    filtered_reason TEXT,
    fetched_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interactions_question_time ON interactions(question_time_ts);
CREATE INDEX IF NOT EXISTS idx_interactions_code ON interactions(code);
"""

# ``executescript`` implicitly commits an open SQLite transaction.  Keep the
# interaction-table DDL as individual statements too, so migrations can repair
# older databases atomically without breaking their BEGIN IMMEDIATE boundary.
INTERACTION_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS interactions (
        record_id TEXT PRIMARY KEY,
        platform_key TEXT NOT NULL,
        platform_name TEXT NOT NULL,
        code TEXT NOT NULL,
        stock_name TEXT NOT NULL,
        question TEXT NOT NULL,
        question_time_ts INTEGER NOT NULL,
        question_url TEXT NOT NULL,
        reply TEXT,
        reply_time_ts INTEGER,
        replied INTEGER NOT NULL DEFAULT 0,
        filtered_reason TEXT,
        fetched_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_interactions_question_time ON interactions(question_time_ts)",
    "CREATE INDEX IF NOT EXISTS idx_interactions_code ON interactions(code)",
)

RESEARCH_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS source_documents (
        document_id TEXT PRIMARY KEY,
        provider_key TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        source_url TEXT NOT NULL,
        document_url TEXT,
        title TEXT NOT NULL,
        published_ts INTEGER NOT NULL,
        body_text TEXT NOT NULL DEFAULT '',
        content_hash TEXT NOT NULL,
        parse_status TEXT NOT NULL DEFAULT 'metadata_only',
        parse_error TEXT,
        page_count INTEGER,
        fetched_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_source_documents_published ON source_documents(published_ts)",
    "CREATE INDEX IF NOT EXISTS idx_source_documents_hash ON source_documents(content_hash)",
    """
    CREATE TABLE IF NOT EXISTS source_document_stocks (
        document_id TEXT NOT NULL REFERENCES source_documents(document_id) ON DELETE CASCADE,
        stock_code TEXT NOT NULL,
        stock_name TEXT,
        PRIMARY KEY (document_id, stock_code)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_source_document_stocks_code ON source_document_stocks(stock_code)",
    """
    CREATE TABLE IF NOT EXISTS evidence_refs (
        evidence_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES source_documents(document_id) ON DELETE CASCADE,
        start_offset INTEGER,
        end_offset INTEGER,
        excerpt TEXT NOT NULL,
        source_url TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_evidence_refs_document ON evidence_refs(document_id)",
    """
    CREATE TABLE IF NOT EXISTS llm_extraction_cache (
        document_id TEXT NOT NULL
            REFERENCES source_documents(document_id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        prompt_schema_version TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_ts INTEGER NOT NULL,
        PRIMARY KEY (document_id, model, prompt_schema_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_clusters (
        event_id TEXT PRIMARY KEY,
        canonical_title TEXT NOT NULL,
        first_seen_ts INTEGER NOT NULL,
        last_seen_ts INTEGER NOT NULL,
        representative_document_id TEXT,
        historical_similar_event_id TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_clusters_first_seen ON event_clusters(first_seen_ts)",
    "CREATE INDEX IF NOT EXISTS idx_event_clusters_last_seen ON event_clusters(last_seen_ts)",
    """
    CREATE TABLE IF NOT EXISTS event_cluster_stocks (
        event_id TEXT NOT NULL REFERENCES event_clusters(event_id) ON DELETE CASCADE,
        stock_code TEXT NOT NULL,
        PRIMARY KEY (event_id, stock_code)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_cluster_stocks_code ON event_cluster_stocks(stock_code)",
    """
    CREATE TABLE IF NOT EXISTS event_cluster_documents (
        event_id TEXT NOT NULL REFERENCES event_clusters(event_id) ON DELETE CASCADE,
        document_id TEXT NOT NULL REFERENCES source_documents(document_id) ON DELETE CASCADE,
        PRIMARY KEY (event_id, document_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_cluster_documents_doc ON event_cluster_documents(document_id)",
    """
    CREATE TABLE IF NOT EXISTS event_extractions (
        event_id TEXT NOT NULL REFERENCES event_clusters(event_id) ON DELETE CASCADE,
        stock_code TEXT NOT NULL,
        event_type TEXT NOT NULL,
        direction TEXT NOT NULL,
        positive_mechanism TEXT,
        metrics_json TEXT NOT NULL DEFAULT '[]',
        certainty_stage TEXT NOT NULL,
        certainty REAL NOT NULL DEFAULT 0,
        novelty REAL NOT NULL DEFAULT 0,
        unexpectedness REAL NOT NULL DEFAULT 0,
        materiality_level INTEGER NOT NULL DEFAULT 0,
        counter_evidence_json TEXT NOT NULL DEFAULT '[]',
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        no_valid_signal INTEGER NOT NULL DEFAULT 0,
        extractor_kind TEXT NOT NULL,
        extractor_version TEXT NOT NULL,
        created_ts INTEGER NOT NULL,
        PRIMARY KEY (event_id, stock_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_signals (
        event_id TEXT NOT NULL REFERENCES event_clusters(event_id) ON DELETE CASCADE,
        stock_code TEXT NOT NULL,
        board TEXT NOT NULL,
        score REAL NOT NULL,
        source_confidence REAL NOT NULL,
        materiality_level INTEGER NOT NULL,
        certainty REAL NOT NULL,
        unexpectedness REAL NOT NULL,
        novelty REAL NOT NULL,
        timeliness REAL NOT NULL,
        penalty REAL NOT NULL,
        provisional INTEGER NOT NULL DEFAULT 0,
        snapshot_id INTEGER,
        created_ts INTEGER NOT NULL,
        PRIMARY KEY (event_id, stock_code)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_signals_board_score ON event_signals(board, score DESC)",
    """
    CREATE TABLE IF NOT EXISTS institutions (
        institution_id TEXT PRIMARY KEY,
        canonical_name TEXT NOT NULL,
        group_id TEXT NOT NULL,
        institution_type TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        created_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_institutions_group ON institutions(group_id)",
    """
    CREATE TABLE IF NOT EXISTS institution_aliases (
        normalized_alias TEXT PRIMARY KEY,
        institution_id TEXT NOT NULL REFERENCES institutions(institution_id) ON DELETE CASCADE,
        source TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_institution_aliases_inst ON institution_aliases(institution_id)",
    """
    CREATE TABLE IF NOT EXISTS research_activities (
        activity_id TEXT PRIMARY KEY,
        stock_code TEXT NOT NULL,
        source_document_id TEXT NOT NULL REFERENCES source_documents(document_id) ON DELETE CASCADE,
        activity_type TEXT NOT NULL,
        reported_participant_count INTEGER,
        named_participant_count INTEGER NOT NULL DEFAULT 0,
        question_count INTEGER NOT NULL DEFAULT 0,
        high_depth_question_count INTEGER NOT NULL DEFAULT 0,
        topic_counts_json TEXT NOT NULL DEFAULT '{}',
        depth_counts_json TEXT NOT NULL DEFAULT '{}',
        date_precision TEXT NOT NULL DEFAULT 'explicit',
        fetched_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_activities_stock ON research_activities(stock_code)",
    """
    CREATE TABLE IF NOT EXISTS research_activity_dates (
        activity_id TEXT NOT NULL REFERENCES research_activities(activity_id) ON DELETE CASCADE,
        activity_date TEXT NOT NULL,
        PRIMARY KEY (activity_id, activity_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_activity_dates_date ON research_activity_dates(activity_date)",
    """
    CREATE TABLE IF NOT EXISTS research_participants (
        activity_id TEXT NOT NULL REFERENCES research_activities(activity_id) ON DELETE CASCADE,
        institution_id TEXT NOT NULL REFERENCES institutions(institution_id) ON DELETE CASCADE,
        analyst_name TEXT,
        evidence_id TEXT NOT NULL,
        PRIMARY KEY (activity_id, institution_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_research_participants_inst ON research_participants(institution_id)",
    """
    CREATE TABLE IF NOT EXISTS institution_metric_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_code TEXT NOT NULL,
        window_kind TEXT NOT NULL,
        window_start_ts INTEGER,
        window_end_ts INTEGER,
        snapshot_ts INTEGER NOT NULL,
        metrics_json TEXT NOT NULL DEFAULT '{}',
        metric_version TEXT NOT NULL DEFAULT 'z20_legacy',
        source_cohort_id TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_metric_snapshots_window ON institution_metric_snapshots(window_kind, snapshot_ts)",
    "CREATE INDEX IF NOT EXISTS idx_metric_snapshots_stock_window ON institution_metric_snapshots(stock_code, window_kind)",
    """
    CREATE TABLE IF NOT EXISTS source_sync_state (
        source_key TEXT NOT NULL,
        sync_kind TEXT NOT NULL,
        cursor_json TEXT,
        target_start TEXT,
        covered_start TEXT,
        last_success_ts INTEGER,
        last_error TEXT,
        updated_ts INTEGER NOT NULL,
        PRIMARY KEY (source_key, sync_kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trading_days (
        trading_date TEXT PRIMARY KEY,
        is_trading INTEGER NOT NULL,
        source TEXT NOT NULL,
        year INTEGER NOT NULL,
        updated_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trading_days_year ON trading_days(year)",
)

# 后 1.1.0 可靠性里程碑 (plan.md 里程碑 7): 待核验事件发现层与可恢复附件队列。
DISCOVERY_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS discovery_candidates (
        document_id TEXT PRIMARY KEY
            REFERENCES source_documents(document_id) ON DELETE CASCADE,
        source_key TEXT NOT NULL,
        source_name TEXT NOT NULL,
        provider_key TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        stock_code TEXT,
        title TEXT NOT NULL,
        published_ts INTEGER NOT NULL,
        discovery_type TEXT NOT NULL,
        trigger_reason TEXT NOT NULL,
        queue_status TEXT NOT NULL,
        attachment_type TEXT,
        document_url TEXT,
        enqueued_ts INTEGER,
        updated_ts INTEGER NOT NULL,
        signal_priority INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_queue "
    "ON discovery_candidates(queue_status, enqueued_ts)",
    "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_source "
    "ON discovery_candidates(source_key, queue_status)",
    "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_published "
    "ON discovery_candidates(published_ts)",
)

# v1.2 官方市场覆盖闭环 (plan.md 第二部分, v1.2 里程碑 0): 每源每日 manifest、
# 政策文档/链接、OCR 页结果与覆盖快照。
V120_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS source_manifests (
        source_key TEXT NOT NULL,
        manifest_date TEXT NOT NULL,
        total_count INTEGER NOT NULL,
        document_id_count INTEGER NOT NULL,
        document_id_set_hash TEXT,
        watermark_json TEXT,
        failure_intervals_json TEXT NOT NULL DEFAULT '[]',
        ocr_status TEXT NOT NULL DEFAULT 'not_applicable',
        scheduled_task_result_json TEXT,
        coverage_status TEXT NOT NULL,
        updated_ts INTEGER NOT NULL,
        PRIMARY KEY (source_key, manifest_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_source_manifests_date "
    "ON source_manifests(manifest_date)",
    "CREATE INDEX IF NOT EXISTS idx_source_manifests_status "
    "ON source_manifests(coverage_status)",
    """
    CREATE TABLE IF NOT EXISTS policy_documents (
        document_id TEXT PRIMARY KEY,
        source_key TEXT NOT NULL,
        title TEXT NOT NULL,
        published_ts INTEGER NOT NULL,
        source_url TEXT NOT NULL,
        document_url TEXT,
        body_text TEXT NOT NULL DEFAULT '',
        body_hash TEXT,
        body_status TEXT NOT NULL DEFAULT 'metadata_only',
        body_error TEXT,
        content_hash TEXT NOT NULL,
        updated_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_policy_documents_source "
    "ON policy_documents(source_key, published_ts)",
    """
    CREATE TABLE IF NOT EXISTS policy_links (
        link_id TEXT PRIMARY KEY,
        policy_document_id TEXT NOT NULL
            REFERENCES policy_documents(document_id) ON DELETE CASCADE,
        target_document_id TEXT,
        stock_code TEXT,
        link_kind TEXT NOT NULL,
        evidence_excerpt TEXT NOT NULL,
        evidence_id TEXT,
        created_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_policy_links_policy "
    "ON policy_links(policy_document_id)",
    "CREATE INDEX IF NOT EXISTS idx_policy_links_stock "
    "ON policy_links(stock_code)",
    """
    CREATE TABLE IF NOT EXISTS ocr_pages (
        document_id TEXT NOT NULL
            REFERENCES source_documents(document_id) ON DELETE CASCADE,
        page_index INTEGER NOT NULL,
        confidence REAL,
        text TEXT NOT NULL DEFAULT '',
        model_version TEXT,
        evidence_url TEXT,
        status TEXT NOT NULL,
        error TEXT,
        updated_ts INTEGER NOT NULL,
        PRIMARY KEY (document_id, page_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ocr_pages_status "
    "ON ocr_pages(status, updated_ts)",
    """
    CREATE TABLE IF NOT EXISTS coverage_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        snapshot_ts INTEGER NOT NULL,
        statuses_json TEXT NOT NULL,
        manifest_count INTEGER NOT NULL DEFAULT 0,
        policy_document_count INTEGER NOT NULL DEFAULT 0,
        ocr_pending_count INTEGER NOT NULL DEFAULT 0,
        provisional INTEGER NOT NULL DEFAULT 0,
        error TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_coverage_snapshots_ts "
    "ON coverage_snapshots(snapshot_ts)",
)

# v2 优化计划（plan.md 第三部分）：多事实候选、参与者原始提及与结构化披露
# 总数（schema 120 -> 121）。事件簇/抽取/信号仍按 legacy 兼容读取；历史数据
# 重算完成前旧口径明确显示。
V121_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS event_claims (
        claim_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL
            REFERENCES source_documents(document_id) ON DELETE CASCADE,
        stock_code TEXT NOT NULL,
        event_type TEXT NOT NULL,
        direction TEXT NOT NULL,
        positive_mechanism TEXT,
        metrics_json TEXT NOT NULL DEFAULT '[]',
        certainty_stage TEXT NOT NULL DEFAULT '',
        certainty REAL NOT NULL DEFAULT 0.0,
        materiality_level INTEGER NOT NULL DEFAULT 0,
        counter_evidence_json TEXT NOT NULL DEFAULT '[]',
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        rejection_reason TEXT,
        review_status TEXT NOT NULL DEFAULT 'pending_review',
        gate_trace_json TEXT NOT NULL DEFAULT '[]',
        extractor_kind TEXT NOT NULL DEFAULT 'rules',
        extractor_version TEXT NOT NULL DEFAULT '',
        created_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_claims_document "
    "ON event_claims(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_claims_stock "
    "ON event_claims(stock_code, event_type)",
    """
    CREATE TABLE IF NOT EXISTS research_participant_mentions (
        mention_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL
            REFERENCES source_documents(document_id) ON DELETE CASCADE,
        activity_id TEXT NOT NULL
            REFERENCES research_activities(activity_id) ON DELETE CASCADE,
        raw_name TEXT NOT NULL,
        start_offset INTEGER,
        end_offset INTEGER,
        organization_category TEXT NOT NULL DEFAULT 'other_organization',
        parse_version TEXT NOT NULL DEFAULT '',
        review_status TEXT NOT NULL DEFAULT 'pending_review',
        evidence_id TEXT,
        created_ts INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mentions_activity "
    "ON research_participant_mentions(activity_id)",
    "CREATE INDEX IF NOT EXISTS idx_mentions_document "
    "ON research_participant_mentions(document_id)",
    """
    CREATE TABLE IF NOT EXISTS reported_participant_counts (
        activity_id TEXT PRIMARY KEY
            REFERENCES research_activities(activity_id) ON DELETE CASCADE,
        named_research_count INTEGER NOT NULL DEFAULT 0,
        all_named_org_count INTEGER NOT NULL DEFAULT 0,
        reported_institution_count INTEGER,
        reported_person_count INTEGER,
        evidence_id TEXT,
        updated_ts INTEGER NOT NULL
    )
    """,
)

# 机构升温科学性修正的数据底座（schema 121 -> 122）。旧的
# research_activity_dates / research_participants 与 z20 指标快照保留可读；
# occurrence 表提供可靠日期和“机构—日期”关系，逐来源覆盖表用于后续构建
# 可比 cohort，指标快照元数据用于 legacy/v2 并行灰度。
V122_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS activity_occurrences (
        occurrence_id TEXT PRIMARY KEY,
        activity_id TEXT NOT NULL
            REFERENCES research_activities(activity_id) ON DELETE CASCADE,
        occurred_on TEXT,
        period_start TEXT,
        period_end TEXT,
        date_precision TEXT NOT NULL DEFAULT 'unknown'
            CHECK (date_precision IN (
                'explicit_day', 'explicit_range', 'disclosure_day', 'unknown'
            )),
        metric_eligible INTEGER NOT NULL DEFAULT 0
            CHECK (metric_eligible IN (0, 1)),
        exclusion_reason TEXT,
        evidence_id TEXT,
        parse_version TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_activity_occurrences_activity "
    "ON activity_occurrences(activity_id)",
    "CREATE INDEX IF NOT EXISTS idx_activity_occurrences_day "
    "ON activity_occurrences(occurred_on, metric_eligible)",
    """
    CREATE TABLE IF NOT EXISTS research_participant_occurrences (
        participant_occurrence_id TEXT PRIMARY KEY,
        activity_occurrence_id TEXT NOT NULL
            REFERENCES activity_occurrences(occurrence_id) ON DELETE CASCADE,
        activity_id TEXT NOT NULL
            REFERENCES research_activities(activity_id) ON DELETE CASCADE,
        institution_id TEXT NOT NULL
            REFERENCES institutions(institution_id) ON DELETE CASCADE,
        analyst_name TEXT,
        research_eligible INTEGER NOT NULL DEFAULT 0
            CHECK (research_eligible IN (0, 1)),
        eligibility_reason TEXT NOT NULL DEFAULT '',
        evidence_id TEXT,
        parse_version TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_participant_occurrences_activity "
    "ON research_participant_occurrences(activity_id)",
    "CREATE INDEX IF NOT EXISTS idx_participant_occurrences_occurrence "
    "ON research_participant_occurrences(activity_occurrence_id)",
    "CREATE INDEX IF NOT EXISTS idx_participant_occurrences_institution "
    "ON research_participant_occurrences(institution_id, research_eligible)",
    """
    CREATE TABLE IF NOT EXISTS source_window_coverages (
        source_key TEXT NOT NULL,
        market TEXT NOT NULL CHECK (market IN ('sh', 'sz', 'bj')),
        source_kind TEXT NOT NULL DEFAULT 'research_activity'
            CHECK (source_kind = 'research_activity'),
        window_kind TEXT NOT NULL,
        source_cohort_id TEXT NOT NULL,
        requested_start TEXT NOT NULL,
        requested_end TEXT NOT NULL,
        covered_start TEXT,
        covered_end TEXT,
        reached_cutoff INTEGER NOT NULL DEFAULT 0
            CHECK (reached_cutoff IN (0, 1)),
        reconciled INTEGER NOT NULL DEFAULT 0
            CHECK (reconciled IN (0, 1)),
        cohort_eligible INTEGER NOT NULL DEFAULT 0
            CHECK (cohort_eligible IN (0, 1)),
        last_success_ts INTEGER,
        last_error TEXT,
        exclusion_reason TEXT,
        updated_ts INTEGER NOT NULL,
        PRIMARY KEY (source_key, market, window_kind, source_cohort_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_source_window_coverages_window "
    "ON source_window_coverages(market, window_kind, source_cohort_id)",
    "CREATE INDEX IF NOT EXISTS idx_source_window_coverages_updated "
    "ON source_window_coverages(updated_ts)",
)

# Industry heat history is deliberately independent from the short-lived
# article cache.  The date key is the Shanghai natural day and makes the
# no-overwrite rule explicit at the database level.
V123_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS industry_heat_snapshots (
        snapshot_date TEXT PRIMARY KEY,
        snapshot_at_ts INTEGER NOT NULL,
        window_start_ts INTEGER,
        window_end_ts INTEGER,
        top100_total INTEGER NOT NULL DEFAULT 0,
        top100_mapped INTEGER NOT NULL DEFAULT 0,
        mapping_coverage REAL NOT NULL DEFAULT 0,
        research_article_total INTEGER NOT NULL DEFAULT 0,
        research_article_mapped INTEGER NOT NULL DEFAULT 0,
        unmapped_article_count INTEGER NOT NULL DEFAULT 0,
        mapping_status TEXT NOT NULL,
        source_status TEXT NOT NULL,
        source_error TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_industry_heat_snapshots_at "
    "ON industry_heat_snapshots(snapshot_at_ts DESC)",
    """
    CREATE TABLE IF NOT EXISTS industry_heat_rows (
        snapshot_date TEXT NOT NULL
            REFERENCES industry_heat_snapshots(snapshot_date) ON DELETE CASCADE,
        rank INTEGER NOT NULL,
        industry TEXT NOT NULL,
        heat REAL NOT NULL,
        a INTEGER NOT NULL,
        a_percentile REAL NOT NULL,
        b INTEGER NOT NULL,
        b_percentile REAL NOT NULL,
        mapping_status TEXT NOT NULL,
        source_status TEXT NOT NULL,
        article_urls_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (snapshot_date, industry),
        UNIQUE (snapshot_date, rank)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_industry_heat_rows_date_rank "
    "ON industry_heat_rows(snapshot_date, rank)",
)

# Schema 124 keeps attribution evidence and the industry -> stock navigation
# durable.  Columns are additive so old snapshots and article rows remain
# readable with safe empty/zero defaults.
V124_TABLE_STATEMENTS: tuple[str, ...] = ()

POPULARITY_STATE_KEY = "popularity"
SOURCE_CACHE_PREFIX = "source_cache:"


@dataclass(frozen=True, slots=True)
class RefreshRunSummary:
    run_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class StorageStats:
    database_bytes: int
    article_count: int
    snapshot_count: int
    latest_run: RefreshRunSummary | None
    source_document_count: int = 0
    event_cluster_count: int = 0
    institution_count: int = 0
    research_activity_count: int = 0
    trading_day_count: int = 0
    discovery_candidate_count: int = 0
    source_manifest_count: int = 0
    policy_document_count: int = 0
    ocr_page_count: int = 0
    coverage_snapshot_count: int = 0
    event_claim_count: int = 0
    participant_mention_count: int = 0
    reported_participant_count_count: int = 0
    activity_occurrence_count: int = 0
    participant_occurrence_count: int = 0
    source_window_coverage_count: int = 0
    industry_daily_snapshot_count: int = 0


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _fetchall(
        self, query: str, parameters: tuple[object, ...] = ()
    ) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(query, parameters).fetchall()

    def initialize(self) -> None:
        """Create a fresh schema or migrate an existing database in place.

        - A brand-new database is created with the full schema and
          ``PRAGMA user_version`` set to :data:`SCHEMA_VERSION`.
        - A version-0 database (any database without a version number) gets a
          one-time ``hotpot.db.pre-110.bak`` backup and is migrated inside a
          ``BEGIN IMMEDIATE`` transaction, then walks the 110 -> 111 -> 120
          chain with one-time ``pre-111.bak`` / ``pre-120.bak`` backups.
        - A version-110 database gets a one-time ``hotpot.db.pre-111.bak``
          backup and is migrated to 111 (discovery layer), then to 120
          (coverage layer) with a one-time ``pre-120.bak`` backup.
        - A version-111 database gets a one-time ``hotpot.db.pre-120.bak``
          backup and is migrated to 120 inside a ``BEGIN IMMEDIATE``
          transaction, then to 121 (v2 多事实/参与者提及层) and 122
          (机构发生日/逐来源覆盖层), with one-time backups for each step.
        - A version-120 database gets a one-time ``hotpot.db.pre-121.bak``
          backup and is migrated through 121 to 122.
        - A version-121 database gets a one-time ``hotpot.db.pre-122.bak``
          backup and is migrated to 122 inside a ``BEGIN IMMEDIATE``
          transaction.
        - A version-123 database gets a one-time ``hotpot.db.pre-124.bak``
          backup and gains transparent concept-attribution and industry-stock
          drill-down columns without rewriting legacy rows.
        - Calling ``initialize`` again on an already-migrated database safely
          repairs any missing additive tables (including the v0.2 interaction
          cache) and never creates a second backup.
        """

        if not self._database_file_exists():
            self._create_fresh_schema()
        else:
            with self._connect() as connection:
                version = self._schema_version(connection)
                if version > SCHEMA_VERSION:
                    raise RuntimeError(
                        "数据库版本 %d 高于当前程序支持的版本 %d，请升级程序"
                        % (version, SCHEMA_VERSION)
                    )
                if version == SCHEMA_VERSION:
                    self._ensure_research_schema(connection)
                elif self._has_any_table(connection):
                    if version == 0:
                        # Legacy database without a version number: backup
                        # once, then walk the migration chain atomically.
                        self._create_backup_if_needed(BACKUP_NAME)
                        self._migrate_to_110(connection)
                        version = 110
                    if version == 110:
                        self._create_backup_if_needed(BACKUP_NAME_111)
                        self._migrate_to_111(connection)
                        version = 111
                    if version == 111:
                        self._create_backup_if_needed(BACKUP_NAME_120)
                        self._migrate_to_120(connection)
                        version = 120
                    if version == 120:
                        self._create_backup_if_needed(BACKUP_NAME_121)
                        self._migrate_to_121(connection)
                        version = 121
                    if version == 121:
                        self._create_backup_if_needed(BACKUP_NAME_122)
                        self._migrate_to_122(connection)
                        version = 122
                    if version == 122:
                        self._create_backup_if_needed(BACKUP_NAME_123)
                        self._migrate_to_123(connection)
                        version = 123
                    if version == 123:
                        self._create_backup_if_needed(BACKUP_NAME_124)
                        self._migrate_to_124(connection)
                        version = 124
                    if version != SCHEMA_VERSION:
                        raise RuntimeError(
                            "数据库版本 %d 无法升级到 %d" % (version, SCHEMA_VERSION)
                        )
                else:
                    # Existing but empty database file behaves like a new DB.
                    self._create_fresh_schema()
        self._migrate_legacy_guba()

    @staticmethod
    def _schema_version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _has_any_table(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        return int(row[0]) > 0

    def _database_file_exists(self) -> bool:
        try:
            return self.database_path.exists() and self.database_path.stat().st_size > 0
        except OSError:
            return False

    def _create_fresh_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(LEGACY_SCHEMA)
            for statement in RESEARCH_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in DISCOVERY_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V120_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V121_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V122_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V123_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V124_TABLE_STATEMENTS:
                connection.execute(statement)
            self._ensure_article_industry_tags_column(connection)
            self._ensure_v124_columns(connection)
            self._ensure_institution_metric_metadata_columns(connection)
            self._ensure_institution_metric_batch_state(connection)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _ensure_research_schema(self, connection: sqlite3.Connection) -> None:
        """Idempotently repair additive tables on an already-current database.

        v1.1.1 could label a v0.2 database as schema 122 without creating its
        later-added ``interactions`` cache table.  Re-check it here so those
        installations recover on their next launch rather than crashing while
        rendering a saved research snapshot.
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_interaction_schema(connection)
            for statement in RESEARCH_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in DISCOVERY_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V120_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V121_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V122_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V123_TABLE_STATEMENTS:
                connection.execute(statement)
            for statement in V124_TABLE_STATEMENTS:
                connection.execute(statement)
            self._ensure_source_documents_page_count(connection)
            self._ensure_source_document_stock_names(connection)
            self._ensure_article_industry_tags_column(connection)
            self._ensure_v124_columns(connection)
            self._ensure_research_activity_columns(connection)
            self._ensure_institution_metric_metadata_columns(connection)
            self._ensure_institution_metric_batch_state(connection)
            self._backfill_discovery_candidates(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _ensure_interaction_schema(connection: sqlite3.Connection) -> None:
        """Create the additive official-Q&A cache table inside the caller's transaction."""

        for statement in INTERACTION_SCHEMA_STATEMENTS:
            connection.execute(statement)

    @staticmethod
    def _ensure_source_documents_page_count(
        connection: sqlite3.Connection,
    ) -> None:
        """Idempotently add the ``page_count`` column to already-migrated
        databases created by an earlier 110 development build."""

        existing = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(source_documents)"
            ).fetchall()
        }
        if "page_count" not in existing:
            connection.execute(
                "ALTER TABLE source_documents ADD COLUMN page_count INTEGER"
            )

    @staticmethod
    def _ensure_research_activity_columns(
        connection: sqlite3.Connection,
    ) -> None:
        """Idempotently add milestone-4 columns to already-migrated 110
        databases created by earlier development builds."""

        existing = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(research_activities)"
            ).fetchall()
        }
        migrations = (
            ("depth_counts_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("date_precision", "TEXT NOT NULL DEFAULT 'explicit'"),
        )
        for column, definition in migrations:
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE research_activities ADD COLUMN {column} {definition}"  # noqa: S608
                )

    @staticmethod
    def _ensure_source_document_stock_names(
        connection: sqlite3.Connection,
    ) -> None:
        """Idempotently add the ``stock_name`` column to already-migrated
        databases created by an earlier 110 development build."""

        existing = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(source_document_stocks)"
            ).fetchall()
        }
        if "stock_name" not in existing:
            connection.execute(
                "ALTER TABLE source_document_stocks ADD COLUMN stock_name TEXT"
            )

    @staticmethod
    def _ensure_institution_metric_metadata_columns(
        connection: sqlite3.Connection,
    ) -> None:
        """Add legacy/v2 coexistence metadata without rewriting old rows."""

        existing = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(institution_metric_snapshots)"
            ).fetchall()
        }
        migrations = (
            ("metric_version", "TEXT NOT NULL DEFAULT 'z20_legacy'"),
            ("source_cohort_id", "TEXT NOT NULL DEFAULT ''"),
        )
        for column, definition in migrations:
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE institution_metric_snapshots ADD COLUMN {column} {definition}"  # noqa: S608
                )

    @staticmethod
    def _ensure_institution_metric_batch_state(
        connection: sqlite3.Connection,
    ) -> None:
        """Seed the visible metric batch for older schema-110 databases."""

        row = connection.execute(
            "SELECT MAX(snapshot_ts) FROM institution_metric_snapshots"
        ).fetchone()
        snapshot_ts = int(row[0]) if row and row[0] is not None else None
        connection.execute(
            "INSERT OR IGNORE INTO app_state(key, value_json, updated_ts) "
            "VALUES (?, ?, ?)",
            (
                INSTITUTION_METRIC_BATCH_STATE_KEY,
                json.dumps({"snapshot_ts": snapshot_ts}),
                snapshot_ts or 0,
            ),
        )

    def _migrate_to_110(self, connection: sqlite3.Connection) -> None:
        """Migrate a version-0 database to 110 inside one transaction.

        On any failure the whole migration is rolled back so the legacy
        database stays usable and the migration can be retried.
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            # v0.2.0 predates the official-Q&A cache table.  It must exist
            # before the migrated database is marked as a later schema.
            self._ensure_interaction_schema(connection)
            self._migrate_articles_columns(connection)
            for statement in RESEARCH_TABLE_STATEMENTS:
                connection.execute(statement)
            self._ensure_source_documents_page_count(connection)
            self._ensure_source_document_stock_names(connection)
            self._ensure_research_activity_columns(connection)
            self._ensure_institution_metric_batch_state(connection)
            connection.execute("PRAGMA user_version = 110")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _create_backup_if_needed(self, backup_name: str = BACKUP_NAME) -> bool:
        """Create ``hotpot.db.pre-110.bak`` / ``pre-111.bak`` / ``pre-120.bak``
        once.

        Uses the SQLite online backup API so the copy is consistent even when
        the database is in WAL mode.  An existing backup is never overwritten.
        """

        backup_path = self.database_path.with_name(backup_name)
        if backup_path.exists():
            return False
        source = sqlite3.connect(self.database_path)
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        return True

    def _migrate_to_111(self, connection: sqlite3.Connection) -> None:
        """Migrate a version-110 database to 111 inside one transaction.

        Adds the discovery-candidate table and backfills candidate rows from
        already-persisted ``source_documents`` so existing installations get
        the recoverable attachment queue without a re-scan.  On any failure
        the whole migration is rolled back and the old database stays usable.
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in DISCOVERY_TABLE_STATEMENTS:
                connection.execute(statement)
            self._backfill_discovery_candidates(connection)
            connection.execute("PRAGMA user_version = 111")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_to_120(self, connection: sqlite3.Connection) -> None:
        """Migrate a version-111 database to 120 inside one transaction.

        Adds the v1.2 coverage tables (source manifests, policy documents and
        links, OCR pages, coverage snapshots).  On any failure the whole
        migration is rolled back and the old database stays usable; the
        ``hotpot.db.pre-120.bak`` backup is created exactly once by the caller
        before this runs.
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in V120_TABLE_STATEMENTS:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 120")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_to_121(self, connection: sqlite3.Connection) -> None:
        """Migrate a version-120 database to 121 inside one transaction.

        Adds the v2 tables: ``event_claims`` (多事实候选),
        ``research_participant_mentions`` (参与者原始提及) and
        ``reported_participant_counts`` (结构化披露总数).  On any failure the
        whole migration is rolled back and the old database stays usable; the
        ``hotpot.db.pre-121.bak`` backup is created exactly once by the caller
        before this runs.
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in V121_TABLE_STATEMENTS:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 121")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_to_122(self, connection: sqlite3.Connection) -> None:
        """Migrate a version-121 database to the institution warming v2 base.

        The migration is additive: legacy activity dates, participants,
        snapshots and z20 payloads are kept intact.  New occurrence/coverage
        tables and metric-version columns are committed atomically; the caller
        creates the one-time ``pre-122`` backup before entering this method.
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in V122_TABLE_STATEMENTS:
                connection.execute(statement)
            self._ensure_institution_metric_metadata_columns(connection)
            connection.execute("PRAGMA user_version = 122")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_to_123(self, connection: sqlite3.Connection) -> None:
        """Add durable industry-heat day snapshots atomically.

        Existing articles and all institution-era tables are retained.  The
        industry tag column is additive and defaults to an empty list for
        legacy articles that predate the industry board.
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in V123_TABLE_STATEMENTS:
                connection.execute(statement)
            self._ensure_article_industry_tags_column(connection)
            connection.execute("PRAGMA user_version = 123")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_to_124(self, connection: sqlite3.Connection) -> None:
        """Add transparent industry attribution and drill-down data."""

        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in V124_TABLE_STATEMENTS:
                connection.execute(statement)
            self._ensure_v124_columns(connection)
            connection.execute("PRAGMA user_version = 124")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _backfill_discovery_candidates(
        self, connection: sqlite3.Connection
    ) -> None:
        """Idempotently insert discovery rows missing from existing documents.

        Only documents without a candidate row are inserted; re-runs never
        overwrite queue state.  The queue status derives from the document's
        persisted parse state so old metadata-only attachments re-enter the
        recoverable queue instead of being skipped permanently.
        """

        now = datetime.now(SHANGHAI_TZ)
        rows = connection.execute(
            "SELECT document_id, provider_key, provider_name, kind, title, "
            "published_ts, parse_status, document_url "
            "FROM source_documents WHERE document_id NOT IN "
            "(SELECT document_id FROM discovery_candidates)"
        ).fetchall()
        for row in rows:
            document_id = str(row["document_id"])
            kind = str(row["kind"])
            parse_status = str(row["parse_status"] or "metadata_only")
            document_url = row["document_url"]
            if parse_status == "parsed":
                queue_status = QUEUE_STATUS_AWAITING_REVIEW
            elif parse_status in ("empty_text", "failed"):
                queue_status = parse_status
            elif document_url:
                queue_status = QUEUE_STATUS_PENDING_ATTACHMENT
            else:
                queue_status = QUEUE_STATUS_AWAITING_REVIEW
            discovery_type, trigger_reason = classify_discovery(
                str(row["title"] or ""), kind
            )
            signal_priority = int(bool(event_type_hint(str(row["title"] or ""))))
            source_key = self._backfill_source_key(
                str(row["provider_key"] or ""), kind
            )
            stock_code = self._backfill_discovery_stock_code(
                connection, document_id
            )
            enqueued_ts = (
                int(now.timestamp())
                if queue_status == QUEUE_STATUS_PENDING_ATTACHMENT
                else None
            )
            connection.execute(
                "INSERT OR IGNORE INTO discovery_candidates ("
                "document_id, source_key, source_name, provider_key, "
                "provider_name, kind, stock_code, title, published_ts, "
                "discovery_type, trigger_reason, queue_status, "
                "attachment_type, document_url, enqueued_ts, updated_ts, "
                "signal_priority"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    source_key,
                    source_key,
                    str(row["provider_key"] or ""),
                    str(row["provider_name"] or ""),
                    kind,
                    stock_code,
                    str(row["title"] or ""),
                    int(row["published_ts"]),
                    discovery_type,
                    trigger_reason,
                    queue_status,
                    None,
                    document_url,
                    enqueued_ts,
                    int(now.timestamp()),
                    signal_priority,
                ),
            )

    @staticmethod
    def _backfill_source_key(provider_key: str, kind: str) -> str:
        """Best-effort source key for legacy rows (new sync runs persist the
        real per-source key)."""

        if provider_key == "irm":
            return "irm_ircs"
        if provider_key == "sse":
            return "sse_publish"
        if kind == "research_activity":
            return "cninfo_research"
        return "cninfo_announcement"

    @staticmethod
    def _backfill_discovery_stock_code(
        connection: sqlite3.Connection, document_id: str
    ) -> str | None:
        row = connection.execute(
            "SELECT stock_code FROM source_document_stocks "
            "WHERE document_id=? ORDER BY stock_code LIMIT 1",
            (document_id,),
        ).fetchone()
        return str(row["stock_code"]) if row is not None else None

    @staticmethod
    def _migrate_articles_columns(connection: sqlite3.Connection) -> None:
        """Add the provider/content-type columns to databases created by
        earlier versions of the app."""

        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(articles)").fetchall()
        }
        migrations = (
            ("provider_key", "TEXT NOT NULL DEFAULT 'ths'"),
            ("provider_name", "TEXT NOT NULL DEFAULT '同花顺'"),
            ("content_type", "TEXT NOT NULL DEFAULT '新闻'"),
        )
        for column, definition in migrations:
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE articles ADD COLUMN {column} {definition}"  # noqa: S608
                )

    @staticmethod
    def _ensure_article_industry_tags_column(connection: sqlite3.Connection) -> None:
        """Add the serialized explicit industry tags used by industry B."""

        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(articles)").fetchall()
        }
        if "industry_tags_json" not in existing:
            connection.execute(
                "ALTER TABLE articles ADD COLUMN industry_tags_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )

    @staticmethod
    def _ensure_v124_columns(connection: sqlite3.Connection) -> None:
        article_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(articles)").fetchall()
        }
        if "industry_concepts_json" not in article_columns:
            connection.execute(
                "ALTER TABLE articles ADD COLUMN industry_concepts_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        if "industry_parse_version" not in article_columns:
            connection.execute(
                "ALTER TABLE articles ADD COLUMN industry_parse_version "
                "INTEGER NOT NULL DEFAULT 0"
            )
        row_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(industry_heat_rows)"
            ).fetchall()
        }
        if "stock_codes_json" not in row_columns:
            connection.execute(
                "ALTER TABLE industry_heat_rows ADD COLUMN stock_codes_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        snapshot_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(industry_heat_snapshots)"
            ).fetchall()
        }
        migrations = (
            ("explicit_article_count", "INTEGER NOT NULL DEFAULT 0"),
            ("concept_article_count", "INTEGER NOT NULL DEFAULT 0"),
            ("stock_fallback_article_count", "INTEGER NOT NULL DEFAULT 0"),
            ("unknown_label_article_count", "INTEGER NOT NULL DEFAULT 0"),
            ("unknown_concept_article_count", "INTEGER NOT NULL DEFAULT 0"),
            ("no_evidence_article_count", "INTEGER NOT NULL DEFAULT 0"),
            ("stock_industry_unmapped_article_count", "INTEGER NOT NULL DEFAULT 0"),
        )
        for column, definition in migrations:
            if column not in snapshot_columns:
                connection.execute(
                    f"ALTER TABLE industry_heat_snapshots ADD COLUMN {column} {definition}"  # noqa: S608
                )

    def _migrate_legacy_guba(self) -> None:
        """Clear the old per-stock-bar scan data and drop old self-computed guba
        results from historical snapshots while keeping the news snapshot."""

        with self._connect() as connection:
            connection.execute("DELETE FROM guba_posts")
            connection.execute("DELETE FROM guba_scan_state")
            connection.execute("DELETE FROM guba_stock_catalog")
            rows = connection.execute("SELECT id, payload_json FROM snapshots").fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if "guba" not in payload:
                    continue
                payload.pop("guba")
                connection.execute(
                    "UPDATE snapshots SET payload_json=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), row["id"]),
                )

    def create_run(self, started_at: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO refresh_runs(started_ts, status) VALUES (?, 'running')",
                (int(started_at.timestamp()),),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, message: str, finished_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE refresh_runs SET finished_ts=?, status=?, message=? WHERE id=?",
                (int(finished_at.timestamp()), status, message[:2000], run_id),
            )

    def upsert_article(self, article: ParsedArticle, fetched_at: datetime) -> None:
        payload = json.dumps([stock.to_dict() for stock in article.stocks], ensure_ascii=False)
        industry_tags_payload = json.dumps(list(article.industry_tags), ensure_ascii=False)
        industry_concepts_payload = json.dumps(
            list(article.industry_concepts), ensure_ascii=False
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO articles(
                    url, seq, title, summary, published_ts, channel_key, channel_name,
                    source_name, provider_key, provider_name, content_type, stocks_json,
                    industry_tags_json, industry_concepts_json, industry_parse_version,
                    filtered_reason, fetch_error, fetched_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    seq=excluded.seq,
                    title=excluded.title,
                    summary=excluded.summary,
                    published_ts=excluded.published_ts,
                    channel_key=excluded.channel_key,
                    channel_name=excluded.channel_name,
                    source_name=excluded.source_name,
                    provider_key=excluded.provider_key,
                    provider_name=excluded.provider_name,
                    content_type=excluded.content_type,
                    stocks_json=excluded.stocks_json,
                    industry_tags_json=excluded.industry_tags_json,
                    industry_concepts_json=excluded.industry_concepts_json,
                    industry_parse_version=excluded.industry_parse_version,
                    filtered_reason=excluded.filtered_reason,
                    fetch_error=excluded.fetch_error,
                    fetched_ts=excluded.fetched_ts
                """,
                (
                    article.url,
                    article.seq,
                    article.title,
                    article.summary,
                    int(article.published_at.timestamp()),
                    article.channel_key,
                    article.channel_name,
                    article.source_name,
                    article.provider_key,
                    article.provider_name,
                    article.content_type,
                    payload,
                    industry_tags_payload,
                    industry_concepts_payload,
                    article.industry_parse_version,
                    article.filtered_reason,
                    article.fetch_error,
                    int(fetched_at.timestamp()),
                ),
            )

    def get_cached_article(self, url: str) -> ParsedArticle | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM articles WHERE url=?", (url,)).fetchone()
        if row is None or row["fetch_error"]:
            return None
        return self._row_to_article(row)

    def get_articles_between(self, start: datetime, end: datetime) -> list[ParsedArticle]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM articles
                WHERE published_ts >= ? AND published_ts <= ?
                ORDER BY published_ts DESC
                """,
                (int(start.timestamp()), int(end.timestamp())),
            ).fetchall()
        return [self._row_to_article(row) for row in rows]

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> ParsedArticle:
        try:
            industry_tags = json.loads(row["industry_tags_json"] or "[]")
        except (IndexError, KeyError, TypeError, json.JSONDecodeError):
            industry_tags = []
        try:
            industry_concepts = json.loads(row["industry_concepts_json"] or "[]")
        except (IndexError, KeyError, TypeError, json.JSONDecodeError):
            industry_concepts = []
        payload: dict[str, Any] = {
            "seq": row["seq"],
            "url": row["url"],
            "title": row["title"],
            "summary": row["summary"],
            "published_at": datetime.fromtimestamp(row["published_ts"], tz=SHANGHAI_TZ).isoformat(),
            "channel_key": row["channel_key"],
            "channel_name": row["channel_name"],
            "source_name": row["source_name"],
            "provider_key": row["provider_key"],
            "provider_name": row["provider_name"],
            "content_type": row["content_type"],
            "stocks": json.loads(row["stocks_json"]),
            "industry_tags": industry_tags,
            "industry_concepts": industry_concepts,
            "industry_parse_version": (
                int(row["industry_parse_version"])
                if "industry_parse_version" in row.keys()
                else 0
            ),
            "filtered_reason": row["filtered_reason"],
            "fetch_error": row["fetch_error"],
        }
        return ParsedArticle.from_dict(payload)

    def save_snapshot(
        self,
        snapshot: Snapshot,
        *,
        event_signals: Iterable[EventSignal] | None = None,
        institution_metric_batch_at: datetime | None = None,
    ) -> Snapshot:
        """Persist a refresh and atomically publish its research boards.

        ``event_signals=None`` preserves the previous completed board after a
        degraded signal run; an empty iterable publishes a valid empty board.
        Metric rows are staged separately and become visible only when their
        completed-batch marker advances in this transaction.
        """

        signal_rows = tuple(event_signals) if event_signals is not None else None
        payload = snapshot.to_dict()
        payload["snapshot_id"] = None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO snapshots(created_ts, window_start_ts, window_end_ts, partial, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(snapshot.created_at.timestamp()),
                    int(snapshot.window_start.timestamp()),
                    int(snapshot.window_end.timestamp()),
                    int(snapshot.partial),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            snapshot.snapshot_id = int(cursor.lastrowid)
            if signal_rows is not None:
                connection.execute("DELETE FROM event_signals")
                for signal in signal_rows:
                    self._upsert_event_signal(
                        connection,
                        signal,
                        snapshot_id=snapshot.snapshot_id,
                        created_at=snapshot.window_end,
                    )
            if institution_metric_batch_at is not None:
                self._set_institution_metric_batch(
                    connection, institution_metric_batch_at
                )
            updated_payload = snapshot.to_dict()
            connection.execute(
                "UPDATE snapshots SET payload_json=? WHERE id=?",
                (json.dumps(updated_payload, ensure_ascii=False), snapshot.snapshot_id),
            )
        return snapshot

    def load_latest_snapshot(self) -> Snapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM snapshots ORDER BY created_ts DESC, id DESC LIMIT 1"
            ).fetchone()
        return Snapshot.from_dict(json.loads(row["payload_json"])) if row else None

    def get_latest_refresh_run(self) -> RefreshRunSummary | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, started_ts, finished_ts, status, message "
                "FROM refresh_runs ORDER BY started_ts DESC, id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return RefreshRunSummary(
            run_id=int(row["id"]),
            started_at=datetime.fromtimestamp(row["started_ts"], tz=SHANGHAI_TZ),
            finished_at=(
                datetime.fromtimestamp(row["finished_ts"], tz=SHANGHAI_TZ)
                if row["finished_ts"] is not None
                else None
            ),
            status=str(row["status"]),
            message=str(row["message"] or ""),
        )

    def get_storage_stats(self) -> StorageStats:
        with self._connect() as connection:
            article_count = int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
            snapshot_count = int(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
            source_document_count = int(
                connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0]
            )
            event_cluster_count = int(
                connection.execute("SELECT COUNT(*) FROM event_clusters").fetchone()[0]
            )
            institution_count = int(
                connection.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
            )
            research_activity_count = int(
                connection.execute("SELECT COUNT(*) FROM research_activities").fetchone()[0]
            )
            trading_day_count = int(
                connection.execute("SELECT COUNT(*) FROM trading_days").fetchone()[0]
            )
            discovery_candidate_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM discovery_candidates"
                ).fetchone()[0]
            )
            source_manifest_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_manifests"
                ).fetchone()[0]
            )
            policy_document_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM policy_documents"
                ).fetchone()[0]
            )
            ocr_page_count = int(
                connection.execute("SELECT COUNT(*) FROM ocr_pages").fetchone()[0]
            )
            coverage_snapshot_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM coverage_snapshots"
                ).fetchone()[0]
            )
            event_claim_count = int(
                connection.execute("SELECT COUNT(*) FROM event_claims").fetchone()[0]
            )
            participant_mention_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM research_participant_mentions"
                ).fetchone()[0]
            )
            reported_participant_count_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reported_participant_counts"
                ).fetchone()[0]
            )
            activity_occurrence_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM activity_occurrences"
                ).fetchone()[0]
            )
            participant_occurrence_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM research_participant_occurrences"
                ).fetchone()[0]
            )
            source_window_coverage_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_window_coverages"
                ).fetchone()[0]
            )
            industry_daily_snapshot_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM industry_heat_snapshots"
                ).fetchone()[0]
            )
        try:
            database_bytes = self.database_path.stat().st_size
        except OSError:
            database_bytes = 0
        return StorageStats(
            database_bytes=database_bytes,
            article_count=article_count,
            snapshot_count=snapshot_count,
            latest_run=self.get_latest_refresh_run(),
            source_document_count=source_document_count,
            event_cluster_count=event_cluster_count,
            institution_count=institution_count,
            research_activity_count=research_activity_count,
            trading_day_count=trading_day_count,
            discovery_candidate_count=discovery_candidate_count,
            source_manifest_count=source_manifest_count,
            policy_document_count=policy_document_count,
            ocr_page_count=ocr_page_count,
            coverage_snapshot_count=coverage_snapshot_count,
            event_claim_count=event_claim_count,
            participant_mention_count=participant_mention_count,
            reported_participant_count_count=reported_participant_count_count,
            activity_occurrence_count=activity_occurrence_count,
            participant_occurrence_count=participant_occurrence_count,
            source_window_coverage_count=source_window_coverage_count,
            industry_daily_snapshot_count=industry_daily_snapshot_count,
        )

    def save_industry_daily_snapshot(
        self,
        snapshot: IndustryHeatSnapshot,
        snapshot_date: date | None = None,
    ) -> bool:
        """Persist one complete Shanghai-day point without overwriting it.

        The refresh service decides when the 18:00 gate has been reached;
        storage enforces the second safety boundary that only complete
        results enter history and an existing day is immutable.
        """

        if not snapshot.is_complete or snapshot.snapshot_at is None:
            return False
        day = snapshot_date or snapshot.snapshot_at.astimezone(SHANGHAI_TZ).date()
        day_key = day.isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO industry_heat_snapshots(
                    snapshot_date, snapshot_at_ts, window_start_ts, window_end_ts,
                    top100_total, top100_mapped, mapping_coverage,
                    research_article_total, research_article_mapped,
                    unmapped_article_count, mapping_status, source_status,
                    source_error, explicit_article_count,
                    concept_article_count, stock_fallback_article_count,
                    unknown_label_article_count, no_evidence_article_count,
                    stock_industry_unmapped_article_count,
                    unknown_concept_article_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day_key,
                    int(snapshot.snapshot_at.timestamp()),
                    int(snapshot.window_start.timestamp())
                    if snapshot.window_start
                    else None,
                    int(snapshot.window_end.timestamp())
                    if snapshot.window_end
                    else None,
                    snapshot.top100_total,
                    snapshot.top100_mapped,
                    snapshot.mapping_coverage,
                    snapshot.research_article_total,
                    snapshot.research_article_mapped,
                    snapshot.unmapped_article_count,
                    snapshot.mapping_status,
                    snapshot.source_status,
                    snapshot.source_error,
                    snapshot.explicit_article_count,
                    snapshot.concept_article_count,
                    snapshot.stock_fallback_article_count,
                    snapshot.unknown_label_article_count,
                    snapshot.no_evidence_article_count,
                    snapshot.stock_industry_unmapped_article_count,
                    snapshot.unknown_concept_article_count,
                ),
            )
            if cursor.rowcount == 0:
                return False
            connection.executemany(
                """
                INSERT INTO industry_heat_rows(
                    snapshot_date, rank, industry, heat, a, a_percentile,
                    b, b_percentile, mapping_status, source_status,
                    article_urls_json, stock_codes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        day_key,
                        row.rank,
                        row.industry,
                        row.heat,
                        row.a,
                        row.a_percentile,
                        row.b,
                        row.b_percentile,
                        row.mapping_status,
                        row.source_status,
                        json.dumps(list(row.article_urls), ensure_ascii=False),
                        json.dumps(list(row.stock_codes), ensure_ascii=False),
                    )
                    for row in snapshot.rows
                ],
            )
        return True

    def get_industry_daily_snapshot(self, snapshot_date: date) -> IndustryHeatSnapshot | None:
        """Read one immutable industry history point, including its rows."""

        with self._connect() as connection:
            snapshot_row = connection.execute(
                "SELECT * FROM industry_heat_snapshots WHERE snapshot_date=?",
                (snapshot_date.isoformat(),),
            ).fetchone()
            if snapshot_row is None:
                return None
            row_rows = connection.execute(
                "SELECT * FROM industry_heat_rows WHERE snapshot_date=? "
                "ORDER BY rank ASC, industry ASC",
                (snapshot_date.isoformat(),),
            ).fetchall()
        return self._industry_heat_snapshot_from_rows(snapshot_row, row_rows)

    def get_industry_daily_snapshots(self, limit: int = 30) -> list[IndustryHeatSnapshot]:
        """Read the newest valid daily points for the trend view."""

        bounded_limit = max(0, int(limit))
        if bounded_limit == 0:
            return []
        with self._connect() as connection:
            snapshot_rows = connection.execute(
                "SELECT * FROM industry_heat_snapshots "
                "ORDER BY snapshot_date DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
            result: list[IndustryHeatSnapshot] = []
            for snapshot_row in snapshot_rows:
                row_rows = connection.execute(
                    "SELECT * FROM industry_heat_rows WHERE snapshot_date=? "
                    "ORDER BY rank ASC, industry ASC",
                    (snapshot_row["snapshot_date"],),
                ).fetchall()
                result.append(self._industry_heat_snapshot_from_rows(snapshot_row, row_rows))
        return result

    @staticmethod
    def _industry_heat_snapshot_from_rows(
        snapshot_row: sqlite3.Row,
        row_rows: Iterable[sqlite3.Row],
    ) -> IndustryHeatSnapshot:
        def _timestamp(value: object) -> datetime | None:
            return (
                datetime.fromtimestamp(int(value), tz=SHANGHAI_TZ)
                if value is not None
                else None
            )

        rows: list[IndustryHeatRow] = []
        for row in row_rows:
            try:
                article_urls = json.loads(row["article_urls_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                article_urls = []
            try:
                stock_codes = json.loads(row["stock_codes_json"] or "[]")
            except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                stock_codes = []
            rows.append(
                IndustryHeatRow(
                    rank=int(row["rank"]),
                    industry=str(row["industry"]),
                    heat=float(row["heat"]),
                    a=int(row["a"]),
                    a_percentile=float(row["a_percentile"]),
                    b=int(row["b"]),
                    b_percentile=float(row["b_percentile"]),
                    mapping_status=str(row["mapping_status"]),
                    source_status=str(row["source_status"]),
                    article_urls=tuple(str(item) for item in article_urls),
                    stock_codes=tuple(str(item) for item in stock_codes),
                )
            )
        return IndustryHeatSnapshot(
            snapshot_at=_timestamp(snapshot_row["snapshot_at_ts"]),
            window_start=_timestamp(snapshot_row["window_start_ts"]),
            window_end=_timestamp(snapshot_row["window_end_ts"]),
            rows=rows,
            top100_total=int(snapshot_row["top100_total"]),
            top100_mapped=int(snapshot_row["top100_mapped"]),
            mapping_coverage=float(snapshot_row["mapping_coverage"]),
            research_article_total=int(snapshot_row["research_article_total"]),
            research_article_mapped=int(snapshot_row["research_article_mapped"]),
            unmapped_article_count=int(snapshot_row["unmapped_article_count"]),
            explicit_article_count=int(snapshot_row["explicit_article_count"]),
            concept_article_count=int(snapshot_row["concept_article_count"]),
            stock_fallback_article_count=int(
                snapshot_row["stock_fallback_article_count"]
            ),
            unknown_label_article_count=int(
                snapshot_row["unknown_label_article_count"]
            ),
            unknown_concept_article_count=int(
                snapshot_row["unknown_concept_article_count"]
            ),
            no_evidence_article_count=int(snapshot_row["no_evidence_article_count"]),
            stock_industry_unmapped_article_count=int(
                snapshot_row["stock_industry_unmapped_article_count"]
            ),
            mapping_status=str(snapshot_row["mapping_status"]),
            source_status=str(snapshot_row["source_status"]),
            source_error=snapshot_row["source_error"],
        )

    def get_stock_industries(self, codes: set[str]) -> dict[str, str]:
        if not codes:
            return {}
        result: dict[str, str] = {}
        ordered_codes = sorted(codes)
        with self._connect() as connection:
            for start in range(0, len(ordered_codes), 900):
                batch = ordered_codes[start : start + 900]
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT code, industry FROM stock_industries WHERE code IN ({placeholders})",  # noqa: S608
                    batch,
                ).fetchall()
                result.update({str(row["code"]): str(row["industry"]) for row in rows})
        return result

    def get_all_stock_industries(self) -> dict[str, str]:
        """Return the cached listed-company industry universe.

        The caller must separately establish that the cache is complete before
        using it for a cross-sectional percentile.
        """

        rows = self._fetchall(
            "SELECT code, industry FROM stock_industries ORDER BY code"
        )
        return {str(row["code"]): str(row["industry"]) for row in rows}

    def upsert_stock_industries(self, industries: dict[str, str], updated_at: datetime) -> None:
        rows = [
            (code, industry, int(updated_at.timestamp()))
            for code, industry in industries.items()
            if code and industry
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO stock_industries(code, industry, updated_ts) VALUES (?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET industry=excluded.industry, updated_ts=excluded.updated_ts
                """,
                rows,
            )

    def get_stock_names(self, codes: set[str]) -> dict[str, str]:
        """Best-effort stock name lookup for display-only research rows.

        There is no central stock table; names are recovered from cached news
        articles, official Q&A records and research/announcement documents.
        Codes without a known name fall back to the code itself so board rows
        always render.
        """

        if not codes:
            return {}
        result: dict[str, str] = {code: code for code in codes}
        with self._connect() as connection:
            article_rows = connection.execute(
                "SELECT stocks_json FROM articles"
            ).fetchall()
            for row in article_rows:
                for item in json.loads(row["stocks_json"] or "[]"):
                    code = str(item.get("code") or "")
                    name = str(item.get("name") or "")
                    if code in result and name and name != code:
                        result[code] = name
            ordered_codes = sorted(codes)
            for start in range(0, len(ordered_codes), 900):
                batch = ordered_codes[start : start + 900]
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT code, stock_name FROM interactions "
                    f"WHERE code IN ({placeholders})",  # noqa: S608
                    batch,
                ).fetchall()
                for row in rows:
                    code = str(row["code"])
                    name = str(row["stock_name"] or "")
                    if code in result and name and name != code:
                        result[code] = name
            for start in range(0, len(ordered_codes), 900):
                batch = ordered_codes[start : start + 900]
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT stock_code, MIN(stock_name) AS stock_name "
                    f"FROM source_document_stocks "
                    f"WHERE stock_code IN ({placeholders}) "
                    f"AND stock_name IS NOT NULL AND stock_name != stock_code "
                    f"GROUP BY stock_code",  # noqa: S608
                    batch,
                ).fetchall()
                for row in rows:
                    code = str(row["stock_code"])
                    name = str(row["stock_name"] or "")
                    if code in result and name and name != code:
                        result[code] = name
        return result

    def set_popularity_state(self, snapshot: OfficialPopularitySnapshot, updated_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state(key, value_json, updated_ts) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_ts=excluded.updated_ts
                """,
                (
                    POPULARITY_STATE_KEY,
                    json.dumps(snapshot.to_dict(), ensure_ascii=False),
                    int(updated_at.timestamp()),
                ),
            )

    def get_popularity_state(self) -> OfficialPopularitySnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key=?",
                (POPULARITY_STATE_KEY,),
            ).fetchone()
        if row is None:
            return None
        try:
            return OfficialPopularitySnapshot.from_dict(json.loads(row["value_json"]))
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def set_source_cache(
        self,
        source_key: str,
        *,
        articles: list[ParsedArticle] | None = None,
        records: list[InteractionRecord] | None = None,
        fetched_at: datetime,
        reached_cutoff: bool = True,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "fetched_at": fetched_at.isoformat(),
            "reached_cutoff": reached_cutoff,
        }
        if window_start is not None:
            payload["window_start"] = window_start.isoformat()
        if window_end is not None:
            payload["window_end"] = window_end.isoformat()
        if articles is not None:
            payload["kind"] = "articles"
            payload["articles"] = [article.to_dict() for article in articles]
        elif records is not None:
            payload["kind"] = "records"
            payload["records"] = [record.to_dict() for record in records]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state(key, value_json, updated_ts) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_ts=excluded.updated_ts
                """,
                (
                    f"{SOURCE_CACHE_PREFIX}{source_key}",
                    json.dumps(payload, ensure_ascii=False),
                    int(fetched_at.timestamp()),
                ),
            )

    def get_source_cache(
        self, source_key: str
    ) -> tuple[
        datetime,
        bool,
        datetime | None,
        datetime | None,
        list[ParsedArticle] | list[InteractionRecord],
    ] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key=?",
                (f"{SOURCE_CACHE_PREFIX}{source_key}",),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["value_json"])
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            reached_cutoff = bool(payload.get("reached_cutoff", True))
            window_start = (
                datetime.fromisoformat(payload["window_start"])
                if payload.get("window_start")
                else None
            )
            window_end = (
                datetime.fromisoformat(payload["window_end"])
                if payload.get("window_end")
                else None
            )
            if payload.get("kind") == "articles":
                return (
                    fetched_at,
                    reached_cutoff,
                    window_start,
                    window_end,
                    [ParsedArticle.from_dict(item) for item in payload.get("articles", [])],
                )
            if payload.get("kind") == "records":
                return (
                    fetched_at,
                    reached_cutoff,
                    window_start,
                    window_end,
                    [InteractionRecord.from_dict(item) for item in payload.get("records", [])],
                )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        return None

    def upsert_interaction(self, record: InteractionRecord, fetched_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interactions(
                    record_id, platform_key, platform_name, code, stock_name,
                    question, question_time_ts, question_url, reply, reply_time_ts,
                    replied, filtered_reason, fetched_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    platform_key=excluded.platform_key,
                    platform_name=excluded.platform_name,
                    code=excluded.code,
                    stock_name=excluded.stock_name,
                    question=excluded.question,
                    question_time_ts=excluded.question_time_ts,
                    question_url=excluded.question_url,
                    reply=excluded.reply,
                    reply_time_ts=excluded.reply_time_ts,
                    replied=excluded.replied,
                    filtered_reason=excluded.filtered_reason,
                    fetched_ts=excluded.fetched_ts
                """,
                (
                    record.record_id,
                    record.platform_key,
                    record.platform_name,
                    record.code,
                    record.stock_name,
                    record.question,
                    int(record.question_time.timestamp()),
                    record.question_url,
                    record.reply,
                    int(record.reply_time.timestamp()) if record.reply_time else None,
                    int(record.replied),
                    record.filtered_reason,
                    int(fetched_at.timestamp()),
                ),
            )

    def get_interactions_between(self, start: datetime, end: datetime) -> list[InteractionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM interactions
                WHERE question_time_ts >= ? AND question_time_ts <= ?
                ORDER BY question_time_ts DESC
                """,
                (int(start.timestamp()), int(end.timestamp())),
            ).fetchall()
        return [self._row_to_interaction(row) for row in rows]

    @staticmethod
    def _row_to_interaction(row: sqlite3.Row) -> InteractionRecord:
        payload: dict[str, Any] = {
            "record_id": row["record_id"],
            "platform_key": row["platform_key"],
            "platform_name": row["platform_name"],
            "code": row["code"],
            "stock_name": row["stock_name"],
            "question": row["question"],
            "question_time": datetime.fromtimestamp(
                row["question_time_ts"], tz=SHANGHAI_TZ
            ).isoformat(),
            "question_url": row["question_url"],
            "reply": row["reply"],
            "reply_time": (
                datetime.fromtimestamp(row["reply_time_ts"], tz=SHANGHAI_TZ).isoformat()
                if row["reply_time_ts"] is not None
                else None
            ),
            "filtered_reason": row["filtered_reason"],
        }
        return InteractionRecord.from_dict(payload)

    # ------------------------------------------------------------------
    # Source documents and evidence
    # ------------------------------------------------------------------

    def upsert_source_document(self, document: SourceDocument, fetched_at: datetime) -> None:
        with self._connect() as connection:
            self._upsert_source_document(connection, document, fetched_at)

    @staticmethod
    def _upsert_source_document(
        connection: sqlite3.Connection,
        document: SourceDocument,
        fetched_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO source_documents(
                document_id, provider_key, provider_name, kind, source_url,
                document_url, title, published_ts, body_text, content_hash,
                parse_status, parse_error, page_count, fetched_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                provider_key=excluded.provider_key,
                provider_name=excluded.provider_name,
                kind=excluded.kind,
                source_url=excluded.source_url,
                document_url=excluded.document_url,
                title=excluded.title,
                published_ts=excluded.published_ts,
                body_text=excluded.body_text,
                content_hash=excluded.content_hash,
                parse_status=excluded.parse_status,
                parse_error=excluded.parse_error,
                page_count=excluded.page_count,
                fetched_ts=excluded.fetched_ts
            """,
            (
                document.document_id,
                document.provider_key,
                document.provider_name,
                document.kind,
                document.source_url,
                document.document_url,
                document.title,
                int(document.published_at.timestamp()),
                document.body_text,
                document.content_hash,
                document.parse_status,
                document.parse_error,
                document.page_count,
                int(fetched_at.timestamp()),
            ),
        )
        connection.execute(
            "DELETE FROM source_document_stocks WHERE document_id=?",
            (document.document_id,),
        )
        connection.executemany(
            "INSERT INTO source_document_stocks(document_id, stock_code, stock_name) "
            "VALUES (?, ?, ?)",
            [
                (
                    document.document_id,
                    code,
                    document.stock_names.get(code),
                )
                for code in sorted(set(document.stock_codes))
            ],
        )

    def upsert_source_document_stock_names(
        self, document_id: str, names: dict[str, str]
    ) -> None:
        """Backfill display-only stock names for an already persisted document.

        Used when a previously synced document lacked names because the
        ``stock_name`` column was added later; keeps the document row itself
        untouched and never invents names for codes the source did not name.
        """

        rows = [
            (document_id, code, name)
            for code, name in names.items()
            if code and name and name != code
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO source_document_stocks(document_id, stock_code, stock_name) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(document_id, stock_code) DO UPDATE SET "
                "stock_name=excluded.stock_name",
                rows,
            )

    def source_document_exists(self, document_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM source_documents WHERE document_id=?", (document_id,)
            ).fetchone()
        return row is not None

    def save_research_batch(
        self,
        documents: Iterable[SourceDocument],
        candidates: Iterable[DiscoveryCandidate],
        cursor: SyncCursor,
        fetched_at: datetime,
    ) -> None:
        """Persist one complete list page and its new cursor atomically.

        All documents, their discovery-candidate rows and the advanced cursor
        are committed in a single ``BEGIN IMMEDIATE`` transaction; on any
        failure everything rolls back so the previous page state stays intact
        and a cancelled or failed run never leaves a partially committed page.
        """

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for document in documents:
                    self._upsert_source_document(connection, document, fetched_at)
                for candidate in candidates:
                    self._upsert_discovery_candidate(connection, candidate, fetched_at)
                self._save_sync_state(connection, cursor)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _upsert_discovery_candidate(
        connection: sqlite3.Connection,
        candidate: DiscoveryCandidate,
        fetched_at: datetime,
    ) -> None:
        """Upsert one discovery row; keeps the earliest enqueue time.

        ``enqueued_ts`` is preserved (or set) while the candidate stays in the
        attachment queue and cleared once it leaves it, so “最早待处理时间”
        always reflects the oldest currently-pending attachment.
        """

        now_ts = int(fetched_at.timestamp())
        connection.execute(
            """
            INSERT INTO discovery_candidates(
                document_id, source_key, source_name, provider_key,
                provider_name, kind, stock_code, title, published_ts,
                discovery_type, trigger_reason, queue_status, attachment_type,
                document_url, enqueued_ts, updated_ts, signal_priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                source_key=excluded.source_key,
                source_name=excluded.source_name,
                provider_key=excluded.provider_key,
                provider_name=excluded.provider_name,
                kind=excluded.kind,
                stock_code=excluded.stock_code,
                title=excluded.title,
                published_ts=excluded.published_ts,
                discovery_type=excluded.discovery_type,
                trigger_reason=excluded.trigger_reason,
                queue_status=excluded.queue_status,
                attachment_type=excluded.attachment_type,
                document_url=excluded.document_url,
                enqueued_ts=CASE
                    WHEN excluded.queue_status = ?
                        THEN COALESCE(discovery_candidates.enqueued_ts, excluded.enqueued_ts)
                    ELSE NULL
                END,
                updated_ts=excluded.updated_ts,
                signal_priority=excluded.signal_priority
            """,
            (
                candidate.document_id,
                candidate.source_key,
                candidate.source_name,
                candidate.provider_key,
                candidate.provider_name,
                candidate.kind,
                candidate.stock_codes[0] if candidate.stock_codes else None,
                candidate.title,
                int(candidate.published_at.timestamp()),
                candidate.discovery_type,
                candidate.trigger_reason,
                candidate.queue_status,
                candidate.attachment_type,
                candidate.document_url,
                int(candidate.enqueued_at.timestamp())
                if candidate.enqueued_at is not None
                else now_ts,
                now_ts,
                int(candidate.signal_priority),
                QUEUE_STATUS_PENDING_ATTACHMENT,
            ),
        )

    def get_source_document(self, document_id: str) -> SourceDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_documents WHERE document_id=?", (document_id,)
            ).fetchone()
            if row is None:
                return None
            stocks = connection.execute(
                "SELECT stock_code, stock_name FROM source_document_stocks "
                "WHERE document_id=? ORDER BY stock_code",
                (document_id,),
            ).fetchall()
            stock_names = {
                str(item["stock_code"]): str(item["stock_name"] or "")
                for item in stocks
            }
        return self._row_to_source_document(row, stock_names)

    def get_source_documents_between(
        self, start: datetime, end: datetime
    ) -> list[SourceDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_documents "
                "WHERE published_ts >= ? AND published_ts <= ? "
                "ORDER BY published_ts DESC",
                (int(start.timestamp()), int(end.timestamp())),
            ).fetchall()
            stocks_by_document = self._stock_names_by_documents(
                connection, [str(row["document_id"]) for row in rows]
            )
        return [
            self._row_to_source_document(
                row, stocks_by_document.get(str(row["document_id"]), {})
            )
            for row in rows
        ]

    def get_source_documents_by_ids(self, document_ids: list[str]) -> list[SourceDocument]:
        """Fetch persisted documents by id, preserving the requested order."""

        if not document_ids:
            return []
        ordered_ids = list(dict.fromkeys(str(item) for item in document_ids))
        rows: list[sqlite3.Row] = []
        with self._connect() as connection:
            for start in range(0, len(ordered_ids), 900):
                batch = ordered_ids[start : start + 900]
                placeholders = ", ".join("?" for _ in batch)
                rows.extend(
                    connection.execute(
                        f"SELECT * FROM source_documents "
                        f"WHERE document_id IN ({placeholders})",  # noqa: S608
                        batch,
                    ).fetchall()
                )
            stocks_by_document = self._stock_names_by_documents(
                connection, [str(row["document_id"]) for row in rows]
            )
        by_id = {
            str(row["document_id"]): self._row_to_source_document(
                row, stocks_by_document.get(str(row["document_id"]), {})
            )
            for row in rows
        }
        return [by_id[document_id] for document_id in ordered_ids if document_id in by_id]

    def get_source_documents_by_stock(
        self,
        stock_code: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[SourceDocument]:
        clauses = ["sds.stock_code = ?"]
        params: list[object] = [stock_code]
        if start is not None:
            clauses.append("sd.published_ts >= ?")
            params.append(int(start.timestamp()))
        if end is not None:
            clauses.append("sd.published_ts <= ?")
            params.append(int(end.timestamp()))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sd.* FROM source_documents sd "
                "JOIN source_document_stocks sds ON sds.document_id = sd.document_id "
                f"WHERE {' AND '.join(clauses)} ORDER BY sd.published_ts DESC",  # noqa: S608
                params,
            ).fetchall()
            stocks_by_document = self._stock_names_by_documents(
                connection, [str(row["document_id"]) for row in rows]
            )
        return [
            self._row_to_source_document(
                row, stocks_by_document.get(str(row["document_id"]), {})
            )
            for row in rows
        ]

    # ---- 待核验事件发现层（plan.md 里程碑 7）----------------------------

    def get_discovery_candidates(
        self,
        *,
        source_key: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[DiscoveryCandidate]:
        """Read discovery rows, newest published first, stable by document id."""

        clauses: list[str] = []
        params: list[object] = []
        if source_key:
            clauses.append("source_key = ?")
            params.append(source_key)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"queue_status IN ({placeholders})")  # noqa: S608
            params.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT document_id, source_key, source_name, provider_key, "
            "provider_name, kind, stock_code, title, published_ts, "
            "discovery_type, trigger_reason, queue_status, attachment_type, "
            "document_url, enqueued_ts, updated_ts, signal_priority "
            "FROM discovery_candidates "
            f"{where} ORDER BY published_ts DESC, document_id ASC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [
            self._row_to_discovery_candidate(row)
            for row in self._fetchall(sql, tuple(params))
        ]

    def get_pending_attachment_queue(
        self,
        source_key: str,
        *,
        limit: int,
        kind: str | None = None,
        discovery_types: frozenset[str] | None = None,
        exclude_discovery_types: frozenset[str] | None = None,
        signal_priority: bool | None = None,
        newest_first: bool = False,
    ) -> list[DiscoveryCandidate]:
        """One bucket of the recoverable attachment work queue.

        Buckets: 新调研资料 (kind=research_activity, newest first),
        高优先级待核验事件 (signal-worthy titles, oldest first) and
        最旧普通待解析资料 (remaining, oldest first).  Callers interleave the
        buckets round-robin so no bucket starves the others.
        """

        clauses = ["source_key = ?", "queue_status = ?"]
        params: list[object] = [source_key, QUEUE_STATUS_PENDING_ATTACHMENT]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if discovery_types:
            placeholders = ", ".join("?" for _ in discovery_types)
            clauses.append(f"discovery_type IN ({placeholders})")  # noqa: S608
            params.extend(discovery_types)
        if exclude_discovery_types:
            placeholders = ", ".join("?" for _ in exclude_discovery_types)
            clauses.append(
                f"discovery_type NOT IN ({placeholders})"  # noqa: S608
            )
            params.extend(exclude_discovery_types)
        if signal_priority is not None:
            clauses.append("signal_priority = ?")
            params.append(1 if signal_priority else 0)
        ordering = "enqueued_ts DESC, published_ts DESC" if newest_first else "enqueued_ts ASC, published_ts ASC"
        rows = self._fetchall(
            "SELECT document_id, source_key, source_name, provider_key, "
            "provider_name, kind, stock_code, title, published_ts, "
            "discovery_type, trigger_reason, queue_status, attachment_type, "
            "document_url, enqueued_ts, updated_ts, signal_priority "
            "FROM discovery_candidates "
            f"WHERE {' AND '.join(clauses)} "  # noqa: S608
            f"ORDER BY {ordering} LIMIT {int(limit)}",
            tuple(params),
        )
        return [self._row_to_discovery_candidate(row) for row in rows]

    def get_discovery_queue_statuses(
        self, document_ids: Iterable[str]
    ) -> dict[str, str]:
        """Queue status per document id (used by page re-scans)."""

        ordered_ids = list(dict.fromkeys(str(item) for item in document_ids))
        if not ordered_ids:
            return {}
        result: dict[str, str] = {}
        with self._connect() as connection:
            for start in range(0, len(ordered_ids), 900):
                batch = ordered_ids[start : start + 900]
                placeholders = ", ".join("?" for _ in batch)
                for row in connection.execute(
                    "SELECT document_id, queue_status FROM discovery_candidates "
                    f"WHERE document_id IN ({placeholders})",  # noqa: S608
                    batch,
                ).fetchall():
                    result[str(row["document_id"])] = str(row["queue_status"])
        return result

    def set_discovery_queue_status(
        self,
        document_id: str,
        queue_status: str,
        updated_at: datetime,
    ) -> None:
        """Update one candidate's queue state (parsed/empty_text/failed)."""

        with self._connect() as connection:
            connection.execute(
                "UPDATE discovery_candidates SET queue_status=?, enqueued_ts=CASE "
                "WHEN ? = ? THEN COALESCE(enqueued_ts, ?) ELSE NULL END, "
                "updated_ts=? WHERE document_id=?",
                (
                    queue_status,
                    queue_status,
                    QUEUE_STATUS_PENDING_ATTACHMENT,
                    int(updated_at.timestamp()),
                    int(updated_at.timestamp()),
                    document_id,
                ),
            )

    def get_promoted_document_ids(self) -> set[str]:
        """Documents whose event currently carries a strict-board signal."""

        rows = self._fetchall(
            "SELECT DISTINCT ecd.document_id "
            "FROM event_cluster_documents ecd "
            "JOIN event_signals es ON es.event_id = ecd.event_id"
        )
        return {str(row["document_id"]) for row in rows}

    def get_discovery_stats(self) -> list[dict[str, object]]:
        """Per-source discovery/queue counters for the data-quality area."""

        rows = self._fetchall(
            "SELECT source_key, source_name, COUNT(*) AS discovered, "
            "SUM(CASE WHEN queue_status = 'pending_attachment' THEN 1 ELSE 0 END) "
            "AS pending, "
            "SUM(CASE WHEN queue_status = 'awaiting_review' THEN 1 ELSE 0 END) "
            "AS awaiting, "
            "SUM(CASE WHEN queue_status = 'empty_text' THEN 1 ELSE 0 END) "
            "AS empty_text, "
            "SUM(CASE WHEN queue_status = 'failed' THEN 1 ELSE 0 END) AS failed, "
            "MIN(CASE WHEN queue_status = 'pending_attachment' THEN enqueued_ts END) "
            "AS earliest_pending_ts "
            "FROM discovery_candidates GROUP BY source_key, source_name "
            "ORDER BY source_key"
        )
        return [
            {
                "source_key": str(row["source_key"]),
                "source_name": str(row["source_name"]),
                "discovered": int(row["discovered"] or 0),
                "pending": int(row["pending"] or 0),
                "awaiting": int(row["awaiting"] or 0),
                "empty_text": int(row["empty_text"] or 0),
                "failed": int(row["failed"] or 0),
                "earliest_pending_ts": (
                    int(row["earliest_pending_ts"])
                    if row["earliest_pending_ts"] is not None
                    else None
                ),
            }
            for row in rows
        ]

    @staticmethod
    def _row_to_discovery_candidate(row: sqlite3.Row) -> DiscoveryCandidate:
        return DiscoveryCandidate(
            document_id=str(row["document_id"]),
            source_key=str(row["source_key"] or ""),
            source_name=str(row["source_name"] or ""),
            provider_key=str(row["provider_key"] or ""),
            provider_name=str(row["provider_name"] or ""),
            kind=str(row["kind"] or "announcement"),
            stock_codes=(
                (str(row["stock_code"]),) if row["stock_code"] is not None else ()
            ),
            title=str(row["title"] or ""),
            published_at=datetime.fromtimestamp(
                int(row["published_ts"]), tz=SHANGHAI_TZ
            ),
            discovery_type=str(row["discovery_type"] or "other_disclosure"),
            trigger_reason=str(row["trigger_reason"] or ""),
            queue_status=str(row["queue_status"] or QUEUE_STATUS_AWAITING_REVIEW),
            attachment_type=row["attachment_type"],
            document_url=row["document_url"],
            enqueued_at=(
                datetime.fromtimestamp(int(row["enqueued_ts"]), tz=SHANGHAI_TZ)
                if row["enqueued_ts"] is not None
                else None
            ),
            updated_at=datetime.fromtimestamp(
                int(row["updated_ts"]), tz=SHANGHAI_TZ
            ),
            signal_priority=bool(int(row["signal_priority"] or 0)),
        )

    @staticmethod
    def _stock_names_by_documents(
        connection: sqlite3.Connection, document_ids: list[str]
    ) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for start in range(0, len(document_ids), 900):
            batch = document_ids[start : start + 900]
            placeholders = ", ".join("?" for _ in batch)
            rows = connection.execute(
                "SELECT document_id, stock_code, stock_name FROM source_document_stocks "
                f"WHERE document_id IN ({placeholders}) ORDER BY stock_code",  # noqa: S608
                batch,
            ).fetchall()
            for row in rows:
                result.setdefault(str(row["document_id"]), {})[
                    str(row["stock_code"])
                ] = str(row["stock_name"] or "")
        return result

    @staticmethod
    def _row_to_source_document(
        row: sqlite3.Row, stock_names: dict[str, str]
    ) -> SourceDocument:
        return SourceDocument(
            document_id=str(row["document_id"]),
            provider_key=str(row["provider_key"]),
            provider_name=str(row["provider_name"]),
            kind=str(row["kind"]),
            source_url=str(row["source_url"]),
            document_url=row["document_url"],
            title=str(row["title"]),
            published_at=datetime.fromtimestamp(row["published_ts"], tz=SHANGHAI_TZ),
            stock_codes=tuple(sorted(stock_names)),
            body_text=str(row["body_text"] or ""),
            content_hash=str(row["content_hash"]),
            parse_status=str(row["parse_status"]),
            parse_error=row["parse_error"],
            page_count=row["page_count"],
            stock_names={
                code: name
                for code, name in stock_names.items()
                if name and name != code
            },
        )

    def upsert_evidence_ref(self, evidence: EvidenceRef) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evidence_refs(
                    evidence_id, document_id, start_offset, end_offset,
                    excerpt, source_url
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    document_id=excluded.document_id,
                    start_offset=excluded.start_offset,
                    end_offset=excluded.end_offset,
                    excerpt=excluded.excerpt,
                    source_url=excluded.source_url
                """,
                (
                    evidence.evidence_id,
                    evidence.document_id,
                    evidence.start_offset,
                    evidence.end_offset,
                    evidence.excerpt,
                    evidence.source_url,
                ),
            )

    def get_evidence_refs_for_document(self, document_id: str) -> list[EvidenceRef]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence_refs WHERE document_id=? ORDER BY evidence_id",
                (document_id,),
            ).fetchall()
        return [EvidenceRef.from_dict(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # LLM extraction cache (follows document retention via cascade)
    # ------------------------------------------------------------------

    def get_llm_extraction_cache(
        self, document_id: str, model: str, prompt_schema_version: str
    ) -> dict[str, object] | None:
        """Return a cached LLM extraction response for one document, or None.

        The cache key is ``content_hash + model + prompt_schema_version``; the
        document id identifies the row and the hash is part of the persisted
        response so stale content can be detected by the caller.
        """

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM llm_extraction_cache "
                "WHERE document_id=? AND model=? AND prompt_schema_version=?",
                (document_id, model, prompt_schema_version),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["response_json"])
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def save_llm_extraction_cache(
        self,
        document_id: str,
        model: str,
        prompt_schema_version: str,
        response: dict[str, object],
        created_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO llm_extraction_cache(
                    document_id, model, prompt_schema_version, response_json, created_ts
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(document_id, model, prompt_schema_version) DO UPDATE SET
                    response_json=excluded.response_json,
                    created_ts=excluded.created_ts
                """,
                (
                    document_id,
                    model,
                    prompt_schema_version,
                    json.dumps(response, ensure_ascii=False),
                    int(created_at.timestamp()),
                ),
            )

    # ------------------------------------------------------------------
    # Event clusters, extractions and signals
    # ------------------------------------------------------------------

    def upsert_event_cluster(self, cluster: EventCluster) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO event_clusters(
                    event_id, canonical_title, first_seen_ts, last_seen_ts,
                    representative_document_id, historical_similar_event_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    canonical_title=excluded.canonical_title,
                    first_seen_ts=excluded.first_seen_ts,
                    last_seen_ts=excluded.last_seen_ts,
                    representative_document_id=excluded.representative_document_id,
                    historical_similar_event_id=excluded.historical_similar_event_id
                """,
                (
                    cluster.event_id,
                    cluster.canonical_title,
                    int(cluster.first_seen_at.timestamp()),
                    int(cluster.last_seen_at.timestamp()),
                    cluster.representative_document_id,
                    cluster.historical_similar_event_id,
                ),
            )
            connection.execute(
                "DELETE FROM event_cluster_stocks WHERE event_id=?",
                (cluster.event_id,),
            )
            connection.executemany(
                "INSERT INTO event_cluster_stocks(event_id, stock_code) VALUES (?, ?)",
                [(cluster.event_id, code) for code in sorted(set(cluster.stock_codes))],
            )
            for document_id in cluster.document_ids:
                connection.execute(
                    "INSERT OR IGNORE INTO event_cluster_documents(event_id, document_id) "
                    "VALUES (?, ?)",
                    (cluster.event_id, document_id),
                )

    def get_event_cluster(self, event_id: str) -> EventCluster | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM event_clusters WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None:
                return None
            codes = connection.execute(
                "SELECT stock_code FROM event_cluster_stocks "
                "WHERE event_id=? ORDER BY stock_code",
                (event_id,),
            ).fetchall()
            documents = connection.execute(
                "SELECT document_id FROM event_cluster_documents "
                "WHERE event_id=? ORDER BY document_id",
                (event_id,),
            ).fetchall()
        return EventCluster(
            event_id=str(row["event_id"]),
            stock_codes=tuple(str(item["stock_code"]) for item in codes),
            canonical_title=str(row["canonical_title"]),
            first_seen_at=datetime.fromtimestamp(row["first_seen_ts"], tz=SHANGHAI_TZ),
            last_seen_at=datetime.fromtimestamp(row["last_seen_ts"], tz=SHANGHAI_TZ),
            representative_document_id=str(row["representative_document_id"] or ""),
            document_ids=[str(item["document_id"]) for item in documents],
            historical_similar_event_id=row["historical_similar_event_id"],
        )

    def link_event_document(self, event_id: str, document_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO event_cluster_documents(event_id, document_id) "
                "VALUES (?, ?)",
                (event_id, document_id),
            )

    def get_event_clusters_by_document(
        self, document_id: str
    ) -> list[EventCluster]:
        """Return every cluster a document is currently linked to."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id FROM event_cluster_documents "
                "WHERE document_id=? ORDER BY event_id",
                (document_id,),
            ).fetchall()
        clusters = [
            self.get_event_cluster(str(row["event_id"])) for row in rows
        ]
        return [cluster for cluster in clusters if cluster is not None]

    def delete_event_cluster(self, event_id: str) -> None:
        """Remove an event cluster and its dependent rows (cascade)."""

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM event_cluster_documents WHERE event_id=?", (event_id,)
            )
            connection.execute(
                "DELETE FROM event_cluster_stocks WHERE event_id=?", (event_id,)
            )
            connection.execute(
                "DELETE FROM event_extractions WHERE event_id=?", (event_id,)
            )
            connection.execute(
                "DELETE FROM event_signals WHERE event_id=?", (event_id,)
            )
            connection.execute(
                "DELETE FROM event_clusters WHERE event_id=?", (event_id,)
            )

    def find_event_cluster_candidates(
        self,
        stock_codes: set[str],
        earliest_seen: datetime,
        latest_seen: datetime | None = None,
    ) -> list[EventCluster]:
        """Clusters overlapping any of ``stock_codes`` seen at/after
        ``earliest_seen`` (and at/before ``latest_seen`` when given),
        ordered by most recent activity first."""

        if not stock_codes:
            return []
        ordered_codes = sorted(stock_codes)
        placeholders = ", ".join("?" for _ in ordered_codes)
        time_clause = "ec.last_seen_ts >= ?"
        params: list[object] = [*ordered_codes, int(earliest_seen.timestamp())]
        if latest_seen is not None:
            time_clause = "ec.last_seen_ts >= ? AND ec.last_seen_ts <= ?"
            params.append(int(latest_seen.timestamp()))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT ec.event_id FROM event_clusters ec "
                "JOIN event_cluster_stocks ecs ON ecs.event_id = ec.event_id "
                f"WHERE ecs.stock_code IN ({placeholders}) AND {time_clause} "  # noqa: S608
                "ORDER BY ec.last_seen_ts DESC",
                params,
            ).fetchall()
            clusters = [
                self.get_event_cluster(str(row["event_id"]))
                for row in rows
            ]
        return [cluster for cluster in clusters if cluster is not None]

    def get_event_clusters_active(
        self, start: datetime, end: datetime
    ) -> list[EventCluster]:
        """Clusters with last activity inside ``[start, end]``, newest first.

        Hour-granularity semantics follow the selected observation window
        (plan.md 10.5): precise timestamps must fall inside
        ``[start, end]``.  Date-granularity sources (cninfo announcements are
        dated by day and stored at exactly 00:00:00+08:00) are treated as
        active for the whole disclosure day: a cluster whose ``last_seen_at``
        is exactly midnight counts when its date falls within
        ``[start.date(), end.date()]`` instead of requiring it to be after
        ``start``.  This keeps announcement-day disclosures inside an
        hour-based message window even when the window starts after midnight.
        """

        day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id FROM event_clusters "
                "WHERE last_seen_ts >= ? AND last_seen_ts <= ? "
                "ORDER BY last_seen_ts DESC",
                (int(day_start.timestamp()), int(end.timestamp())),
            ).fetchall()
        clusters = [
            self.get_event_cluster(str(row["event_id"])) for row in rows
        ]
        clusters = [cluster for cluster in clusters if cluster is not None]
        kept: list[EventCluster] = []
        for cluster in clusters:
            last = cluster.last_seen_at
            if last >= start:
                kept.append(cluster)
                continue
            is_date_only = (
                last.hour == 0
                and last.minute == 0
                and last.second == 0
                and last.microsecond == 0
                and last.date() >= start.date()
            )
            if is_date_only:
                kept.append(cluster)
        return kept

    def set_event_historical_similar(
        self, event_id: str, historical_event_id: str | None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE event_clusters SET historical_similar_event_id=? WHERE event_id=?",
                (historical_event_id, event_id),
            )

    def upsert_event_extraction(
        self, extraction: EventExtraction, created_at: datetime
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO event_extractions(
                    event_id, stock_code, event_type, direction, positive_mechanism,
                    metrics_json, certainty_stage, certainty, novelty, unexpectedness,
                    materiality_level, counter_evidence_json, evidence_ids_json,
                    no_valid_signal, extractor_kind, extractor_version, created_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, stock_code) DO UPDATE SET
                    event_type=excluded.event_type,
                    direction=excluded.direction,
                    positive_mechanism=excluded.positive_mechanism,
                    metrics_json=excluded.metrics_json,
                    certainty_stage=excluded.certainty_stage,
                    certainty=excluded.certainty,
                    novelty=excluded.novelty,
                    unexpectedness=excluded.unexpectedness,
                    materiality_level=excluded.materiality_level,
                    counter_evidence_json=excluded.counter_evidence_json,
                    evidence_ids_json=excluded.evidence_ids_json,
                    no_valid_signal=excluded.no_valid_signal,
                    extractor_kind=excluded.extractor_kind,
                    extractor_version=excluded.extractor_version,
                    created_ts=excluded.created_ts
                """,
                (
                    extraction.event_id,
                    extraction.stock_code,
                    extraction.event_type,
                    extraction.direction,
                    extraction.positive_mechanism,
                    json.dumps([dict(item) for item in extraction.metrics], ensure_ascii=False),
                    extraction.certainty_stage,
                    extraction.certainty,
                    extraction.novelty,
                    extraction.unexpectedness,
                    extraction.materiality_level,
                    json.dumps(
                        [dict(item) for item in extraction.counter_evidence],
                        ensure_ascii=False,
                    ),
                    json.dumps(list(extraction.evidence_ids), ensure_ascii=False),
                    int(extraction.no_valid_signal),
                    extraction.extractor_kind,
                    extraction.extractor_version,
                    int(created_at.timestamp()),
                ),
            )

    def get_event_extraction(
        self, event_id: str, stock_code: str
    ) -> EventExtraction | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM event_extractions WHERE event_id=? AND stock_code=?",
                (event_id, stock_code),
            ).fetchone()
        if row is None:
            return None
        return EventExtraction(
            event_id=str(row["event_id"]),
            stock_code=str(row["stock_code"]),
            event_type=str(row["event_type"]),
            direction=str(row["direction"]),
            positive_mechanism=row["positive_mechanism"],
            metrics=tuple(json.loads(row["metrics_json"] or "[]")),
            certainty_stage=str(row["certainty_stage"]),
            certainty=float(row["certainty"]),
            novelty=float(row["novelty"]),
            unexpectedness=float(row["unexpectedness"]),
            materiality_level=int(row["materiality_level"]),
            counter_evidence=tuple(json.loads(row["counter_evidence_json"] or "[]")),
            evidence_ids=tuple(json.loads(row["evidence_ids_json"] or "[]")),
            no_valid_signal=bool(row["no_valid_signal"]),
            extractor_kind=str(row["extractor_kind"]),
            extractor_version=str(row["extractor_version"]),
        )

    def upsert_event_signal(
        self,
        signal: EventSignal,
        *,
        snapshot_id: int | None = None,
        created_at: datetime,
    ) -> None:
        with self._connect() as connection:
            self._upsert_event_signal(
                connection,
                signal,
                snapshot_id=snapshot_id,
                created_at=created_at,
            )

    def replace_event_signals(
        self,
        signals: Iterable[EventSignal],
        *,
        created_at: datetime,
        snapshot_id: int | None = None,
    ) -> None:
        """Atomically replace the current short-term research boards."""

        rows = tuple(signals)
        with self._connect() as connection:
            connection.execute("DELETE FROM event_signals")
            for signal in rows:
                self._upsert_event_signal(
                    connection,
                    signal,
                    snapshot_id=snapshot_id,
                    created_at=created_at,
                )

    @staticmethod
    def _upsert_event_signal(
        connection: sqlite3.Connection,
        signal: EventSignal,
        *,
        snapshot_id: int | None,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO event_signals(
                event_id, stock_code, board, score, source_confidence,
                materiality_level, certainty, unexpectedness, novelty,
                timeliness, penalty, provisional, snapshot_id, created_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, stock_code) DO UPDATE SET
                board=excluded.board,
                score=excluded.score,
                source_confidence=excluded.source_confidence,
                materiality_level=excluded.materiality_level,
                certainty=excluded.certainty,
                unexpectedness=excluded.unexpectedness,
                novelty=excluded.novelty,
                timeliness=excluded.timeliness,
                penalty=excluded.penalty,
                provisional=excluded.provisional,
                snapshot_id=excluded.snapshot_id,
                created_ts=excluded.created_ts
            """,
            (
                signal.event_id,
                signal.stock_code,
                signal.board,
                signal.score,
                signal.source_confidence,
                signal.materiality_level,
                signal.certainty,
                signal.unexpectedness,
                signal.novelty,
                signal.timeliness,
                signal.penalty,
                int(signal.provisional),
                snapshot_id,
                int(created_at.timestamp()),
            ),
        )

    def get_event_signals(self, board: str | None = None) -> list[EventSignal]:
        if board is None:
            rows = self._fetchall(
                "SELECT es.* FROM event_signals es "
                "JOIN event_clusters ec ON ec.event_id = es.event_id "
                "ORDER BY es.score DESC, es.materiality_level DESC, "
                "es.certainty DESC, ec.last_seen_ts DESC, es.stock_code ASC"
            )
        else:
            rows = self._fetchall(
                "SELECT es.* FROM event_signals es "
                "JOIN event_clusters ec ON ec.event_id = es.event_id "
                "WHERE es.board=? "
                "ORDER BY es.score DESC, es.materiality_level DESC, "
                "es.certainty DESC, ec.last_seen_ts DESC, es.stock_code ASC",
                (board,),
            )
        return [self._row_to_event_signal(row) for row in rows]

    @staticmethod
    def _row_to_event_signal(row: sqlite3.Row) -> EventSignal:
        return EventSignal(
            event_id=str(row["event_id"]),
            stock_code=str(row["stock_code"]),
            board=str(row["board"]),
            score=float(row["score"]),
            source_confidence=float(row["source_confidence"]),
            materiality_level=int(row["materiality_level"]),
            certainty=float(row["certainty"]),
            unexpectedness=float(row["unexpectedness"]),
            novelty=float(row["novelty"]),
            timeliness=float(row["timeliness"]),
            penalty=float(row["penalty"]),
            provisional=bool(row["provisional"]),
        )

    # ------------------------------------------------------------------
    # Institutions, activities and metric snapshots
    # ------------------------------------------------------------------

    def upsert_institution(self, institution: Institution, created_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO institutions(
                    institution_id, canonical_name, group_id, institution_type,
                    verification_status, created_ts
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(institution_id) DO UPDATE SET
                    canonical_name=excluded.canonical_name,
                    group_id=excluded.group_id,
                    institution_type=excluded.institution_type,
                    verification_status=excluded.verification_status
                """,
                (
                    institution.institution_id,
                    institution.canonical_name,
                    institution.group_id,
                    institution.institution_type,
                    institution.verification_status,
                    int(created_at.timestamp()),
                ),
            )

    def get_institution(self, institution_id: str) -> Institution | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM institutions WHERE institution_id=?", (institution_id,)
            ).fetchone()
        if row is None:
            return None
        return Institution(
            institution_id=str(row["institution_id"]),
            canonical_name=str(row["canonical_name"]),
            group_id=str(row["group_id"]),
            institution_type=str(row["institution_type"]),
            verification_status=str(row["verification_status"]),
        )

    def upsert_institution_alias(self, alias: InstitutionAlias) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO institution_aliases(normalized_alias, institution_id, source)
                VALUES (?, ?, ?)
                ON CONFLICT(normalized_alias) DO UPDATE SET
                    institution_id=excluded.institution_id,
                    source=excluded.source
                """,
                (alias.normalized_alias, alias.institution_id, alias.source),
            )

    def resolve_institution_alias(self, normalized_alias: str) -> InstitutionAlias | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM institution_aliases WHERE normalized_alias=?",
                (normalized_alias,),
            ).fetchone()
        if row is None:
            return None
        return InstitutionAlias(
            normalized_alias=str(row["normalized_alias"]),
            institution_id=str(row["institution_id"]),
            source=str(row["source"]),
        )

    def get_institution_aliases(self, institution_id: str) -> list[InstitutionAlias]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM institution_aliases WHERE institution_id=? ORDER BY normalized_alias",
                (institution_id,),
            ).fetchall()
        return [
            InstitutionAlias(
                normalized_alias=str(row["normalized_alias"]),
                institution_id=str(row["institution_id"]),
                source=str(row["source"]),
            )
            for row in rows
        ]

    def upsert_research_activity(
        self, activity: ResearchActivity, fetched_at: datetime
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_activities(
                    activity_id, stock_code, source_document_id, activity_type,
                    reported_participant_count, named_participant_count, question_count,
                    high_depth_question_count, topic_counts_json, depth_counts_json,
                    date_precision, fetched_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(activity_id) DO UPDATE SET
                    stock_code=excluded.stock_code,
                    source_document_id=excluded.source_document_id,
                    activity_type=excluded.activity_type,
                    reported_participant_count=excluded.reported_participant_count,
                    named_participant_count=excluded.named_participant_count,
                    question_count=excluded.question_count,
                    high_depth_question_count=excluded.high_depth_question_count,
                    topic_counts_json=excluded.topic_counts_json,
                    depth_counts_json=excluded.depth_counts_json,
                    date_precision=excluded.date_precision,
                    fetched_ts=excluded.fetched_ts
                """,
                (
                    activity.activity_id,
                    activity.stock_code,
                    activity.source_document_id,
                    activity.activity_type,
                    activity.reported_participant_count,
                    activity.named_participant_count,
                    activity.question_count,
                    activity.high_depth_question_count,
                    json.dumps(activity.topic_counts, ensure_ascii=False),
                    json.dumps(activity.depth_counts or {}, ensure_ascii=False),
                    activity.date_precision,
                    int(fetched_at.timestamp()),
                ),
            )
            connection.execute(
                "DELETE FROM research_activity_dates WHERE activity_id=?",
                (activity.activity_id,),
            )
            connection.executemany(
                "INSERT INTO research_activity_dates(activity_id, activity_date) VALUES (?, ?)",
                [(activity.activity_id, day.isoformat()) for day in activity.activity_dates],
            )

    def get_research_activity(self, activity_id: str) -> ResearchActivity | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_activities WHERE activity_id=?", (activity_id,)
            ).fetchone()
            if row is None:
                return None
            dates = connection.execute(
                "SELECT activity_date FROM research_activity_dates "
                "WHERE activity_id=? ORDER BY activity_date",
                (activity_id,),
            ).fetchall()
        return self._row_to_research_activity(row, [str(item["activity_date"]) for item in dates])

    def get_research_activities_between(
        self,
        start: date,
        end: date,
        stock_code: str | None = None,
    ) -> list[ResearchActivity]:
        clauses = ["rad.activity_date >= ?", "rad.activity_date <= ?"]
        params: list[object] = [start.isoformat(), end.isoformat()]
        if stock_code is not None:
            clauses.append("ra.stock_code = ?")
            params.append(stock_code)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT ra.* FROM research_activities ra "
                "JOIN research_activity_dates rad ON rad.activity_id = ra.activity_id "
                f"WHERE {' AND '.join(clauses)} ORDER BY ra.activity_id",  # noqa: S608
                params,
            ).fetchall()
            dates_by_activity: dict[str, list[str]] = {}
            for row in rows:
                dates = connection.execute(
                    "SELECT activity_date FROM research_activity_dates "
                    "WHERE activity_id=? ORDER BY activity_date",
                    (str(row["activity_id"]),),
                ).fetchall()
                dates_by_activity[str(row["activity_id"])] = [
                    str(item["activity_date"]) for item in dates
                ]
        return [
            self._row_to_research_activity(
                row, dates_by_activity.get(str(row["activity_id"]), [])
            )
            for row in rows
        ]

    @staticmethod
    def _row_to_research_activity(
        row: sqlite3.Row, activity_dates: list[str]
    ) -> ResearchActivity:
        return ResearchActivity(
            activity_id=str(row["activity_id"]),
            stock_code=str(row["stock_code"]),
            source_document_id=str(row["source_document_id"]),
            activity_dates=tuple(date.fromisoformat(value) for value in activity_dates),
            activity_type=str(row["activity_type"]),
            reported_participant_count=row["reported_participant_count"],
            named_participant_count=int(row["named_participant_count"]),
            question_count=int(row["question_count"]),
            high_depth_question_count=int(row["high_depth_question_count"]),
            topic_counts={
                str(key): int(value)
                for key, value in json.loads(row["topic_counts_json"] or "{}").items()
            },
            depth_counts={
                str(key): int(value)
                for key, value in json.loads(row["depth_counts_json"] or "{}").items()
            },
            date_precision=str(row["date_precision"] or "explicit"),
        )

    def add_research_participant(self, participant: ResearchParticipant) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO research_participants(
                    activity_id, institution_id, analyst_name, evidence_id
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    participant.activity_id,
                    participant.institution_id,
                    participant.analyst_name,
                    participant.evidence_id,
                ),
            )

    def replace_research_participants(
        self,
        activity_id: str,
        participants: list[ResearchParticipant],
    ) -> None:
        """Replace all participants of one activity in a single transaction.

        Re-parsing a research document must not leave stale participants from
        earlier parsing versions behind (they would otherwise inflate the
        institution set with obsolete or garbage entities).
        """

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM research_participants WHERE activity_id=?",
                (activity_id,),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO research_participants(
                    activity_id, institution_id, analyst_name, evidence_id
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        participant.activity_id,
                        participant.institution_id,
                        participant.analyst_name,
                        participant.evidence_id,
                    )
                    for participant in participants
                ],
            )

    def get_research_participants(self, activity_id: str) -> list[ResearchParticipant]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_participants WHERE activity_id=? "
                "ORDER BY institution_id",
                (activity_id,),
            ).fetchall()
        return [
            ResearchParticipant(
                activity_id=str(row["activity_id"]),
                institution_id=str(row["institution_id"]),
                analyst_name=row["analyst_name"],
                evidence_id=str(row["evidence_id"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Institution warming v2 occurrence/coverage base (schema 122)
    # ------------------------------------------------------------------

    def replace_research_occurrences(
        self,
        activity_id: str,
        occurrences: list[ActivityOccurrence],
        participant_occurrences: list[ResearchParticipantOccurrence],
    ) -> None:
        """Atomically replace reliable dates and institution/date mappings.

        Parent and child rows are replaced together so a parser rerun cannot
        expose a mixed occurrence version.  Legacy activity dates and
        participants are deliberately untouched during the compatibility
        period.
        """

        if any(item.activity_id != activity_id for item in occurrences):
            raise ValueError("活动发生日包含不匹配的 activity_id")
        for item in occurrences:
            if item.date_precision not in ACTIVITY_DATE_PRECISIONS:
                raise ValueError("活动发生日包含未知的日期精度")
            if (
                item.period_start is not None
                and item.period_end is not None
                and item.period_start > item.period_end
            ):
                raise ValueError("活动发生日区间起点晚于终点")
            if item.metric_eligible and (
                item.occurred_on is None
                or item.date_precision != ACTIVITY_DATE_PRECISION_EXPLICIT_DAY
            ):
                raise ValueError("只有明确到日的活动时间可参与日期指标")
        occurrence_ids = {item.occurrence_id for item in occurrences}
        if any(
            item.activity_id != activity_id
            or item.activity_occurrence_id not in occurrence_ids
            for item in participant_occurrences
        ):
            raise ValueError("参与者发生日未关联到同一活动的发生日")

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM activity_occurrences WHERE activity_id=?",
                (activity_id,),
            )
            connection.executemany(
                """
                INSERT INTO activity_occurrences(
                    occurrence_id, activity_id, occurred_on, period_start,
                    period_end, date_precision, metric_eligible,
                    exclusion_reason, evidence_id, parse_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.occurrence_id,
                        item.activity_id,
                        item.occurred_on.isoformat() if item.occurred_on else None,
                        item.period_start.isoformat() if item.period_start else None,
                        item.period_end.isoformat() if item.period_end else None,
                        item.date_precision,
                        int(item.metric_eligible),
                        item.exclusion_reason,
                        item.evidence_id,
                        item.parse_version,
                    )
                    for item in occurrences
                ],
            )
            connection.executemany(
                """
                INSERT INTO research_participant_occurrences(
                    participant_occurrence_id, activity_occurrence_id,
                    activity_id, institution_id, analyst_name,
                    research_eligible, eligibility_reason, evidence_id,
                    parse_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.participant_occurrence_id,
                        item.activity_occurrence_id,
                        item.activity_id,
                        item.institution_id,
                        item.analyst_name,
                        int(item.research_eligible),
                        item.eligibility_reason,
                        item.evidence_id,
                        item.parse_version,
                    )
                    for item in participant_occurrences
                ],
            )

    def replace_research_activity_bundles(
        self,
        bundles: Iterable[
            tuple[
                ResearchActivity,
                tuple[EvidenceRef, ...],
                tuple[ResearchParticipant, ...],
                tuple[ResearchParticipantMention, ...],
                tuple[ActivityOccurrence, ...],
                tuple[ResearchParticipantOccurrence, ...],
                ReportedParticipantCount,
            ]
        ],
        fetched_at: datetime,
    ) -> None:
        """Atomically publish a fully parsed 550-day activity staging run."""

        rows = tuple(bundles)
        for activity, _refs, _participants, _mentions, occurrences, participant_occurrences, _count in rows:
            if any(item.activity_id != activity.activity_id for item in occurrences):
                raise ValueError("活动发生日包含不匹配的 activity_id")
            occurrence_ids = {item.occurrence_id for item in occurrences}
            if len(occurrence_ids) != len(occurrences):
                raise ValueError("同一活动包含重复 occurrence_id")
            if any(
                item.activity_id != activity.activity_id
                or item.activity_occurrence_id not in occurrence_ids
                for item in participant_occurrences
            ):
                raise ValueError("参与者发生日未关联到同一活动的发生日")
        fetched_ts = int(fetched_at.timestamp())
        with self._connect() as connection:
            for (
                activity,
                evidence_refs,
                participants,
                mentions,
                occurrences,
                participant_occurrences,
                count,
            ) in rows:
                connection.execute(
                    """
                    INSERT INTO research_activities(
                        activity_id, stock_code, source_document_id, activity_type,
                        reported_participant_count, named_participant_count,
                        question_count, high_depth_question_count,
                        topic_counts_json, depth_counts_json, date_precision,
                        fetched_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(activity_id) DO UPDATE SET
                        stock_code=excluded.stock_code,
                        source_document_id=excluded.source_document_id,
                        activity_type=excluded.activity_type,
                        reported_participant_count=excluded.reported_participant_count,
                        named_participant_count=excluded.named_participant_count,
                        question_count=excluded.question_count,
                        high_depth_question_count=excluded.high_depth_question_count,
                        topic_counts_json=excluded.topic_counts_json,
                        depth_counts_json=excluded.depth_counts_json,
                        date_precision=excluded.date_precision,
                        fetched_ts=excluded.fetched_ts
                    """,
                    (
                        activity.activity_id,
                        activity.stock_code,
                        activity.source_document_id,
                        activity.activity_type,
                        activity.reported_participant_count,
                        activity.named_participant_count,
                        activity.question_count,
                        activity.high_depth_question_count,
                        json.dumps(activity.topic_counts, ensure_ascii=False),
                        json.dumps(activity.depth_counts or {}, ensure_ascii=False),
                        activity.date_precision,
                        fetched_ts,
                    ),
                )
                connection.execute(
                    "DELETE FROM research_activity_dates WHERE activity_id=?",
                    (activity.activity_id,),
                )
                connection.executemany(
                    "INSERT INTO research_activity_dates(activity_id, activity_date) VALUES (?, ?)",
                    [
                        (activity.activity_id, day.isoformat())
                        for day in activity.activity_dates
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO evidence_refs(
                        evidence_id, document_id, start_offset, end_offset,
                        excerpt, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                        document_id=excluded.document_id,
                        start_offset=excluded.start_offset,
                        end_offset=excluded.end_offset,
                        excerpt=excluded.excerpt,
                        source_url=excluded.source_url
                    """,
                    [
                        (
                            ref.evidence_id,
                            ref.document_id,
                            ref.start_offset,
                            ref.end_offset,
                            ref.excerpt,
                            ref.source_url,
                        )
                        for ref in evidence_refs
                    ],
                )
                connection.execute(
                    "DELETE FROM research_participants WHERE activity_id=?",
                    (activity.activity_id,),
                )
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO research_participants(
                        activity_id, institution_id, analyst_name, evidence_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            item.activity_id,
                            item.institution_id,
                            item.analyst_name,
                            item.evidence_id,
                        )
                        for item in participants
                    ],
                )
                connection.execute(
                    "DELETE FROM research_participant_mentions WHERE activity_id=?",
                    (activity.activity_id,),
                )
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO research_participant_mentions(
                        mention_id, document_id, activity_id, raw_name,
                        start_offset, end_offset, organization_category,
                        parse_version, review_status, evidence_id, created_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item.mention_id,
                            item.document_id,
                            item.activity_id,
                            item.raw_name,
                            item.start_offset,
                            item.end_offset,
                            item.organization_category,
                            item.parse_version,
                            item.review_status,
                            item.evidence_id,
                            int(item.created_at.timestamp()),
                        )
                        for item in mentions
                    ],
                )
                connection.execute(
                    "DELETE FROM activity_occurrences WHERE activity_id=?",
                    (activity.activity_id,),
                )
                connection.executemany(
                    """
                    INSERT INTO activity_occurrences(
                        occurrence_id, activity_id, occurred_on, period_start,
                        period_end, date_precision, metric_eligible,
                        exclusion_reason, evidence_id, parse_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item.occurrence_id,
                            item.activity_id,
                            item.occurred_on.isoformat() if item.occurred_on else None,
                            item.period_start.isoformat() if item.period_start else None,
                            item.period_end.isoformat() if item.period_end else None,
                            item.date_precision,
                            int(item.metric_eligible),
                            item.exclusion_reason,
                            item.evidence_id,
                            item.parse_version,
                        )
                        for item in occurrences
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO research_participant_occurrences(
                        participant_occurrence_id, activity_occurrence_id,
                        activity_id, institution_id, analyst_name,
                        research_eligible, eligibility_reason, evidence_id,
                        parse_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item.participant_occurrence_id,
                            item.activity_occurrence_id,
                            item.activity_id,
                            item.institution_id,
                            item.analyst_name,
                            int(item.research_eligible),
                            item.eligibility_reason,
                            item.evidence_id,
                            item.parse_version,
                        )
                        for item in participant_occurrences
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO reported_participant_counts(
                        activity_id, named_research_count, all_named_org_count,
                        reported_institution_count, reported_person_count,
                        evidence_id, updated_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(activity_id) DO UPDATE SET
                        named_research_count=excluded.named_research_count,
                        all_named_org_count=excluded.all_named_org_count,
                        reported_institution_count=excluded.reported_institution_count,
                        reported_person_count=excluded.reported_person_count,
                        evidence_id=excluded.evidence_id,
                        updated_ts=excluded.updated_ts
                    """,
                    (
                        count.activity_id,
                        count.named_research_count,
                        count.all_named_org_count,
                        count.reported_institution_count,
                        count.reported_person_count,
                        count.evidence_id,
                        int(count.updated_at.timestamp()),
                    ),
                )

    def get_activity_occurrences(
        self, activity_id: str, *, metric_eligible_only: bool = False
    ) -> list[ActivityOccurrence]:
        query = "SELECT * FROM activity_occurrences WHERE activity_id=?"
        if metric_eligible_only:
            query += " AND metric_eligible=1"
        query += " ORDER BY COALESCE(occurred_on, period_start, period_end), occurrence_id"
        rows = self._fetchall(query, (activity_id,))
        return [
            ActivityOccurrence(
                occurrence_id=str(row["occurrence_id"]),
                activity_id=str(row["activity_id"]),
                occurred_on=(
                    date.fromisoformat(str(row["occurred_on"]))
                    if row["occurred_on"]
                    else None
                ),
                period_start=(
                    date.fromisoformat(str(row["period_start"]))
                    if row["period_start"]
                    else None
                ),
                period_end=(
                    date.fromisoformat(str(row["period_end"]))
                    if row["period_end"]
                    else None
                ),
                date_precision=str(row["date_precision"]),
                metric_eligible=bool(row["metric_eligible"]),
                exclusion_reason=row["exclusion_reason"],
                evidence_id=row["evidence_id"],
                parse_version=str(row["parse_version"] or ""),
            )
            for row in rows
        ]

    def get_research_participant_occurrences(
        self, activity_id: str, *, research_eligible_only: bool = False
    ) -> list[ResearchParticipantOccurrence]:
        query = (
            "SELECT * FROM research_participant_occurrences WHERE activity_id=?"
        )
        if research_eligible_only:
            query += " AND research_eligible=1"
        query += (
            " ORDER BY activity_occurrence_id, institution_id, "
            "COALESCE(analyst_name, ''), participant_occurrence_id"
        )
        rows = self._fetchall(query, (activity_id,))
        return [
            ResearchParticipantOccurrence(
                participant_occurrence_id=str(row["participant_occurrence_id"]),
                activity_occurrence_id=str(row["activity_occurrence_id"]),
                activity_id=str(row["activity_id"]),
                institution_id=str(row["institution_id"]),
                analyst_name=row["analyst_name"],
                research_eligible=bool(row["research_eligible"]),
                eligibility_reason=str(row["eligibility_reason"] or ""),
                evidence_id=row["evidence_id"],
                parse_version=str(row["parse_version"] or ""),
            )
            for row in rows
        ]

    def upsert_source_window_coverage(
        self, coverage: SourceWindowCoverage
    ) -> None:
        if coverage.source_kind != "research_activity":
            raise ValueError("机构窗口覆盖只能记录 research_activity 来源")
        if coverage.requested_start > coverage.requested_end:
            raise ValueError("请求覆盖区间起点晚于终点")
        if (
            coverage.covered_start is not None
            and coverage.covered_end is not None
            and coverage.covered_start > coverage.covered_end
        ):
            raise ValueError("实际覆盖区间起点晚于终点")
        if (
            coverage.covered_end is not None
            and coverage.covered_end > coverage.requested_end
        ):
            raise ValueError("实际覆盖结束日不得晚于请求结束日")
        if coverage.cohort_eligible and (
            not coverage.reached_cutoff
            or not coverage.reconciled
            or coverage.error is not None
            or coverage.covered_start is None
            or coverage.covered_start > coverage.requested_start
            or coverage.covered_end is None
            or coverage.covered_end < coverage.requested_end
        ):
            raise ValueError("来源未满足完整、无错误且到达回填边界的 cohort 条件")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_window_coverages(
                    source_key, market, source_kind, window_kind,
                    source_cohort_id, requested_start, requested_end,
                    covered_start, covered_end, reached_cutoff, reconciled,
                    cohort_eligible, last_success_ts, last_error,
                    exclusion_reason, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key, market, window_kind, source_cohort_id)
                DO UPDATE SET
                    source_kind=excluded.source_kind,
                    requested_start=excluded.requested_start,
                    requested_end=excluded.requested_end,
                    covered_start=excluded.covered_start,
                    covered_end=excluded.covered_end,
                    reached_cutoff=excluded.reached_cutoff,
                    reconciled=excluded.reconciled,
                    cohort_eligible=excluded.cohort_eligible,
                    last_success_ts=excluded.last_success_ts,
                    last_error=excluded.last_error,
                    exclusion_reason=excluded.exclusion_reason,
                    updated_ts=excluded.updated_ts
                """,
                (
                    coverage.source_key,
                    coverage.market,
                    coverage.source_kind,
                    coverage.window_kind,
                    coverage.source_cohort_id,
                    coverage.requested_start.isoformat(),
                    coverage.requested_end.isoformat(),
                    coverage.covered_start.isoformat()
                    if coverage.covered_start
                    else None,
                    coverage.covered_end.isoformat() if coverage.covered_end else None,
                    int(coverage.reached_cutoff),
                    int(coverage.reconciled),
                    int(coverage.cohort_eligible),
                    int(coverage.last_success_at.timestamp())
                    if coverage.last_success_at
                    else None,
                    coverage.error,
                    coverage.exclusion_reason,
                    int(coverage.updated_at.timestamp()),
                ),
            )

    def get_source_window_coverages(
        self,
        *,
        market: str | None = None,
        window_kind: str | None = None,
        source_cohort_id: str | None = None,
    ) -> list[SourceWindowCoverage]:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("market", market),
            ("window_kind", window_kind),
            ("source_cohort_id", source_cohort_id),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                parameters.append(value)
        query = "SELECT * FROM source_window_coverages"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY market, window_kind, source_key, source_cohort_id"
        rows = self._fetchall(query, tuple(parameters))
        return [
            SourceWindowCoverage(
                source_key=str(row["source_key"]),
                market=str(row["market"]),
                source_kind=str(row["source_kind"]),
                window_kind=str(row["window_kind"]),
                source_cohort_id=str(row["source_cohort_id"]),
                requested_start=date.fromisoformat(str(row["requested_start"])),
                requested_end=date.fromisoformat(str(row["requested_end"])),
                covered_start=(
                    date.fromisoformat(str(row["covered_start"]))
                    if row["covered_start"]
                    else None
                ),
                covered_end=(
                    date.fromisoformat(str(row["covered_end"]))
                    if row["covered_end"]
                    else None
                ),
                reached_cutoff=bool(row["reached_cutoff"]),
                reconciled=bool(row["reconciled"]),
                cohort_eligible=bool(row["cohort_eligible"]),
                last_success_at=(
                    datetime.fromtimestamp(row["last_success_ts"], tz=SHANGHAI_TZ)
                    if row["last_success_ts"] is not None
                    else None
                ),
                error=row["last_error"],
                exclusion_reason=row["exclusion_reason"],
                updated_at=datetime.fromtimestamp(row["updated_ts"], tz=SHANGHAI_TZ),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # v2 多事实/参与者提及层 (plan.md 第三部分, schema 121)
    # ------------------------------------------------------------------

    def upsert_event_claim(self, claim: EventClaim) -> None:
        """Upsert one candidate event fact (v2 多事实管线)."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO event_claims (
                    claim_id, document_id, stock_code, event_type, direction,
                    positive_mechanism, metrics_json, certainty_stage,
                    certainty, materiality_level, counter_evidence_json,
                    evidence_ids_json, rejection_reason, review_status,
                    gate_trace_json, extractor_kind, extractor_version,
                    created_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    document_id=excluded.document_id,
                    stock_code=excluded.stock_code,
                    event_type=excluded.event_type,
                    direction=excluded.direction,
                    positive_mechanism=excluded.positive_mechanism,
                    metrics_json=excluded.metrics_json,
                    certainty_stage=excluded.certainty_stage,
                    certainty=excluded.certainty,
                    materiality_level=excluded.materiality_level,
                    counter_evidence_json=excluded.counter_evidence_json,
                    evidence_ids_json=excluded.evidence_ids_json,
                    rejection_reason=excluded.rejection_reason,
                    review_status=excluded.review_status,
                    gate_trace_json=excluded.gate_trace_json,
                    extractor_kind=excluded.extractor_kind,
                    extractor_version=excluded.extractor_version,
                    created_ts=excluded.created_ts
                """,
                (
                    claim.claim_id,
                    claim.document_id,
                    claim.stock_code,
                    claim.event_type,
                    claim.direction,
                    claim.positive_mechanism,
                    json.dumps(claim.metrics, ensure_ascii=False),
                    claim.certainty_stage,
                    claim.certainty,
                    claim.materiality_level,
                    json.dumps(claim.counter_evidence, ensure_ascii=False),
                    json.dumps(claim.evidence_ids, ensure_ascii=False),
                    claim.rejection_reason,
                    claim.review_status,
                    json.dumps(claim.gate_trace, ensure_ascii=False),
                    claim.extractor_kind,
                    claim.extractor_version,
                    int(claim.created_at.timestamp()),
                ),
            )

    def get_event_claims_by_document(
        self, document_id: str
    ) -> list[EventClaim]:
        """Read all candidate facts for one document (newest first)."""

        rows = self._fetchall(
            "SELECT * FROM event_claims WHERE document_id = ? "
            "ORDER BY created_ts DESC, claim_id ASC",
            (document_id,),
        )
        return [self._event_claim_from_row(row) for row in rows]

    def get_event_claims_by_stock(
        self, stock_code: str, limit: int | None = None
    ) -> list[EventClaim]:
        """Read candidate facts for one stock (newest first)."""

        query = (
            "SELECT * FROM event_claims WHERE stock_code = ? "
            "ORDER BY created_ts DESC, claim_id ASC"
        )
        parameters: list[object] = [stock_code]
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(int(limit))
        rows = self._fetchall(query, tuple(parameters))
        return [self._event_claim_from_row(row) for row in rows]

    @staticmethod
    def _event_claim_from_row(row: sqlite3.Row) -> EventClaim:
        return EventClaim(
            claim_id=str(row["claim_id"]),
            document_id=str(row["document_id"]),
            stock_code=str(row["stock_code"]),
            event_type=str(row["event_type"]),
            direction=str(row["direction"]),
            positive_mechanism=row["positive_mechanism"],
            metrics=tuple(
                dict(item) for item in json.loads(row["metrics_json"] or "[]")
            ),
            certainty_stage=str(row["certainty_stage"] or ""),
            certainty=float(row["certainty"] or 0.0),
            materiality_level=int(row["materiality_level"] or 0),
            counter_evidence=tuple(
                dict(item)
                for item in json.loads(row["counter_evidence_json"] or "[]")
            ),
            evidence_ids=tuple(
                str(item) for item in json.loads(row["evidence_ids_json"] or "[]")
            ),
            rejection_reason=row["rejection_reason"],
            review_status=str(row["review_status"] or "pending_review"),
            gate_trace=tuple(
                dict(item) for item in json.loads(row["gate_trace_json"] or "[]")
            ),
            extractor_kind=str(row["extractor_kind"] or "rules"),
            extractor_version=str(row["extractor_version"] or ""),
            created_at=datetime.fromtimestamp(row["created_ts"], tz=SHANGHAI_TZ),
        )

    def replace_participant_mentions(
        self,
        activity_id: str,
        mentions: list[ResearchParticipantMention],
    ) -> None:
        """Atomically replace one activity's raw participant mentions.

        每份活动按新解析版本原子替换旧提及（v2 里程碑 4：历史重算时不允许
        旧版本残留）。
        """

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM research_participant_mentions WHERE activity_id=?",
                (activity_id,),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO research_participant_mentions (
                    mention_id, document_id, activity_id, raw_name,
                    start_offset, end_offset, organization_category,
                    parse_version, review_status, evidence_id, created_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        mention.mention_id,
                        mention.document_id,
                        mention.activity_id,
                        mention.raw_name,
                        mention.start_offset,
                        mention.end_offset,
                        mention.organization_category,
                        mention.parse_version,
                        mention.review_status,
                        mention.evidence_id,
                        int(mention.created_at.timestamp()),
                    )
                    for mention in mentions
                ],
            )

    def get_participant_mentions(
        self, activity_id: str
    ) -> list[ResearchParticipantMention]:
        """Read one activity's raw participant mentions (stable row order)."""

        rows = self._fetchall(
            "SELECT * FROM research_participant_mentions "
            "WHERE activity_id = ? ORDER BY mention_id ASC",
            (activity_id,),
        )
        return [self._mention_from_row(row) for row in rows]

    @staticmethod
    def _mention_from_row(row: sqlite3.Row) -> ResearchParticipantMention:
        return ResearchParticipantMention(
            mention_id=str(row["mention_id"]),
            document_id=str(row["document_id"]),
            activity_id=str(row["activity_id"]),
            raw_name=str(row["raw_name"]),
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
            organization_category=str(
                row["organization_category"] or "other_organization"
            ),
            parse_version=str(row["parse_version"] or ""),
            review_status=str(row["review_status"] or "pending_review"),
            evidence_id=row["evidence_id"],
            created_at=datetime.fromtimestamp(row["created_ts"], tz=SHANGHAI_TZ),
        )

    def upsert_reported_participant_count(
        self, count: ReportedParticipantCount
    ) -> None:
        """Upsert one activity's structured disclosure totals."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reported_participant_counts (
                    activity_id, named_research_count, all_named_org_count,
                    reported_institution_count, reported_person_count,
                    evidence_id, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(activity_id) DO UPDATE SET
                    named_research_count=excluded.named_research_count,
                    all_named_org_count=excluded.all_named_org_count,
                    reported_institution_count=excluded.reported_institution_count,
                    reported_person_count=excluded.reported_person_count,
                    evidence_id=excluded.evidence_id,
                    updated_ts=excluded.updated_ts
                """,
                (
                    count.activity_id,
                    count.named_research_count,
                    count.all_named_org_count,
                    count.reported_institution_count,
                    count.reported_person_count,
                    count.evidence_id,
                    int(count.updated_at.timestamp()),
                ),
            )

    def get_reported_participant_count(
        self, activity_id: str
    ) -> ReportedParticipantCount | None:
        rows = self._fetchall(
            "SELECT * FROM reported_participant_counts WHERE activity_id = ?",
            (activity_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return ReportedParticipantCount(
            activity_id=str(row["activity_id"]),
            named_research_count=int(row["named_research_count"] or 0),
            all_named_org_count=int(row["all_named_org_count"] or 0),
            reported_institution_count=row["reported_institution_count"],
            reported_person_count=row["reported_person_count"],
            evidence_id=row["evidence_id"],
            updated_at=datetime.fromtimestamp(row["updated_ts"], tz=SHANGHAI_TZ),
        )

    def upsert_institution_metric_snapshot(
        self,
        *,
        stock_code: str,
        window_kind: str,
        metrics: dict[str, object],
        window_start: datetime | None,
        window_end: datetime | None,
        snapshot_at: datetime,
        publish: bool = True,
        metric_version: str = "z20_legacy",
        source_cohort_id: str = "",
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO institution_metric_snapshots(
                    stock_code, window_kind, window_start_ts, window_end_ts,
                    snapshot_ts, metrics_json, metric_version, source_cohort_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stock_code,
                    window_kind,
                    int(window_start.timestamp()) if window_start else None,
                    int(window_end.timestamp()) if window_end else None,
                    int(snapshot_at.timestamp()),
                    json.dumps(metrics, ensure_ascii=False),
                    metric_version,
                    source_cohort_id,
                ),
            )
            if publish:
                self._set_institution_metric_batch(connection, snapshot_at)
            return int(cursor.lastrowid)

    def mark_institution_metric_batch(self, snapshot_at: datetime) -> None:
        """Publish all institution metric rows from one completed run."""

        with self._connect() as connection:
            self._set_institution_metric_batch(connection, snapshot_at)

    @staticmethod
    def _set_institution_metric_batch(
        connection: sqlite3.Connection, snapshot_at: datetime
    ) -> None:
        snapshot_ts = int(snapshot_at.timestamp())
        connection.execute(
            """
            INSERT INTO app_state(key, value_json, updated_ts) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_ts=excluded.updated_ts
            """,
            (
                INSTITUTION_METRIC_BATCH_STATE_KEY,
                json.dumps({"snapshot_ts": snapshot_ts}),
                snapshot_ts,
            ),
        )

    def get_institution_metric_snapshots(
        self, stock_code: str, window_kind: str
    ) -> list[tuple[datetime, dict[str, object]]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT snapshot_ts, metrics_json FROM institution_metric_snapshots "
                "WHERE stock_code=? AND window_kind=? ORDER BY snapshot_ts DESC",
                (stock_code, window_kind),
            ).fetchall()
        return [
            (
                datetime.fromtimestamp(row["snapshot_ts"], tz=SHANGHAI_TZ),
                dict(json.loads(row["metrics_json"] or "{}")),
            )
            for row in rows
        ]

    def get_latest_institution_metric_snapshots(
        self, window_kind: str
    ) -> dict[str, tuple[datetime, dict[str, object]]]:
        """Rows from the latest completed metric batch for one window kind."""

        with self._connect() as connection:
            state = connection.execute(
                "SELECT value_json FROM app_state WHERE key=?",
                (INSTITUTION_METRIC_BATCH_STATE_KEY,),
            ).fetchone()
            if state is None:
                # Compatibility for a database opened without initialize().
                batch_row = connection.execute(
                    "SELECT MAX(snapshot_ts) FROM institution_metric_snapshots"
                ).fetchone()
                batch_ts = batch_row[0] if batch_row else None
            else:
                try:
                    batch_ts = json.loads(state["value_json"]).get("snapshot_ts")
                except (AttributeError, json.JSONDecodeError, TypeError):
                    batch_ts = None
            if batch_ts is None:
                return {}
            rows = connection.execute(
                "SELECT stock_code, snapshot_ts, metrics_json "
                "FROM institution_metric_snapshots "
                "WHERE window_kind=? AND snapshot_ts=? ORDER BY stock_code",
                (window_kind, int(batch_ts)),
            ).fetchall()
        return {
            str(row["stock_code"]): (
                datetime.fromtimestamp(row["snapshot_ts"], tz=SHANGHAI_TZ),
                dict(json.loads(row["metrics_json"] or "{}")),
            )
            for row in rows
        }

    def get_latest_institution_metric_snapshot_records(
        self, window_kind: str
    ) -> dict[str, InstitutionMetricSnapshotRecord]:
        """Version-aware rows from the latest completed metric batch.

        The existing tuple-returning API remains unchanged for legacy callers;
        v2 consumers use this method to surface metric/cohort provenance.
        """

        with self._connect() as connection:
            state = connection.execute(
                "SELECT value_json FROM app_state WHERE key=?",
                (INSTITUTION_METRIC_BATCH_STATE_KEY,),
            ).fetchone()
            if state is None:
                batch_row = connection.execute(
                    "SELECT MAX(snapshot_ts) FROM institution_metric_snapshots"
                ).fetchone()
                batch_ts = batch_row[0] if batch_row else None
            else:
                try:
                    batch_ts = json.loads(state["value_json"]).get("snapshot_ts")
                except (AttributeError, json.JSONDecodeError, TypeError):
                    batch_ts = None
            if batch_ts is None:
                return {}
            rows = connection.execute(
                "SELECT stock_code, window_kind, window_start_ts, window_end_ts, "
                "snapshot_ts, metrics_json, metric_version, source_cohort_id "
                "FROM institution_metric_snapshots "
                "WHERE window_kind=? AND snapshot_ts=? ORDER BY stock_code",
                (window_kind, int(batch_ts)),
            ).fetchall()
        return {
            str(row["stock_code"]): InstitutionMetricSnapshotRecord(
                stock_code=str(row["stock_code"]),
                window_kind=str(row["window_kind"]),
                window_start=(
                    datetime.fromtimestamp(row["window_start_ts"], tz=SHANGHAI_TZ)
                    if row["window_start_ts"] is not None
                    else None
                ),
                window_end=(
                    datetime.fromtimestamp(row["window_end_ts"], tz=SHANGHAI_TZ)
                    if row["window_end_ts"] is not None
                    else None
                ),
                snapshot_at=datetime.fromtimestamp(
                    row["snapshot_ts"], tz=SHANGHAI_TZ
                ),
                metrics=dict(json.loads(row["metrics_json"] or "{}")),
                metric_version=str(row["metric_version"] or "z20_legacy"),
                source_cohort_id=str(row["source_cohort_id"] or ""),
            )
            for row in rows
        }

    # ------------------------------------------------------------------
    # Sync state and trading calendar
    # ------------------------------------------------------------------

    def save_sync_state(self, cursor: SyncCursor) -> None:
        with self._connect() as connection:
            self._save_sync_state(connection, cursor)

    @staticmethod
    def _save_sync_state(connection: sqlite3.Connection, cursor: SyncCursor) -> None:
        connection.execute(
            """
            INSERT INTO source_sync_state(
                source_key, sync_kind, cursor_json, target_start, covered_start,
                last_success_ts, last_error, updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, sync_kind) DO UPDATE SET
                cursor_json=excluded.cursor_json,
                target_start=excluded.target_start,
                covered_start=excluded.covered_start,
                last_success_ts=excluded.last_success_ts,
                last_error=excluded.last_error,
                updated_ts=excluded.updated_ts
            """,
            (
                cursor.source_key,
                cursor.sync_kind,
                (
                    json.dumps(cursor.cursor, ensure_ascii=False)
                    if cursor.cursor is not None
                    else None
                ),
                cursor.target_start.isoformat() if cursor.target_start else None,
                cursor.covered_start.isoformat() if cursor.covered_start else None,
                (
                    int(cursor.last_success_at.timestamp())
                    if cursor.last_success_at
                    else None
                ),
                cursor.last_error,
                int(cursor.updated_at.timestamp()),
            ),
        )

    def get_sync_state(self, source_key: str, sync_kind: str) -> SyncCursor | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_sync_state WHERE source_key=? AND sync_kind=?",
                (source_key, sync_kind),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_sync_cursor(row)

    def list_sync_states(self) -> list[SyncCursor]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_sync_state ORDER BY source_key, sync_kind"
            ).fetchall()
        return [self._row_to_sync_cursor(row) for row in rows]

    @staticmethod
    def _row_to_sync_cursor(row: sqlite3.Row) -> SyncCursor:
        cursor_json = row["cursor_json"]
        return SyncCursor(
            source_key=str(row["source_key"]),
            sync_kind=str(row["sync_kind"]),
            cursor=json.loads(cursor_json) if cursor_json else None,
            target_start=(
                date.fromisoformat(row["target_start"]) if row["target_start"] else None
            ),
            covered_start=(
                date.fromisoformat(row["covered_start"]) if row["covered_start"] else None
            ),
            last_success_at=(
                datetime.fromtimestamp(row["last_success_ts"], tz=SHANGHAI_TZ)
                if row["last_success_ts"] is not None
                else None
            ),
            last_error=row["last_error"],
            updated_at=datetime.fromtimestamp(row["updated_ts"], tz=SHANGHAI_TZ),
        )

    def replace_trading_days(
        self,
        year: int,
        trading_dates: Iterable[date],
        *,
        source: str,
        updated_at: datetime,
    ) -> None:
        """Replace all cached calendar rows for one year in one transaction."""

        rows = [
            (day.isoformat(), 1, source, year, int(updated_at.timestamp()))
            for day in trading_dates
        ]
        with self._connect() as connection:
            connection.execute("DELETE FROM trading_days WHERE year=?", (year,))
            connection.executemany(
                "INSERT INTO trading_days(trading_date, is_trading, source, year, updated_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )

    def get_trading_days_between(self, start: date, end: date) -> list[date]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT trading_date FROM trading_days "
                "WHERE is_trading=1 AND trading_date BETWEEN ? AND ? "
                "ORDER BY trading_date",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [date.fromisoformat(str(row["trading_date"])) for row in rows]

    def trading_day_count_between(self, start: date, end: date) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM trading_days "
                "WHERE is_trading=1 AND trading_date BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()
        return int(row[0]) if row else 0

    def is_trading_day(self, day: date) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT is_trading FROM trading_days WHERE trading_date=?", (day.isoformat(),)
            ).fetchone()
        return bool(row and row["is_trading"])

    def get_trading_day_source(self, year: int) -> str | None:
        """``sse`` for the official schedule, ``fallback`` for the Mon–Fri
        fallback calendar, ``None`` when no calendar rows exist for the year."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT source FROM trading_days WHERE year=? LIMIT 1", (year,)
            ).fetchone()
        return str(row["source"]) if row else None

    def purge_older_than(self, timestamp: datetime) -> None:
        cutoff = int(timestamp.timestamp())
        with self._connect() as connection:
            connection.execute("DELETE FROM articles WHERE published_ts < ?", (cutoff,))
            connection.execute("DELETE FROM interactions WHERE question_time_ts < ?", (cutoff,))

    def purge_research_retention(self, now: datetime) -> None:
        """Purge research tables with their plan-defined retention periods.

        - news bodies: 30 days
        - event clusters/extractions/signals: 180 days
        - research documents, discovery candidates, institutions, activities,
          participants and metric snapshots: 400 days

        Trading days (permanent cache) and sync cursors are never purged here;
        the ordinary article/interaction purge keeps its own window.
        """

        news_cutoff = int((now - timedelta(days=NEWS_BODY_RETENTION_DAYS)).timestamp())
        event_cutoff = int((now - timedelta(days=EVENT_RETENTION_DAYS)).timestamp())
        research_cutoff = int((now - timedelta(days=RESEARCH_RETENTION_DAYS)).timestamp())
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM source_documents WHERE kind='news' AND published_ts < ?",
                (news_cutoff,),
            )
            connection.execute(
                "DELETE FROM event_clusters WHERE last_seen_ts < ?", (event_cutoff,)
            )
            connection.execute(
                "DELETE FROM source_documents "
                "WHERE kind IN ('announcement', 'research_activity') "
                "AND published_ts < ?",
                (research_cutoff,),
            )
            # Discovery rows cascade with their documents; the explicit
            # statement keeps the queue free of orphaned rows even if a
            # foreign-key path was disabled by an older connection.
            connection.execute(
                "DELETE FROM discovery_candidates WHERE document_id NOT IN "
                "(SELECT document_id FROM source_documents)"
            )
            # v2 多事实/参与者提及行跟随父行清理（显式孤儿清理，防 FK 关闭路径）。
            connection.execute(
                "DELETE FROM event_claims WHERE document_id NOT IN "
                "(SELECT document_id FROM source_documents)"
            )
            connection.execute(
                "DELETE FROM research_participant_mentions WHERE activity_id NOT IN "
                "(SELECT activity_id FROM research_activities)"
            )
            connection.execute(
                "DELETE FROM reported_participant_counts WHERE activity_id NOT IN "
                "(SELECT activity_id FROM research_activities)"
            )
            connection.execute(
                "DELETE FROM research_participant_occurrences "
                "WHERE activity_occurrence_id NOT IN "
                "(SELECT occurrence_id FROM activity_occurrences) "
                "OR activity_id NOT IN (SELECT activity_id FROM research_activities)"
            )
            connection.execute(
                "DELETE FROM activity_occurrences WHERE activity_id NOT IN "
                "(SELECT activity_id FROM research_activities)"
            )
            connection.execute(
                "DELETE FROM research_activities WHERE fetched_ts < ?",
                (research_cutoff,),
            )
            connection.execute(
                "DELETE FROM institutions WHERE created_ts < ?", (research_cutoff,)
            )
            connection.execute(
                "DELETE FROM institution_metric_snapshots WHERE snapshot_ts < ?",
                (research_cutoff,),
            )

    def purge_coverage_retention(self, now: datetime) -> None:
        """Purge v1.2 coverage-layer rows with their plan-defined windows.

        - daily source manifests: 30 days (每日重对账近 30 天)
        - policy documents (and their links, via cascade): 400 days, aligned
          with the announcement baseline; OCR pages belong to source documents
          and are removed by the research retention purge.

        Institutions, activities, sync cursors and trading days are never
        touched here.
        """

        manifest_cutoff = int((now - timedelta(days=MANIFEST_RETENTION_DAYS)).timestamp())
        policy_cutoff = int((now - timedelta(days=POLICY_RETENTION_DAYS)).timestamp())
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM source_manifests WHERE updated_ts < ?",
                (manifest_cutoff,),
            )
            connection.execute(
                "DELETE FROM policy_documents WHERE published_ts < ?",
                (policy_cutoff,),
            )

    def clear_all(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM snapshots")
            connection.execute("DELETE FROM industry_heat_rows")
            connection.execute("DELETE FROM industry_heat_snapshots")
            connection.execute("DELETE FROM refresh_runs")
            connection.execute("DELETE FROM articles")
            connection.execute("DELETE FROM stock_industries")
            connection.execute("DELETE FROM guba_posts")
            connection.execute("DELETE FROM guba_stock_catalog")
            connection.execute("DELETE FROM guba_scan_state")
            connection.execute("DELETE FROM app_state")
            connection.execute("DELETE FROM interactions")
            connection.execute("DELETE FROM event_claims")
            connection.execute("DELETE FROM research_participant_mentions")
            connection.execute("DELETE FROM reported_participant_counts")
            connection.execute("DELETE FROM research_participant_occurrences")
            connection.execute("DELETE FROM activity_occurrences")
            connection.execute("DELETE FROM research_participants")
            connection.execute("DELETE FROM research_activity_dates")
            connection.execute("DELETE FROM research_activities")
            connection.execute("DELETE FROM institution_aliases")
            connection.execute("DELETE FROM institutions")
            connection.execute("DELETE FROM institution_metric_snapshots")
            connection.execute("DELETE FROM event_extractions")
            connection.execute("DELETE FROM event_signals")
            connection.execute("DELETE FROM event_cluster_documents")
            connection.execute("DELETE FROM event_cluster_stocks")
            connection.execute("DELETE FROM event_clusters")
            connection.execute("DELETE FROM evidence_refs")
            connection.execute("DELETE FROM llm_extraction_cache")
            connection.execute("DELETE FROM ocr_pages")
            connection.execute("DELETE FROM source_document_stocks")
            connection.execute("DELETE FROM source_documents")
            connection.execute("DELETE FROM discovery_candidates")
            connection.execute("DELETE FROM source_sync_state")
            connection.execute("DELETE FROM trading_days")
            connection.execute("DELETE FROM policy_links")
            connection.execute("DELETE FROM policy_documents")
            connection.execute("DELETE FROM source_manifests")
            connection.execute("DELETE FROM coverage_snapshots")
            connection.execute("DELETE FROM source_window_coverages")
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ------------------------------------------------------------------
    # v1.2 覆盖层 (plan.md 第二部分, v1.2 里程碑 0)
    # ------------------------------------------------------------------

    def upsert_source_manifest(self, manifest: SourceManifest) -> None:
        """Upsert one source/day manifest row (never overwrites by default —
        the reconciliation layer writes the latest observed state)."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_manifests (
                    source_key, manifest_date, total_count, document_id_count,
                    document_id_set_hash, watermark_json,
                    failure_intervals_json, ocr_status,
                    scheduled_task_result_json, coverage_status, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key, manifest_date) DO UPDATE SET
                    total_count=excluded.total_count,
                    document_id_count=excluded.document_id_count,
                    document_id_set_hash=excluded.document_id_set_hash,
                    watermark_json=excluded.watermark_json,
                    failure_intervals_json=excluded.failure_intervals_json,
                    ocr_status=excluded.ocr_status,
                    scheduled_task_result_json=excluded.scheduled_task_result_json,
                    coverage_status=excluded.coverage_status,
                    updated_ts=excluded.updated_ts
                """,
                (
                    manifest.source_key,
                    manifest.manifest_date.isoformat(),
                    manifest.total_count,
                    manifest.document_id_count,
                    manifest.document_id_set_hash,
                    json.dumps(manifest.watermark, ensure_ascii=False)
                    if manifest.watermark is not None
                    else None,
                    json.dumps(
                        [interval.to_dict() for interval in manifest.failure_intervals],
                        ensure_ascii=False,
                    ),
                    manifest.ocr_status,
                    json.dumps(manifest.scheduled_task_result, ensure_ascii=False)
                    if manifest.scheduled_task_result is not None
                    else None,
                    manifest.coverage_status,
                    int(manifest.updated_at.timestamp()),
                ),
            )

    def get_source_manifests(
        self, source_key: str | None = None, manifest_date: date | None = None
    ) -> list[SourceManifest]:
        """Read manifests, newest first; optional source/day filtering."""

        query = "SELECT * FROM source_manifests"
        conditions: list[str] = []
        parameters: list[object] = []
        if source_key is not None:
            conditions.append("source_key = ?")
            parameters.append(source_key)
        if manifest_date is not None:
            conditions.append("manifest_date = ?")
            parameters.append(manifest_date.isoformat())
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY manifest_date DESC, source_key ASC"
        rows = self._fetchall(query, tuple(parameters))
        return [self._manifest_from_row(row) for row in rows]

    def get_manifest_digest(
        self, source_key: str, manifest_date: date
    ) -> tuple[int, str | None]:
        """Return ``(count, digest)`` persisted for one source/day manifest."""

        rows = self.get_source_manifests(source_key, manifest_date)
        if not rows:
            return 0, None
        return rows[0].document_id_count, rows[0].document_id_set_hash

    def summarize_discovery_day(
        self, source_key: str, day: date
    ) -> tuple[int, str]:
        """(count, sha256 digest) of discovery IDs for one source/day.

        The reconciliation manifest's ``document_id_count`` /
        ``document_id_set_hash`` are computed from the local
        ``discovery_candidates`` rows (every public list item is persisted
        there before body parsing), so repeated sync runs accumulate the day's
        full ID set instead of just the latest page.
        """

        day_start = datetime(
            day.year, day.month, day.day, tzinfo=SHANGHAI_TZ
        )
        start_ts = int(day_start.timestamp())
        end_ts = start_ts + 86400
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_id FROM discovery_candidates "
                "WHERE source_key=? AND published_ts >= ? AND published_ts < ?",
                (source_key, start_ts, end_ts),
            ).fetchall()
        return summarize_document_ids(str(row["document_id"]) for row in rows)

    @staticmethod
    def _manifest_from_row(row: sqlite3.Row) -> SourceManifest:
        return SourceManifest(
            source_key=str(row["source_key"]),
            manifest_date=date.fromisoformat(str(row["manifest_date"])),
            total_count=int(row["total_count"]),
            document_id_count=int(row["document_id_count"]),
            document_id_set_hash=row["document_id_set_hash"],
            watermark=(
                json.loads(row["watermark_json"])
                if row["watermark_json"] is not None
                else None
            ),
            failure_intervals=tuple(
                FailureInterval.from_dict(item)
                for item in json.loads(row["failure_intervals_json"] or "[]")
            ),
            ocr_status=str(row["ocr_status"]),
            scheduled_task_result=(
                json.loads(row["scheduled_task_result_json"])
                if row["scheduled_task_result_json"] is not None
                else None
            ),
            coverage_status=str(row["coverage_status"]),
            updated_at=datetime.fromtimestamp(
                int(row["updated_ts"]), tz=SHANGHAI_TZ
            ),
        )

    def upsert_policy_document(self, document: PolicyDocument) -> None:
        """Upsert one policy document."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO policy_documents (
                    document_id, source_key, title, published_ts, source_url,
                    document_url, body_text, body_hash, body_status, body_error,
                    content_hash, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    source_key=excluded.source_key,
                    title=excluded.title,
                    published_ts=excluded.published_ts,
                    source_url=excluded.source_url,
                    document_url=excluded.document_url,
                    body_text=excluded.body_text,
                    body_hash=excluded.body_hash,
                    body_status=excluded.body_status,
                    body_error=excluded.body_error,
                    content_hash=excluded.content_hash,
                    updated_ts=excluded.updated_ts
                """,
                (
                    document.document_id,
                    document.source_key,
                    document.title,
                    int(document.published_at.timestamp()),
                    document.source_url,
                    document.document_url,
                    document.body_text,
                    document.body_hash,
                    document.body_status,
                    document.body_error,
                    document.content_hash,
                    int(document.updated_at.timestamp()),
                ),
            )

    def get_policy_documents(
        self, source_key: str | None = None, limit: int | None = None
    ) -> list[PolicyDocument]:
        """Read policy documents, newest first; optional source filter."""

        query = "SELECT * FROM policy_documents"
        parameters: list[object] = []
        if source_key is not None:
            query += " WHERE source_key = ?"
            parameters.append(source_key)
        query += " ORDER BY published_ts DESC, document_id ASC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(int(limit))
        rows = self._fetchall(query, tuple(parameters))
        return [self._policy_document_from_row(row) for row in rows]

    def get_policy_document(self, document_id: str) -> PolicyDocument | None:
        """Read one policy document by ID (``None`` when absent)."""

        rows = self._fetchall(
            "SELECT * FROM policy_documents WHERE document_id = ?",
            (document_id,),
        )
        return self._policy_document_from_row(rows[0]) if rows else None

    def summarize_policy_day(
        self, source_key: str, day: date
    ) -> tuple[int, str]:
        """(count, sha256 digest) of policy document IDs for one source/day.

        Powers the per-source policy manifest reconciliation (v1.2/v2 里程碑 2).
        """

        day_start = datetime(
            day.year, day.month, day.day, tzinfo=SHANGHAI_TZ
        )
        start_ts = int(day_start.timestamp())
        end_ts = start_ts + 86400
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_id FROM policy_documents "
                "WHERE source_key=? AND published_ts >= ? AND published_ts < ?",
                (source_key, start_ts, end_ts),
            ).fetchall()
        return summarize_document_ids(str(row["document_id"]) for row in rows)

    @staticmethod
    def _policy_document_from_row(row: sqlite3.Row) -> PolicyDocument:
        return PolicyDocument(
            document_id=str(row["document_id"]),
            source_key=str(row["source_key"]),
            title=str(row["title"]),
            published_at=datetime.fromtimestamp(
                int(row["published_ts"]), tz=SHANGHAI_TZ
            ),
            source_url=str(row["source_url"]),
            document_url=row["document_url"],
            body_text=str(row["body_text"] or ""),
            body_hash=row["body_hash"],
            body_status=str(row["body_status"]),
            body_error=row["body_error"],
            content_hash=str(row["content_hash"]),
            updated_at=datetime.fromtimestamp(
                int(row["updated_ts"]), tz=SHANGHAI_TZ
            ),
        )

    def upsert_policy_link(self, link: PolicyLink) -> None:
        """Upsert one policy/announcement dual-attribution link."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO policy_links (
                    link_id, policy_document_id, target_document_id, stock_code,
                    link_kind, evidence_excerpt, evidence_id, created_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link_id) DO UPDATE SET
                    policy_document_id=excluded.policy_document_id,
                    target_document_id=excluded.target_document_id,
                    stock_code=excluded.stock_code,
                    link_kind=excluded.link_kind,
                    evidence_excerpt=excluded.evidence_excerpt,
                    evidence_id=excluded.evidence_id,
                    created_ts=excluded.created_ts
                """,
                (
                    link.link_id,
                    link.policy_document_id,
                    link.target_document_id,
                    link.stock_code,
                    link.link_kind,
                    link.evidence_excerpt,
                    link.evidence_id,
                    int(link.created_at.timestamp()),
                ),
            )

    def get_policy_links(
        self,
        policy_document_id: str | None = None,
        stock_code: str | None = None,
    ) -> list[PolicyLink]:
        """Read policy links; optional policy document or stock filter."""

        query = "SELECT * FROM policy_links"
        conditions: list[str] = []
        parameters: list[object] = []
        if policy_document_id is not None:
            conditions.append("policy_document_id = ?")
            parameters.append(policy_document_id)
        if stock_code is not None:
            conditions.append("stock_code = ?")
            parameters.append(stock_code)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_ts ASC, link_id ASC"
        rows = self._fetchall(query, tuple(parameters))
        return [self._policy_link_from_row(row) for row in rows]

    @staticmethod
    def _policy_link_from_row(row: sqlite3.Row) -> PolicyLink:
        return PolicyLink(
            link_id=str(row["link_id"]),
            policy_document_id=str(row["policy_document_id"]),
            target_document_id=row["target_document_id"],
            stock_code=row["stock_code"],
            link_kind=str(row["link_kind"]),
            evidence_excerpt=str(row["evidence_excerpt"]),
            evidence_id=row["evidence_id"],
            created_at=datetime.fromtimestamp(
                int(row["created_ts"]), tz=SHANGHAI_TZ
            ),
        )

    def save_ocr_page(self, result: OcrPageResult) -> None:
        """Upsert one OCR page result."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ocr_pages (
                    document_id, page_index, confidence, text, model_version,
                    evidence_url, status, error, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, page_index) DO UPDATE SET
                    confidence=excluded.confidence,
                    text=excluded.text,
                    model_version=excluded.model_version,
                    evidence_url=excluded.evidence_url,
                    status=excluded.status,
                    error=excluded.error,
                    updated_ts=excluded.updated_ts
                """,
                (
                    result.document_id,
                    result.page_index,
                    result.confidence,
                    result.text,
                    result.model_version,
                    result.evidence_url,
                    result.status,
                    result.error,
                    int(result.updated_at.timestamp()),
                ),
            )

    def get_ocr_pages(self, document_id: str) -> list[OcrPageResult]:
        """Read OCR pages for one document, ordered by page index."""

        rows = self._fetchall(
            "SELECT * FROM ocr_pages WHERE document_id = ? "
            "ORDER BY page_index ASC",
            (document_id,),
        )
        return [self._ocr_page_from_row(row) for row in rows]

    @staticmethod
    def _ocr_page_from_row(row: sqlite3.Row) -> OcrPageResult:
        return OcrPageResult(
            document_id=str(row["document_id"]),
            page_index=int(row["page_index"]),
            confidence=(
                float(row["confidence"]) if row["confidence"] is not None else None
            ),
            text=str(row["text"] or ""),
            model_version=row["model_version"],
            evidence_url=row["evidence_url"],
            status=str(row["status"]),
            error=row["error"],
            updated_at=datetime.fromtimestamp(
                int(row["updated_ts"]), tz=SHANGHAI_TZ
            ),
        )

    def save_coverage_snapshot(self, snapshot: CoverageSnapshot) -> None:
        """Upsert one coverage snapshot (replaces the row with same ID)."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO coverage_snapshots (
                    snapshot_id, snapshot_ts, statuses_json, manifest_count,
                    policy_document_count, ocr_pending_count, provisional, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    snapshot_ts=excluded.snapshot_ts,
                    statuses_json=excluded.statuses_json,
                    manifest_count=excluded.manifest_count,
                    policy_document_count=excluded.policy_document_count,
                    ocr_pending_count=excluded.ocr_pending_count,
                    provisional=excluded.provisional,
                    error=excluded.error
                """,
                (
                    snapshot.snapshot_id,
                    int(snapshot.snapshot_ts.timestamp()),
                    json.dumps(snapshot.statuses, ensure_ascii=False),
                    snapshot.manifest_count,
                    snapshot.policy_document_count,
                    snapshot.ocr_pending_count,
                    1 if snapshot.provisional else 0,
                    snapshot.error,
                ),
            )

    def get_latest_coverage_snapshot(self) -> CoverageSnapshot | None:
        """Return the newest coverage snapshot, or ``None`` when absent."""

        rows = self._fetchall(
            "SELECT * FROM coverage_snapshots "
            "ORDER BY snapshot_ts DESC, snapshot_id DESC LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        return CoverageSnapshot(
            snapshot_id=str(row["snapshot_id"]),
            snapshot_ts=datetime.fromtimestamp(
                int(row["snapshot_ts"]), tz=SHANGHAI_TZ
            ),
            statuses={
                str(key): str(value)
                for key, value in json.loads(row["statuses_json"] or "{}").items()
            },
            manifest_count=int(row["manifest_count"]),
            policy_document_count=int(row["policy_document_count"]),
            ocr_pending_count=int(row["ocr_pending_count"]),
            provisional=bool(row["provisional"]),
            error=row["error"],
        )
