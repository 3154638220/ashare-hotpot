from __future__ import annotations

from datetime import datetime, timedelta

from ashare_hotpot.clustering import PersistentEventClusterer
from ashare_hotpot.config import AppSettings, SHANGHAI_TZ
from ashare_hotpot.extraction import RuleBasedSignalExtractor
from ashare_hotpot.models import (
    EventCluster,
    EventExtraction,
    EventSignal,
    SourceDocument,
)
from ashare_hotpot.signals import (
    ShortTermBoardService,
    SignalScorer,
    materiality_score,
    sort_signals,
)
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)


def _doc(
    document_id: str,
    title: str,
    body: str,
    *,
    codes: tuple[str, ...] = ("000001",),
    published_at: datetime | None = None,
    provider_key: str = "cninfo",
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key=provider_key,
        provider_name="巨潮资讯",
        kind="announcement",
        source_url=f"https://example.test/{document_id}",
        document_url=None,
        title=title,
        published_at=published_at or (NOW - timedelta(hours=6)),
        stock_codes=codes,
        body_text=body,
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def _cluster(*, last_seen: datetime | None = None, historical: str | None = None) -> EventCluster:
    return EventCluster(
        event_id="event-1",
        stock_codes=("000001",),
        canonical_title="标题",
        first_seen_at=last_seen or (NOW - timedelta(hours=8)),
        last_seen_at=last_seen or (NOW - timedelta(hours=6)),
        representative_document_id="doc-1",
        document_ids=["doc-1"],
        historical_similar_event_id=historical,
    )


def _extraction(
    *,
    materiality: int = 3,
    certainty: float = 0.9,
    unexpectedness: float = 75.0,
    novelty: float = 100.0,
    counter: tuple[dict[str, object], ...] = (),
    no_valid_signal: bool = False,
    mechanism: str | None = "合同增厚收入",
) -> EventExtraction:
    return EventExtraction(
        event_id="event-1",
        stock_code="000001",
        event_type="major_contract",
        direction="positive",
        positive_mechanism=mechanism,
        metrics=(),
        certainty_stage="signed",
        certainty=certainty,
        novelty=novelty,
        unexpectedness=unexpectedness,
        materiality_level=materiality,
        counter_evidence=counter,
        evidence_ids=("ev-1",),
        no_valid_signal=no_valid_signal,
        extractor_kind="rules",
        extractor_version="rules-v1",
    )


def _representative() -> SourceDocument:
    return _doc("doc-1", "标题", "正文")


def test_score_formula_exact() -> None:
    extraction = _extraction(
        materiality=3, certainty=0.9, unexpectedness=75.0, novelty=100.0
    )
    window_start = NOW - timedelta(hours=24)
    cluster = _cluster(last_seen=NOW - timedelta(hours=12))
    decision = SignalScorer().decide(
        extraction,
        cluster,
        representative=_representative(),
        now=NOW,
        window_start=window_start,
        window_end=NOW,
    )
    signal = decision.signal
    assert signal is not None
    # T at 12h/24h = 50; C = min(1.0, 0.9) = 0.9
    # S = 0.9*(0.35*75 + 0.25*75 + 0.20*100 + 0.20*50) = 0.9*(26.25+18.75+20+10)
    assert signal.score == 0.9 * (26.25 + 18.75 + 20.0 + 10.0)
    assert signal.timeliness == 50.0
    assert signal.source_confidence == 1.00
    assert signal.board == "confirmed_positive"


def test_timeliness_decay_and_clamping() -> None:
    scorer = SignalScorer()
    window_start = NOW - timedelta(hours=24)
    assert scorer._timeliness(NOW, window_start, NOW) == 100.0
    assert scorer._timeliness(NOW - timedelta(hours=12), window_start, NOW) == 50.0
    assert scorer._timeliness(NOW - timedelta(hours=24), window_start, NOW) == 0.0
    assert scorer._timeliness(NOW - timedelta(hours=30), window_start, NOW) == 0.0
    assert scorer._timeliness(NOW + timedelta(hours=1), window_start, NOW) == 100.0


def test_penalties_additive_capped_at_80() -> None:
    scorer = SignalScorer()
    cluster = _cluster(historical="event-old")
    extraction = _extraction(
        materiality=0,
        certainty=0.9,
        unexpectedness=25.0,
        novelty=30.0,
        counter=({"kind": "partial", "reason": "x", "evidence_id": "ev-1"},),
    )
    # 15 (partial) + 20 (已预告) + 20 (低于1级) + 40 (旧闻) = 95 -> capped 80
    assert scorer._penalty(extraction, cluster) == 80.0

    extraction2 = _extraction(
        materiality=2,
        certainty=0.9,
        counter=(
            {"kind": "partial", "reason": "x", "evidence_id": "ev-1"},
            {"kind": "high_uncertainty", "reason": "y", "evidence_id": "ev-2"},
            {"kind": "title_body_conflict", "reason": "z", "evidence_id": "ev-3"},
        ),
    )
    # title_body_conflict 在评分前即被拒绝，不进入惩罚累加；15+35=50
    assert scorer._penalty(extraction2, cluster) == 50.0


def test_confirmed_positive_gates() -> None:
    scorer = SignalScorer()
    window_start = NOW - timedelta(hours=24)
    cluster = _cluster(last_seen=NOW - timedelta(hours=6))
    # M=1 fails confirmed gate -> catalyst (M>=1, certainty 0.9, score?)
    low_materiality = _extraction(materiality=1)
    decision = scorer.decide(
        low_materiality,
        cluster,
        representative=_representative(),
        now=NOW,
        window_start=window_start,
        window_end=NOW,
    )
    assert decision.signal is not None
    assert decision.signal.board == "potential_catalyst"
    assert decision.signal.provisional is True

    # certainty below 0.70 with M=2 -> catalyst (0.45 >= 0.40, U/N=100, T=100)
    low_certainty = _extraction(
        materiality=2,
        certainty=0.45,
        unexpectedness=100.0,
        novelty=100.0,
    )
    decision = scorer.decide(
        low_certainty,
        _cluster(last_seen=NOW),
        representative=_representative(),
        now=NOW,
        window_start=window_start,
        window_end=NOW,
    )
    assert decision.signal is not None
    assert decision.signal.board == "potential_catalyst"

    # certainty below 0.40 -> rejected
    too_low = _extraction(materiality=2, certainty=0.20)
    decision = scorer.decide(
        too_low,
        cluster,
        representative=_representative(),
        now=NOW,
        window_start=window_start,
        window_end=NOW,
    )
    assert decision.signal is None
    assert "确定性不足0.40" in (decision.rejection_reason or "")


def test_high_uncertainty_blocks_confirmed_but_allows_catalyst() -> None:
    scorer = SignalScorer()
    cluster = _cluster(last_seen=NOW - timedelta(hours=6))
    extraction = _extraction(
        materiality=3,
        certainty=0.9,
        counter=({"kind": "high_uncertainty", "reason": "x", "evidence_id": "ev-1"},),
    )
    decision = scorer.decide(
        extraction,
        cluster,
        representative=_representative(),
        now=NOW,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
    )
    # T=75; S = 0.9*(26.25+18.75+20+15) - 35 = 72 - 35 = 37 >= 35 -> catalyst
    assert decision.signal is not None
    assert decision.signal.board == "potential_catalyst"


def test_no_valid_signal_rejected_and_reason_explained() -> None:
    scorer = SignalScorer()
    extraction = _extraction(no_valid_signal=True, mechanism=None)
    decision = scorer.decide(
        extraction,
        _cluster(),
        representative=_representative(),
        now=NOW,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
    )
    assert decision.signal is None
    assert decision.rejection_reason == "无有效正向机制"
    assert scorer.explain_rejection(extraction) == "无有效正向机制"


def test_materiality_score_mapping() -> None:
    assert [materiality_score(level) for level in range(5)] == [0, 25, 50, 75, 100]


def test_sort_signals_uses_plan_tie_breakers() -> None:
    clusters = {
        "e1": _cluster(last_seen=NOW - timedelta(hours=1)),
        "e2": _cluster(last_seen=NOW - timedelta(hours=2)),
        "e3": _cluster(last_seen=NOW - timedelta(hours=3)),
    }
    signals = [
        EventSignal(
            event_id="e2", stock_code="000001", board="confirmed_positive",
            score=80.0, source_confidence=1.0, materiality_level=2,
            certainty=0.9, unexpectedness=50.0, novelty=100.0,
            timeliness=50.0, penalty=0.0, provisional=False,
        ),
        EventSignal(
            event_id="e1", stock_code="000001", board="confirmed_positive",
            score=80.0, source_confidence=1.0, materiality_level=3,
            certainty=0.9, unexpectedness=50.0, novelty=100.0,
            timeliness=50.0, penalty=0.0, provisional=False,
        ),
        EventSignal(
            event_id="e3", stock_code="600519", board="potential_catalyst",
            score=80.0, source_confidence=1.0, materiality_level=3,
            certainty=0.9, unexpectedness=50.0, novelty=100.0,
            timeliness=50.0, penalty=0.0, provisional=True,
        ),
    ]
    ordered = sort_signals(signals, clusters)
    assert [item.event_id for item in ordered] == ["e1", "e3", "e2"]


def test_full_pipeline_generates_boards_and_dedupes_multi_source(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    body = (
        "公司近日与客户签订重大合同，合同金额1.2亿元，"
        "占公司最近一个会计年度营业收入的20%，合同已签署生效。"
    )
    official = _doc(
        "doc-1",
        "公司签订重大合同公告",
        body,
        published_at=NOW - timedelta(hours=2),
    )
    media = _doc(
        "doc-2",
        "公司签订重大合同公告",
        body,
        provider_key="ths",
        published_at=NOW - timedelta(hours=2),
    )
    for document in (official, media):
        storage.upsert_source_document(document, NOW)

    settings = AppSettings(app_root=tmp_path)
    service = ShortTermBoardService(settings, storage)
    result = service.run(
        now=NOW,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
    )

    # 一件事多来源只计一次：两篇文档合成一个事件，只产生一个信号。
    assert result.clusters_created == 1
    assert result.clusters_merged == 1
    assert result.clusters_processed == 1
    assert result.extractions_persisted == 1
    assert result.signals_confirmed == 1
    assert result.signals_catalyst == 0
    signals = storage.get_event_signals("confirmed_positive")
    assert len(signals) == 1
    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=24), NOW + timedelta(hours=1)
    )
    extraction = storage.get_event_extraction(clusters[0].event_id, "000001")
    assert extraction is not None
    assert extraction.extractor_kind == "rules"
    assert extraction.no_valid_signal is False
    assert extraction.event_id == signals[0].event_id
    # v2 多事实管线：候选事实与逐门控决策轨迹落库。
    claims = storage.get_event_claims_by_stock("000001")
    assert claims
    claim = claims[0]
    assert claim.event_type == "major_contract"
    assert claim.document_id == "doc-1"
    assert claim.review_status == "pending_review"
    gates = {item["gate"] for item in claim.gate_trace}
    assert gates == {
        "mechanism",
        "title_body_conflict",
        "materiality",
        "certainty",
        "score",
    }
    score_gate = next(
        item for item in claim.gate_trace if item["gate"] == "score"
    )
    assert score_gate["passed"] is True

    # 下一次完整计算窗口内已无活动事件时，当前榜应原子替换为空，
    # 不能继续展示上一轮的短期信号。
    later = NOW + timedelta(days=2)
    expired = service.run(
        now=later,
        window_start=later - timedelta(hours=24),
        window_end=later,
    )
    assert expired.completed is True
    assert expired.clusters_processed == 0
    assert storage.get_event_signals() == []


def test_full_pipeline_multi_fact_document_keeps_alternate_claim(
    tmp_path,
) -> None:
    """v2 多事实：一个文档的两个候选事实只产生一条信号，未入榜事实留明细。"""

    storage = Storage(tmp_path / "hotpot.db")
    doc = _doc(
        "doc-multi",
        "关于收购控股子公司股权暨药品获注册批准的公告",
        "公司拟收购控股子公司剩余股权，交易金额5亿元，占上年营业收入20%。"
        "同时，公司控股子公司产品获得国家药品监督管理局核准签发的"
        "《药品注册证书》，可开展相关商业化活动。",
        published_at=NOW - timedelta(hours=2),
    )
    storage.upsert_source_document(doc, NOW)

    service = ShortTermBoardService(AppSettings(app_root=tmp_path), storage)
    result = service.run(
        now=NOW,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
    )
    assert result.signals_confirmed + result.signals_catalyst == 1
    signals = storage.get_event_signals()
    assert len(signals) == 1
    extraction = storage.get_event_extraction(signals[0].event_id, "000001")
    assert extraction is not None
    assert extraction.event_type == "mna"

    claims = storage.get_event_claims_by_stock("000001")
    claim_types = {claim.event_type for claim in claims}
    assert claim_types == {"mna", "approval"}
    alternate = next(
        claim for claim in claims if claim.event_type == "approval"
    )
    assert any(
        gate.get("gate") == "board_selection" and not gate.get("passed")
        for gate in alternate.gate_trace
    )


def test_full_pipeline_ambiguity_review_marks_verified_keeps_rules(
    tmp_path, monkeypatch
) -> None:
    """v2 里程碑 5：低置信边界候选事实经 AI 复核（一致→verified），
    榜单仍由规则结果决定。"""

    from ashare_hotpot.ambiguity_review import (
        REVIEW_STATUS_AGREE,
        ReviewOutcome,
    )

    storage = Storage(tmp_path / "hotpot.db")
    doc = _doc(
        "doc-review",
        "关于拟签订重大合同的公告",
        "公司拟与客户签订重大合同，合同金额1.2亿元，占上年营业收入10%。",
        published_at=NOW - timedelta(hours=2),
    )
    storage.upsert_source_document(doc, NOW)

    class _AgreeReviewer:
        def review(self, claim, document):
            return ReviewOutcome(
                status=REVIEW_STATUS_AGREE, rationale="规则结论可信"
            )

    monkeypatch.setattr(
        "ashare_hotpot.signals.build_ambiguity_reviewer",
        lambda settings: _AgreeReviewer(),
    )
    # 本测试只验证接线：强制该候选事实进入复核（低置信边界判定见单测）。
    monkeypatch.setattr(
        "ashare_hotpot.signals.should_review_claim", lambda claim: True
    )
    service = ShortTermBoardService(
        AppSettings(app_root=tmp_path, ai_enabled=True), storage
    )
    result = service.run(
        now=NOW,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
    )
    # 规则榜单照常发布（潜在催化：框架阶段确定性 0.45）。
    assert result.signals_confirmed + result.signals_catalyst >= 1
    claims = storage.get_event_claims_by_stock("000001")
    reviewed = [
        claim for claim in claims if claim.event_type == "major_contract"
    ]
    assert reviewed
    assert reviewed[0].review_status == "verified"
    assert any(
        gate.get("gate") == "ai_review" and gate.get("passed")
        for gate in reviewed[0].gate_trace
    )


def _seed_signal_cluster(
    storage: Storage,
    *,
    event_id: str,
    title: str,
    stock_code: str,
    event_type: str,
    metrics: tuple[dict[str, object], ...] = (),
    score: float = 60.0,
    board: str = "confirmed_positive",
    materiality: int = 2,
    certainty: float = 0.9,
) -> None:
    document_id = f"doc-{event_id}"
    storage.upsert_source_document(
        SourceDocument(
            document_id=document_id,
            provider_key="cninfo",
            provider_name="巨潮资讯",
            kind="announcement",
            source_url=f"https://example.test/{document_id}",
            document_url=None,
            title=title,
            published_at=NOW - timedelta(hours=2),
            stock_codes=(stock_code,),
            body_text="",
            content_hash=f"hash-{event_id}",
            parse_status="parsed",
            parse_error=None,
        ),
        NOW,
    )
    cluster = EventCluster(
        event_id=event_id,
        stock_codes=(stock_code,),
        canonical_title=title,
        first_seen_at=NOW - timedelta(hours=2),
        last_seen_at=NOW - timedelta(hours=1),
        representative_document_id=document_id,
        document_ids=[document_id],
        historical_similar_event_id=None,
    )
    storage.upsert_event_cluster(cluster)
    storage.upsert_event_extraction(
        EventExtraction(
            event_id=event_id,
            stock_code=stock_code,
            event_type=event_type,
            direction="positive",
            positive_mechanism="正向机制",
            metrics=metrics,
            certainty_stage="executed",
            certainty=certainty,
            novelty=1.0,
            unexpectedness=0.5,
            materiality_level=materiality,
            counter_evidence=(),
            evidence_ids=(),
            no_valid_signal=False,
            extractor_kind="rules",
            extractor_version="rules-v1",
        ),
        NOW,
    )
    storage.upsert_event_signal(
        EventSignal(
            event_id=event_id,
            stock_code=stock_code,
            board=board,
            score=score,
            source_confidence=1.0,
            materiality_level=materiality,
            certainty=certainty,
            unexpectedness=0.5,
            novelty=1.0,
            timeliness=0.5,
            penalty=0.0,
            provisional=board != "confirmed_positive",
        ),
        created_at=NOW,
    )


def test_board_dedupe_identical_title_approval_collapses(tmp_path) -> None:
    """同股票同标题批文族（600196 模式）：榜单只保留分数最高的一行。"""

    storage = Storage(tmp_path / "hotpot.db")
    _seed_signal_cluster(
        storage,
        event_id="evt-a",
        title="复星医药关于控股子公司药品获注册批准的公告",
        stock_code="600196",
        event_type="approval",
        score=55.0,
        board="potential_catalyst",
        materiality=1,
    )
    _seed_signal_cluster(
        storage,
        event_id="evt-b",
        title="复星医药关于控股子公司药品获注册批准的公告",
        stock_code="600196",
        event_type="approval",
        score=41.0,
        board="potential_catalyst",
        materiality=1,
    )
    service = ShortTermBoardService(AppSettings(app_root=tmp_path), storage)
    signals = service._dedupe_board_families(
        list(storage.get_event_signals()), NOW
    )
    assert len(signals) == 1
    assert signals[0].event_id == "evt-a"  # 分数更高者保留


def test_board_dedupe_report_and_summary_collapses(tmp_path) -> None:
    """半年报+摘要（001389/600581/603001 模式）榜单只保留一行。"""

    storage = Storage(tmp_path / "hotpot.db")
    _seed_signal_cluster(
        storage,
        event_id="evt-report",
        title="2026年半年度报告",
        stock_code="001389",
        event_type="earnings_upgrade",
        metrics=(
            {
                "name": "归母净利润同比变动",
                "value": 94.39,
                "unit": "%",
                "comparison_basis": "上年同期",
                "comparison_ratio": 0.9439,
                "evidence_id": "ev-1",
            },
        ),
        score=67.0,
        board="potential_catalyst",
        materiality=1,
    )
    _seed_signal_cluster(
        storage,
        event_id="evt-summary",
        title="2026年半年度报告摘要",
        stock_code="001389",
        event_type="earnings_upgrade",
        score=46.0,
        board="potential_catalyst",
        materiality=1,
    )
    service = ShortTermBoardService(AppSettings(app_root=tmp_path), storage)
    signals = service._dedupe_board_families(
        list(storage.get_event_signals()), NOW
    )
    assert len(signals) == 1
    assert signals[0].event_id == "evt-report"


def test_board_dedupe_buyback_family_collapses(tmp_path) -> None:
    """同次回购文件族（688381 模式）榜单只保留一行。"""

    storage = Storage(tmp_path / "hotpot.db")
    _seed_signal_cluster(
        storage,
        event_id="evt-plan",
        title="关于以集中竞价交易方式回购股份方案的公告",
        stock_code="688381",
        event_type="buyback_or_increase",
        metrics=(
            {
                "name": "回购/增持金额",
                "value": 3000.0,
                "unit": "万元",
                "comparison_basis": None,
                "comparison_ratio": None,
                "evidence_id": "ev-1",
            },
        ),
        score=61.5,
        board="potential_catalyst",
        materiality=1,
    )
    _seed_signal_cluster(
        storage,
        event_id="evt-opinion",
        title="中信建投证券股份有限公司关于江苏帝奥微电子股份有限公司"
        "使用部分超募资金回购股份的核查意见",
        stock_code="688381",
        event_type="buyback_or_increase",
        metrics=(
            {
                "name": "回购/增持金额",
                "value": 6305.0,
                "unit": "万元",
                "comparison_basis": None,
                "comparison_ratio": None,
                "evidence_id": "ev-2",
            },
        ),
        score=47.5,
        board="potential_catalyst",
        materiality=1,
    )
    service = ShortTermBoardService(AppSettings(app_root=tmp_path), storage)
    signals = service._dedupe_board_families(
        list(storage.get_event_signals()), NOW
    )
    assert len(signals) == 1
    assert signals[0].event_id == "evt-plan"


def test_board_dedupe_conflicting_amounts_stay_separate(tmp_path) -> None:
    """同标题但关键金额冲突的重大合同（plan.md 9.2）不得被榜单折叠。"""

    storage = Storage(tmp_path / "hotpot.db")
    _seed_signal_cluster(
        storage,
        event_id="evt-contract-1",
        title="公司签订重大合同公告",
        stock_code="000001",
        event_type="major_contract",
        metrics=(
            {
                "name": "合同金额",
                "value": 1.2,
                "unit": "亿元",
                "comparison_basis": "最近一个会计年度营业收入",
                "comparison_ratio": 0.10,
                "evidence_id": "ev-1",
            },
        ),
        score=75.0,
    )
    _seed_signal_cluster(
        storage,
        event_id="evt-contract-2",
        title="公司签订重大合同公告",
        stock_code="000001",
        event_type="major_contract",
        metrics=(
            {
                "name": "合同金额",
                "value": 3.5,
                "unit": "亿元",
                "comparison_basis": "最近一个会计年度营业收入",
                "comparison_ratio": 0.25,
                "evidence_id": "ev-2",
            },
        ),
        score=88.0,
    )
    service = ShortTermBoardService(AppSettings(app_root=tmp_path), storage)
    signals = service._dedupe_board_families(
        list(storage.get_event_signals()), NOW
    )
    assert len(signals) == 2


def test_board_dedupe_different_event_types_stay_separate(tmp_path) -> None:
    """同股票不同事件类型（同标题巧合）不折叠。"""

    storage = Storage(tmp_path / "hotpot.db")
    _seed_signal_cluster(
        storage,
        event_id="evt-a",
        title="关于签署战略合作协议的公告",
        stock_code="000001",
        event_type="major_contract",
        score=75.0,
    )
    _seed_signal_cluster(
        storage,
        event_id="evt-b",
        title="关于签署战略合作协议的公告",
        stock_code="000001",
        event_type="customer_breakthrough",
        score=70.0,
        materiality=2,
    )
    service = ShortTermBoardService(AppSettings(app_root=tmp_path), storage)
    signals = service._dedupe_board_families(
        list(storage.get_event_signals()), NOW
    )
    assert len(signals) == 2


def test_rejected_events_persist_no_valid_signal_without_signal_row(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    doc = _doc(
        "doc-1",
        "公司2026年半年度业绩预亏公告",
        "公司预计2026年上半年归母净利润亏损5亿元，同比下降。",
    )
    storage.upsert_source_document(doc, NOW)
    settings = AppSettings(app_root=tmp_path)
    result = ShortTermBoardService(settings, storage).run(
        now=NOW,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
    )
    assert result.signals_confirmed == 0
    assert result.signals_catalyst == 0
    assert result.rejected >= 1
    assert storage.get_event_signals() == []
    clusters = storage.get_event_clusters_active(
        NOW - timedelta(hours=24), NOW + timedelta(hours=1)
    )
    extraction = storage.get_event_extraction(clusters[0].event_id, "000001")
    assert extraction is not None
    assert extraction.no_valid_signal is True


def test_ai_disabled_uses_rules_extractor(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    settings = AppSettings(app_root=tmp_path, ai_enabled=False)
    service = ShortTermBoardService(settings, storage)
    extractor = service._default_extractor()
    assert isinstance(extractor, RuleBasedSignalExtractor)


# ---------------------------------------------------------------------------
# Message-window matching for date-granularity announcements (plan.md 10.5)
# ---------------------------------------------------------------------------

_ANNOUNCEMENT_DAY = datetime(2026, 8, 8, tzinfo=SHANGHAI_TZ)

_EARNINGS_BODY = (
    "2026年半年度报告显示，本期归属于上市公司股东的净利润较上年同期增加"
    "127,282.95万元，同比增长122.61%，本期营业收入同比增长108.13%。"
)


def test_date_only_announcement_captured_when_window_starts_after_midnight(
    tmp_path,
) -> None:
    """A cninfo announcement dated by day (00:00) stays inside an hour-based
    message window whose start is after midnight (the 寒武纪 case)."""

    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(
        _doc(
            "doc-h1",
            "2026年半年度报告",
            _EARNINGS_BODY,
            published_at=_ANNOUNCEMENT_DAY.replace(hour=0, minute=0),
        ),
        _ANNOUNCEMENT_DAY,
    )
    window_start = _ANNOUNCEMENT_DAY.replace(hour=0, minute=18)
    window_end = _ANNOUNCEMENT_DAY.replace(hour=18, minute=18)

    service = ShortTermBoardService(AppSettings(app_root=tmp_path), storage)
    result = service.run(
        now=window_end,
        window_start=window_start,
        window_end=window_end,
    )

    assert result.completed is True
    assert result.clusters_processed == 1
    assert result.signals_confirmed == 1
    signals = storage.get_event_signals("confirmed_positive")
    assert len(signals) == 1
    assert signals[0].stock_code == "000001"
    assert signals[0].score >= 60.0
    active = storage.get_event_clusters_active(window_start, window_end)
    assert [cluster.event_id for cluster in active] == [signals[0].event_id]


def test_precise_timestamp_before_window_start_stays_excluded(tmp_path) -> None:
    """Hour-granularity is preserved: a precise 00:10 disclosure before a
    00:18 window start is not captured."""

    storage = Storage(tmp_path / "hotpot.db")
    storage.upsert_source_document(
        _doc(
            "doc-precise",
            "2026年半年度报告",
            _EARNINGS_BODY,
            published_at=_ANNOUNCEMENT_DAY.replace(hour=0, minute=10),
        ),
        _ANNOUNCEMENT_DAY,
    )
    window_start = _ANNOUNCEMENT_DAY.replace(hour=0, minute=18)
    window_end = _ANNOUNCEMENT_DAY.replace(hour=18, minute=18)

    service = ShortTermBoardService(AppSettings(app_root=tmp_path), storage)
    result = service.run(
        now=window_end,
        window_start=window_start,
        window_end=window_end,
    )

    assert result.completed is True
    assert result.clusters_processed == 0
    assert storage.get_event_signals() == []
    assert (
        storage.get_event_clusters_active(window_start, window_end) == []
    )


def test_previous_day_date_only_announcement_excluded_for_single_day_window(
    tmp_path,
) -> None:
    """A date-only announcement from the previous day stays out when the
    window's date span does not cover it."""

    storage = Storage(tmp_path / "hotpot.db")
    previous_day = _ANNOUNCEMENT_DAY - timedelta(days=1)
    storage.upsert_source_document(
        _doc(
            "doc-prev",
            "2026年半年度报告",
            _EARNINGS_BODY,
            published_at=previous_day.replace(hour=0, minute=0),
        ),
        previous_day,
    )
    PersistentEventClusterer(storage).process_window(
        previous_day - timedelta(days=1),
        _ANNOUNCEMENT_DAY + timedelta(days=1),
        _ANNOUNCEMENT_DAY,
    )
    window_start = _ANNOUNCEMENT_DAY.replace(hour=0, minute=18)
    window_end = _ANNOUNCEMENT_DAY.replace(hour=18, minute=18)

    assert (
        storage.get_event_clusters_active(window_start, window_end) == []
    )


def test_window_spanning_two_dates_includes_both_date_only_days(tmp_path) -> None:
    """An hour window crossing midnight covers announcements from both
    disclosure days it spans."""

    storage = Storage(tmp_path / "hotpot.db")
    previous_day = _ANNOUNCEMENT_DAY - timedelta(days=1)
    contract_body = (
        "公司近日与客户签订重大合同，合同金额1.2亿元，"
        "占公司最近一个会计年度营业收入的20%，合同已签署生效。"
    )
    for document_id, published_at in (
        ("doc-prev", previous_day.replace(hour=0, minute=0)),
        ("doc-h1", _ANNOUNCEMENT_DAY.replace(hour=0, minute=0)),
    ):
        body = contract_body if document_id == "doc-prev" else _EARNINGS_BODY
        storage.upsert_source_document(
            _doc(
                document_id,
                f"{document_id} 2026年半年度报告",
                body,
                published_at=published_at,
            ),
            published_at,
        )
    PersistentEventClusterer(storage).process_window(
        previous_day - timedelta(days=1),
        _ANNOUNCEMENT_DAY + timedelta(days=1),
        _ANNOUNCEMENT_DAY,
    )
    window_start = previous_day.replace(hour=17, minute=0)
    window_end = _ANNOUNCEMENT_DAY.replace(hour=11, minute=0)

    active = storage.get_event_clusters_active(window_start, window_end)
    assert len(active) == 2
