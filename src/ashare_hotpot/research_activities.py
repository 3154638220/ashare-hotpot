"""Structured parsing of research activity records (plan.md 12.2/12.3).

Input is the extracted plain text of a research/投资者关系 activity document
(``SourceDocument.body_text``).  The parser is conservative: amounts, vague
participant totals and analyst names are only kept when the text states them
explicitly; every participant mention carries an :class:`EvidenceRef`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from hashlib import sha1

from .config import SHANGHAI_TZ
from .institutions import (
    InstitutionRegistry,
    SEED_ALIASES,
    normalize_institution_name,
    participant_qualifies,
)
from .models import (
    EvidenceRef,
    PARTICIPANT_MENTION_REVIEW_PENDING,
    ResearchActivity,
    ResearchParticipant,
    ResearchParticipantMention,
    SourceDocument,
)


DEPTH_WEIGHTS = {"low": 0.25, "medium": 0.60, "high": 1.00}
MENTION_PARSE_VERSION = "v2-20260809"
# v1 兼容口径（v2 里程碑 5 回退/并行比较）：发布前整篇正文行级提取。
MENTION_PARSE_VERSION_V1 = "v1-legacy"
# v2 机构主指标只统计券商/基金/保险/资管/私募/信托/银行研究部门/境外投资机构；
# 企业、律所、咨询等保留在明细但不计入主榜（plan.md 第三部分）。
RESEARCH_INSTITUTION_TYPES = (
    "brokerage",
    "public_fund",
    "private_fund",
    "insurance",
    "asset_management",
    "foreign_institution",
)


_RANGE_FULL_RE = re.compile(
    r"(?P<y>\d{4})年(?P<m1>\d{1,2})月(?P<d1>\d{1,2})日"
    r"(?:至|—|–|-|～|~|到)"
    r"(?:(?P<m2>\d{1,2})月)?(?P<d2>\d{1,2})日"
)
_RANGE_SHORT_RE = re.compile(
    r"(?<!\d)(?P<m1>\d{1,2})月(?P<d1>\d{1,2})日"
    r"(?:至|—|–|-|～|~|到)"
    r"(?:(?P<m2>\d{1,2})月)?(?P<d2>\d{1,2})日(?!\d)"
)
_SINGLE_FULL_RE = re.compile(r"(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日")
_SINGLE_SHORT_RE = re.compile(r"(?<!\d)(?P<m>\d{1,2})月(?P<d>\d{1,2})日(?!\d)")
_REPORTED_RE = re.compile(r"约?(?P<n>\d{1,4})(?:家|位|名)(?:机构|基金公司|基金|投资者|单位)")
_QUESTION_RE = re.compile(r"(?:问|Q(?:\d+)?|投资者提问)\s*[:：]\s*(?P<q>[^\n]+)")

# Institution mentions must start at a line/list boundary; the suffix set
# includes full company suffixes so truncated fragments ("投资银行") and
# truncated full names ("中国人寿保险(集团") are not produced.
_INSTITUTION_RE = re.compile(
    r"(?:(?<=^)|(?<=[、，,；;：: \t\n(（【]))"
    r"[\u4e00-\u9fffA-Za-z0-9（）()·＆&]{2,24}"
    r"(?:证券股份有限公司|证券有限责任公司|证券有限公司|证券股份|证券|"
    r"基金管理有限责任公司|基金管理有限公司|基金管理|基金|"
      r"保险(?:集团)?股份有限公司|保险有限公司|保险|人寿|信托|"
      r"资产管理有限责任公司|资产管理有限公司|资产管理|资本管理|投资管理|创业投资|"
      r"资管|资产|资本|投资|私募|理财|养老|有限合伙|合伙企业|创投|"
      r"科技|生物|医药|实业|材料|"
      r"银行|期货|租赁|金控|集团|控股|研究(?:院|所)|分公司|自营部|自营|"
      r"（有限合伙）|(有限合伙)|"
      r"股份有限公司|有限责任公司|有限公司|公司)"
    r"(?![\u4e00-\u9fffA-Za-z0-9])"
)
# Full institution-token pattern used for 参与单位 field values: a token that
# does not carry a complete institution suffix is never turned into an entity,
# so person names and descriptive phrases in the list stay out.
_INSTITUTION_FULL_RE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z0-9（）()·＆&]{2,24}"
    r"(?:证券股份有限公司|证券有限责任公司|证券有限公司|证券股份|证券|"
    r"基金管理有限责任公司|基金管理有限公司|基金管理|基金|"
      r"保险(?:集团)?股份有限公司|保险有限公司|保险|人寿|信托|"
      r"资产管理有限责任公司|资产管理有限公司|资产管理|资本管理|投资管理|创业投资|"
      r"资管|资产|资本|投资|私募|理财|养老|有限合伙|合伙企业|创投|"
      r"科技|生物|医药|实业|材料|"
      r"银行|期货|租赁|金控|集团|控股|研究(?:院|所)|分公司|自营部|自营|"
      r"（有限合伙）|(有限合伙)|"
      r"股份有限公司|有限责任公司|有限公司|公司)$"
)
# v2 优化计划（plan.md 第三部分 里程碑 1/4）：英文机构后缀与常见结构
# （Morgan Stanley、Point72、DM Capital Limited、OBS Investments 等）。
_ENGLISH_SUFFIX_PATTERN = (
    r"Asset Management|Investment Management|Securities|Investments|"
    r"Investment|Capital|Funds?|Partners|Partnership|Bank|Insurance|Trust|"
    r"Advisors?|Advisory|Management|Group|Holdings|Limited|Ltd|"
    r"Global Investors|Ventures|Equity|Private Equity|Hong Kong|"
    r"Financial|Corporation|Corp|Pension|Asset|LTD|LLC|INC"
)
_ENGLISH_INSTITUTION_RE = re.compile(
    r"(?:(?<=^)|(?<=[、，,；;：: \t\n(（【]))"
    r"[A-Za-z0-9][A-Za-z0-9 .&'·.()（）-]{1,60}"
    r"(?:" + _ENGLISH_SUFFIX_PATTERN + r")"
    r"(?:\.?(?:Co|Inc|LLC|LP|PLC))?"
    r"(?![A-Za-z0-9])"
)
# 无后缀的外文品牌名单（种子别名）：UBS、GIC、Point72、Morgan Stanley 等。
_FOREIGN_BRAND_NAMES = (
    "Morgan Stanley",
    "Point72",
    "UBS",
    "GIC",
    "Goldman Sachs",
    "J.P. Morgan",
    "JP Morgan",
    "Citadel",
    "BlackRock",
    "Fidelity",
    "Schroders",
    "Temasek",
    "Marshall Wace",
    "Pinpoint",
    "Vision Point",
    "BofA",
    "Greenwoods",
    "Dymon Asia",
    "Grand Alliance",
    "Harding Loevner",
    "AllianceBernstein",
    "Canada Pension Plan",
    "Boyu Capital",
    "Bridgewater",
    "Two Sigma",
    "Millennium",
    "Man Group",
    "AQR",
    "HSBC",
    "Deutsche Bank",
    "Barclays",
    "Nomura",
    "Daiwa",
    "Mizuho",
    "Credit Suisse",
)
_FOREIGN_BRAND_RE = re.compile(
    r"(?:(?<=^)|(?<=[、，,；;：: \t\n(（【]))"
    + "|".join(re.escape(name) for name in sorted(_FOREIGN_BRAND_NAMES, key=len, reverse=True))
    + r"(?![A-Za-z0-9])"
)
# Q&A answer paragraphs are prose, not participant lists; skip lines with
# sentence punctuation unless they carry explicit list hints.
_SENTENCE_PUNCT_RE = re.compile(r"[。！？]")
_LIST_HINT_KEYWORDS = (
    "参与机构",
    "参加单位",
    "出席单位",
    "机构名称",
    "名单",
    "附件",
    "出席",
    "到场",
    "接待",
    "调研机构",
)
_NAME_BAD_PREFIX_RE = re.compile(
    r"^(?:公司|本公司|上市公司|上述|该|相关|以及|成为|结合|围绕|主要|在|与|及|对|为|"
    r"的|是|有|向|从|将|就|以|于|根据|由|目前|此外|另外|其中|比如|例如|旗下|包括|包含|"
    r"通过|借助|依托|基于|涉及|是否|参与|接待|实现|凭借|开始|同时|并|且|或|还|已|未|拟|"
    r"其他|短期|具体|关于|获得|（|\(|参与单位|单位名称|参会机构|参会单位|"
    r"附件清单|详见附件|见附件|详见|见|面向|全体|采用|线上|网络远程|"
    r"活动形式|活动暨|问题|请问|回答|本次)"
)
_ANALYST_RE = re.compile(
    r"([\u4e00-\u9fff·]{2,4})\s*(?:证券)?\s*(?:分析师|研究员)"
)
_ANALYST_SKIP = {"首席", "资深", "高级", "助理", "证券", "研究", "行业", "研究员", "分析师"}
_META_LINE_KEYWORDS = (
    "记录表",
    "时间",
    "方式",
    "地点",
    "日期",
    "参与单位",
    "单位名称",
    "交流内容",
    "公司简介",
    "证券代码",
    "股票代码",
    "公告编号",
)

# 参与单位/参加单位 字段：结构化名单来源（plan.md 里程碑 7）。
_PARTICIPANT_FIELD_KEYWORDS = (
    "参与单位",
    "参加单位",
    "出席单位",
    "单位名称",
    "调研单位",
    "来访单位",
    "参会单位",
    "机构名称",
    "接待单位",
)
# v2 里程碑 4：名单章节定位。先定位“参与单位/参会机构/投资者名单/附件清单”
# 章节，再在章节内解析表格、跨行字段、编号列表、中英文混排和机构—人员组合；
# 取消面向整篇正文的宽泛兜底提取，避免 Q&A 正文片段（“请介绍公司”“持续拓展
# 银行理财子公司”等）被当成机构实体。
_LIST_REGION_HEADER_RE = re.compile(
    r"参与单位|参与机构|参加单位|出席单位|参会机构|参会单位|调研机构|"
    r"来访单位|接待单位|机构名称|单位名称|投资者名单|机构名单|参与人员|"
    r"参与人|出席人|活动参与|出席人员|名单如下|附件清单|参会人员名单|调研名单|机构与人员|"
    r"参与者名单|投资者清单|参会机构名单|附件[:：]|附表[:：]|时间及?参与|"
    r"序号[^。；;]{0,8}(?:单位|公司|机构|姓名)"
)
_LIST_REGION_END_RE = re.compile(
    r"时间(?!(?:先后|顺序)|及?参与)[:：]?|地点[:：]?|方式[:：]?|日期[:：]?|上市公司接待|"
    r"接待人[员]?(?:\s*姓名)?|"
    r"交流内容|主要内容|问答情况|投资者提出的主要问题|"
    r"问题[一二三四五六七八九十\d]*\s*[:：]|"
    r"投资者关系活动[：:]|备注[:：]?|以下为|公司简介|业务介绍|"
    r"公司介绍|座谈交流|会议纪要|问\s*[:：]|答\s*[:：]"
)
_LIST_HEADER_RE = re.compile(
    r"参与单位|参与机构|参加单位|出席单位|参会机构|参会单位|调研机构|"
    r"来访单位|接待单位|机构名称|单位名称|投资者名单|机构名单|参与人员|"
    r"出席人员|名单如下|附件清单|附件"
)
_PARTICIPANT_SPLIT_RE = re.compile(r"[、，,；;/\s]+")
_PARTICIPANT_FIELD_SEPARATORS = ("：", ":", "|")
# 名单字段中的裸名（无机构后缀）只接受 3–12 字，避免“张明”这类人名成实体；
# 以职务/称谓结尾或含模糊总数标记的片段一律排除。
_PERSON_TITLE_SUFFIXES = (
    "先生",
    "女士",
    "老师",
    "分析师",
    "研究员",
    "经理",
    "总监",
    "董事长",
    "总经理",
    "秘书",
    "董事",
    "监事",
    "主任",
    "主管",
    "行长",
    "会计师",
    "总会计师",
    "部长",
    "总经理助理",
    "事务代表",
    "负责人",
    "总裁",
    "副总裁",
    "副董事长",
    "独立董事",
)
_VAGUE_TOKEN_MARKERS = ("等", "机构", "投资者", "基金公司", "家", "位", "名", "人")
# “建信基金等31家机构34人。”末尾的模糊总数后缀：先剥离再按分隔符拆分，
# 避免“建信基金”“招商基金”等最后一个列名被吞掉。
_VAGUE_SUFFIX_RE = re.compile(
    r"(?:等\d{1,4}家机构\d{1,4}人|等\d{1,4}家机构|等机构的?\d{1,4}位投资者|"
    r"等\d{1,4}位投资者|等机构|等\d{1,4}人|"
    r"(?:近|约|共)\d{1,4}位(?:机构)?投资者(?:人员)?|"
    r"及其他?(?:线上)?参会的?(?:个人|其他)?投资者|"
    r"及个人投资者等?|等其他(?:机构|投资者)?|等其他参会的?(?:个人|其他)?投资者"
    r")[。！？!?]*$"
)


def parse_activity_dates(
    text: str, published_at: datetime
) -> tuple[tuple[date, ...], str]:
    """Extract explicit activity dates from the record text.

    Returns ``(dates, precision)`` where precision is ``explicit`` when any
    date was found in the text and ``disclosure_end`` when the disclosure
    date is used as the activity date (plan.md 6.3).
    """

    explicit: set[date] = set()
    consumed: list[tuple[int, int]] = []
    for match in _RANGE_FULL_RE.finditer(text):
        year = int(match.group("y"))
        m1, d1 = int(match.group("m1")), int(match.group("d1"))
        m2 = int(match.group("m2")) if match.group("m2") else m1
        d2 = int(match.group("d2"))
        try:
            start, end = date(year, m1, d1), date(year, m2, d2)
        except ValueError:
            continue
        if end < start:
            continue
        day = start
        while day <= end:
            explicit.add(day)
            day += timedelta(days=1)
        consumed.append((match.start(), match.end()))
    for match in _RANGE_SHORT_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in consumed):
            continue
        m1, d1 = int(match.group("m1")), int(match.group("d1"))
        m2 = int(match.group("m2")) if match.group("m2") else m1
        d2 = int(match.group("d2"))
        year = (
            published_at.year
            if m2 <= published_at.month + 1
            else published_at.year - 1
        )
        try:
            start, end = date(year, m1, d1), date(year, m2, d2)
        except ValueError:
            continue
        if end < start:
            continue
        day = start
        while day <= end:
            explicit.add(day)
            day += timedelta(days=1)
        consumed.append((match.start(), match.end()))
    for match in _SINGLE_FULL_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in consumed):
            continue
        try:
            explicit.add(
                date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
            )
        except ValueError:
            continue
        consumed.append((match.start(), match.end()))
    for match in _SINGLE_SHORT_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in consumed):
            continue
        month, day = int(match.group("m")), int(match.group("d"))
        year = published_at.year if month <= published_at.month + 1 else published_at.year - 1
        try:
            explicit.add(date(year, month, day))
        except ValueError:
            continue
    if explicit:
        return tuple(sorted(explicit)), "explicit"
    return (published_at.date(),), "disclosure_end"


def infer_activity_type(text: str) -> str:
    """Map the record wording to a fixed activity type label."""

    if "业绩说明会" in text:
        return "performance_briefing"
    if "说明会" in text:
        return "briefing"
    if "路演" in text:
        return "roadshow"
    if "调研" in text:
        return "survey"
    if "参观" in text or "考察" in text:
        return "site_visit"
    return "other"


def extract_reported_participant_count(text: str) -> int | None:
    """Vague totals such as “约30家机构”; never generates entities from them."""

    counts = [int(match.group("n")) for match in _REPORTED_RE.finditer(text)]
    return max(counts) if counts else None


def participant_field_lines(text: str) -> list[tuple[str, int]]:
    """结构化“参与单位”字段：取分隔符（：/: / |）后的名单值（里程碑 7）。

    Returns ``(field_value, line_offset)`` pairs.  “参与单位：约30家机构”
    这类模糊总数仍交给 ``extract_reported_participant_count``，不会生成实体。
    """

    results: list[tuple[str, int]] = []
    for line in text.splitlines():
        keyword_positions = [
            (keyword, line.find(keyword))
            for keyword in _PARTICIPANT_FIELD_KEYWORDS
            if line.find(keyword) >= 0
        ]
        if not keyword_positions:
            continue
        keyword, position = max(keyword_positions, key=lambda item: item[1])
        keyword_end = position + len(keyword)
        separator_positions = [
            position
            for position in (
                line.find(separator, keyword_end)
                for separator in _PARTICIPANT_FIELD_SEPARATORS
            )
            if position >= 0
        ]
        if not separator_positions:
            continue
        separator = min(separator_positions)
        value = line[separator + 1 :].strip()
        # 值内仍可能带“参与机构与人数：”等前缀标签，取最后一个冒号后的名单。
        if "：" in value:
            value = value.rsplit("：", 1)[-1].strip()
        elif ":" in value:
            value = value.rsplit(":", 1)[-1].strip()
        if not value:
            continue
        offset = text.find(line)
        results.append((value, offset if offset >= 0 else 0))
    return results


def participant_regions(text: str) -> list[tuple[int, int]]:
    """Locate participant-list sections (v2 里程碑 4 名单章节定位).

    Returns ``(start, end)`` character spans: from a list header
    （“参与单位名称”“附件清单”“参会人员名单”等）到下一个章节边界
    （“时间”“地点”“上市公司接待”“交流内容”“问题”等）。Q&A 正文
    不参与机构提取；没有名单标题时退回正文首段（首边界之前），保持
    “机构：姓名”等无标题名单的召回。
    """

    spans: list[tuple[int, int]] = []
    for match in _LIST_REGION_HEADER_RE.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        rest = text[match.start() :]
        end_match = _LIST_REGION_END_RE.search(rest)
        end = (
            match.start() + end_match.start()
            if end_match is not None
            else len(text)
        )
        if end > match.start():
            spans.append((match.start(), end))
    if not spans:
        # 无名单标题：只取正文首段（第一个章节边界之前），避免 Q&A 片段。
        end_match = _LIST_REGION_END_RE.search(text)
        end = end_match.start() if end_match is not None else len(text)
        spans = [(0, end)] if end > 0 else []
    return spans


def _region_line_mentions(line: str) -> list[str]:
    """Extract institution mentions from one participant-list line.

    ①“机构：姓名”组合（“道仁资产：李晓光”“国新投资：孙语梁、王千”）
    取冒号前的机构名；②英文机构整块提取；③无冒号行按顿号/逗号名单拆分，
    接受完整后缀、种子别名与 3–12 字裸名（仅名单章节内调用，避免把
    表格行“序号 姓名 机构”中的人名误当机构——无冒号行只接受后缀/种子
    形态，裸名由“机构：姓名”路径接收）。
    """

    if "\n" in line and "、" in line:
        return _wrapped_list_mentions(line)
    # 压缩格式：标题与名单同行（“参与单位名称…及人员姓名天弘基金、…”），
    # 先剥离重复/折叠的标题前缀，再按名单解析。
    collapsed = re.sub(
        r"^(?:参与单位名称|参与单位|参会机构|参会单位|单位名称|机构名称|"
        r"及人员姓名|人员姓名|参与机构|名单|附件清单[：:]?|活动参与人员"
        r"(?:（排名不分先后）)?)+",
        "",
        line,
    )
    # 编号点号列表（“1. Aspex Management (HK) Limited  2. DeShaw  3.
    # FENGHE ASIA”）：每个“N. ”段整体作为一个机构名（编号列表语境高置信，
    # 不要求后缀——DeShaw/FENGHE ASIA/Gain.pro 等无后缀品牌也可提取）。
    numbered = re.match(r"^\s*\d{1,3}[.、]\s*", collapsed)
    if numbered is not None:
        segments = [
            seg.strip()
            for seg in re.split(r"(?<=\S)\s+\d{1,3}[.、]\s*", collapsed)
            if seg.strip()
        ]
        result: list[str] = []
        for segment in segments:
            segment = re.sub(r"^\d{1,3}[.、]\s*", "", segment).strip()
            if not segment or _NAME_BAD_PREFIX_RE.match(segment):
                continue
            if "：" in segment or ":" in segment:
                # 编号 + “机构：姓名”同行（“1、开源证券：徐剑峰”）：
                # 取冒号前的机构名，不把整段（含姓名）当机构。
                sub = segment.split("：", 1)[0].split(":", 1)[0].strip()
                if sub and not _NAME_BAD_PREFIX_RE.match(sub):
                    result.append(sub)
                continue
            result.append(segment)
        return _unique_mentions(result)
    english = [
        match.group(0).strip()
        for match in (
            *_ENGLISH_INSTITUTION_RE.finditer(collapsed),
            *_FOREIGN_BRAND_RE.finditer(collapsed),
        )
    ]
    english = _unique_mentions(english)
    result: list[str] = list(english)
    if "：" in collapsed or ":" in collapsed:
        # 机构：姓名 —— 提取行内全部“机构名：”前缀（支持“华海资本：董承明
        # 上海约牛: 虞文娟、张思龙”同行多组与“中泰电新：郭琳；申万宏源：
        # 王艺儒；…”分号分隔），冒号后的姓名/人员段不生成实体。
        for match in re.finditer(
            r"([\u4e00-\u9fffA-Za-z0-9（）()·＆&]{2,20})\s*[：:]",
            collapsed,
        ):
            name_part = match.group(1).strip()
            if _NAME_BAD_PREFIX_RE.match(name_part):
                continue
            for name in split_participant_names(name_part):
                if name not in result:
                    result.append(name)
        return result
    for match in _INSTITUTION_RE.finditer(collapsed):
        name = match.group(0).strip()
        # 单行名单里的折行粘连（“正心谷中金公司”）同样按种子别名拆分。
        for piece in _split_embedded_seed(name):
            if piece not in result:
                result.append(piece)
    # 英文逗号分隔列表（“Vision Point, Allianz, Aspen, Farallon…”）：
    # 名单章节内的无后缀英文品牌（Allianz/Jain Global/DE.Shaw 等）高置信，
    # 按英文逗号切 token 补提取；中英混合 token 留给其他路径。
    if re.search(
        r"[A-Za-z0-9][A-Za-z0-9 .&'·.-]{1,40},\s*[A-Za-z]",
        collapsed,
    ):
        for token in re.split(r",\s*", collapsed):
            token = token.strip().rstrip(".").strip()
            if (
                not token
                or not re.search(r"[A-Za-z]", token)
                or re.search(r"[\u4e00-\u9fff]{2,}", token)
                or token in result
            ):
                continue
            if token.startswith("和"):
                token = token[1:]
            if 2 <= len(token) <= 60:
                result.append(token)
    # 种子/品牌裸名（“国泰海通”“申万宏源”“淡马锡”）：无后缀但名单
    # 章节内高置信；表格行“序号 姓名 机构”中的人名无后缀/种子不会命中。
    for name in _seed_bare_mentions(collapsed):
        if name not in result:
            result.append(name)
    # “机构名+姓名”压缩名单（“南方基金史博，华泰证券王龙钰”）。
    prefix_hits = False
    if not result:
        for token in re.split(r"[、，,；;]+", collapsed):
            for piece in _split_he_connector(token.strip()):
                name = _prefix_institution(piece)
                if name and name not in result:
                    result.append(name)
                    prefix_hits = True
    # 折叠单行顿号列表内的 3–4 字裸名（“中银国际”“摩根华鑫”“上海环懿”）：
    # 单行无换行不走 wrapped 路径，此处按顿号 token 级补提取；行内存在
    # “机构名+姓名”压缩特征时跳过（避免“邹寅隆”等人名被当作机构）。
    if not prefix_hits:
        for token in re.split(r"[、，,；;]+", collapsed):
            for piece in _split_he_connector(token.strip()):
                if (
                    3 <= len(piece) <= 4
                    and not _NAME_BAD_PREFIX_RE.match(piece)
                    and not piece.endswith(_PERSON_TITLE_SUFFIXES)
                    and piece not in result
                ):
                    result.append(piece)
    return result


def _seed_bare_mentions(line: str) -> list[str]:
    """Find seed short names appearing at boundaries in a list line."""

    result: list[str] = []
    for alias in sorted(SEED_ALIASES, key=len, reverse=True):
        if len(alias) < 3 or not re.search(r"[\u4e00-\u9fff]", alias):
            continue
        for match in re.finditer(re.escape(alias), line):
            start, end = match.start(), match.end()
            before = line[start - 1 : start] if start > 0 else ""
            after = line[end : end + 1]
            if before and re.match(r"[\u4e00-\u9fffA-Za-z0-9]", before):
                continue
            if after and re.match(r"[\u4e00-\u9fffA-Za-z0-9]", after):
                continue
            result.append(line[start:end])
    return _unique_mentions(result)


def _prefix_institution(token: str) -> str | None:
    """“南方基金史博”“国联民生周泰”：机构名后紧跟 ≤4 字姓名的压缩名单。"""

    for match in re.finditer(
        r"[\u4e00-\u9fffA-Za-z0-9（）()·＆&]{2,20}?"
        r"(?:证券|基金管理|基金|保险|资管|资产|资本|投资|信托|理财|养老|"
        r"集团|控股|银行|研究(?:院|所))",
        token,
    ):
        name = match.group(0)
        rest = token[len(name) :]
        if rest and re.fullmatch(r"\s*[\u4e00-\u9fff]{1,4}", rest):
            return name
    for alias in sorted(SEED_ALIASES, key=len, reverse=True):
        if (
            len(alias) >= 3
            and re.search(r"[\u4e00-\u9fff]", alias)
            and token.startswith(alias)
        ):
            rest = token[len(alias) :]
            if rest and re.fullmatch(r"\s*[\u4e00-\u9fff]{1,4}", rest):
                return alias
    return None


def split_participant_names(value: str) -> list[str]:
    """按名单分隔符（、 ， ； / 空白）拆分并保守筛选机构名。

    接受：①完整机构后缀形态；②种子别名（如“汇添富”）；③3–12 字且非人员
    称谓、非模糊总数的裸名（如“交银施罗德”“申万宏源”）。人员、媒体和描述
    短语由后续 ``participant_qualifies`` / ``_is_company_self`` 与这里的
    词形规则共同排除；模糊名称只会进入 ``needs_review``，从不自动合并。
    v2：英文机构（含空格/点号，如 “DM Capital Limited”）先整体提取，
    再按中文分隔符拆分剩余名单。
    """

    cleaned_value = _VAGUE_SUFFIX_RE.sub("", value)
    english_mentions = [
        match.group(0).strip()
        for match in (
            *_ENGLISH_INSTITUTION_RE.finditer(cleaned_value),
            *_FOREIGN_BRAND_RE.finditer(cleaned_value),
        )
    ]
    english_mentions = _unique_mentions(english_mentions)
    for mention in english_mentions:
        cleaned_value = cleaned_value.replace(mention, " ")
    tokens = [
        part.strip() for part in _PARTICIPANT_SPLIT_RE.split(cleaned_value)
    ]
    result: list[str] = list(english_mentions)
    for token in tokens:
        if not token:
            continue
        token = token.rstrip("。！？!?").lstrip("和与及")
        if not token:
            continue
        if _INSTITUTION_FULL_RE.fullmatch(token):
            result.append(token)
            continue
        alias = normalize_institution_name(token)
        if alias and alias in SEED_ALIASES:
            result.append(token)
            continue
        if not (3 <= len(token) <= 12):
            continue
        if token.endswith(_PERSON_TITLE_SUFFIXES):
            continue
        if any(marker in token for marker in _VAGUE_TOKEN_MARKERS):
            continue
        result.append(token)
    return result


def _is_list_joinable(line: str) -> bool:
    """True when a physical line can participate in a wrapped list join."""

    stripped = line.strip()
    if not stripped:
        return False
    if "：" in stripped or ":" in stripped:
        # “机构：姓名”行自带分隔符，是完整名单项（可能“汇添富基金：郑乐凯、
        # 徐延锋、”折行）；不参与顿号名单的整块合并，避免把多行“机构：姓名”
        # 拼成一块后被词元切分毁掉（京东方 0616 回归）。
        return False
    if _SENTENCE_PUNCT_RE.search(stripped):
        return False
    if any(
        keyword in stripped
        for keyword in (
            *_META_LINE_KEYWORDS,
            *_PARTICIPANT_FIELD_KEYWORDS,
            *_LIST_HINT_KEYWORDS,
        )
    ):
        return False
    return True


def _logical_lines(text: str) -> list[tuple[str, int]]:
    """Split text into logical lines, joining wrapped participant lists.

    互动易“参与单位”名单按列宽折行时会把机构名拆在两行（如“高”+“毅资产”、
    “大”+“湾区发展基金”）。只有紧跟在名单标题（“参与单位名称”等）之后的
    连续名单行才合并为一条逻辑行，避免把 Q&A 正文中含顿号的散文（“公司结合
    小分子、多肽药物…”）误当成折行名单。
    """

    physical = text.splitlines()
    offsets: list[int] = []
    running = 0
    for line in physical:
        offsets.append(running)
        running += len(line) + 1
    logical: list[tuple[str, int]] = []
    index = 0
    while index < len(physical):
        line = physical[index]
        if _LIST_HEADER_RE.search(line):
            logical.append((line, offsets[index]))
            index += 1
            # 名单标题后紧跟的折行名单块：合并为一条逻辑行。
            if (
                index < len(physical)
                and "、" in physical[index]
                and _is_list_joinable(physical[index])
            ):
                joined = [physical[index]]
                pos = index + 1
                while pos < len(physical):
                    next_line = physical[pos]
                    if not _is_list_joinable(next_line):
                        break
                    if "、" not in next_line and len(next_line.strip()) > 40:
                        break
                    joined.append(next_line)
                    pos += 1
                logical.append(("\n".join(joined), offsets[index]))
                index = pos
            continue
        logical.append((line, offsets[index]))
        index += 1
    return logical


def _wrapped_list_mentions(block: str) -> list[str]:
    """Tokenize a wrapped 参与单位 block into complete institution mentions.

    折行名单的物理换行可能在机构名中间断开（“大”+“湾区发展基金”、“高”+
    “毅资产”、“中信建投证”+“券”）：先按英文词边界拼接提取英文机构，再把
    中文名单去空白后按名单分隔符拆分，≤3 字中文短片段与下一词元合并；
    种子别名内嵌粘连（“正心谷”+“中金公司”）在词元内拆分；“及个人投资者等”
    等名单尾缀在词元级剥离。
    """

    continuous = block.replace("\n", " ")
    english = [
        match.group(0).strip()
        for match in (
            *_ENGLISH_INSTITUTION_RE.finditer(continuous),
            *_FOREIGN_BRAND_RE.finditer(continuous),
        )
    ]
    english = _unique_mentions(english)
    remainder = block
    for mention in english:
        remainder = remainder.replace(mention, " ")
    # 英文逗号列表的无后缀品牌（“Allianz, Aspen, Farallon, Fullerton…”）：
    # 在英文/品牌提取后剩余段中按英文逗号切 token 补提取。
    if re.search(r"[A-Za-z0-9][A-Za-z0-9 .&'·.-]{1,40},\s*[A-Za-z]", remainder):
        for token in re.split(r",\s*", remainder):
            token = token.strip().rstrip(".").strip()
            if (
                not token
                or not re.search(r"[A-Za-z]", token)
                or re.search(r"[\u4e00-\u9fff]{2,}", token)
                or any(token == e or token in e or e in token for e in english)
            ):
                continue
            if token.startswith("和"):
                token = token[1:]
            if 2 <= len(token) <= 60:
                english.append(token)
    # 中文名单：去掉所有空白后按顿号/逗号拆分（“中信建投证”+“券”恢复完整名）。
    continuous_zh = re.sub(r"\s+", "", remainder)
    tokens = [
        token for token in re.split(r"[、，,；;]+", continuous_zh) if token
    ]
    merged: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if index + 1 < len(tokens) and re.fullmatch(
            r"[\u4e00-\u9fff]{1,3}", token
        ):
            merged.append(token + tokens[index + 1])
            index += 2
            continue
        merged.append(token)
        index += 1
    expanded: list[str] = []
    for token in merged:
        for piece in _split_he_connector(token):
            expanded.extend(_split_embedded_seed(piece))
    cleaned = [
        _VAGUE_SUFFIX_RE.sub("", token).lstrip("和与及")
        for token in expanded
    ]
    # 机构词形校验：完整后缀 / 种子别名 / 3–4 字高置信裸名；其他中文片段
    # （“准确”“保证信息披露真实”等）在名单词元级即被拒绝。
    validated: list[str] = []
    for token in cleaned:
        if _INSTITUTION_FULL_RE.fullmatch(token):
            validated.append(token)
            continue
        normalized = normalize_institution_name(token)
        if normalized and normalized in SEED_ALIASES:
            validated.append(token)
            continue
        # 3–4 字裸名（“西部利得”“农银汇理”“上海环懿”）：折行名单章节内
        # 高置信（人名/职务已由 title 后缀与词首黑名单排除）。
        if (
            3 <= len(token) <= 4
            and not _NAME_BAD_PREFIX_RE.match(token)
            and not token.endswith(_PERSON_TITLE_SUFFIXES)
        ):
            validated.append(token)
            continue
        # “机构名+姓名”压缩项（“西南证券胡光怿”“银河基金傅鑫”）。
        prefix_name = _prefix_institution(token)
        if prefix_name is not None:
            validated.append(prefix_name)
            continue
        # 括号注释截断兜底（“敦美投资（参会者已签署…”“天风证券股份
        # 有限公司（中小盘研究团队）”）——括号前的主体按机构验证。
        base = _strip_parenthetical(token)
        if base != token:
            if _INSTITUTION_FULL_RE.fullmatch(base):
                validated.append(base)
                continue
            base_norm = normalize_institution_name(base)
            if base_norm and base_norm in SEED_ALIASES:
                validated.append(base)
                continue
            base_prefix = _prefix_institution(base)
            if base_prefix is not None:
                validated.append(base_prefix)
    return [
        *english,
        *validated,
    ]


def _split_embedded_seed(token: str) -> list[str]:
    """Split a seed alias glued to a short bare prefix without separator.

    折行名单把“正心谷”与“中金公司”粘成“正心谷中金公司”时，按种子别名
    在词尾切分，避免把两家机构误合并成一个实体。
    """

    for alias in sorted(SEED_ALIASES, key=len, reverse=True):
        if len(alias) < 3 or len(token) <= len(alias):
            continue
        if token.endswith(alias):
            prefix = token[: -len(alias)]
            if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", prefix):
                return [prefix, alias]
    return [token]


_BODY_SHORT_NAME_RE = re.compile(
    r"(?:证券简称|股票简称|公司简称)[：: ]*([\u4e00-\u9fffA-Za-z0-9*·]+)"
)
_BODY_HEADER_NAME_RE = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9·]{2,32}?"
    r"(?:集团股份有限公司|股份有限公司|有限责任公司|有限公司|集团公司|集团))"
)
_ST_SUFFIX_RE = re.compile(r"^[*＊]?ST", re.IGNORECASE)
_SHARE_CLASS_SUFFIX_RE = re.compile(r"[ABHV]$")


def _strip_parenthetical(token: str) -> str:
    """截断尾部括号注释（“敦美投资（参会者已签署…”“天风证券股份
    有限公司（中小盘研究团队）”→ 机构主体）。“（有限合伙）”等名称组成
    部分由后缀列表单独识别，不受影响。"""

    for sep in ("（", "("):
        if sep in token:
            return token.split(sep, 1)[0].strip()
    return token


def _split_he_connector(token: str) -> list[str]:
    """“和”连接词切分（“淡水泉投资和中欧基金”→“淡水泉投资”“中欧基金”；
    “和中欧基金”→“中欧基金”）。机构名以“和”开头的（“和风亚洲基金”
    “和信投资”）不误拆；仅当左右段或剥离首字后的剩余是机构特征时切分。"""

    _HE_BRAND_PREFIXES = ("和风", "和信", "和聚", "和君", "和泰", "和润")
    if token.startswith("和") and not token.startswith(_HE_BRAND_PREFIXES):
        rest = token[1:]
        if _INSTITUTION_FULL_RE.fullmatch(rest) or (
            normalize_institution_name(rest) in SEED_ALIASES
        ):
            return [rest]
    for match in re.finditer("和", token):
        left = token[: match.start()]
        right = token[match.end() :]
        if not left or not right:
            continue
        left_ok = (
            _INSTITUTION_FULL_RE.fullmatch(left)
            or normalize_institution_name(left) in SEED_ALIASES
            or 3 <= len(left) <= 4
        )
        right_ok = (
            _INSTITUTION_FULL_RE.fullmatch(right)
            or normalize_institution_name(right) in SEED_ALIASES
            or 3 <= len(right) <= 4
        )
        if left_ok and right_ok:
            return [left, right]
    return [token]


def _document_self_names(document: SourceDocument) -> tuple[str, ...]:
    """上市公司自身名称：文档股票名、正文证券简称与正文抬头法定名称。

    cninfo/irm 正文头部通常带“证券简称：京东方A”与公司法定名称抬头，
    解析期即可排除自身，无需依赖外部队列补股票名（v2 里程碑 4）。
    """

    names: set[str] = set()
    for code in document.stock_codes:
        name = (document.stock_names or {}).get(code)
        if name:
            names.add(name)
    body = document.body_text or ""
    short_match = _BODY_SHORT_NAME_RE.search(body)
    if short_match is not None:
        short = _ST_SUFFIX_RE.sub("", short_match.group(1))
        short = _SHARE_CLASS_SUFFIX_RE.sub("", short)
        if short:
            names.add(short)
    for line in body.splitlines()[:2]:
        stripped = line.strip()
        if not stripped or re.search(
            r"证券代码|股票代码|公告编号|时间|地点|参与单位|单位名称",
            stripped,
        ):
            continue
        header_match = _BODY_HEADER_NAME_RE.match(stripped)
        if header_match is not None:
            names.add(header_match.group(1))
            break
    return tuple(names)


def _is_company_self(raw: str, document: SourceDocument) -> bool:
    """上市公司自身不计入机构广度（plan.md 里程碑 7）。

    Matches the exact short name and its legal-form variants
    （“特变电工股份有限公司”“上海医药集团股份有限公司”），以及短名形态
    （“京东方科技集团”对“京东方科技集团股份有限公司”、“顾地科技公司”对
    “顾地科技股份有限公司”）；子公司等非自身名称不受影响，仍然作为独立
    实体进入 ``needs_review``。v2：股票名缺失时从正文“证券简称/股票简称”
    与抬头法定名称推导。
    """

    for name in _document_self_names(document):
        raw_clean = raw.replace(" ", "").replace("\u3000", "")
        name_clean = name.replace(" ", "").replace("\u3000", "")
        if raw_clean == name_clean:
            return True
        if raw_clean.startswith(name_clean):
            remainder = raw_clean[len(name_clean) :]
            if remainder in _COMPANY_SELF_SUFFIXES:
                return True
        if name_clean.startswith(raw_clean):
            remainder = name_clean[len(raw_clean) :]
            if remainder in _COMPANY_SELF_SUFFIXES:
                return True
        # “顾地科技公司”这类带尾缀“公司”的短名。
        if raw_clean.endswith("公司") and name_clean.startswith(raw_clean[:-2]):
            remainder = name_clean[len(raw_clean[:-2]) :]
            if remainder in _COMPANY_SELF_SUFFIXES:
                return True
    return False


_COMPANY_SELF_SUFFIXES = (
    "集团",
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "股份公司",
    "集团股份有限公司",
    "集团有限责任公司",
    "集团有限公司",
)


def _is_english_institution_name(name: str) -> bool:
    """True for a complete English institution mention (suffix or brand)."""

    return bool(
        _ENGLISH_INSTITUTION_RE.fullmatch(name)
        or _FOREIGN_BRAND_RE.fullmatch(name)
    )


def _unique_mentions(mentions: list[str]) -> list[str]:
    """Drop brand sub-mentions already covered by a longer mention.

    “Point72 Hong Kong”（后缀命中）同时会命中品牌名“Point72”，只保留较长
    的完整名单项，避免同一机构生成两个实体。
    """

    result: list[str] = []
    for mention in mentions:
        if any(mention != other and mention in other for other in mentions):
            continue
        if mention not in result:
            result.append(mention)
    return result


def extract_questions(text: str) -> list[str]:
    return [
        match.group("q").strip()
        for match in _QUESTION_RE.finditer(text)
        if match.group("q").strip()
    ]


_HIGH_KEYWORDS = (
    "单位经济",
    "成本拆解",
    "价格拆解",
    "产能释放",
    "客户认证",
    "认证进度",
    "量化",
    "指引",
    "份额变化",
    "良率",
)
_MEDIUM_KEYWORDS = (
    "毛利率",
    "订单节奏",
    "产能利用率",
    "库存",
    "客户结构",
    "订单",
    "产能",
    "排产",
    "费用率",
    "现金流",
)
_LOW_KEYWORDS = ("公司概况", "行业", "战略", "规划", "前景", "展望", "竞争优势", "布局", "未来")


def classify_question_depth(question: str) -> str:
    """plan.md 12.3 depth levels: low 0.25 / medium 0.60 / high 1.00."""

    if any(keyword in question for keyword in _HIGH_KEYWORDS):
        return "high"
    if any(keyword in question for keyword in _MEDIUM_KEYWORDS):
        return "medium"
    if any(keyword in question for keyword in _LOW_KEYWORDS):
        return "low"
    return "low"


_TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("orders", ("订单", "合同", "签约", "中标", "预收", "在手订单")),
    ("customers", ("客户", "大客户", "认证", "导入", "份额", "下游", "绑定")),
    ("capacity", ("产能", "投产", "扩产", "利用率", "爬坡", "达产", "排产", "交期", "产能释放")),
    ("products", ("产品", "新品", "研发", "技术", "迭代", "型号", "量产", "良率", "发布")),
    (
        "profitability",
        ("毛利", "毛利率", "净利", "利润", "盈利", "费用率", "成本", "价格", "涨价", "降价"),
    ),
    ("growth", ("增长", "成长", "空间", "增速", "发展", "景气", "渗透率", "规划")),
    (
        "risks",
        ("风险", "减值", "诉讼", "合规", "竞争加剧", "需求下滑", "不确定", "波动"),
    ),
    (
        "governance",
        ("治理", "股东", "分红", "回购", "股权激励", "减持", "增持", "董事会", "管理层", "关联交易"),
    ),
)


def classify_question_topic(question: str) -> str:
    """Map a question to one of the nine fixed topics (plan.md 12.3)."""

    for topic, keywords in _TOPIC_RULES:
        if any(keyword in question for keyword in keywords):
            return topic
    return "other"


def _extract_analyst_names(line: str) -> list[str]:
    names: list[str] = []
    for match in _ANALYST_RE.finditer(line):
        name = match.group(1)
        if name not in _ANALYST_SKIP and not name.endswith(("师", "员", "长", "理")):
            names.append(name)
    return names


@dataclass(frozen=True, slots=True)
class ActivityParseResult:
    activity: ResearchActivity
    participants: tuple[ResearchParticipant, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    raw_mentions: tuple[ResearchParticipantMention, ...] = ()


def parse_research_activity(
    document: SourceDocument,
    registry: InstitutionRegistry,
    *,
    pipeline_version: str = "v2",
) -> ActivityParseResult | None:
    """Parse one research document into one activity plus participants.

    Returns ``None`` for empty bodies, non-research kinds or documents without
    an A-share code.  The activity id is deterministic per document+stock, so
    re-runs upsert instead of duplicating.  ``pipeline_version`` selects the
    parsing pipeline: ``"v2"``（名单章节定位 + 种子归一，默认）或 ``"v1"``
    （发布前整篇正文行级提取，v2 里程碑 5 回退/并行比较用）。
    """

    if pipeline_version == "v1":
        return _parse_activity_legacy_v1(document, registry)
    if pipeline_version != "v2":
        raise ValueError(
            f"unknown research pipeline version: {pipeline_version!r}"
        )

    text = (document.body_text or "").strip()
    if not text or document.kind != "research_activity":
        return None
    if not document.stock_codes:
        return None
    stock_code = document.stock_codes[0]

    activity_dates, precision = parse_activity_dates(text, document.published_at)
    activity_type = infer_activity_type(text)
    reported = extract_reported_participant_count(text)
    questions = extract_questions(text)
    depth_counts = {"low": 0, "medium": 0, "high": 0}
    topic_counts: dict[str, int] = {}
    for question in questions:
        depth = classify_question_depth(question)
        depth_counts[depth] += 1
        topic = classify_question_topic(question)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    activity_id = "activity:" + sha1(
        (document.document_id + "|" + stock_code).encode("utf-8")
    ).hexdigest()[:16]
    activity = ResearchActivity(
        activity_id=activity_id,
        stock_code=stock_code,
        source_document_id=document.document_id,
        activity_dates=activity_dates,
        activity_type=activity_type,
        reported_participant_count=reported,
        named_participant_count=0,
        question_count=len(questions),
        high_depth_question_count=depth_counts["high"],
        topic_counts=topic_counts,
        depth_counts=depth_counts,
        date_precision=precision,
    )

    participants: list[ResearchParticipant] = []
    evidence_refs: list[EvidenceRef] = []
    mention_rows: list[ResearchParticipantMention] = []
    seen: set[str] = set()
    field_lines = participant_field_lines(text)
    field_offsets = {offset for _value, offset in field_lines}

    def record_mention(
        raw: str,
        institution: object,
        evidence_id: str,
        start_offset: int | None,
    ) -> None:
        mention_id = sha1(
            (
                f"{document.document_id}|{activity_id}|{raw}|"
                f"{start_offset or 0}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        mention_rows.append(
            ResearchParticipantMention(
                mention_id=f"mention:{mention_id}",
                document_id=document.document_id,
                activity_id=activity_id,
                raw_name=raw,
                start_offset=start_offset,
                end_offset=(
                    start_offset + len(raw) if start_offset is not None else None
                ),
                organization_category=(
                    "research_institution"
                    if institution.institution_type
                    in RESEARCH_INSTITUTION_TYPES
                    else "other_organization"
                ),
                parse_version=MENTION_PARSE_VERSION,
                review_status=PARTICIPANT_MENTION_REVIEW_PENDING,
                evidence_id=evidence_id,
                created_at=datetime.now(SHANGHAI_TZ),
            )
        )

    def add_mention(
        raw: str, evidence_id: str, start_offset: int | None = None
    ) -> None:
        cleaned = raw.strip()
        if len(cleaned) > 60 or (
            len(cleaned) > 20 and not _is_english_institution_name(cleaned)
        ) or _NAME_BAD_PREFIX_RE.match(cleaned):
            return
        if _is_company_self(cleaned, document):
            return
        if not participant_qualifies(cleaned):
            return
        try:
            institution = registry.resolve(cleaned)
        except ValueError:
            return
        if institution.institution_id in seen:
            return
        seen.add(institution.institution_id)
        participants.append(
            ResearchParticipant(
                activity_id=activity_id,
                institution_id=institution.institution_id,
                analyst_name=None,
                evidence_id=evidence_id,
            )
        )
        record_mention(raw, institution, evidence_id, start_offset)

    # 1) 结构化“参与单位”字段：整行拆分为名单，同一行共享一条证据。
    for value, offset in field_lines:
        line_no = text.count("\n", 0, offset)
        evidence_id = f"evidence:{document.document_id}:p{line_no}"
        excerpt = re.sub(r"\s+", " ", value).strip()[:240]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=evidence_id,
                document_id=document.document_id,
                start_offset=offset if offset >= 0 else None,
                end_offset=offset + len(value) if offset >= 0 else None,
                excerpt=excerpt,
                source_url=document.source_url,
            )
        )
        for name in split_participant_names(value):
            name_offset = value.find(name)
            add_mention(
                name,
                evidence_id,
                start_offset=(
                    offset + name_offset if name_offset >= 0 else None
                ),
            )

    # 2) 名单章节内逐行解析（v2 里程碑 4：先定位名单章节，取消面向整篇
    #    正文的宽泛兜底提取）。表格行“序号 姓名 机构”、折行名单、
    #    “机构：姓名”组合与中英文混排都在章节内处理；Q&A 正文不再参与。
    for region_start, region_end in participant_regions(text):
        region = text[region_start:region_end]
        for line_no, (line, line_offset) in enumerate(_logical_lines(region)):
            if region_start + line_offset in field_offsets:
                continue
            if (
                any(keyword in line for keyword in _META_LINE_KEYWORDS)
                and not _LIST_REGION_HEADER_RE.search(line)
                and not re.search(r"[、：:]", line)
            ):
                # 纯元数据行（“时间 2026 年…”）跳过；标题与名单同行的
                # 压缩格式（“参与单位名称…及人员姓名天弘基金、…”）保留。
                continue
            stripped = line.strip()
            if not stripped:
                continue
            if _LIST_REGION_HEADER_RE.fullmatch(stripped):
                # 纯名单标题行（“参与单位名称”“附件清单”）。
                continue
            if _SENTENCE_PUNCT_RE.search(stripped) and not re.search(
                r"[、：:]", stripped
            ):
                # 名单章节内的散文/说明句（“…面向全体投资者。”）不是名单；
                # 仅有逗号的长句仍按散文处理，只有顿号/冒号才算名单特征。
                continue
            raw_mentions = _region_line_mentions(line)
            if not raw_mentions:
                continue
            analysts = _extract_analyst_names(line)
            excerpt = re.sub(r"\s+", " ", line).strip()[:240]
            offset = region_start + line_offset
            evidence_id = f"evidence:{document.document_id}:p{line_no}"
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=evidence_id,
                    document_id=document.document_id,
                    start_offset=offset if offset >= 0 else None,
                    end_offset=offset + len(line) if offset >= 0 else None,
                    excerpt=excerpt,
                    source_url=document.source_url,
                )
            )
            paired_analyst = (
                analysts[0] if len(raw_mentions) == 1 and analysts else None
            )
            for cleaned in raw_mentions:
                if len(cleaned) > 60 or (
                    len(cleaned) > 20
                    and not _is_english_institution_name(cleaned)
                ) or _NAME_BAD_PREFIX_RE.match(cleaned):
                    continue
                if _is_company_self(cleaned, document):
                    continue
                if not participant_qualifies(cleaned):
                    continue
                try:
                    institution = registry.resolve(cleaned)
                except ValueError:
                    continue
                if institution.institution_id in seen:
                    continue
                seen.add(institution.institution_id)
                participants.append(
                    ResearchParticipant(
                        activity_id=activity_id,
                        institution_id=institution.institution_id,
                        analyst_name=paired_analyst,
                        evidence_id=evidence_id,
                    )
                )
                cleaned_offset = line.find(cleaned)
                mention_rows.append(
                    ResearchParticipantMention(
                        mention_id="mention:"
                        + sha1(
                            (
                                f"{document.document_id}|{activity_id}|{cleaned}|"
                                f"{region_start + line_offset + max(0, cleaned_offset)}"
                            ).encode("utf-8")
                        ).hexdigest()[:16],
                        document_id=document.document_id,
                        activity_id=activity_id,
                        raw_name=cleaned,
                        start_offset=(
                            region_start + line_offset + cleaned_offset
                            if cleaned_offset >= 0
                            else None
                        ),
                        end_offset=(
                            region_start + line_offset + cleaned_offset
                            + len(cleaned)
                            if cleaned_offset >= 0
                            else None
                        ),
                        organization_category=(
                            "research_institution"
                            if institution.institution_type
                            in RESEARCH_INSTITUTION_TYPES
                            else "other_organization"
                        ),
                        parse_version=MENTION_PARSE_VERSION,
                        review_status=PARTICIPANT_MENTION_REVIEW_PENDING,
                        evidence_id=evidence_id,
                        created_at=datetime.now(SHANGHAI_TZ),
                    )
                )

    activity = replace(activity, named_participant_count=len(participants))
    return ActivityParseResult(
        activity=activity,
        participants=tuple(participants),
        evidence_refs=tuple(evidence_refs),
        raw_mentions=tuple(mention_rows),
    )


# ---------------------------------------------------------------------------
# v1 兼容口径（v2 里程碑 5 回退/并行比较）：发布前整篇正文行级提取的冻结快照。
# 与本模块主线的主要差异：不定位名单章节（整篇正文行级处理）、不使用 M4
# 扩展后缀集（资产/投资/资本/私募/理财/养老/科技等）、无“机构：姓名”组合/
# 种子裸名/压缩名单提取、上市公司自身只用 stock_names 排除。回退只用于
# 版本周期的临时兜底；种子表与词形常量沿用当前版本（使 v1 只略优于历史值，
# 不构成回归风险）。
# ---------------------------------------------------------------------------

_LEGACY_SUFFIXES = (
    r"证券股份有限公司|证券有限责任公司|证券有限公司|证券股份|证券|"
    r"基金管理有限责任公司|基金管理有限公司|基金管理|基金|"
    r"保险(?:集团)?股份有限公司|保险有限公司|保险|人寿|信托|"
    r"资产管理有限责任公司|资产管理有限公司|资产管理|资本管理|投资管理|创业投资|"
    r"资管|银行|期货|租赁|金控|集团|控股|研究(?:院|所)|"
    r"股份有限公司|有限责任公司|有限公司|公司"
)
_LEGACY_INSTITUTION_RE = re.compile(
    r"(?:(?<=^)|(?<=[、，,；;：: \t\n(（【]))"
    r"[\u4e00-\u9fffA-Za-z0-9（）()·＆&]{2,24}"
    r"(?:" + _LEGACY_SUFFIXES + r")"
    r"(?![\u4e00-\u9fffA-Za-z0-9])"
)
_LEGACY_ENGLISH_SUFFIX_PATTERN = (
    r"Asset Management|Investment Management|Securities|Investments|"
    r"Investment|Capital|Funds?|Partners|Partnership|Bank|Insurance|Trust|"
    r"Advisors?|Advisory|Management|Group|Holdings|Limited|Ltd|"
    r"Global Investors|Ventures|Equity|Private Equity|Hong Kong"
)
_LEGACY_ENGLISH_RE = re.compile(
    r"(?:(?<=^)|(?<=[、，,；;：: \t\n(（【]))"
    r"[A-Za-z][A-Za-z0-9 .&'·.-]{1,60}"
    r"(?:" + _LEGACY_ENGLISH_SUFFIX_PATTERN + r")"
    r"(?:\.?(?:Co|Inc|LLC|LP|PLC))?"
    r"(?![A-Za-z0-9])"
)


def _legacy_wrapped_mentions(block: str) -> list[str]:
    """v1 折行名单合并（空格拼接 + ≤3 字中文短片段与下一词元合并）。"""

    continuous = block.replace("\n", " ")
    english = [
        match.group(0).strip()
        for match in (
            *_LEGACY_ENGLISH_RE.finditer(continuous),
            *_FOREIGN_BRAND_RE.finditer(continuous),
        )
    ]
    english = _unique_mentions(english)
    remainder = continuous
    for mention in english:
        remainder = remainder.replace(mention, " ")
    tokens = [
        token for token in re.split(r"[、，,；;/\s]+", remainder) if token
    ]
    merged: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if index + 1 < len(tokens) and re.fullmatch(
            r"[\u4e00-\u9fff]{1,3}", token
        ):
            merged.append(token + tokens[index + 1])
            index += 2
            continue
        merged.append(token)
        index += 1
    return [
        *english,
        *[
            token
            for token in merged
            if re.fullmatch(r"[\u4e00-\u9fff]{2,24}", token)
        ],
    ]


def _parse_activity_legacy_v1(
    document: SourceDocument, registry: InstitutionRegistry
) -> ActivityParseResult | None:
    """发布前 v1 兼容解析（整篇正文行级正则，无名单章节定位）。"""

    text = (document.body_text or "").strip()
    if not text or document.kind != "research_activity":
        return None
    if not document.stock_codes:
        return None
    stock_code = document.stock_codes[0]

    activity_dates, precision = parse_activity_dates(text, document.published_at)
    activity_type = infer_activity_type(text)
    reported = extract_reported_participant_count(text)
    questions = extract_questions(text)
    depth_counts = {"low": 0, "medium": 0, "high": 0}
    topic_counts: dict[str, int] = {}
    for question in questions:
        depth = classify_question_depth(question)
        depth_counts[depth] += 1
        topic = classify_question_topic(question)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    activity_id = "activity:" + sha1(
        (document.document_id + "|" + stock_code).encode("utf-8")
    ).hexdigest()[:16]
    activity = ResearchActivity(
        activity_id=activity_id,
        stock_code=stock_code,
        source_document_id=document.document_id,
        activity_dates=activity_dates,
        activity_type=activity_type,
        reported_participant_count=reported,
        named_participant_count=0,
        question_count=len(questions),
        high_depth_question_count=depth_counts["high"],
        topic_counts=topic_counts,
        depth_counts=depth_counts,
        date_precision=precision,
    )

    participants: list[ResearchParticipant] = []
    evidence_refs: list[EvidenceRef] = []
    mention_rows: list[ResearchParticipantMention] = []
    seen: set[str] = set()
    field_lines = participant_field_lines(text)
    field_offsets = {offset for _value, offset in field_lines}

    def _add_mention(
        cleaned: str,
        evidence_id: str,
        start_offset: int | None,
        analyst_name: str | None = None,
    ) -> None:
        if len(cleaned) > 60 or (
            len(cleaned) > 20 and not _is_english_institution_name(cleaned)
        ) or _NAME_BAD_PREFIX_RE.match(cleaned):
            return
        # v1：自身排除只使用 stock_names（无正文推导）。
        if _is_company_self_v1(cleaned, document):
            return
        if not participant_qualifies(cleaned):
            return
        try:
            institution = registry.resolve(cleaned)
        except ValueError:
            return
        if institution.institution_id in seen:
            return
        seen.add(institution.institution_id)
        participants.append(
            ResearchParticipant(
                activity_id=activity_id,
                institution_id=institution.institution_id,
                analyst_name=analyst_name,
                evidence_id=evidence_id,
            )
        )
        mention_rows.append(
            ResearchParticipantMention(
                mention_id="mention:"
                + sha1(
                    (
                        f"{document.document_id}|{activity_id}|{cleaned}|"
                        f"{start_offset or 0}"
                    ).encode("utf-8")
                ).hexdigest()[:16],
                document_id=document.document_id,
                activity_id=activity_id,
                raw_name=cleaned,
                start_offset=start_offset,
                end_offset=(
                    start_offset + len(cleaned)
                    if start_offset is not None
                    else None
                ),
                organization_category=(
                    "research_institution"
                    if institution.institution_type
                    in RESEARCH_INSTITUTION_TYPES
                    else "other_organization"
                ),
                parse_version=MENTION_PARSE_VERSION_V1,
                review_status=PARTICIPANT_MENTION_REVIEW_PENDING,
                evidence_id=evidence_id,
                created_at=datetime.now(SHANGHAI_TZ),
            )
        )

    # 1) 结构化“参与单位”字段（v1 同路径）。
    for value, offset in field_lines:
        line_no = text.count("\n", 0, offset)
        evidence_id = f"evidence:{document.document_id}:p{line_no}"
        excerpt = re.sub(r"\s+", " ", value).strip()[:240]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=evidence_id,
                document_id=document.document_id,
                start_offset=offset if offset >= 0 else None,
                end_offset=offset + len(value) if offset >= 0 else None,
                excerpt=excerpt,
                source_url=document.source_url,
            )
        )
        for name in split_participant_names(value):
            name_offset = value.find(name)
            _add_mention(
                name,
                evidence_id,
                start_offset=(
                    offset + name_offset if name_offset >= 0 else None
                ),
            )

    # 2) 整篇正文行级正则（v1 无名单章节定位）。
    for line_no, (line, line_offset) in enumerate(_logical_lines(text)):
        if any(offset == text.find(line) for offset in field_offsets):
            continue
        if any(keyword in line for keyword in _META_LINE_KEYWORDS):
            continue
        if _SENTENCE_PUNCT_RE.search(line) and not any(
            keyword in line for keyword in _LIST_HINT_KEYWORDS
        ):
            continue
        if "\n" in line and "、" in line:
            raw_mentions = _legacy_wrapped_mentions(line)
        else:
            raw_mentions = [
                match.group(0).strip()
                for match in (
                    *_LEGACY_INSTITUTION_RE.finditer(line),
                    *_LEGACY_ENGLISH_RE.finditer(line),
                    *_FOREIGN_BRAND_RE.finditer(line),
                )
            ]
            raw_mentions = _unique_mentions(raw_mentions)
        if not raw_mentions:
            continue
        analysts = _extract_analyst_names(line)
        excerpt = re.sub(r"\s+", " ", line).strip()[:240]
        offset = line_offset if line_offset >= 0 else text.find(line)
        evidence_id = f"evidence:{document.document_id}:p{line_no}"
        evidence_refs.append(
            EvidenceRef(
                evidence_id=evidence_id,
                document_id=document.document_id,
                start_offset=offset if offset >= 0 else None,
                end_offset=offset + len(line) if offset >= 0 else None,
                excerpt=excerpt,
                source_url=document.source_url,
            )
        )
        paired_analyst = (
            analysts[0] if len(raw_mentions) == 1 and analysts else None
        )
        for cleaned in raw_mentions:
            cleaned_offset = line.find(cleaned)
            _add_mention(
                cleaned,
                evidence_id,
                start_offset=(
                    line_offset + cleaned_offset
                    if cleaned_offset >= 0
                    else None
                ),
                analyst_name=paired_analyst,
            )

    activity = replace(activity, named_participant_count=len(participants))
    return ActivityParseResult(
        activity=activity,
        participants=tuple(participants),
        evidence_refs=tuple(evidence_refs),
        raw_mentions=tuple(mention_rows),
    )


def _is_company_self_v1(raw: str, document: SourceDocument) -> bool:
    """v1 自身排除：只用 stock_names（不推导正文证券简称/抬头）。"""

    for code in document.stock_codes:
        name = (document.stock_names or {}).get(code)
        if not name:
            continue
        raw_clean = raw.replace(" ", "").replace("\u3000", "")
        name_clean = name.replace(" ", "").replace("\u3000", "")
        if raw_clean == name_clean:
            return True
        if raw_clean.startswith(name_clean):
            remainder = raw_clean[len(name_clean) :]
            if remainder in _COMPANY_SELF_SUFFIXES:
                return True
        if name_clean.startswith(raw_clean):
            remainder = name_clean[len(raw_clean) :]
            if remainder in _COMPANY_SELF_SUFFIXES:
                return True
        if raw_clean.endswith("公司") and name_clean.startswith(raw_clean[:-2]):
            remainder = name_clean[len(raw_clean[:-2]) :]
            if remainder in _COMPANY_SELF_SUFFIXES:
                return True
    return False
