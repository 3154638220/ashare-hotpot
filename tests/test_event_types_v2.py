"""v2 六类新增事件的检测与门控（plan.md 第三部分，十六类事件契约）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.extraction import RuleBasedSignalExtractor, event_type_hint
from ashare_hotpot.models import EventCluster, SourceDocument
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=SHANGHAI_TZ)


def _parsed(
    document_id: str,
    title: str,
    body: str,
    *,
    codes: tuple[str, ...] = ("000001",),
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="announcement",
        source_url=f"https://example.test/{document_id}",
        document_url=None,
        title=title,
        published_at=NOW - timedelta(hours=1),
        stock_codes=codes,
        body_text=body,
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def _extract(documents: tuple[SourceDocument, ...]):
    storage = Storage.__new__(Storage)  # noqa: SLF001
    storage.upsert_evidence_ref = lambda _evidence: None  # type: ignore[attr-defined]
    storage.get_event_cluster = lambda _event_id: None  # type: ignore[attr-defined]
    storage.get_source_documents_between = lambda _a, _b: []  # type: ignore[attr-defined]
    extractor = RuleBasedSignalExtractor(storage)
    cluster = EventCluster(
        event_id="event-1",
        stock_codes=("000001",),
        canonical_title=documents[0].title,
        first_seen_at=NOW - timedelta(hours=2),
        last_seen_at=NOW - timedelta(hours=1),
        representative_document_id=documents[0].document_id,
        document_ids=[documents[0].document_id],
        historical_similar_event_id=None,
    )
    return extractor.extract_for_stock(cluster, documents, "000001")


def test_shareholder_return_cash_dividend_positive() -> None:
    doc = _parsed(
        "doc-div",
        "关于2026年半年度现金分红方案的公告",
        "公司拟向全体股东每10股派发现金红利5元，现金分红总额2亿元，"
        "占2026年上半年归母净利润的30%，方案已获董事会审议通过。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "shareholder_return"
    assert extraction.direction == "positive"
    assert extraction.materiality_level >= 2
    assert extraction.certainty_stage in ("signed", "executed")
    assert any(
        metric.get("name") == "现金分红/注销金额" for metric in extraction.metrics
    )


def test_shareholder_return_excludes_pure_stock_dividend() -> None:
    doc = _parsed(
        "doc-stock-div",
        "2026年半年度资本公积金转增股本预案公告",
        "公司拟以资本公积金向全体股东每10股转增4股，不派发现金红利。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "unsupported_event_type"


def test_shareholder_return_cancelled_is_negative() -> None:
    doc = _parsed(
        "doc-div-cancel",
        "关于终止实施2026年半年度现金分红方案的公告",
        "公司决定终止实施本次现金分红方案。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "shareholder_return"
    assert extraction.direction == "negative"
    assert extraction.no_valid_signal is True


def test_rd_milestone_positive_and_approval_stays_approval() -> None:
    doc = _parsed(
        "doc-rd",
        "关于创新药临床试验达到主要终点的公告",
        "公司创新药III期临床试验达到主要临床终点，统计学显著，"
        "公司将推进后续注册申报。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "rd_milestone"
    assert extraction.direction == "positive"
    assert extraction.certainty_stage == "awarded"

    approval = _parsed(
        "doc-approval",
        "关于获得药品注册证书的公告",
        "公司产品获得国家药品监督管理局核准签发的《药品注册证书》。",
    )
    approval_extraction = _extract((approval,))
    assert approval_extraction is not None
    assert approval_extraction.event_type == "approval"


def test_rd_milestone_missed_endpoint_is_negative() -> None:
    doc = _parsed(
        "doc-rd-miss",
        "关于创新药临床试验未达主要终点的公告",
        "公司创新药III期临床试验未达到主要临床终点。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "rd_milestone"
    assert extraction.no_valid_signal is True


def test_risk_resolution_positive() -> None:
    doc = _parsed(
        "doc-risk",
        "关于撤销公司股票交易退市风险警示的公告",
        "公司股票交易的退市风险警示自2026年8月10日起撤销，"
        "股票简称恢复正常。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "risk_resolution"
    assert extraction.direction == "positive"
    assert extraction.certainty_stage == "executed"


def test_risk_resolution_imposition_is_not_signal() -> None:
    doc = _parsed(
        "doc-risk-new",
        "关于公司股票交易被实施退市风险警示的公告",
        "公司股票交易自2026年8月10日起被实施退市风险警示。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "unsupported_event_type"


def test_equity_incentive_full_disclosure_catalyst_only() -> None:
    doc = _parsed(
        "doc-incentive",
        "2026年限制性股票激励计划（草案）公告",
        "本激励计划拟授予激励对象120人限制性股票500万股，占公司总股本的1%，"
        "业绩考核目标为2026-2028年营业收入分别不低于30亿元、36亿元、43亿元。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "equity_incentive"
    assert extraction.direction == "positive"
    assert extraction.certainty == 0.45  # 方案阶段：只进入潜在催化
    assert any(metric.get("name") == "授予规模" for metric in extraction.metrics)


def test_equity_incentive_missing_target_is_rejected() -> None:
    doc = _parsed(
        "doc-incentive-missing",
        "2026年限制性股票激励计划（草案）公告",
        "本激励计划拟授予激励对象120人限制性股票500万股，占公司总股本的1%。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "equity_incentive"
    assert extraction.no_valid_signal is True
    assert extraction.positive_mechanism is None


def test_financing_completion_positive_with_quantified_use() -> None:
    doc = _parsed(
        "doc-fin",
        "关于向特定对象发行股票发行完成暨募集资金到位的公告",
        "公司向特定对象发行股票已完成，募集资金总额10亿元，扣除发行费用后"
        "募集资金净额9.5亿元，将全部用于新能源汽车电驱系统生产基地建设项目，"
        "项目总投资8亿元。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "financing_completion"
    assert extraction.direction == "positive"
    assert extraction.certainty_stage == "executed"
    assert any(
        metric.get("name") == "资金用途量化" for metric in extraction.metrics
    )


def test_financing_plan_only_is_not_signal() -> None:
    doc = _parsed(
        "doc-fin-plan",
        "关于向特定对象发行股票预案的公告",
        "公司拟向特定对象发行股票，募集资金总额不超过10亿元。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "unsupported_event_type"


def test_asset_disposal_positive_with_one_off_marker() -> None:
    doc = _parsed(
        "doc-disposal",
        "关于出售全资子公司100%股权的公告",
        "本次交易已完成交割，交易金额3亿元，预计增加投资收益5000万元，"
        "该收益属一次性、非经常性损益。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "asset_disposal"
    assert extraction.direction == "positive"
    assert extraction.certainty_stage == "executed"
    assert any(metric.get("name") == "一次性属性" for metric in extraction.metrics)
    assert any(
        item.get("kind") == "partial" for item in extraction.counter_evidence
    )


def test_asset_disposal_without_status_is_rejected() -> None:
    doc = _parsed(
        "doc-disposal-gate",
        "关于拟出售资产的公告",
        "公司拟出售部分资产，相关事项尚在筹划中。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "asset_disposal"
    assert extraction.no_valid_signal is True


def test_event_type_hint_recognizes_new_types() -> None:
    assert (
        event_type_hint(
            "关于2026年半年度现金分红方案的公告\n公司拟现金分红2亿元"
        )
        == "shareholder_return"
    )
    assert (
        event_type_hint(
            "关于创新药临床试验达到主要终点的公告\n临床试验达到主要终点"
        )
        == "rd_milestone"
    )
    assert (
        event_type_hint(
            "关于撤销公司股票交易退市风险警示的公告\n退市风险警示撤销"
        )
        == "risk_resolution"
    )


def test_financing_hshare_filing_is_framework_not_completed() -> None:
    """v2 检测覆盖（绿联科技回归）：境外上市备案是审批阶段，不是融资完成；
    识别为 financing_completion 但按门控落在框架阶段（无量化资金用途 → 不入榜）。"""

    doc = _parsed(
        "doc-hshare",
        "关于发行境外上市股份（H股）获得中国证监会备案的公告",
        "公司正在进行申请发行境外上市股份（H股）并在香港联交所主板挂牌上市的"
        "相关工作。公司于近日收到中国证监会出具的《境外发行上市备案通知书》。"
        "公司拟发行不超过84,202,100股境外上市普通股。本次发行并上市尚需取得"
        "香港有关监管机构批准，仍存在不确定性。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "financing_completion"
    assert extraction.certainty_stage == "framework"
    assert extraction.materiality_level == 0
    assert extraction.no_valid_signal is True


def test_shareholder_return_cancellation_of_repurchased_shares() -> None:
    """注销回购股份并减少注册资本是“已回购股份注销”的股东回报口径
    （plan v2 数据契约），必须进入 shareholder_return 候选。"""

    from ashare_hotpot.extraction import detect_all_facts

    doc = _parsed(
        "doc-cancel",
        "关于注销回购股份并减少注册资本暨通知债权人的公告",
        "公司已于近日在中国证券登记结算有限责任公司办理完成本次注销回购股份"
        "的手续，本次注销回购股份12,000,000股，占注销前总股本的1.2%。",
    )
    facts = detect_all_facts((doc,))
    assert any(fact.event_type == "shareholder_return" for fact in facts)
