"""v2 优化计划里程碑 1 固定回归集：机构名单解析（plan.md 第三部分）。

覆盖互动易外文名单、跨行表格（折行名单）、短名归一对齐与上市公司自身
误识别四类真实样本缺陷。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.institutions import InstitutionRegistry
from ashare_hotpot.models import SourceDocument
from ashare_hotpot.research_activities import parse_research_activity
from ashare_hotpot.storage import Storage


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=SHANGHAI_TZ)


def _document(
    *,
    document_id: str,
    body: str,
    codes: tuple[str, ...] = ("300999",),
    stock_names: dict[str, str] | None = None,
) -> SourceDocument:
    return SourceDocument(
        document_id=document_id,
        provider_key="cninfo",
        provider_name="巨潮资讯",
        kind="research_activity",
        source_url=f"https://example.test/list?{document_id}",
        document_url=f"https://example.test/pdf/{document_id}.pdf",
        title="投资者关系活动记录表",
        published_at=NOW,
        stock_codes=codes,
        stock_names=stock_names or {},
        body_text=body,
        content_hash=f"hash-{document_id}",
        parse_status="parsed",
        parse_error=None,
    )


def _participant_names(
    tmp_path: Path, document: SourceDocument
) -> set[str]:
    storage = Storage(tmp_path / "hotpot.db")
    registry = InstitutionRegistry(storage, now=NOW)
    result = parse_research_activity(document, registry)
    assert result is not None
    return {
        storage.get_institution(participant.institution_id).canonical_name
        for participant in result.participants
    }


def test_foreign_institution_list_parses_english_names(tmp_path: Path) -> None:
    """互动易外文名单：后缀形态与品牌名均解析为参与者（v2 里程碑 1）。"""

    document = _document(
        document_id="doc-foreign",
        body=(
            "参与单位：Morgan Stanley、Point72 Hong Kong、DM Capital Limited、"
            "OBS Investments、UBS、GIC、中信证券\n"
            "交流内容：略\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "摩根士丹利" in names  # Morgan Stanley -> 种子实体
    assert "瑞银集团" in names  # UBS -> 种子实体
    assert "中信证券股份有限公司" in names
    assert "Point72 Hong Kong" in names
    assert "DM Capital Limited" in names
    assert "OBS Investments" in names
    assert "GIC" in names


def test_wrapped_list_joins_cross_row_institution_names(tmp_path: Path) -> None:
    """跨行表格：折行名单把机构名拆在两行时先合并再提取。"""

    document = _document(
        document_id="doc-wrapped",
        body=(
            "参与单位名称\n"
            "国寿安保基金、中信期货、人保资产、淳厚基金、大\n"
            "湾区发展基金、谦信基金、阳光保险、IGWT\n"
            "Investment、TX Capital、三井住友德思资管、高\n"
            "毅资产、合晟资产、黑岩投资、泽安私募\n"
            "交流内容：略\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    # 跨行拼接后的完整机构名（“大”+“湾区发展基金”、“高”+“毅资产”）。
    assert "大湾区发展基金" in names
    # 种子归一：高毅资产 → 上海高毅资产管理合伙企业（有限合伙）。
    assert "上海高毅资产管理合伙企业（有限合伙）" in names
    # 截断片段不得被当作独立机构实体。
    assert "湾区发展基金" not in names
    assert "毅资产" not in names
    assert "大" not in names
    assert "高" not in names
    assert "TX Capital" in names
    assert "三井住友德思资管" in names
    # 种子归一：国寿安保基金 → 国寿安保基金管理有限公司。
    assert "国寿安保基金管理有限公司" in names
    # 种子归一：阳光保险 → 阳光保险集团股份有限公司。
    assert "阳光保险集团股份有限公司" in names
    assert "IGWT Investment" in names


def test_qa_prose_with_enumeration_is_not_treated_as_wrapped_list(
    tmp_path: Path,
) -> None:
    """Q&A 正文含顿号的散文不得被当成折行名单（precision 回归防护）。"""

    document = _document(
        document_id="doc-qa",
        body=(
            "交流内容：\n"
            "公司结合小分子、多肽药物等新药研发技术平台优势，并逐步打造"
            "AI 驱动的药物发现平台、小核酸技术创新平台、多肽/抗体筛选技术平台，"
            "在呼吸系统疾病、代谢性疾病领域布局备具差异化优势的管线。\n"
            "参与单位名称\n"
            "中信证券、中金公司\n"
            "时间 2026年7月9日\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "中信证券股份有限公司" in names
    assert "中国国际金融股份有限公司" in names


def test_region_targeting_excludes_qa_fragments(tmp_path: Path) -> None:
    """v2 里程碑 4：名单章节定位。Q&A 正文片段（“请介绍公司”“持续拓展
    银行理财子公司”等）不得成为机构实体，只取名单章节内的机构。"""

    document = _document(
        document_id="doc-region",
        body=(
            "参与单位名称\n"
            "中信证券、中金公司\n"
            "时间 2026年7月9日\n"
            "上市公司接待人员姓名\n"
            "董事长 张三\n"
            "投资者关系活动主要内容介绍\n"
            "请介绍公司持续拓展银行理财子公司等业务的情况。\n"
            "问答情况如下：\n"
            "问：公司如何推进业务？答：请介绍公司最新进展。\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "中信证券股份有限公司" in names
    assert "中国国际金融股份有限公司" in names
    # Q&A 片段与公司自身/接待人员不得成实体。
    assert not any(
        name in names
        for name in (
            "请介绍公司",
            "持续拓展银行理财子公司",
            "上市公司接待人员姓名",
            "董事长",
        )
    )


def test_colon_list_institution_person_pairs(tmp_path: Path) -> None:
    """v2 里程碑 4：“机构：姓名”组合（京东方模式）逐行提取机构名，
    冒号后的姓名/人员段不生成实体；“资产/投资/私募/养老”等后缀形态
    在名单章节内接受。"""

    document = _document(
        document_id="doc-colon",
        body=(
            "参与单位名称\n"
            "道仁资产：李晓光\n"
            "大家资产：刘竞远\n"
            "国新投资：孙语梁、王千、马浩翔\n"
            "上海瀛赐私募：陈翼\n"
            "新华养老：卢珊\n"
            "时间 2026 年 6 月 4 日\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    for expected in ("道仁资产", "大家资产", "国新投资", "上海瀛赐私募"):
        assert expected in names, expected
    # 种子归一：新华养老 → 新华养老保险股份有限公司。
    assert "新华养老保险股份有限公司" in names
    assert "李晓光" not in names
    assert "孙语梁" not in names


def test_self_company_excluded_from_body_header(tmp_path: Path) -> None:
    """v2 里程碑 4：上市公司自身名称从正文抬头/证券简称推导并排除
    （stock_names 缺失时仍可用），京东方/欧陆通模式不再误识别。"""

    document = _document(
        document_id="doc-self",
        body=(
            "深圳欧陆通电子股份有限公司\n"
            "证券代码：300870 证券简称：欧陆通 公告编号：2026-018\n"
            "参与单位名称\n"
            "盘京投资：王莉\n"
            "华夏基金：金灿\n"
            "时间 2026 年 6 月 16 日\n"
        ),
        codes=("300870",),
        stock_names={},
    )
    names = _participant_names(tmp_path, document)
    assert "上海盘京投资管理中心（有限合伙）" in names
    assert "华夏基金管理有限公司" in names
    assert "深圳欧陆通电子股份有限公司" not in names
    assert "欧陆通" not in names


def test_wrapped_chinese_name_rejoined_and_vague_suffix_stripped(
    tmp_path: Path,
) -> None:
    """v2 里程碑 4：折行名单“中信建投证”+“券”恢复完整名；“华泰资产及
    个人投资者等”名单尾缀剥离后保留机构名。"""

    document = _document(
        document_id="doc-wrap2",
        body=(
            "参与单位名称\n"
            "国信证券、富国基金、国海证券、汇添富基金、中信建投证\n"
            "券、盘京投资、天弘基金、长信基金、柏基投资、兴业证券、\n"
            "嘉实基金、友邦保险、银华基金、平安资管、华泰资产及个\n"
            "人投资者等\n"
            "时间 2026 年 5 月 11 日\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "中信建投证券股份有限公司" in names
    assert "华泰资产管理有限公司" in names
    assert "国信证券股份有限公司" in names
    assert "天弘基金管理有限公司" in names
    assert "友邦保险有限公司" in names
    # 截断/尾缀残留不得成实体。
    assert "券" not in names
    assert "个人投资者" not in names
    assert "华泰资产及个人投资者等" not in names


def test_embedded_seed_split_and_bare_seed_names(tmp_path: Path) -> None:
    """v2 里程碑 4：折行粘连“正心谷中金公司”按种子别名拆分；
    无后缀种子/品牌裸名（申万宏源、淡马锡）在名单章节内提取。"""

    document = _document(
        document_id="doc-embed",
        body=(
            "参与单位名称\n"
            "浙商证券、正心谷中金公司、中欧基金、申万宏源、淡马锡\n"
            "时间 2026 年 8 月 2 日\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "浙商证券股份有限公司" in names
    assert "上海正心谷投资管理有限公司" in names
    assert "中国国际金融股份有限公司" in names
    assert "申万宏源证券有限公司" in names
    assert "淡马锡控股（私人）有限公司" in names
    assert "正心谷中金公司" not in names


def test_compressed_institution_person_list(tmp_path: Path) -> None:
    """v2 里程碑 4：“机构名+姓名”压缩名单（“南方基金史博，华泰证券
    王龙钰”“国海证券徐萌，国联民生周泰”）逐组提取机构名。"""

    document = _document(
        document_id="doc-compressed",
        body=(
            "参与单位名称及人员姓名\n"
            "南方基金史博、应帅、邹寅隆、陈梓源、华泰证券王龙钰\n"
            "时间 2026 年 5 月 15 日\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "南方基金管理股份有限公司" in names
    assert "华泰证券股份有限公司" in names
    assert "史博" not in names
    assert "王龙钰" not in names


def test_investor_list_table_rows(tmp_path: Path) -> None:
    """v2 里程碑 4：“参会投资者清单”表格（“序号 公司”）逐行提取；
    种子/后缀名称在表格行内可命中。"""

    document = _document(
        document_id="doc-table2",
        body=(
            "附件清单（如有） 参会投资者清单\n"
            "日期 2026年7月7日-8日\n"
            "参会投资者清单\n"
            "序号 公司\n"
            "1 工银瑞信\n"
            "2 泰康资产\n"
            "3 长江证券\n"
            "4 东吴证券\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "工银瑞信基金管理有限公司" in names
    assert "泰康资产管理有限责任公司" in names
    assert "长江证券股份有限公司" in names
    assert "东吴证券股份有限公司" in names
    # 散文片段不得成为机构实体。
    assert "驱动的药物发现平台" not in names
    assert "多肽药物等新药研发技术平台优势" not in names
    assert "小核酸技术创新平台" not in names


def test_short_name_resolves_via_seed_alias(tmp_path: Path) -> None:
    """短名名单：种子别名“大筝资管”归一为上海大筝资产管理有限公司。"""

    document = _document(
        document_id="doc-short",
        body=(
            "参与单位：大筝资管、中信证券、吉林省信托\n"
            "交流内容：略\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "上海大筝资产管理有限公司" in names
    assert "吉林省信托有限责任公司" in names
    assert "中信证券股份有限公司" in names


def test_listed_company_self_short_forms_excluded(tmp_path: Path) -> None:
    """上市公司自身误识别：短名与法律全称形态均不计入机构广度。"""

    document = _document(
        document_id="doc-self",
        codes=("000725",),
        stock_names={"000725": "京东方科技集团股份有限公司"},
        body=(
            "参与单位：京东方科技集团、京东方科技集团股份有限公司、"
            "易方达基金管理有限公司\n"
            "交流内容：略\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "易方达基金管理有限公司" in names
    assert "京东方科技集团" not in names
    assert "京东方科技集团股份有限公司" not in names


def test_listed_company_self_trailing_gongsi_form_excluded(tmp_path: Path) -> None:
    """上市公司自身误识别：“顾地科技公司”对法律全称同样排除。"""

    document = _document(
        document_id="doc-self-gudi",
        codes=("002694",),
        stock_names={"002694": "顾地科技股份有限公司"},
        body=(
            "参与单位：顾地科技公司、顾地科技股份有限公司、国泰君安证券\n"
            "交流内容：略\n"
        ),
    )
    names = _participant_names(tmp_path, document)
    assert "国泰君安证券股份有限公司" in names
    assert "顾地科技公司" not in names
    assert "顾地科技股份有限公司" not in names
