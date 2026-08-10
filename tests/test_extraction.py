from __future__ import annotations

from datetime import datetime, timedelta

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.extraction import (
    EXCERPT_MAX_CHARS,
    RuleBasedSignalExtractor,
    _direction,
    parse_amount,
    parse_percent,
)
from ashare_hotpot.models import EventCluster, SourceDocument
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 6, 20, 0, tzinfo=SHANGHAI_TZ)


def _parsed(
    document_id: str,
    title: str,
    body: str,
    *,
    codes: tuple[str, ...] = ("000001",),
    provider_key: str = "cninfo",
    provider_name: str = "巨潮资讯",
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key=provider_key,
        provider_name=provider_name,
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


def _cluster(document_id: str = "doc-1") -> EventCluster:
    return EventCluster(
        event_id="event-1",
        stock_codes=("000001",),
        canonical_title="标题",
        first_seen_at=NOW - timedelta(hours=2),
        last_seen_at=NOW - timedelta(hours=1),
        representative_document_id=document_id,
        document_ids=[document_id],
        historical_similar_event_id=None,
    )


def _extract(documents: tuple[SourceDocument, ...], cluster: EventCluster | None = None):
    storage = Storage.__new__(Storage)  # no storage IO in pure extraction tests

    storage.upsert_evidence_ref = lambda _evidence: None  # type: ignore[attr-defined]
    storage.get_event_cluster = lambda _event_id: None  # type: ignore[attr-defined]
    storage.get_source_documents_between = lambda _a, _b: []  # type: ignore[attr-defined]
    extractor = RuleBasedSignalExtractor(storage)
    return extractor.extract_for_stock(
        cluster or _cluster(), documents, "000001"
    )


def test_parse_amount_keeps_original_unit_and_value() -> None:
    assert parse_amount("合同金额1.2亿元") == (120000000.0, "亿元", 1.2)
    assert parse_amount("收到政府补助3000万元") == (30000000.0, "万元", 3000.0)
    assert parse_amount("金额5,000万元") == (50000000.0, "万元", 5000.0)
    assert parse_amount("未披露金额") is None


def test_parse_percent_distinguishes_percent_and_ratio() -> None:
    assert parse_percent("同比增长12%") == 0.12
    assert parse_percent("增长0.12个百分点") == 0.0012
    assert parse_percent("产量翻倍") is None


def test_earnings_upgrade_detection_with_ratio_and_amount() -> None:
    doc = _parsed(
        "doc-1",
        "平安银行2026年半年度业绩预告",
        "公司预计2026年上半年归母净利润为80亿元至90亿元，同比增长30%，"
        "上年同期为62亿元。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "earnings_upgrade"
    assert extraction.direction == "positive"
    assert extraction.positive_mechanism is not None
    names = {metric["name"] for metric in extraction.metrics}
    assert "归母净利润同比变动" in names
    ratio_metric = next(
        metric for metric in extraction.metrics if metric["name"] == "归母净利润同比变动"
    )
    assert ratio_metric["value"] == 30.0
    assert ratio_metric["unit"] == "%"
    assert ratio_metric["comparison_ratio"] == 0.30
    assert extraction.materiality_level == 4  # 30% >= 30%
    assert extraction.no_valid_signal is False


def test_earnings_periodic_report_detected_with_ratio_and_level_amount() -> None:
    doc = _parsed(
        "doc-periodic-1",
        "2026年半年度报告",
        "本期营业收入599,557.36万元，较上年同期增长108.13%。"
        "本期归属于上市公司股东的净利润较上年同期增加127,282.95万元，"
        "同比增长122.61%；归属于上市公司股东的净利润达到231,091.21万元，"
        "归属于上市公司股东的扣除非经常性损益的净利润达到216,555.58万元。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "earnings_upgrade"
    assert extraction.direction == "positive"
    assert extraction.no_valid_signal is False
    assert extraction.certainty_stage == "executed"
    assert extraction.certainty == 1.0
    assert extraction.materiality_level == 4
    ratio_metric = next(
        metric
        for metric in extraction.metrics
        if metric["name"] == "归母净利润同比变动"
    )
    assert ratio_metric["value"] == 122.61
    assert ratio_metric["unit"] == "%"
    assert abs(ratio_metric["comparison_ratio"] - 1.2261) < 1e-6
    amount_metric = next(
        metric for metric in extraction.metrics if metric["name"] == "净利润"
    )
    assert amount_metric["value"] == 231091.21
    assert amount_metric["unit"] == "万元"
    assert extraction.counter_evidence == ()


def test_earnings_annual_and_quarterly_reports_detected() -> None:
    for title, document_id in (
        ("2025年年度报告", "doc-annual-1"),
        ("2026年第一季度报告", "doc-quarter-1"),
    ):
        doc = _parsed(
            document_id,
            title,
            "报告期内公司实现归属于上市公司股东的净利润为85,000万元，"
            "较上年同期增长35.5%，归属于上市公司股东的净利润"
            "较上年同期增加22,260万元。",
        )
        extraction = _extract((doc,))
        assert extraction is not None
        assert extraction.event_type == "earnings_upgrade"
        assert extraction.no_valid_signal is False
        assert extraction.certainty_stage == "executed"
        assert extraction.materiality_level == 4
        ratio_metric = next(
            metric
            for metric in extraction.metrics
            if metric["name"] == "归母净利润同比变动"
        )
        assert ratio_metric["value"] == 35.5


def test_earnings_periodic_report_decline_is_rejected() -> None:
    doc = _parsed(
        "doc-periodic-3",
        "2026年半年度报告",
        "报告期内归属于上市公司股东的净利润为45,000万元，"
        "较上年同期下降40.2%。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "earnings_upgrade"
    assert extraction.no_valid_signal is True
    assert extraction.positive_mechanism is None
    assert extraction.materiality_level == 0


def test_earnings_periodic_report_inquiry_reply_is_not_an_event() -> None:
    doc = _parsed(
        "doc-periodic-4",
        "关于上海证券交易所2025年年度报告问询函的回复公告",
        "公司2025年年度报告显示归属于上市公司股东的净利润为85,000万元，"
        "较上年同期增长35.5%。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "unsupported_event_type"
    assert extraction.no_valid_signal is True


def test_major_contract_detection_with_revenue_ratio() -> None:
    doc = _parsed(
        "doc-2",
        "公司签订重大合同公告",
        "公司近日与客户签订重大合同，合同金额1.2亿元，"
        "占公司最近一个会计年度营业收入的10%。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "major_contract"
    amount_metric = next(metric for metric in extraction.metrics if metric["name"] == "合同金额")
    assert amount_metric["value"] == 1.2
    assert amount_metric["unit"] == "亿元"
    assert amount_metric["comparison_ratio"] == 0.10
    assert extraction.materiality_level == 2
    assert extraction.certainty_stage == "signed"
    assert extraction.certainty == 0.90


def test_price_increase_detection() -> None:
    doc = _parsed(
        "doc-3",
        "公司产品价格上调公告",
        "公司宣布产品价格上调，涨幅10%，自2026年9月1日起执行。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "price_increase"
    assert extraction.direction == "positive"
    assert extraction.materiality_level == 2


def test_approval_detection_executed() -> None:
    doc = _parsed(
        "doc-4",
        "公司产品获批上市公告",
        "公司产品获得国家药品监督管理局批准上市，已取得注册证。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "approval"
    assert extraction.certainty_stage == "executed"
    assert extraction.certainty == 1.00


def test_intellectual_property_certificate_is_not_regulatory_approval() -> None:
    doc = _parsed(
        "doc-patent",
        "关于公司及子公司取得发明专利证书的公告",
        "公司及子公司近日取得三项发明专利证书，有利于完善知识产权保护体系。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "unsupported_event_type"
    assert extraction.no_valid_signal is True


def test_buyback_detection_with_high_uncertainty() -> None:
    doc = _parsed(
        "doc-5",
        "公司回购股份方案公告",
        "公司拟以不低于10亿元不超过20亿元回购公司股份，回购价格上限25元/股，"
        "占公司总股本的2%。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "buyback_or_increase"
    assert extraction.materiality_level == 3  # 2% >= 1%
    kinds = {item["kind"] for item in extraction.counter_evidence}
    assert "high_uncertainty" in kinds


def test_buyback_report_boilerplate_is_not_counter_evidence() -> None:
    """Approved buyback reports: risk boilerplate must not block the signal."""

    doc = _parsed(
        "doc-buyback-boilerplate",
        "关于以集中竞价方式回购公司股份方案的公告",
        "本次回购方案已经公司董事会审议通过。拟回购股份的种类：人民币普通股（A股）。"
        "拟回购资金总额不超过5000万元，占公司总股本的2%。"
        "若发生对公司股票交易价格产生重大影响的重大事项或公司董事会决定终止本回购"
        "方案等事项，则存在回购方案无法顺利实施的风险。"
        "相关股东是否存在减持计划：截至公告披露日，公司未收到控股股东减持计划。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "buyback_or_increase"
    assert extraction.certainty_stage in ("signed", "executed")
    kinds = {item["kind"] for item in extraction.counter_evidence}
    assert "title_body_conflict" not in kinds
    assert "high_uncertainty" not in kinds


def test_mna_review_approval_maps_to_signed_certainty() -> None:
    doc = _parsed(
        "doc-mna-approval",
        "公司关于发行股份购买资产并募集配套资金暨关联交易事项获得上海证券交易所"
        "并购重组审核委员会审核通过的公告",
        "公司发行股份购买资产并募集配套资金暨关联交易事项已获得上海证券交易所"
        "并购重组审核委员会审核通过。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "mna"
    assert extraction.certainty_stage == "signed"
    assert extraction.certainty == 0.90


def test_mna_detects_major_asset_purchase_report() -> None:
    doc = _parsed(
        "doc-mna-purchase",
        "浙江帅丰电器股份有限公司重大资产购买报告书（草案）",
        "公司拟通过支付现金方式购买标的公司100%股权，构成重大资产购买。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "mna"


def test_project_dingdian_risk_boilerplate_not_high_uncertainty() -> None:
    doc = _parsed(
        "doc-dingdian",
        "项目定点公告",
        "公司近日与某欧洲汽车制造商客户签署《供货合同》，公司获得该客户X项目定点，"
        "公司作为合格供应商将为该客户X项目在欧洲地区供货。"
        "定点通知并不反映客户最终的实际采购量，在签发采购订单之前，客户有权因采购"
        "流程所依据的制造要求发生变化而取消或推迟采购产品。"
        "实际供货量可能会受到宏观经济、汽车产业政策、市场需求和客户生产计划等"
        "因素影响，具有不确定性。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "customer_breakthrough"
    kinds = {item["kind"] for item in extraction.counter_evidence}
    assert "high_uncertainty" not in kinds
    assert "title_body_conflict" not in kinds


def test_approval_detects_plasma_license() -> None:
    doc = _parsed(
        "doc-plasma",
        "关于达拉特旗浆站获得单采血浆许可证的公告",
        "近日，公司达拉特旗浆站获得单采血浆许可证，可正式开展单采血浆业务。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "approval"
    assert extraction.direction == "positive"


def test_buyback_flexible_termination_clause_is_not_conflict() -> None:
    """'择机修订或适时终止回购' boilerplate must not block a buyback."""

    doc = _parsed(
        "doc-buyback-flexible",
        "关于以集中竞价方式回购公司股份方案的公告",
        "本次回购方案已经公司董事会审议通过。拟回购股份的种类：人民币普通股（A股）。"
        "公司可以根据相关法律法规及《公司章程》规定履行相应的审议和信息披露程序，"
        "择机修订或适时终止回购方案。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "buyback_or_increase"
    kinds = {item["kind"] for item in extraction.counter_evidence}
    assert "title_body_conflict" not in kinds
    assert "high_uncertainty" not in kinds


def test_mna_detection_two_evidence_qualitative_level() -> None:
    doc = _parsed(
        "doc-6",
        "公司重大资产重组公告",
        "公司拟通过发行股份购买资产方式收购甲公司100%股权，构成重大资产重组，"
        "交易金额50亿元。",
        provider_key="cninfo",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "mna"
    assert extraction.direction == "positive"
    assert extraction.materiality_level == 4
    assert len(extraction.evidence_ids) >= 2


def test_mna_single_evidence_capped_at_level_2() -> None:
    doc = _parsed(
        "doc-7",
        "公司重大资产重组公告",
        "公司拟收购甲公司100%股权，构成重大资产重组。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "mna"
    assert extraction.materiality_level == 2


def test_mna_formal_regulatory_reply_is_executed() -> None:
    doc = _parsed(
        "doc-mna-reply",
        "关于收到中国证监会同意发行股份购买资产注册批复的公告",
        "公司近日收到中国证券监督管理委员会出具的批复，"
        "同意公司发行股份购买资产并募集配套资金的注册申请。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "mna"
    assert extraction.certainty_stage == "executed"
    assert extraction.certainty == 1.00


def test_internal_subsidiary_merger_is_not_external_mna_catalyst() -> None:
    doc = _parsed(
        "doc-internal-merger",
        "关于子公司之间吸收合并的进展公告",
        "为优化内部管理架构，公司两家全资子公司之间实施吸收合并，"
        "本次事项属于集团内部重组。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "unsupported_event_type"
    assert extraction.no_valid_signal is True


def test_capacity_launch_detection() -> None:
    doc = _parsed(
        "doc-8",
        "公司新产线投产公告",
        "公司年产2万吨新产线正式投产，达产后总产能翻倍。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "capacity_launch"
    assert extraction.certainty_stage == "executed"
    assert extraction.materiality_level == 2


def test_direct_policy_benefit_detection() -> None:
    doc = _parsed(
        "doc-9",
        "公司受益于以旧换新政策公告",
        "公司业务直接受益于以旧换新政策，政策已明确落地实施。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "direct_policy_benefit"
    assert extraction.direction == "positive"


def test_customer_breakthrough_detection() -> None:
    doc = _parsed(
        "doc-10",
        "公司进入重要客户供应链公告",
        "公司已进入华为供应链并开始批量供货，订单预计带来新增收入。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "customer_breakthrough"
    names = {metric["name"] for metric in extraction.metrics}
    assert "客户" in names
    customer = next(metric for metric in extraction.metrics if metric["name"] == "客户")
    assert customer["value"] == "华为"


def test_subsidy_detection_with_ratio_and_one_off() -> None:
    doc = _parsed(
        "doc-11",
        "公司收到政府补助公告",
        "公司收到政府补助1亿元，占最近一个会计年度净利润的20%，属于一次性收益。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "subsidy_or_compensation"
    assert extraction.materiality_level == 3
    names = {metric["name"] for metric in extraction.metrics}
    assert "一次性属性" in names


def test_missing_fields_return_none_not_invented() -> None:
    doc = _parsed(
        "doc-12",
        "公司签订重大合同公告",
        "公司签订重大合同，具体金额未披露，以正式公告为准。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "major_contract"
    assert extraction.metrics == ()
    assert extraction.materiality_level == 1


def test_partial_offset_counter_evidence() -> None:
    doc = _parsed(
        "doc-13",
        "公司签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元，但合同尚未生效。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    kinds = {item["kind"] for item in extraction.counter_evidence}
    assert "partial" in kinds
    assert all(
        item.get("evidence_id") for item in extraction.counter_evidence
    )


def test_risk_disclosure_is_partial_not_title_body_conflict() -> None:
    doc = _parsed(
        "doc-risk-disclosure",
        "公司签订重大合同公告",
        "公司已与客户签订重大合同，合同金额1.2亿元。"
        "该合同尚需按约履行，风险提示：履行进度存在不确定性。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "major_contract"
    kinds = {item["kind"] for item in extraction.counter_evidence}
    assert "partial" in kinds
    assert "title_body_conflict" not in kinds


def test_explicit_title_body_reversal_is_conflict() -> None:
    doc = _parsed(
        "doc-explicit-conflict",
        "公司中标重大项目公告",
        "经核实，公司未能中标该项目，相关报道与实际情况不符。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "major_contract"
    kinds = {item["kind"] for item in extraction.counter_evidence}
    assert "title_body_conflict" in kinds


def test_unrelated_formal_disclosures_do_not_trigger_body_only_events() -> None:
    cases = (
        (
            "convertible-bond",
            "向不特定对象发行可转换公司债券募集资金使用的可行性分析报告（修订稿）",
            "本次募集资金拟用于建设项目，项目合同金额为81000万元，"
            "公司已完成大额募集资金使用的可行性分析。",
        ),
        (
            "credit-guarantee",
            "关于2026年度申请综合授信及担保额度的进展公告",
            "公司的授信申请已通过银行审批，综合授信额度为10亿元。",
        ),
        (
            "board-resolution",
            "第六届董事会第七次会议决议公告",
            "会议审议通过关于收购甲公司股权及回购股份方案的议案。",
        ),
    )
    for document_id, title, body in cases:
        extraction = _extract((_parsed(document_id, title, body),))
        assert extraction is not None
        assert extraction.event_type == "unsupported_event_type", title
        assert extraction.no_valid_signal is True


def test_restricted_share_cancellation_is_not_shareholder_return_buyback() -> None:
    doc = _parsed(
        "restricted-share-cancellation",
        "关于部分限制性股票回购注销完成的公告",
        "公司已完成部分限制性股票回购注销，本次注销不属于以股东回报为目的的股份回购。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "unsupported_event_type"
    assert extraction.no_valid_signal is True


def test_negative_earnings_gives_no_valid_signal() -> None:
    doc = _parsed(
        "doc-14",
        "公司2026年半年度业绩预亏公告",
        "公司预计2026年上半年归母净利润亏损5亿元，同比下降。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.no_valid_signal is True
    assert extraction.positive_mechanism is None


def test_terminated_mna_explanation_meeting_has_no_positive_signal() -> None:
    """v2 里程碑 1 反例：“终止重大资产重组说明会”固定为无正向信号。"""

    doc = _parsed(
        "doc-terminated-mna",
        "关于终止重大资产重组投资者说明会的投资者活动记录表",
        "公司于2026年8月6日召开关于终止重大资产重组的投资者说明会，"
        "就终止本次重大资产重组事项的原因与投资者进行交流。"
        "本次重组终止前曾预计增强盈利能力，但相关方案未能实施。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "mna"
    assert extraction.direction == "negative"
    assert extraction.no_valid_signal is True
    assert extraction.positive_mechanism is None


def test_multi_fact_document_selects_highest_gate_and_keeps_alternates() -> None:
    """v2 多事实管线：同一文档的并购重组与获批认证两个候选事实只入榜门控
    最高者（并购重组），获批认证保留在明细（EventClaim）。"""

    doc = _parsed(
        "doc-multi-fact",
        "关于收购控股子公司股权暨药品获注册批准的公告",
        "公司拟收购控股子公司剩余股权，交易金额5亿元，占上年营业收入20%。"
        "同时，公司控股子公司产品获得国家药品监督管理局核准签发的"
        "《药品注册证书》，可开展相关商业化活动。",
    )
    storage = Storage.__new__(Storage)  # noqa: SLF001 - fake storage
    storage.upsert_evidence_ref = lambda _evidence: None  # type: ignore[attr-defined]
    storage.get_event_cluster = lambda _event_id: None  # type: ignore[attr-defined]
    storage.get_source_documents_between = lambda _a, _b: []  # type: ignore[attr-defined]
    extractor = RuleBasedSignalExtractor(storage)
    extraction = extractor.extract_for_stock(_cluster(), (doc,), "000001")
    assert extraction is not None
    # 门控最高（并购重组重大性高于获批认证）进入榜单。
    assert extraction.event_type == "mna"
    assert extraction.positive_mechanism is not None
    # 其余候选事实保留在明细。
    alternates = extractor.alternate_facts(
        _cluster(), (doc,), "000001", "mna"
    )
    assert {alternate.event_type for alternate in alternates} == {"approval"}


def test_section_targeting_prefers_operating_financial_sections() -> None:
    """v2 章节定位：定期报告的句段事实优先取自经营/财务章节，而非附注/风险。"""

    doc = _parsed(
        "doc-section",
        "2026年半年度报告",
        "重要提示：本报告期财务数据详见下文。\n"
        "附注：报告期内子公司归母净利润为负，同比减少20%。\n"
        "主要财务数据：\n"
        "公司2026年上半年实现归母净利润9.56亿元，同比增长94.39%，"
        "上年同期4.92亿元。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "earnings_upgrade"
    # 比例来自“主要财务数据”章节，而非附注中的负净利润。
    assert any(
        metric.get("name") == "归母净利润同比变动"
        and metric.get("value") == 94.39
        for metric in extraction.metrics
    )
    assert extraction.direction == "positive"


def test_clinical_treatment_failure_is_not_terminal() -> None:
    """恒瑞医药回归（v2 评估 direction_error）：适应症中“治疗失败的…患者”
    是临床口径，不是公司事件终态，获批认证方向必须为正。"""

    doc = _parsed(
        "doc-approval-clinical",
        "关于获得药品注册批准的公告",
        "公司产品获得国家药品监督管理局核准签发的《药品注册证书》，"
        "批准的适应症为用于经奥沙利铂、氟尿嘧啶和伊立替康治疗失败的"
        "HER2阳性结直肠癌成人患者。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "approval"
    assert extraction.direction == "positive"
    assert extraction.no_valid_signal is False


def test_weisongguo_negation_is_not_terminal() -> None:
    """康辰药业回归（v2 评估 direction_error）：“亦未通过…获知”是否定介词
    用法，不是“审核未通过”，不得把激励计划自查报告判为负面终态。"""

    direction = _direction(
        "公司2026年限制性股票激励计划内幕信息知情人买卖公司股票情况的自查报告\n"
        "其在买卖公司股票时并未知悉、亦未通过任何内幕信息知情人获知"
        "本次激励计划，不存在内幕交易行为。",
        title="公司2026年限制性股票激励计划内幕信息知情人买卖公司股票情况的自查报告",
    )
    # 关键是不得因“未通过”被判负向终态；是否为正由其他正向线索决定。
    assert direction != "negative"


def test_direction_terminal_state_overrides_positive_cues() -> None:
    """v2 终态优先：正负词同时出现时，已发生的终止/失败判为负向。"""

    # 标题终态优先（“终止重大资产重组说明会”等反例）。
    assert (
        _direction("本次重组曾预计增强盈利能力", title="关于终止重大资产重组的公告")
        == "negative"
    )
    assert (
        _direction("公司曾预计改善财务状况", title="关于撤回发行申请的公告")
        == "negative"
    )
    # 正文明确决策表述。
    assert _direction("公司决定终止本次重大资产重组，曾预计增强盈利能力") == "negative"
    assert _direction("公司决定撤回发行申请，曾预计改善财务状况") == "negative"
    # 假设/风险提示与显式否定不构成终态。
    assert (
        _direction(
            "若发生重大事项或公司董事会决定终止本重组方案，则存在方案无法实施的风险，"
            "本次重组曾预计增强盈利能力"
        )
        == "positive"
    )
    assert _direction("公司未终止本次重大资产重组，重组预计增强盈利能力") == "positive"
    # 无歧义终态（未通过/被否/驳回/失败）出现在事件关键词近旁时判为负向。
    assert (
        _direction("本次重大资产重组审核未通过，公司曾预计增强盈利能力")
        == "negative"
    )
    assert _direction("公司试生产失败，曾预计产能释放") == "negative"
    # 定期报告正文中与事件无关的会计/风险口径不翻转方向。
    assert (
        _direction(
            "公司2026年上半年归母净利润同比增长，"
            "公司股票将被终止上市的风险提示，敬请投资者注意投资风险。",
            title="2026年半年度报告",
        )
        == "positive"
    )
    # 会计/诉讼口径的“未通过/被驳回”即使出现在定期报告正文也不翻转方向。
    assert (
        _direction(
            "公司2026年上半年归母净利润同比增长，未通过单独主体达成的合营安排，"
            "中掘公司在前期解除合同诉讼被驳回后，既未继续履行合同也未撤场。",
            title="2026年半年度报告",
        )
        == "positive"
    )


def test_unsupported_event_type_gives_no_valid_signal() -> None:
    doc = _parsed(
        "doc-15",
        "公司召开股东大会公告",
        "公司将于下周召开2026年第一次临时股东大会。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.no_valid_signal is True
    assert extraction.event_type == "unsupported_event_type"


def test_evidence_refs_are_persisted_with_short_excerpts(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    doc = _parsed(
        "doc-16",
        "公司签订重大合同公告",
        "公司近日与客户签订重大合同，合同金额1.2亿元，"
        "占公司最近一个会计年度营业收入的10%。",
    )
    storage.upsert_source_document(doc, NOW)
    extraction = RuleBasedSignalExtractor(storage).extract_for_stock(
        _cluster("doc-16"), (doc,), "000001"
    )
    assert extraction is not None
    refs = storage.get_evidence_refs_for_document("doc-16")
    assert len(refs) >= 1
    for ref in refs:
        assert len(ref.excerpt) <= EXCERPT_MAX_CHARS
        assert ref.start_offset is not None
        assert ref.end_offset is not None
        assert ref.source_url


def test_late_body_match_persists_non_empty_context_excerpt(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    prefix = "募集说明与一般性风险。" * 80
    doc = _parsed(
        "doc-late-evidence",
        "公司签订重大合同公告",
        prefix
        + "公司近日与客户签订重大合同，合同金额1.2亿元，"
        "占公司最近一个会计年度营业收入的10%。",
    )
    storage.upsert_source_document(doc, NOW)
    extraction = RuleBasedSignalExtractor(storage).extract_for_stock(
        _cluster("doc-late-evidence"), (doc,), "000001"
    )
    assert extraction is not None
    refs = storage.get_evidence_refs_for_document("doc-late-evidence")
    assert refs
    assert all(ref.excerpt for ref in refs)
    assert any("重大合同" in ref.excerpt for ref in refs)


def test_extract_all_returns_one_extraction_per_stock(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    doc = _parsed(
        "doc-17",
        "公司与合作方签订重大合同公告",
        "公司签订重大合同，合同金额1.2亿元。",
        codes=("000001", "600519"),
    )
    storage.upsert_source_document(doc, NOW)
    cluster = EventCluster(
        event_id="event-2",
        stock_codes=("000001", "600519"),
        canonical_title=doc.title,
        first_seen_at=NOW - timedelta(hours=2),
        last_seen_at=NOW - timedelta(hours=1),
        representative_document_id="doc-17",
        document_ids=["doc-17"],
        historical_similar_event_id=None,
    )
    extractions = RuleBasedSignalExtractor(storage).extract_all(cluster, (doc,))
    assert {item.stock_code for item in extractions} == {"000001", "600519"}
    assert all(item.event_id == "event-2" for item in extractions)


def test_mna_share_and_cash_purchase_regulatory_reply() -> None:
    """v2 检测覆盖（东方证券回归）：“发行股份及支付现金购买资产”获国资委
    批复是并购重组实质进展，必须识别为 mna 正向（董事会已通过 + 监管批复）。"""

    doc = _parsed(
        "doc-dfzq",
        "东方证券股份有限公司关于发行股份及支付现金购买资产暨关联交易事项"
        "获得上海市国资委批复的公告",
        "公司拟通过发行A股股份及支付现金的方式购买上海证券有限责任公司100%股权。"
        "2026年7月27日，公司召开第六届董事会第十七次会议，审议通过了本次交易"
        "相关议案。近日，上海市国有资产监督管理委员会出具批复，原则同意本次交易方案。"
        "本次交易方案尚需公司股东会审议批准，并经有关主管部门批准后方可正式实施。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "mna"
    assert extraction.direction == "positive"
    # 单一证据的定性重大性按保守上限 2（计划 10.5/证据规则），仍可入潜在催化。
    assert extraction.materiality_level >= 2
    assert extraction.certainty_stage == "signed"
    assert extraction.no_valid_signal is False


def test_mna_land_purchase_subsidiary_is_not_ma() -> None:
    """v2 检测覆盖（东睦股份回归）：“设立孙公司并购买土地投资建设新项目”
    是产能投资计划，不是并购重组；标题中的“并购买”不得命中“并购”。"""

    doc = _parsed(
        "doc-dongmu",
        "关于设立孙公司并购买土地投资建设新科技项目的公告",
        "公司控股子公司拟在东莞市购买土地使用权，投资设立全资子公司，"
        "预计总投资7亿元。本次对外投资事项尚需提交公司股东会审议。"
        "本次交易不属于关联交易，亦不构成重大资产重组情形。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type != "mna"
    assert extraction.no_valid_signal is True


def test_capacity_launch_already_in_production_executed_certainty() -> None:
    """v2 检测覆盖（湖北宜化回归）：“近期已安全顺利投产”的产能项目
    确定性为已执行（executed），不是框架阶段。"""

    doc = _parsed(
        "doc-hbyh",
        "关于硫磺渣综合利用8万吨/年保险粉升级改造项目投产的公告",
        "截至本公告披露日，保险粉项目的生产装置和配套设施均已建成，"
        "经相关主管部门审核后，近期已安全顺利投产，生产负荷稳步提升，"
        "产能逐步释放。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "capacity_launch"
    assert extraction.direction == "positive"
    assert extraction.certainty_stage == "executed"
    assert extraction.certainty == 1.0
    assert extraction.no_valid_signal is False


def test_earnings_loss_narrowing_not_positive() -> None:
    """v2 检测覆盖（八一钢铁回归）：归母净利润为负（亏损收窄、*ST/净资产为负）
    不是业绩上修，方向必须为负且不入榜。"""

    doc = _parsed(
        "doc-bagang",
        "八一钢铁2026年半年度报告摘要",
        "营业收入 9,318,860,868.39，较上年同期增长6.71%。"
        "归属于上市公司股东的净利润 -342,104,089.84 -696,573,704.40 不适用。"
        "归属于上市公司股东的扣除非经常性损益的净利润 -349,954,560.49 "
        "-707,095,911.99 不适用。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "earnings_upgrade"
    assert extraction.direction == "negative"
    assert extraction.no_valid_signal is True
    assert extraction.materiality_level == 0


def test_earnings_deduction_profit_decline_keeps_positive_direction() -> None:
    """扣非净利润同比下降只作部分反证（plan 10.7），不翻转归母净利润增长方向。"""

    doc = _parsed(
        "doc-kf",
        "2026年半年度报告",
        "主要财务数据：公司2026年上半年实现归母净利润9.56亿元，同比增长94.39%，"
        "上年同期4.92亿元；归属于上市公司股东的扣除非经常性损益的净利润"
        "同比下降5%，主要系非经常性损益增加。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "earnings_upgrade"
    assert extraction.direction == "positive"


def test_earnings_table_yoy_decline_negative_direction() -> None:
    """大中矿业回归（5a44de6f）：财务表“归属于上市公司股东的净利润（元）
    N M -20.59%”没有“同比”提示词，也必须按净利润行内的负百分比判为负向，
    且金额以“（元）”行口径解析。"""

    doc = _parsed(
        "doc-table-yoy",
        "2026年半年度报告摘要",
        "营业收入（元） 1,000,000,000.00 900,000,000.00 11.11%\n"
        "归属于上市公司股东的净利润（元） 322,041,110.42 405,561,886.96 "
        "-20.59%\n"
        "归属于上市公司股东的扣除非经常性损益的净利润（元） 300,000,000.00 "
        "390,000,000.00 -23.08%",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "earnings_upgrade"
    assert extraction.direction == "negative"
    assert extraction.no_valid_signal is True
    assert extraction.materiality_level == 0


def test_best_document_prefers_main_announcement_over_legal_opinion() -> None:
    """万孚生物回归：增持完成公告 + 法律意见书同簇时，确定性取主公告
    （已实施完成 → executed），法律意见书正文的“或存在”不得拖成 rumor。"""

    main = _parsed(
        "doc-wf-main",
        "关于控股股东、实际控制人增持公司股份计划实施完成的公告",
        "增持计划已实施完成。通过集中竞价交易方式增持公司股份1,156,000股，"
        "占公司总股本的0.2470%；增持金额为人民币20,044,081.00元，"
        "本次增持计划已实施完毕。",
    )
    opinion = _parsed(
        "doc-wf-opinion",
        "北京市君合（广州）律师事务所关于广州万孚生物技术股份有限公司"
        "控股股东、实际控制人增持股份的法律意见书",
        "本所律师认为，增持人具备实施本次增持的主体资格。"
        "本法律意见书对出具日前已经发生或存在的事实发表意见。",
    )
    extraction = _extract((main, opinion))
    assert extraction is not None
    assert extraction.event_type == "buyback_or_increase"
    assert extraction.certainty_stage == "executed"
    assert extraction.certainty == 1.0
    assert extraction.no_valid_signal is False


def test_mna_inquiry_delay_is_not_mna() -> None:
    """柳钢股份回归（a9a37455）：延期回复审核问询函只是监管流程文书，
    不是并购重组实质进展。"""

    doc = _parsed(
        "doc-liugang",
        "柳钢股份关于延期回复《关于柳州钢铁股份有限公司发行股份及支付现金购买"
        "资产并募集配套资金暨关联交易申请的审核问询函》的公告",
        "公司正在推进发行股份及支付现金购买资产并募集配套资金事项，"
        "因相关工作尚未完成，公司申请延期回复上海证券交易所审核问询函。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type != "mna"
    assert extraction.no_valid_signal is True


def test_buyback_price_adjustment_is_not_buyback() -> None:
    """广合科技回归（c546da67）：调整股票期权行权价格和限制性股票回购价格
    是例行会计调整，不是回购增持事件。"""

    doc = _parsed(
        "doc-gh",
        "关于调整股票期权行权价格和限制性股票回购价格的公告",
        "根据公司2024年股票期权与限制性股票激励计划的规定，因2025年年度权益"
        "分派实施完毕，公司对股票期权行权价格和限制性股票回购价格进行调整。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type != "buyback_or_increase"
    assert extraction.no_valid_signal is True


def test_negated_financial_assistance_is_not_subsidy() -> None:
    """定增“不存在/不提供…财务资助或补偿”是合规承诺，不是补贴赔偿事件
    （63b76f6b/b0d59160 回归）。"""

    doc = _parsed(
        "doc-fa",
        "关于本次向特定对象发行股票不存在直接或通过利益相关方向参与认购的"
        "投资者提供财务资助或补偿的公告",
        "公司本次向特定对象发行股票不存在直接或通过利益相关方向参与认购的"
        "投资者提供财务资助或补偿的情形。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type != "subsidy_or_compensation"
    assert extraction.no_valid_signal is True


def test_financing_completed_with_quantified_total_is_positive() -> None:
    """中信证券回归（70f17e22）：H股发行已完成交割且披露量化募集资金总额时，
    financing_completion 门控通过（不要求正文出现“用于…项目”字样）。"""

    doc = _parsed(
        "doc-citic",
        "中信证券股份有限公司关于完成向特定对象发行H股股票暨股本变动的"
        "提示性公告",
        "公司已以23.13港元/股的发行价格向控股股东成功发行H股803,725,383股，"
        "并已于2026年8月6日完成相关股份交割事宜。本次发行的募集资金总额为"
        "人民币160亿元。",
    )
    extraction = _extract((doc,))
    assert extraction is not None
    assert extraction.event_type == "financing_completion"
    assert extraction.direction == "positive"
    assert extraction.no_valid_signal is False
    assert extraction.materiality_level >= 1
