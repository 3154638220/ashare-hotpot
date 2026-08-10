from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.institution_metrics import (
    ResearchBoardService,
    industry_percentiles,
    persistence_components,
    persistence_score,
    sort_persistence,
    sort_z20,
    structural_comparison,
    trading_buckets,
    z20_from_counts,
    _ActivityData,
)
from ashare_hotpot.models import (
    Institution,
    PersistenceRow,
    ResearchActivity,
    ResearchParticipant,
    SourceDocument,
    SyncCursor,
    Z20Row,
)
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)
END = NOW.date()


def _weekdays(end: date, n: int) -> list[date]:
    days: list[date] = []
    day = end
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return list(reversed(days))


def _seed_calendar(storage: Storage, end: date, n: int, *, source: str = "sse") -> list[date]:
    days = _weekdays(end, n)
    storage.replace_trading_days(end.year, days, source=source, updated_at=NOW)
    return days


def _seed_research_coverage(
    storage: Storage,
    days: list[date],
    *,
    kinds: tuple[str, ...] = ("announcement", "research_activity"),
) -> None:
    """Seed sync cursors so research coverage spans ``days``."""

    from ashare_hotpot.config import RESEARCH_SOURCES

    for config in RESEARCH_SOURCES:
        if config.kind not in kinds:
            continue
        storage.save_sync_state(
            SyncCursor(
                source_key=config.key,
                sync_kind=config.kind,
                cursor={"page": 1, "covered_end": days[-1].isoformat()},
                target_start=days[0],
                covered_start=days[0],
                last_success_at=NOW,
                last_error=None,
                updated_at=NOW,
            )
        )


def _institution(
    storage: Storage, institution_id: str, group_id: str, name: str, itype: str
) -> None:
    storage.upsert_institution(
        Institution(institution_id, name, group_id, itype, "normalized"), NOW
    )


def _activity(
    storage: Storage,
    *,
    activity_id: str,
    stock: str,
    dates: tuple[date, ...],
    institution_ids: tuple[str, ...] = (),
    analysts: tuple[str, ...] = (),
    questions: int = 0,
    high: int = 0,
    depth: dict[str, int] | None = None,
    topics: dict[str, int] | None = None,
    reported: int | None = None,
) -> None:
    storage.upsert_source_document(
        SourceDocument(
            document_id=f"doc-{activity_id}",
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="research_activity",
            source_url=f"https://example.test/list?{activity_id}",
            document_url=None,
            title="投资者关系活动记录表",
            published_at=NOW,
            stock_codes=(stock,),
            body_text="",
            content_hash=f"hash-{activity_id}",
            parse_status="metadata_only",
            parse_error=None,
        ),
        NOW,
    )
    storage.upsert_research_activity(
        ResearchActivity(
            activity_id=activity_id,
            stock_code=stock,
            source_document_id=f"doc-{activity_id}",
            activity_dates=dates,
            activity_type="survey",
            reported_participant_count=reported,
            named_participant_count=len(institution_ids),
            question_count=questions,
            high_depth_question_count=high,
            topic_counts=topics or {},
            depth_counts=depth or {},
            date_precision="explicit",
        ),
        NOW,
    )
    for index, institution_id in enumerate(institution_ids):
        storage.add_research_participant(
            ResearchParticipant(
                activity_id=activity_id,
                institution_id=institution_id,
                analyst_name=analysts[index] if index < len(analysts) else None,
                evidence_id=f"ev-{activity_id}-{index}",
            )
        )


def _activity_data(
    activity_id: str,
    stock: str,
    dates: tuple[date, ...],
    groups: tuple[str, ...],
    *,
    analysts: tuple[str, ...] = (),
    questions: int = 0,
    high: int = 0,
    depth: dict[str, int] | None = None,
    topics: dict[str, int] | None = None,
    type_counts: dict[str, int] | None = None,
) -> _ActivityData:
    return _ActivityData(
        activity_id=activity_id,
        stock_code=stock,
        activity_dates=dates,
        end_date=max(dates),
        groups=frozenset(groups),
        analysts=frozenset(analysts),
        question_count=questions,
        high_depth_question_count=high,
        depth_counts=depth or {},
        topics=topics or {},
        type_counts=type_counts or {},
    )


def test_trading_buckets_split_120_days_into_six() -> None:
    days = _weekdays(END, 120)
    buckets = trading_buckets(days)
    assert len(buckets) == 6
    assert all(
        days.index(bucket[1]) - days.index(bucket[0]) + 1 == 20
        for bucket in buckets
    )
    assert buckets[-1] == (days[-20], days[-1])
    assert buckets[0] == (days[0], days[19])
    for (_, end_a), (start_b, _) in zip(buckets, buckets[1:]):
        assert end_a < start_b


def test_z20_formula_and_zero_std_fallback() -> None:
    assert z20_from_counts(8, [4, 4, 4, 4, 4]) == 4.0
    # 零标准差退化为除以 1，保留原始差值。
    assert z20_from_counts(8, [8, 8, 8, 8, 8]) == 0.0
    assert z20_from_counts(10, [8, 8, 8, 8, 8]) == 2.0
    # 基线存在波动时使用标准差。
    value = z20_from_counts(8, [4, 6, 5, 7, 3])
    mean = 5.0
    std = ((1 + 1 + 0 + 4 + 4) / 5) ** 0.5
    assert value == round((8 - mean) / max(std, 1.0), 3)


def test_persistence_components_and_single_day_constraint() -> None:
    days = _weekdays(END, 120)
    # 单日大活动：10 家机构同一天。
    one_day = _activity_data(
        "act-1",
        "000001",
        (days[-1],),
        tuple(f"group-{i}" for i in range(10)),
    )
    weeks, awr, rfr, depth, concentration, groups, recent = persistence_components(
        [one_day], days
    )
    assert concentration == 1.0
    assert rfr == 0.0
    assert depth == 0.0
    assert weeks == 1
    low_score = persistence_score(awr, rfr, depth, concentration)

    # 同一批机构分散在 4 个不同日期的活动：集中度显著下降。
    spread = [
        _activity_data(
            f"act-{i}",
            "000001",
            (day,),
            tuple(f"group-{i}" for i in range(10)),
        )
        for i, day in enumerate((days[-1], days[-22], days[-43], days[-64]))
    ]
    _, awr2, rfr2, depth2, concentration2, groups2, _ = persistence_components(
        spread, days
    )
    assert concentration2 < concentration
    spread_score = persistence_score(awr2, rfr2, depth2, concentration2)
    assert spread_score > low_score


def test_repeat_followup_and_depth_score() -> None:
    days = _weekdays(END, 120)
    activities = [
        _activity_data(
            "act-a",
            "000001",
            (days[-1],),
            ("g-common", "g-a"),
            questions=2,
            high=1,
            depth={"low": 1, "medium": 0, "high": 1},
            topics={"customers": 1},
        ),
        _activity_data(
            "act-b",
            "000001",
            (days[-22],),
            ("g-common", "g-b"),
            questions=2,
            high=0,
            depth={"low": 2, "medium": 0, "high": 0},
        ),
    ]
    weeks, awr, rfr, depth, _, groups, recent = persistence_components(
        activities, days
    )
    assert groups == 3
    # g-common 出现在两个不同活动日 → 重复跟进 1/3。
    assert rfr == 1 / 3
    # depth = (0.25*3 + 1.0*1)/4 = 0.4375
    assert depth == round(0.4375, 4)
    assert weeks >= 2
    assert recent == days[-1]


def test_industry_percentiles_degrade_below_five_stocks() -> None:
    industries = {"a": "X", "b": "X", "c": "X", "d": "X", "e": "X", "f": "Y", "g": "Y"}
    counts = {"a": 3, "b": 5, "c": 1, "d": 2, "e": 4, "f": 3, "g": 3}
    percentiles, sample_sizes = industry_percentiles(counts, industries)
    assert sample_sizes["a"] == 5
    # X 行业 5 只股票：c(1) 分位最低、b(5) 最高。
    assert percentiles["c"] < percentiles["a"] < percentiles["e"] < percentiles["b"]
    assert percentiles["b"] == 90.0
    # Y 行业仅 2 只 → 样本不足降级为空。
    assert percentiles["f"] is None
    assert sample_sizes["f"] == 2


def test_sort_z20_full_and_cold_start_orders() -> None:
    full_rows = [
        Z20Row(
            stock_code="000001", industry="X", z20=1.0, current_unique_groups=4,
            new_groups=1, analyst_count=0, high_depth_ratio=0.2, question_count=5,
            recent_activity=date(2026, 8, 1), industry_percentile=50.0,
            industry_sample_size=6, provisional=False,
        ),
        Z20Row(
            stock_code="000002", industry="X", z20=2.0, current_unique_groups=3,
            new_groups=2, analyst_count=0, high_depth_ratio=0.1, question_count=5,
            recent_activity=date(2026, 8, 2), industry_percentile=70.0,
            industry_sample_size=6, provisional=False,
        ),
    ]
    ordered = sort_z20(full_rows)
    assert [row.stock_code for row in ordered] == ["000002", "000001"]

    cold_rows = [
        Z20Row(
            stock_code="000001", industry=None, z20=None, current_unique_groups=2,
            new_groups=2, analyst_count=0, high_depth_ratio=0.0, question_count=0,
            recent_activity=date(2026, 8, 6), industry_percentile=None,
            industry_sample_size=0, provisional=True,
        ),
        Z20Row(
            stock_code="000002", industry=None, z20=None, current_unique_groups=5,
            new_groups=1, analyst_count=0, high_depth_ratio=0.0, question_count=0,
            recent_activity=date(2026, 8, 6), industry_percentile=None,
            industry_sample_size=0, provisional=True,
        ),
    ]
    ordered_cold = sort_z20(cold_rows)
    assert [row.stock_code for row in ordered_cold] == ["000002", "000001"]


def test_sort_persistence_uses_plan_components() -> None:
    rows = [
        PersistenceRow(
            stock_code="000001", window_kind="persistence_120", persistence_score=60.0,
            active_weeks=10, active_week_ratio=0.5, unique_groups=8,
            repeat_followup_ratio=0.4, depth_score=0.6, single_day_concentration=0.3,
            topics={}, recent_activity=date(2026, 8, 6), covered_trading_days=120,
        ),
        PersistenceRow(
            stock_code="000002", window_kind="persistence_120", persistence_score=60.0,
            active_weeks=12, active_week_ratio=0.6, unique_groups=6,
            repeat_followup_ratio=0.4, depth_score=0.6, single_day_concentration=0.3,
            topics={}, recent_activity=date(2026, 8, 6), covered_trading_days=120,
        ),
        PersistenceRow(
            stock_code="000003", window_kind="persistence_120", persistence_score=70.0,
            active_weeks=8, active_week_ratio=0.4, unique_groups=9,
            repeat_followup_ratio=0.4, depth_score=0.6, single_day_concentration=0.3,
            topics={}, recent_activity=date(2026, 8, 6), covered_trading_days=120,
        ),
    ]
    ordered = sort_persistence(rows)
    assert [row.stock_code for row in ordered] == ["000003", "000002", "000001"]


def test_structural_comparison_new_lost_groups_and_type_change() -> None:
    prior_days = _weekdays(END - timedelta(days=120), 60)
    recent_days = _weekdays(END, 60)
    prior = _activity_data(
        "prior",
        "000001",
        (prior_days[-1],),
        ("g-a", "g-b"),
        type_counts={"brokerage": 2},
        questions=2,
        high=0,
    )
    recent = _activity_data(
        "recent",
        "000001",
        (recent_days[-1],),
        ("g-b", "g-c"),
        type_counts={"public_fund": 2},
        questions=2,
        high=2,
    )
    comparison = structural_comparison(
        "000001", [prior, recent], prior_days, recent_days
    )
    assert comparison.new_groups == ("g-c",)
    assert comparison.lost_groups == ("g-a",)
    assert comparison.type_share_changes["brokerage"] == -1.0
    assert comparison.type_share_changes["public_fund"] == 1.0
    assert comparison.high_depth_ratio_change == 1.0


def _document(body: str, document_id: str, code: str = "300999") -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="research_activity",
        source_url=f"https://example.test/list?{document_id}",
        document_url=None,
        title="XX科技投资者关系活动记录表",
        published_at=NOW,
        stock_codes=(code,),
        body_text=body,
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def test_service_cold_start_provisional_and_persistence(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    days = _seed_calendar(storage, END, 60)
    _institution(storage, "inst-a", "group-a", "机构甲", "public_fund")
    _institution(storage, "inst-b", "group-b", "机构乙", "brokerage")
    _institution(storage, "inst-c", "group-c", "机构丙", "insurance")
    _activity(
        storage,
        activity_id="act-cur",
        stock="000001",
        dates=(days[-1],),
        institution_ids=("inst-a", "inst-b"),
        analysts=("分析师1",),
        questions=4,
        high=2,
        depth={"low": 1, "medium": 1, "high": 2},
        topics={"customers": 2},
    )
    _activity(
        storage,
        activity_id="act-prev",
        stock="000001",
        dates=(days[0],),
        institution_ids=("inst-c",),
    )

    settings = AppSettings(app_root=tmp_path)
    result = ResearchBoardService(settings, storage).run(now=NOW)

    # 冷启动：日历不足 120 个交易日 → z20 为空且暂定。
    assert len(result.z20_rows) == 1
    row = result.z20_rows[0]
    assert row.stock_code == "000001"
    assert row.z20 is None
    assert row.provisional is True
    assert row.current_unique_groups == 2
    assert row.new_groups == 2
    assert row.analyst_count == 1
    assert row.high_depth_ratio == 0.5
    assert row.industry_percentile is None

    assert len(result.persistence_60_rows) == 1
    # 日历不足 120 日时持续关注仍按可用交易日计算，并如实标注覆盖天数。
    assert len(result.persistence_120_rows) == 1
    assert result.persistence_120_rows[0].covered_trading_days == 60
    assert result.persistence_120_rows[0].provisional is True
    assert result.errors == ()
    assert result.coverage.provisional is True
    assert result.coverage.sources_total == len(settings.research_sources)
    assert result.coverage.sources_scanned == 0

    snapshots = storage.get_institution_metric_snapshots("000001", "z20")
    assert snapshots and snapshots[0][1]["provisional"] is True


def test_service_full_coverage_z20_and_repost_not_amplified(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    days = _seed_calendar(storage, END, 120)
    _seed_research_coverage(storage, days)
    _institution(storage, "inst-a", "group-a", "机构甲", "public_fund")
    _institution(storage, "inst-b", "group-b", "机构乙", "brokerage")
    _institution(storage, "inst-c", "group-c", "机构丙", "insurance")

    # 前五个桶每桶 3 家（同一批），当前桶 8 家（新增 5 家）。
    for index, start in enumerate(range(0, 100, 20)):
        bucket_day = days[start + 10]
        _activity(
            storage,
            activity_id=f"act-base-{index}",
            stock="000001",
            dates=(bucket_day,),
            institution_ids=("inst-a", "inst-b", "inst-c"),
        )
    for index, code in enumerate(("inst-a", "inst-b", "inst-c")):
        _institution(storage, f"inst-new-{index}", f"group-new-{index}", f"新机构{index}", "other")
    new_ids = ("inst-a", "inst-b", "inst-c", "inst-new-0", "inst-new-1", "inst-new-2")
    _activity(
        storage,
        activity_id="act-current",
        stock="000001",
        dates=(days[-1],),
        institution_ids=new_ids,
    )
    # 同一活动的转载文档：同股票、同日期、同机构，不应放大广度。
    _activity(
        storage,
        activity_id="act-current-repost",
        stock="000001",
        dates=(days[-1],),
        institution_ids=new_ids,
    )

    settings = AppSettings(app_root=tmp_path)
    result = ResearchBoardService(settings, storage).run(now=NOW)

    assert len(result.z20_rows) == 1
    row = result.z20_rows[0]
    assert row.provisional is False
    assert row.current_unique_groups == 6
    assert row.new_groups == 3
    # mean=3, std=0 → z20 = (6-3)/1 = 3.0
    assert row.z20 == 3.0

    assert len(result.persistence_120_rows) == 1
    persistence_row = result.persistence_120_rows[0]
    # 转载活动同日期同机构：group-day 对去重，集中度不受转载影响。
    assert persistence_row.unique_groups == 6
    assert persistence_row.provisional is False
    assert len(result.comparisons) == 1


def test_service_z20_cold_start_when_research_coverage_is_short(tmp_path) -> None:
    """Full calendar alone must not produce a z-score: research-activity
    documents must cover the 120-trading-day baseline too."""

    storage = Storage(tmp_path / "hotpot.db")
    days = _seed_calendar(storage, END, 120)
    # 调研文档只覆盖最近 20 个交易日 → 前五个基线桶无数据。
    _seed_research_coverage(storage, days[-20:])
    _institution(storage, "inst-a", "group-a", "机构甲", "public_fund")
    _activity(
        storage,
        activity_id="act-cur",
        stock="000001",
        dates=(days[-1],),
        institution_ids=("inst-a",),
    )

    settings = AppSettings(app_root=tmp_path)
    result = ResearchBoardService(settings, storage).run(now=NOW)

    assert len(result.z20_rows) == 1
    row = result.z20_rows[0]
    assert row.z20 is None
    assert row.provisional is True
    assert row.current_unique_groups == 1
    # 覆盖不足：持续关注同样标记暂定，而不是照常输出貌似完整的分数。
    assert result.persistence_60_rows[0].provisional is True
    assert result.persistence_120_rows[0].provisional is True


def test_service_zero_institution_activity_is_provisional(tmp_path) -> None:
    """An activity with no recognized institutions must not masquerade as a
    valid institution-attention signal."""

    storage = Storage(tmp_path / "hotpot.db")
    days = _seed_calendar(storage, END, 120)
    _seed_research_coverage(storage, days)
    _activity(
        storage,
        activity_id="act-no-inst",
        stock="000001",
        dates=(days[-1],),
        questions=2,
        high=1,
        depth={"low": 1, "high": 1},
        topics={"customers": 2},
    )

    settings = AppSettings(app_root=tmp_path)
    result = ResearchBoardService(settings, storage).run(now=NOW)

    assert len(result.z20_rows) == 1
    assert result.z20_rows[0].provisional is True
    assert result.z20_rows[0].current_unique_groups == 0
    assert result.persistence_60_rows[0].provisional is True
    assert result.persistence_60_rows[0].unique_groups == 0


def test_service_parses_documents_and_persists_activities(tmp_path) -> None:
    from pathlib import Path

    storage = Storage(tmp_path / "hotpot.db")
    _seed_calendar(storage, END, 120)
    fixture = (
        Path(__file__).parent / "fixtures" / "research_activity_record.txt"
    ).read_text(encoding="utf-8")
    storage.upsert_source_document(_document(fixture, "doc-parsed"), NOW)

    settings = AppSettings(app_root=tmp_path)
    result = ResearchBoardService(settings, storage).run(now=NOW)

    assert result.documents_scanned == 1
    assert result.activities_persisted == 1
    assert result.participants_added == 6
    assert result.institutions_created == 0  # 全部命中种子实体
    activities = storage.get_research_activities_between(END - timedelta(days=10), END)
    assert len(activities) == 1
    activity = storage.get_research_activity(activities[0].activity_id)
    assert activity is not None
    assert activity.question_count == 5
    assert activity.named_participant_count == 6
    assert len(storage.get_research_participants(activity.activity_id)) == 6
    # v2：结构化披露总数落库（列名机构数/披露总数分列）。
    reported = storage.get_reported_participant_count(activity.activity_id)
    assert reported is not None
    assert reported.named_research_count == 6
    assert reported.reported_institution_count == 30  # fixture: 约30家机构
    # v2：参与者原始提及按解析版本原子持久化。
    mentions = storage.get_participant_mentions(activity.activity_id)
    assert len(mentions) == 6
    assert all(m.parse_version == "v2-20260809" for m in mentions)
    assert all(m.review_status == "pending_review" for m in mentions)
    assert len(result.z20_rows) == 1
    assert result.z20_rows[0].stock_code == "300999"


def test_service_550_day_recompute_window(tmp_path) -> None:
    """v2 机构活动基线：backfill_days=550 覆盖 550 天前的活动记录。"""

    storage = Storage(tmp_path / "hotpot.db")
    _seed_calendar(storage, END, 120)
    old_doc = _document(
        (Path(__file__).parent / "fixtures" / "research_activity_record.txt")
        .read_text(encoding="utf-8"),
        "doc-old-550d",
    )
    # 构造 550 天前的活动文档。
    old_published = NOW - timedelta(days=550)
    old_doc = SourceDocument(
        document_id=old_doc.document_id,
        provider_key=old_doc.provider_key,
        provider_name=old_doc.provider_name,
        kind=old_doc.kind,
        source_url=old_doc.source_url,
        document_url=old_doc.document_url,
        title=old_doc.title,
        published_at=old_published,
        stock_codes=old_doc.stock_codes,
        body_text=old_doc.body_text,
        content_hash=old_doc.content_hash,
        parse_status=old_doc.parse_status,
        parse_error=old_doc.parse_error,
    )
    storage.upsert_source_document(old_doc, NOW)
    settings = AppSettings(app_root=tmp_path)

    # 默认 200 天窗口不覆盖 550 天前文档。
    short = ResearchBoardService(settings, storage).run(now=NOW)
    assert short.documents_scanned == 0

    # 550 天基线重算窗口覆盖该文档并按新解析版本落库。
    full = ResearchBoardService(settings, storage).run(
        now=NOW, backfill_days=550
    )
    assert full.documents_scanned == 1
    assert full.activities_persisted == 1
    activities = storage.get_research_activities_between(
        (NOW - timedelta(days=551)).date(), END
    )
    assert len(activities) == 1
    mentions = storage.get_participant_mentions(activities[0].activity_id)
    assert len(mentions) == 6


def test_service_failed_recompute_keeps_previous_batch(tmp_path, monkeypatch) -> None:
    """重算失败时保留上一批已发布指标；修复后重跑才推进批次标记。"""

    storage = Storage(tmp_path / "hotpot.db")
    _seed_calendar(storage, END, 120)
    fixture = (
        Path(__file__).parent / "fixtures" / "research_activity_record.txt"
    ).read_text(encoding="utf-8")
    storage.upsert_source_document(_document(fixture, "doc-good"), NOW)
    settings = AppSettings(app_root=tmp_path)

    first = ResearchBoardService(settings, storage).run(now=NOW)
    assert first.activities_persisted == 1
    first_snapshots = storage.get_latest_institution_metric_snapshots("z20")
    assert first_snapshots

    # 新增一份解析失败的文档，重跑应保留上一批已发布指标。
    storage.upsert_source_document(_document(fixture, "doc-broken"), NOW)

    def failing_parse(*_args, **_kwargs):
        raise RuntimeError("解析器故障")

    monkeypatch.setattr(
        "ashare_hotpot.institution_metrics.parse_research_activity",
        failing_parse,
    )
    failed = ResearchBoardService(settings, storage).run(now=NOW)
    assert failed.activities_persisted == 0
    assert failed.errors
    # 批次标记未推进：上一批指标仍可见，失败的新活动未落库。
    assert storage.get_latest_institution_metric_snapshots("z20") == (
        first_snapshots
    )

    # 修复后重跑成功，批次标记推进。
    monkeypatch.undo()
    retry = ResearchBoardService(settings, storage).run(now=NOW)
    assert retry.activities_persisted == 2
    assert storage.get_latest_institution_metric_snapshots("z20")


def test_service_coverage_uses_sync_states_and_calendar_flag(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    _seed_calendar(storage, END, 60, source="fallback")
    storage.save_sync_state(
        SyncCursor(
            source_key="cninfo_announcement",
            sync_kind="announcement",
            cursor={"page": 2},
            target_start=END - timedelta(days=200),
            covered_start=END - timedelta(days=150),
            last_success_at=NOW,
            last_error=None,
            updated_at=NOW,
        )
    )
    settings = AppSettings(app_root=tmp_path)
    result = ResearchBoardService(settings, storage).run(now=NOW)
    coverage = result.coverage
    assert coverage.sources_scanned == 1
    assert coverage.sources_total == 7
    assert coverage.calendar_fallback is True
    assert coverage.provisional is True
    assert coverage.last_success_at == NOW
    assert coverage.reached_cutoff is False


def test_service_empty_data_returns_empty_boards_without_error(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    _seed_calendar(storage, END, 120)
    storage.upsert_institution_metric_snapshot(
        stock_code="000001",
        window_kind="z20",
        metrics={"z20": 9.0},
        window_start=None,
        window_end=NOW - timedelta(days=1),
        snapshot_at=NOW - timedelta(days=1),
    )
    assert "000001" in storage.get_latest_institution_metric_snapshots("z20")
    settings = AppSettings(app_root=tmp_path)
    result = ResearchBoardService(settings, storage).run(now=NOW)
    assert result.z20_rows == ()
    assert result.persistence_60_rows == ()
    assert result.persistence_120_rows == ()
    assert result.errors == ()
    assert result.coverage.provisional is True
    assert storage.get_latest_institution_metric_snapshots("z20") == {}
