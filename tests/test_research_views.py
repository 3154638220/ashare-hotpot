from __future__ import annotations

from datetime import date, datetime, timedelta

from ashare_hotpot import research_views as rv
from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.exporting import CSV_HEADERS, row_values, tab_separated_row
from ashare_hotpot.models import (
    DiscoveryCandidate,
    EventCluster,
    EventClaim,
    EventExtraction,
    EventSignal,
    EvidenceRef,
    Institution,
    InstitutionZ20ViewRow,
    ParsedArticle,
    PersistenceViewRow,
    ResearchActivity,
    ResearchCoverage,
    ResearchParticipant,
    ReportedParticipantCount,
    ShortTermViewRow,
    SourceDocument,
    StockMention,
    SyncCursor,
)
from ashare_hotpot.storage import Storage
from ashare_hotpot.ui import RESEARCH_HEADERS


NOW = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)


def make_storage(tmp_path) -> Storage:
    storage = Storage(tmp_path / "hotpot.db")
    storage.initialize()
    return storage


def seed_article(storage: Storage, code: str = "000001", name: str = "平安银行") -> None:
    storage.upsert_article(
        ParsedArticle(
            f"seq-{code}",
            f"https://example.test/{code}",
            f"{name}公开公告",
            "",
            NOW,
            "companynews",
            "公司资讯",
            "同花顺",
            (StockMention(code, name),),
        ),
        NOW,
    )


def seed_signal(
    storage: Storage,
    *,
    board: str = "confirmed_positive",
    extractor_kind: str = "rules",
    provisional: bool = False,
    event_id: str = "event-1",
    stock_code: str = "000001",
    certainty: float = 0.9,
    materiality: int = 2,
    score: float = 80.0,
    event_type: str = "major_contract",
    mechanism: str = "新增合同预计增厚营业收入",
    stock_names: dict[str, str] | None = None,
) -> None:
    document = SourceDocument(
        "doc-1",
        "cninfo",
        "巨潮资讯",
        "announcement",
        "https://cninfo.example.test/1",
        None,
        "公司签订重大合同公告",
        NOW - timedelta(hours=2),
        (stock_code,),
        "公司近日与客户签订重大合同，合同金额1.2亿元，占营业收入的10%。",
        "hash-1",
        "parsed",
        None,
        None,
        stock_names=stock_names or {},
    )
    storage.upsert_source_document(document, NOW)
    storage.upsert_event_cluster(
        EventCluster(
            event_id,
            (stock_code,),
            "公司签订重大合同公告",
            NOW - timedelta(hours=3),
            NOW - timedelta(hours=2),
            "doc-1",
            ["doc-1"],
            None,
        )
    )
    storage.link_event_document(event_id, "doc-1")
    extraction = EventExtraction(
        event_id,
        stock_code,
        event_type,
        "positive",
        mechanism,
        (
            {
                "name": "合同金额",
                "value": 1.2,
                "unit": "亿元",
                "comparison_basis": "营业收入",
                "comparison_ratio": 0.1,
                "evidence_id": "ev-1",
            },
        ),
        "signed",
        certainty,
        100.0,
        50.0,
        materiality,
        (),
        ("ev-1",),
        False,
        extractor_kind,
        "rules-v1",
    )
    storage.upsert_event_extraction(extraction, NOW)
    storage.upsert_evidence_ref(
        EvidenceRef(
            "ev-1",
            "doc-1",
            0,
            40,
            "合同金额1.2亿元，占营业收入的10%。",
            "https://cninfo.example.test/1",
        )
    )
    storage.upsert_event_signal(
        EventSignal(
            event_id,
            stock_code,
            board,
            score,
            certainty,
            materiality,
            certainty,
            50.0,
            100.0,
            1.0,
            0.0,
            provisional,
        ),
        created_at=NOW,
    )


def seed_metric_snapshot(
    storage: Storage,
    stock_code: str,
    window_kind: str,
    metrics: dict,
) -> None:
    storage.upsert_institution_metric_snapshot(
        stock_code=stock_code,
        window_kind=window_kind,
        metrics=metrics,
        window_start=NOW - timedelta(days=120),
        window_end=NOW,
        snapshot_at=NOW,
    )


def seed_sync_state(storage: Storage) -> None:
    for source_key, sync_kind in (
        ("cninfo_announcement", "announcement"),
        ("cninfo_research", "research_activity"),
        ("sse_publish", "research_activity"),
    ):
        storage.save_sync_state(
            SyncCursor(
                source_key=source_key,
                sync_kind=sync_kind,
                cursor={"covered_end": "2026-08-06"},
                target_start=date(2026, 1, 19),
                covered_start=date(2026, 1, 19),
                last_success_at=NOW,
                last_error=None,
                updated_at=NOW,
            )
        )


def test_short_term_rows_compose_labels_names_and_metrics(tmp_path) -> None:
    storage = make_storage(tmp_path)
    seed_article(storage)
    seed_signal(storage)
    rows = rv.load_short_term_rows(storage, "confirmed_positive")
    assert len(rows) == 1
    row = rows[0]
    assert row.rank == 1
    assert row.stock_name == "平安银行"
    assert row.stock_code == "000001"
    assert row.event_type == "重大订单"
    assert row.positive_mechanism == "新增合同预计增厚营业收入"
    assert row.materiality_level == 2
    assert "1.2" in row.key_metric and "亿元" in row.key_metric
    assert row.certainty == 0.9
    assert row.extractor_label == "规则"
    # No coverage info was provided -> the row honestly shows cold start.
    assert row.quality_state == "cold_start"


def test_short_term_row_shows_research_name_without_news(tmp_path) -> None:
    """A research-only stock (no news article or Q&A record) must display the
    name captured from the research source instead of falling back to code."""

    storage = make_storage(tmp_path)
    seed_signal(
        storage,
        stock_code="300423",
        event_id="event-300423",
        stock_names={"300423": "昇辉科技"},
    )
    rows = rv.load_short_term_rows(storage, "confirmed_positive")
    assert len(rows) == 1
    assert rows[0].stock_code == "300423"
    assert rows[0].stock_name == "昇辉科技"


def test_short_term_catalyst_rows_keep_board_and_extractor_labels(tmp_path) -> None:
    storage = make_storage(tmp_path)
    seed_article(storage)
    seed_signal(storage, board="potential_catalyst", extractor_kind="rules_fallback", provisional=True)
    rows = rv.load_short_term_rows(storage, "potential_catalyst")
    assert len(rows) == 1
    assert rows[0].board == "potential_catalyst"
    assert rows[0].extractor_label == "规则降级"
    assert rows[0].quality_state == "provisional"
    assert rv.load_short_term_rows(storage, "confirmed_positive") == []


def test_short_term_rows_with_coverage_partial_state(tmp_path) -> None:
    storage = make_storage(tmp_path)
    seed_article(storage)
    seed_signal(storage)
    coverage = rv.research_coverage(AppSettings(app_root=tmp_path), storage, now=NOW)
    rows = rv.load_short_term_rows(
        storage, "confirmed_positive", coverage=coverage
    )
    # No sync cursors yet -> cold start/partial is visible on every row.
    assert rows[0].quality_state in {"cold_start", "partial"}


def test_coverage_state_is_partial_when_one_source_fails_with_usable_cache() -> None:
    coverage = ResearchCoverage(
        requested_start=date(2026, 1, 19),
        covered_start=date(2026, 8, 1),
        covered_end=NOW.date(),
        trading_days_covered=4,
        sources_scanned=2,
        sources_total=3,
        reached_cutoff=False,
        calendar_fallback=False,
        last_success_at=NOW,
        provisional=True,
        error="首屏空数据或结构变化",
    )

    assert rv.coverage_state(coverage, has_rows=True) == "partial"


def test_coverage_state_is_error_when_all_sources_fail_without_usable_data() -> None:
    coverage = ResearchCoverage(
        requested_start=date(2026, 1, 19),
        covered_start=None,
        covered_end=None,
        trading_days_covered=0,
        sources_scanned=0,
        sources_total=3,
        reached_cutoff=False,
        calendar_fallback=False,
        last_success_at=None,
        provisional=True,
        error="全部公告来源失败",
    )

    assert rv.coverage_state(coverage, has_rows=False) == "error"


def test_z20_rows_sort_full_before_cold_start_and_assign_ranks(tmp_path) -> None:
    storage = make_storage(tmp_path)
    seed_article(storage, code="000001", name="平安银行")
    seed_article(storage, code="600519", name="贵州茅台")
    seed_article(storage, code="300750", name="宁德时代")
    seed_metric_snapshot(
        storage,
        "600519",
        "z20",
        {"z20": 2.5, "current_unique_groups": 10, "new_groups": 3, "analyst_count": 4,
         "high_depth_ratio": 0.5, "question_count": 8, "recent_activity": "2026-08-05",
         "industry_percentile": 0.9, "industry_sample_size": 12, "provisional": False},
    )
    seed_metric_snapshot(
        storage,
        "000001",
        "z20",
        {"z20": None, "current_unique_groups": 8, "new_groups": 2, "analyst_count": 1,
         "high_depth_ratio": 0.3, "question_count": 4, "recent_activity": "2026-08-04",
         "industry_percentile": None, "industry_sample_size": 0, "provisional": True},
    )
    seed_metric_snapshot(
        storage,
        "300750",
        "z20",
        {"z20": 1.2, "current_unique_groups": 6, "new_groups": 1, "analyst_count": 2,
         "high_depth_ratio": 0.4, "question_count": 5, "recent_activity": "2026-08-06",
         "industry_percentile": 0.7, "industry_sample_size": 12, "provisional": False},
    )
    rows = rv.load_z20_rows(storage)
    assert [row.stock_code for row in rows] == ["600519", "300750", "000001"]
    assert [row.rank for row in rows] == [1, 2, 3]
    assert rows[0].z20 == 2.5
    assert rows[2].z20 is None
    assert rows[2].provisional is True
    assert rows[2].coverage_state == "provisional"
    assert rows[0].stock_name == "贵州茅台"


def test_persistence_rows_sorted_by_score_with_topics(tmp_path) -> None:
    storage = make_storage(tmp_path)
    seed_article(storage, code="000001", name="平安银行")
    seed_article(storage, code="600519", name="贵州茅台")
    seed_metric_snapshot(
        storage,
        "000001",
        "persistence_60",
        {"persistence_score": 66.0, "active_weeks": 6, "active_week_ratio": 0.5,
         "unique_groups": 9, "repeat_followup_ratio": 0.33, "depth_score": 0.6,
         "single_day_concentration": 0.25, "topics": {"orders": 3, "capacity": 2},
         "recent_activity": "2026-08-05", "covered_trading_days": 60},
    )
    seed_metric_snapshot(
        storage,
        "600519",
        "persistence_60",
        {"persistence_score": 80.0, "active_weeks": 8, "active_week_ratio": 0.7,
         "unique_groups": 12, "repeat_followup_ratio": 0.5, "depth_score": 0.7,
         "single_day_concentration": 0.2, "topics": {"growth": 4, "customers": 1},
         "recent_activity": "2026-08-06", "covered_trading_days": 60},
    )
    rows = rv.load_persistence_rows(storage, "persistence_60")
    assert [row.stock_code for row in rows] == ["600519", "000001"]
    assert rows[0].persistence_score == 80.0
    assert rows[1].topics == {"orders": 3, "capacity": 2}


def test_research_coverage_aggregates_sync_cursors(tmp_path) -> None:
    storage = make_storage(tmp_path)
    settings = AppSettings(app_root=tmp_path)
    cold = rv.research_coverage(settings, storage, now=NOW)
    assert cold.provisional is True
    assert cold.sources_scanned == 0
    assert cold.trading_days_covered == 0

    seed_sync_state(storage)
    covered = rv.research_coverage(settings, storage, now=NOW)
    assert covered.sources_scanned == 1
    assert covered.sources_total == 3
    assert covered.covered_start == date(2026, 1, 19)
    assert covered.last_success_at == NOW
    # 来源游标完整但交易日历仍为空时，必须继续显示冷启动/暂定，不能
    # 把无法计算 20/60/120 日窗口的状态伪装成完整覆盖。
    assert covered.provisional is True


def _seed_discovery_candidate(
    storage: Storage,
    *,
    document_id: str,
    title: str,
    status: str,
    discovery_type: str = "contract_order",
    code: str = "600390",
    document_url: str | None = "https://static.cninfo.com.cn/finalpage/x.PDF",
    source_key: str = "cninfo_announcement",
    published: datetime | None = None,
) -> DiscoveryCandidate:
    published = published or NOW
    document = SourceDocument(
        document_id,
        "cninfo",
        "巨潮资讯",
        "announcement",
        "https://cninfo.example.test/list",
        document_url,
        title,
        published,
        (code,),
        "",
        f"hash-{document_id}",
        "metadata_only",
        None,
        None,
        stock_names={code: "五矿资本"},
    )
    candidate = DiscoveryCandidate(
        document_id=document_id,
        source_key=source_key,
        source_name="巨潮资讯公告",
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        stock_codes=(code,),
        title=title,
        published_at=published,
        discovery_type=discovery_type,
        trigger_reason="标题含测试关键词",
        queue_status=status,
        attachment_type="PDF" if document_url else None,
        document_url=document_url,
        enqueued_at=published if status == "pending_attachment" else None,
        updated_at=NOW,
        signal_priority=True,
    )
    storage.save_research_batch(
        [document],
        [candidate],
        SyncCursor(
            source_key=source_key,
            sync_kind="announcement",
            cursor={"page": 1},
            target_start=NOW.date(),
            covered_start=NOW.date(),
            last_success_at=NOW,
            last_error=None,
            updated_at=NOW,
        ),
        NOW,
    )
    return candidate


def test_load_discovery_rows_orders_and_labels(tmp_path) -> None:
    storage = make_storage(tmp_path)
    _seed_discovery_candidate(
        storage,
        document_id="doc-failed",
        title="回购报告书",
        status="failed",
        discovery_type="capital_action",
    )
    _seed_discovery_candidate(
        storage,
        document_id="doc-pending",
        title="关于拟签订重大合同的公告",
        status="pending_attachment",
        published=NOW - timedelta(hours=1),
    )
    _seed_discovery_candidate(
        storage,
        document_id="doc-awaiting",
        title="2026年半年度报告摘要",
        status="awaiting_review",
        discovery_type="financial_report",
        document_url=None,
        code="688167",
    )

    rows = rv.load_discovery_rows(storage)

    assert [row.document_id for row in rows] == [
        "doc-pending",
        "doc-awaiting",
        "doc-failed",
    ]
    assert rows[0].parse_status_label == "待解析"
    assert rows[1].parse_status_label == "待核验"
    assert rows[2].parse_status_label == "解析失败"
    assert rows[0].discovery_type_label == "合同订单"
    assert rows[1].discovery_type_label == "财务报告"
    assert rows[0].stock_name == "五矿资本"
    assert all(row.quality_state for row in rows)


def test_load_discovery_rows_excludes_promoted_candidates(tmp_path) -> None:
    storage = make_storage(tmp_path)
    _seed_discovery_candidate(
        storage,
        document_id="doc-pending",
        title="关于拟签订重大合同的公告",
        status="pending_attachment",
    )
    _seed_discovery_candidate(
        storage,
        document_id="doc-promoted",
        title="回购报告书",
        status="awaiting_review",
        discovery_type="capital_action",
    )
    seed_signal(storage, event_id="event-promoted", event_type="buyback_or_increase")
    # 把 signal 挂到 doc-promoted 上。
    with storage._connect() as connection:
        connection.execute(
            "UPDATE event_cluster_documents SET document_id='doc-promoted' "
            "WHERE event_id='event-promoted'"
        )
    rows = rv.load_discovery_rows(storage)
    assert [row.document_id for row in rows] == ["doc-pending"]


def test_build_discovery_quality_per_source_stats(tmp_path) -> None:
    storage = make_storage(tmp_path)
    _seed_discovery_candidate(
        storage,
        document_id="doc-1",
        title="关于拟签订重大合同的公告",
        status="pending_attachment",
    )
    _seed_discovery_candidate(
        storage,
        document_id="doc-2",
        title="回购报告书",
        status="failed",
        discovery_type="capital_action",
    )
    settings = AppSettings(app_root=tmp_path)
    text = rv.build_discovery_quality(settings, storage)
    assert "已发现 2" in text
    assert "待解析 1" in text
    assert "失败 1" in text
    assert "最早待处理" in text
    assert "巨潮资讯公告" in text
    assert "北交所公司公告（官方接口返回空列表" not in text

    year_start = date(NOW.year, 1, 1)
    weekdays = [
        year_start + timedelta(days=offset)
        for offset in range((NOW.date() - year_start).days + 1)
        if (year_start + timedelta(days=offset)).weekday() < 5
    ]
    storage.replace_trading_days(
        NOW.year, weekdays, source="sse", updated_at=NOW
    )
    complete = rv.research_coverage(settings, storage, now=NOW)
    assert complete.provisional is False


def test_load_event_detail_includes_documents_and_evidence(tmp_path) -> None:
    storage = make_storage(tmp_path)
    seed_article(storage)
    seed_signal(storage)
    detail = rv.load_event_detail(storage, "event-1", "000001")
    assert detail is not None
    assert detail.stock_name == "平安银行"
    assert len(detail.documents) == 1
    assert detail.documents[0].document_id == "doc-1"
    assert "ev-1" in detail.evidence_by_id
    assert "1.2亿元" in detail.evidence_by_id["ev-1"].excerpt


def test_load_event_detail_includes_claims_with_review_status(tmp_path) -> None:
    """v2 里程碑 5：候选事实与复核状态随事件明细加载。"""

    storage = make_storage(tmp_path)
    seed_article(storage)
    seed_signal(storage)
    storage.upsert_event_claim(
        EventClaim(
            claim_id="claim:1",
            document_id="doc-1",
            stock_code="000001",
            event_type="major_contract",
            direction="positive",
            positive_mechanism="新增合同预计增厚营业收入",
            metrics=(),
            certainty_stage="signed_contract",
            certainty=0.9,
            materiality_level=2,
            counter_evidence=(),
            evidence_ids=(),
            rejection_reason=None,
            review_status="pending_review",
            gate_trace=(
                {
                    "gate": "ai_review",
                    "passed": False,
                    "reason": "规则与AI分歧：AI建议 event_type=mna",
                },
            ),
            extractor_kind="rules",
            extractor_version="rules-v1",
            created_at=NOW,
        )
    )
    detail = rv.load_event_detail(storage, "event-1", "000001")
    assert detail is not None
    assert detail.claims
    assert detail.claims[0].event_type == "major_contract"
    assert detail.claims[0].review_status == "pending_review"


def test_load_institution_detail_includes_participants_metrics_and_comparison(tmp_path) -> None:
    storage = make_storage(tmp_path)
    seed_article(storage, code="000001", name="平安银行")
    document = SourceDocument(
        "doc-r1",
        "cninfo",
        "巨潮资讯",
        "research_activity",
        "https://cninfo.example.test/r1",
        None,
        "投资者关系活动记录表",
        NOW - timedelta(days=2),
        ("000001",),
        "参与单位包括某公募基金与某券商。",
        "hash-r1",
        "parsed",
        None,
        None,
    )
    storage.upsert_source_document(document, NOW)
    storage.upsert_evidence_ref(
        EvidenceRef("ev-r1", "doc-r1", 0, 20, "参与单位包括某公募基金与某券商。", "https://cninfo.example.test/r1")
    )
    institution = Institution("inst-1", "示例公募基金", "group-1", "public_fund", "verified")
    storage.upsert_institution(institution, NOW)
    activity = ResearchActivity(
        activity_id="act-1",
        stock_code="000001",
        source_document_id="doc-r1",
        activity_dates=(date(2026, 8, 4),),
        activity_type="research",
        reported_participant_count=2,
        named_participant_count=1,
        question_count=5,
        high_depth_question_count=2,
        topic_counts={"orders": 3, "growth": 2},
        depth_counts={"high": 2, "medium": 3},
        date_precision="explicit",
    )
    storage.upsert_research_activity(activity, NOW)
    storage.add_research_participant(
        ResearchParticipant("act-1", "inst-1", "张三", "ev-r1")
    )
    storage.upsert_reported_participant_count(
        ReportedParticipantCount(
            activity_id="act-1",
            named_research_count=1,
            all_named_org_count=2,
            reported_institution_count=8,
            reported_person_count=12,
            evidence_id="ev-r1",
            updated_at=NOW,
        )
    )
    seed_metric_snapshot(
        storage,
        "000001",
        "persistence_120",
        {"persistence_score": 70.0, "active_weeks": 10, "active_week_ratio": 0.6,
         "unique_groups": 8, "repeat_followup_ratio": 0.4, "depth_score": 0.6,
         "single_day_concentration": 0.2, "topics": {"orders": 5},
         "recent_activity": "2026-08-04", "covered_trading_days": 120},
    )
    seed_metric_snapshot(
        storage,
        "000001",
        "persistence_120_detail",
        {"new_groups": ["group-2"], "lost_groups": [], "type_share_changes": {"public_fund": 0.1},
         "high_depth_ratio_change": 0.05, "active_week_ratio_change": 0.1,
         "single_day_concentration_change": -0.02},
    )
    coverage = rv.research_coverage(AppSettings(app_root=tmp_path), storage, now=NOW)
    detail = rv.load_institution_detail(
        storage,
        "000001",
        "平安银行",
        "persistence_120",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 8, 6),
        coverage=coverage,
    )
    assert detail.metrics["persistence_score"] == 70.0
    assert detail.comparison_metrics["new_groups"] == ["group-2"]
    assert len(detail.activities) == 1
    assert detail.participants_by_activity["act-1"][0].institution_id == "inst-1"
    assert detail.institutions_by_id["inst-1"].canonical_name == "示例公募基金"
    assert detail.documents_by_id["doc-r1"].title == "投资者关系活动记录表"
    assert "ev-r1" in detail.evidence_by_id
    reported = detail.reported_counts_by_activity["act-1"]
    assert reported.named_research_count == 1
    assert reported.all_named_org_count == 2
    assert reported.reported_institution_count == 8


def test_view_row_roundtrips() -> None:
    short = ShortTermViewRow(
        1, "000001", "平安银行", "银行", "重大订单", "新增合同", 2, "1.2 亿元",
        0.9, "尚未落地", None, "partial", "规则", True, "e1",
        "potential_catalyst", 45.0,
    )
    assert ShortTermViewRow.from_dict(short.to_dict()) == short
    z20 = InstitutionZ20ViewRow(
        1, "000001", "平安银行", "银行", 1.5, 10, 3, 4, 0.5, 6,
        date(2026, 8, 5), 0.8, 10, False, "ok",
    )
    assert InstitutionZ20ViewRow.from_dict(z20.to_dict()) == z20
    persist = PersistenceViewRow(
        1, "000001", "平安银行", "persistence_60", 66.0, 6, 0.5, 9, 0.33,
        0.6, 0.25, {"orders": 3}, date(2026, 8, 5), 60, "partial",
    )
    assert PersistenceViewRow.from_dict(persist.to_dict()) == persist


def test_get_stock_names_falls_back_to_code(tmp_path) -> None:
    storage = make_storage(tmp_path)
    seed_article(storage, code="000001", name="平安银行")
    names = storage.get_stock_names({"000001", "600999"})
    assert names["000001"] == "平安银行"
    assert names["600999"] == "600999"


def test_export_headers_match_table_columns_for_research_views() -> None:
    for source_key in ("confirm", "catalyst", "z20", "persist60", "persist120"):
        assert list(CSV_HEADERS[source_key]) == list(RESEARCH_HEADERS[source_key])


def test_export_research_rows_use_visible_columns() -> None:
    z20 = InstitutionZ20ViewRow(
        1, "000001", "平安银行", "银行", 1.5, 10, 3, 4, 0.5, 6,
        date(2026, 8, 5), 0.8, 10, False, "ok",
    )
    values = row_values("z20", z20)
    assert values[0] == 1
    assert values[1:4] == ("平安银行", "000001", "银行")
    assert values[4] == "1.50"
    assert values[10] == "正常"
    assert "密钥" not in "\t".join(str(value) for value in values)

    persist = PersistenceViewRow(
        1, "000001", "平安银行", "persistence_60", 66.0, 6, 0.5, 9, 0.33,
        0.6, 0.25, {"orders": 3, "capacity": 2}, date(2026, 8, 5), 60, "partial",
    )
    copied = tab_separated_row("persist60", persist)
    assert "订单 3" in copied and "产能 2" in copied
    assert copied.split("\t")[1:4] == ["平安银行", "000001", "60 日"]
