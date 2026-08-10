from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.institutions import InstitutionRegistry
from ashare_hotpot.models import SourceDocument
from ashare_hotpot.research_activities import (
    classify_question_depth,
    classify_question_topic,
    extract_questions,
    extract_reported_participant_count,
    infer_activity_type,
    parse_activity_dates,
    parse_research_activity,
    participant_field_lines,
    split_participant_names,
)
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)
FIXTURES = Path(__file__).parent / "fixtures"


def _document(
    *,
    document_id: str = "doc-act-1",
    body: str,
    kind: str = "research_activity",
    codes: tuple[str, ...] = ("300999",),
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind=kind,
        source_url=f"https://example.test/list?{document_id}",
        document_url=f"https://example.test/pdf/{document_id}.pdf",
        title="XX科技投资者关系活动记录表",
        published_at=NOW,
        stock_codes=codes,
        body_text=body,
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def _fixture_text() -> str:
    return (FIXTURES / "research_activity_record.txt").read_text(encoding="utf-8")


def test_parse_activity_dates_range_enumeration_and_fallback() -> None:
    text = "公司于2026年8月4日至6日举办调研活动"
    dates, precision = parse_activity_dates(text, NOW)
    assert dates == (date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6))
    assert precision == "explicit"

    text2 = "2026年8月4日、8月5日以及8月6日"
    dates2, _ = parse_activity_dates(text2, NOW)
    assert dates2 == (date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6))

    text3 = "7月17日—7月30日接待了多家投资者的调研"
    dates3, _ = parse_activity_dates(text3, NOW)
    assert dates3[0] == date(2026, 7, 17)
    assert dates3[-1] == date(2026, 7, 30)
    assert len(dates3) == 14

    # 无明确日期时回退到披露日并标注日期精度。
    dates4, precision4 = parse_activity_dates("无日期文本", NOW)
    assert dates4 == (date(2026, 8, 6),)
    assert precision4 == "disclosure_end"

    # 带年份的日期不会被短格式规则重复解析到错误年份。
    dates5, _ = parse_activity_dates("公司于2025年1月5日完成调研", NOW)
    assert dates5 == (date(2025, 1, 5),)


def test_activity_type_inference() -> None:
    assert infer_activity_type("特定对象调研") == "survey"
    assert infer_activity_type("2026年半年度业绩说明会") == "performance_briefing"
    assert infer_activity_type("投资者说明会") == "briefing"
    assert infer_activity_type("路演活动") == "roadshow"
    assert infer_activity_type("参观考察活动") == "site_visit"
    assert infer_activity_type("普通交流") == "other"


def test_reported_participant_count_only_explicit_totals() -> None:
    assert extract_reported_participant_count("约30家机构参与") == 30
    assert extract_reported_participant_count("共100家基金公司参与") == 100
    assert extract_reported_participant_count("众多投资者参与") is None
    assert extract_reported_participant_count("无相关表述") is None


def test_question_extraction_depth_and_topic_classification() -> None:
    text = (
        "问：大客户认证进度如何？答：已认证。\n"
        "Q1：当前产能利用率是多少？A：85%。\n"
        "投资者提问：公司未来战略规划？答：见公告。\n"
    )
    questions = extract_questions(text)
    assert len(questions) == 3
    assert classify_question_depth(questions[0]) == "high"
    assert classify_question_depth(questions[1]) == "medium"
    assert classify_question_depth(questions[2]) == "low"
    assert classify_question_topic(questions[0]) == "customers"
    assert classify_question_topic(questions[1]) == "capacity"
    assert classify_question_topic(questions[2]) == "growth"

    # 无有效问答返回 0 计数，不为空文本编造问题。
    assert extract_questions("只有正文没有问答") == []


def test_parse_fixture_activity_and_participants(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    document = _document(body=_fixture_text())
    result = parse_research_activity(document, registry)

    assert result is not None
    activity = result.activity
    assert activity.stock_code == "300999"
    assert activity.source_document_id == "doc-act-1"
    assert activity.activity_dates == (
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
    )
    assert activity.date_precision == "explicit"
    assert activity.activity_type == "survey"
    assert activity.reported_participant_count == 30
    assert activity.question_count == 5
    assert activity.high_depth_question_count == 2
    assert activity.depth_counts["high"] == 2
    assert activity.depth_counts["medium"] == 1
    assert activity.depth_counts["low"] == 2
    assert activity.topic_counts.get("customers", 0) >= 1
    assert activity.topic_counts.get("capacity", 0) >= 1
    assert activity.topic_counts.get("profitability", 0) >= 1
    assert activity.topic_counts.get("growth", 0) >= 1
    assert activity.named_participant_count == 6

    # 参与者名单：6 家明确机构，均来自正文行。
    participants = result.participants
    assert len(participants) == 6
    by_id = {participant.institution_id: participant for participant in participants}
    assert "inst:seed:citic_securities" in by_id
    assert "inst:seed:yifangda" in by_id
    assert "inst:seed:taikang_am" in by_id
    assert "inst:seed:goldman" in by_id
    # 分析师只在同行明确给出时保存；基金经理不计为分析师。
    assert by_id["inst:seed:citic_securities"].analyst_name == "张明"
    assert by_id["inst:seed:yifangda"].analyst_name is None
    assert by_id["inst:seed:huatai"].analyst_name == "赵敏"
    # 每名参与者都有证据引用。
    assert all(participant.evidence_id for participant in participants)

    # 证据摘录不超长且带正文偏移。
    refs = {ref.evidence_id: ref for ref in result.evidence_refs}
    first_ref = refs[participants[0].evidence_id]
    assert first_ref.document_id == "doc-act-1"
    assert first_ref.start_offset is not None
    assert first_ref.end_offset is not None
    assert len(first_ref.excerpt) <= 240


def test_parse_skips_empty_and_non_research_documents(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)

    assert parse_research_activity(_document(body="  "), registry) is None
    announcement = _document(
        document_id="doc-ann",
        body="公司签署重大合同公告",
        kind="announcement",
    )
    assert parse_research_activity(announcement, registry) is None
    no_code = _document(body=_fixture_text(), codes=())
    assert parse_research_activity(no_code, registry) is None


def test_participant_dedup_same_institution_multi_line(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    body = (
        "时间：2026年8月5日\n"
        "参与单位：\n"
        "中信证券股份有限公司 张明 研究员\n"
        "中信证券 李华 研究员\n"
        "华泰证券股份有限公司 王强 研究员\n"
    )
    result = parse_research_activity(_document(body=body), registry)
    assert result is not None
    # 同一机构在同一活动多次出现只计一次。
    assert result.activity.named_participant_count == 2
    assert len(result.participants) == 2


def test_participant_field_lines_and_split() -> None:
    text = (
        "时间：2026年8月6日\n"
        "参与单位：中信证券股份有限公司、易方达基金管理有限公司；华泰证券 广发基金\n"
        "单位名称  姓名  职务\n"
    )
    fields = participant_field_lines(text)
    assert len(fields) == 1
    value, offset = fields[0]
    assert value.startswith("中信证券")
    assert text.find("参与单位") == offset
    assert split_participant_names(value) == [
        "中信证券股份有限公司",
        "易方达基金管理有限公司",
        "华泰证券",
        "广发基金",
    ]


def test_participant_field_ignores_vague_totals_and_persons() -> None:
    assert participant_field_lines("参与单位：约30家机构\n")[0][0] == "约30家机构"
    # 模糊总数与人员姓名不会生成机构实体（缺少完整机构后缀）。
    assert split_participant_names("约30家机构") == []
    assert split_participant_names("张明、李华") == []


def test_structured_field_creates_entities_and_excludes_company_self(
    tmp_path,
) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    body = (
        "参与单位：XX科技股份有限公司、中信证券股份有限公司、易方达基金管理有限公司\n"
        "交流内容：略\n"
    )
    document = _document(
        document_id="doc-field-1",
        body=body,
        codes=("300999",),
    )
    document = SourceDocument(
        document_id=document.document_id,
        provider_key=document.provider_key,
        provider_name=document.provider_name,
        kind=document.kind,
        source_url=document.source_url,
        document_url=document.document_url,
        title=document.title,
        published_at=document.published_at,
        stock_codes=document.stock_codes,
        stock_names={"300999": "XX科技"},
        body_text=document.body_text,
        content_hash=document.content_hash,
        parse_status=document.parse_status,
        parse_error=document.parse_error,
    )
    result = parse_research_activity(document, registry)
    assert result is not None
    # 上市公司自身排除，其余两家机构来自“参与单位”字段。
    assert result.activity.named_participant_count == 2
    canonical_names = {
        storage.get_institution(p.institution_id).canonical_name
        for p in result.participants
    }
    assert "中信证券股份有限公司" in canonical_names
    assert "易方达基金管理有限公司" in canonical_names
    assert "XX科技" not in canonical_names


def test_table_rows_and_field_merge_deduplicated(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    body = (
        "参与单位：中信证券股份有限公司、华泰证券股份有限公司\n"
        "单位名称                姓名    职务\n"
        "中信证券股份有限公司    张明    研究员\n"
        "易方达基金管理有限公司  李华    基金经理\n"
        "问：产能情况如何？答：见公告。\n"
    )
    result = parse_research_activity(
        _document(document_id="doc-table-1", body=body), registry
    )
    assert result is not None
    # 字段与表格行都提到中信证券 → 只计一次；表格行补充易方达。
    assert result.activity.named_participant_count == 3
    canonical_names = {
        storage.get_institution(p.institution_id).canonical_name
        for p in result.participants
    }
    assert canonical_names == {
        "中信证券股份有限公司",
        "华泰证券股份有限公司",
        "易方达基金管理有限公司",
    }


def test_real_tb600089_record_named_institutions_frozen(tmp_path) -> None:
    """600089 特变电工（2026-07 记录表）：实际列名 6 家，已核验并冻结。

    名单行使用“参与单位名称及人员姓名 | …”结构与分隔符；上市公司自身
    （特变电工）不计入机构广度，模糊总数 31 家机构只作披露计数。
    """

    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    body = (
        FIXTURES / "activity_tb600089.txt"
    ).read_text(encoding="utf-8")
    document = _document(
        document_id="doc-tb600089",
        body=body,
        codes=("600089",),
    )
    document = SourceDocument(
        document_id=document.document_id,
        provider_key=document.provider_key,
        provider_name=document.provider_name,
        kind=document.kind,
        source_url=document.source_url,
        document_url=document.document_url,
        title=document.title,
        published_at=document.published_at,
        stock_codes=document.stock_codes,
        stock_names={"600089": "特变电工"},
        body_text=document.body_text,
        content_hash=document.content_hash,
        parse_status=document.parse_status,
        parse_error=document.parse_error,
    )
    result = parse_research_activity(document, registry)
    assert result is not None
    assert result.activity.reported_participant_count == 31
    assert result.activity.named_participant_count == 6
    canonical_names = {
        storage.get_institution(p.institution_id).canonical_name
        for p in result.participants
    }
    assert canonical_names == {
        "华泰证券股份有限公司",
        "交银施罗德基金管理有限公司",
        "申万宏源证券有限公司",
        "汇添富基金管理股份有限公司",
        "中加基金",
        "建信基金管理有限责任公司",
    }


def test_real_sh601607_record_named_institutions_frozen(tmp_path) -> None:
    """601607 上海医药（2026-07 记录表）：实际列名 9 家，已核验并冻结。

    名单行包含“参与机构与人数：”前缀标签，结构化提取取最后一个冒号后的
    名单；模糊总数 29 位投资者只作披露计数，不生成实体。
    """

    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    body = (
        FIXTURES / "activity_sh601607.txt"
    ).read_text(encoding="utf-8")
    document = _document(
        document_id="doc-sh601607",
        body=body,
        codes=("601607",),
    )
    document = SourceDocument(
        document_id=document.document_id,
        provider_key=document.provider_key,
        provider_name=document.provider_name,
        kind=document.kind,
        source_url=document.source_url,
        document_url=document.document_url,
        title=document.title,
        published_at=document.published_at,
        stock_codes=document.stock_codes,
        stock_names={"601607": "上海医药"},
        body_text=document.body_text,
        content_hash=document.content_hash,
        parse_status=document.parse_status,
        parse_error=document.parse_error,
    )
    result = parse_research_activity(document, registry)
    assert result is not None
    assert result.activity.reported_participant_count == 29
    assert result.activity.named_participant_count == 9
    canonical_names = {
        storage.get_institution(p.institution_id).canonical_name
        for p in result.participants
    }
    assert canonical_names == {
        "华创证券有限责任公司",
        "中国国际金融股份有限公司",
        "华夏基金管理有限公司",
        "诺安基金管理有限公司",
        "国邦基金",
        "重鼎资产",
        "嘉实基金管理有限公司",
        "东吴资管",
        "招商基金管理有限公司",
    }


def test_activity_id_is_stable_per_document_and_stock(tmp_path) -> None:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    body = _fixture_text()
    first = parse_research_activity(_document(body=body), registry)
    second = parse_research_activity(_document(body=body), registry)
    assert first is not None and second is not None
    assert first.activity.activity_id == second.activity.activity_id


def test_raw_mentions_recorded_with_category_and_offset(tmp_path) -> None:
    """v2 参与者原始提及：名单片段、位置、组织类别与复核状态落库。"""

    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    body = (
        "参与单位：中信证券、汇正财经、DM Capital Limited\n"
        "交流内容：略\n"
    )
    document = _document(document_id="doc-mentions", body=body)
    result = parse_research_activity(document, registry)
    assert result is not None
    mentions = result.raw_mentions
    by_name = {mention.raw_name: mention for mention in mentions}
    # 券商 → 研究机构；企业/财经类 → 其他组织；英文资产管理 → 境外研究机构。
    assert by_name["中信证券"].organization_category == "research_institution"
    assert by_name["汇正财经"].organization_category == "other_organization"
    assert (
        by_name["DM Capital Limited"].organization_category
        == "research_institution"
    )
    assert by_name["中信证券"].start_offset is not None
    assert by_name["中信证券"].end_offset == by_name["中信证券"].start_offset + 4
    assert by_name["中信证券"].parse_version == "v2-20260809"
    assert by_name["中信证券"].review_status == "pending_review"
    assert by_name["中信证券"].evidence_id
