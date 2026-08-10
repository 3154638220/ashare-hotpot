"""Conservative institution entity normalization (plan.md 12.1).

The registry resolves mentions through an exact normalized-alias pipeline:

1. Unicode NFKC + full-width/half-width + whitespace/punctuation folding.
2. Removal of non-identifying legal suffixes (股份有限公司 / 有限责任公司 /
   有限公司 / 股份公司 / （有限合伙）).
3. A conservative seed alias table for common full/short names.
4. Persisted ``institution_aliases`` exact matches.
5. Unknown names create a new ``needs_review`` entity with a stable hash id.

Edit distance / fuzzy similarity only ever produces ``needs_review``
candidates; it never auto-merges two institutions.  Same-group legal entities
share a ``group_id`` (seed-only) while the original entities are preserved.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha1

from .config import SHANGHAI_TZ
from .models import Institution, InstitutionAlias
from .storage import Storage


INSTITUTION_TYPES = (
    "brokerage",
    "public_fund",
    "private_fund",
    "insurance",
    "asset_management",
    "foreign_institution",
    "other",
)

_LEGAL_SUFFIX_RE = re.compile(
    r"(?:股份有限公司|有限责任公司|股份公司|有限公司|（有限合伙）|\(有限合伙\))$"
)
_STRIP_CHARS = str.maketrans(
    {
        " ": "",
        "\u3000": "",
        "　": "",
        "\t": "",
        "\n": "",
        "\r": "",
        "，": "",
        "。": "",
        "、": "",
        "；": "",
        "：": "",
        "·": "",
        "．": "",
        "（": "",
        "）": "",
        "(": "",
        ")": "",
        "【": "",
        "】": "",
        "[": "",
        "]": "",
        "《": "",
        "》": "",
        "<": "",
        ">": "",
        "“": "",
        "”": "",
        '"': "",
        "'": "",
        "‘": "",
        "’": "",
        "-": "",
        "—": "",
        "–": "",
        "_": "",
        "/": "",
        "\\": "",
        "·": "",
        "&": "",
    }
)


def normalize_institution_name(raw: str) -> str:
    """Fold one institution mention into a stable normalized alias.

    Only removes non-identifying punctuation/whitespace and legal suffixes;
    region, brand and group differences are preserved.
    """

    value = unicodedata.normalize("NFKC", raw or "").strip()
    value = _LEGAL_SUFFIX_RE.sub("", value)
    value = value.translate(_STRIP_CHARS)
    return value.lower()


def _identity(raw: str) -> str:
    """Fold without legal-suffix stripping, used for conflict detection."""

    value = unicodedata.normalize("NFKC", raw or "").strip()
    return value.translate(_STRIP_CHARS)


def _display_name(raw: str) -> str:
    return re.sub(r"\s+", " ", raw or "").strip()


@dataclass(frozen=True, slots=True)
class SeedEntity:
    institution_id: str
    canonical_name: str
    short_names: tuple[str, ...]
    institution_type: str
    group_id: str


# Conservative seed table of well-known public financial institutions.  These
# entries are factual organization names, not personal data; they let exact
# full/short-name matches resolve to ``verified`` entities.
SEED_ENTITIES: tuple[SeedEntity, ...] = (
    SeedEntity(
        "inst:seed:citic_securities",
        "中信证券股份有限公司",
        ("中信证券",),
        "brokerage",
        "group:citic",
    ),
    SeedEntity(
        "inst:seed:guotai_junan",
        "国泰君安证券股份有限公司",
        ("国泰君安", "国泰君安证券"),
        "brokerage",
        "group:guotai_junan",
    ),
    SeedEntity(
        "inst:seed:huatai",
        "华泰证券股份有限公司",
        ("华泰证券",),
        "brokerage",
        "group:huatai",
    ),
    SeedEntity(
        "inst:seed:zhaoshang",
        "招商证券股份有限公司",
        ("招商证券",),
        "brokerage",
        "group:zhaoshang",
    ),
    SeedEntity(
        "inst:seed:guangfa",
        "广发证券股份有限公司",
        ("广发证券",),
        "brokerage",
        "group:guangfa",
    ),
    SeedEntity(
        "inst:seed:cicc",
        "中国国际金融股份有限公司",
        ("中金公司",),
        "brokerage",
        "group:cicc",
    ),
    SeedEntity(
        "inst:seed:yifangda",
        "易方达基金管理有限公司",
        ("易方达基金", "易方达"),
        "public_fund",
        "group:yifangda",
    ),
    SeedEntity(
        "inst:seed:huaxia",
        "华夏基金管理有限公司",
        ("华夏基金",),
        "public_fund",
        "group:huaxia",
    ),
    SeedEntity(
        "inst:seed:jiashi",
        "嘉实基金管理有限公司",
        ("嘉实基金",),
        "public_fund",
        "group:jiashi",
    ),
    SeedEntity(
        "inst:seed:nanfang",
        "南方基金管理股份有限公司",
        ("南方基金",),
        "public_fund",
        "group:nanfang",
    ),
    SeedEntity(
        "inst:seed:guangfa_fund",
        "广发基金管理有限公司",
        ("广发基金",),
        "public_fund",
        "group:guangfa_fund",
    ),
    SeedEntity(
        "inst:seed:fuguo",
        "富国基金管理有限公司",
        ("富国基金",),
        "public_fund",
        "group:fuguo",
    ),
    SeedEntity(
        "inst:seed:huitianfu",
        "汇添富基金管理股份有限公司",
        ("汇添富基金", "汇添富"),
        "public_fund",
        "group:huitianfu",
    ),
    SeedEntity(
        "inst:seed:china_life",
        "中国人寿保险股份有限公司",
        ("中国人寿",),
        "insurance",
        "group:chinalife",
    ),
    SeedEntity(
        "inst:seed:pingan",
        "中国平安保险（集团）股份有限公司",
        ("中国平安",),
        "insurance",
        "group:pingan",
    ),
    SeedEntity(
        "inst:seed:taikang_am",
        "泰康资产管理有限责任公司",
        ("泰康资产",),
        "asset_management",
        "group:taikang",
    ),
    SeedEntity(
        "inst:seed:morgan_stanley",
        "摩根士丹利",
        ("摩根士丹利", "Morgan Stanley"),
        "foreign_institution",
        "group:morgan_stanley",
    ),
    SeedEntity(
        "inst:seed:goldman",
        "高盛集团",
        ("高盛", "Goldman Sachs"),
        "foreign_institution",
        "group:goldman",
    ),
    SeedEntity(
        "inst:seed:ubs",
        "瑞银集团",
        ("瑞银", "UBS"),
        "foreign_institution",
        "group:ubs",
    ),
    SeedEntity(
        "inst:seed:blackrock",
        "贝莱德",
        ("贝莱德", "BlackRock"),
        "foreign_institution",
        "group:blackrock",
    ),
    SeedEntity(
        "inst:seed:guotai_haitong",
        "国泰海通证券股份有限公司",
        ("国泰海通", "国泰海通证券"),
        "brokerage",
        "group:guotai_haitong",
    ),
    SeedEntity(
        "inst:seed:zhongtai",
        "中泰证券股份有限公司",
        ("中泰证券",),
        "brokerage",
        "group:zhongtai",
    ),
    SeedEntity(
        "inst:seed:jilin_trust",
        "吉林省信托有限责任公司",
        ("吉林省信托",),
        "other",
        "group:jilin_trust",
    ),
    SeedEntity(
        "inst:seed:dazheng_am",
        "上海大筝资产管理有限公司",
        ("大筝资管", "上海大筝资管"),
        "asset_management",
        "group:dazheng_am",
    ),
    # ---- v2 里程碑 4：常见券商/基金/保险/资管/理财/私募种子扩展 ----
    SeedEntity(
        "inst:seed:guoxin",
        "国信证券股份有限公司",
        ("国信证券",),
        "brokerage",
        "group:guoxin",
    ),
    SeedEntity(
        "inst:seed:shenwan_hongyuan",
        "申万宏源证券有限公司",
        ("申万宏源", "申万宏源证券"),
        "brokerage",
        "group:shenwan_hongyuan",
    ),
    SeedEntity(
        "inst:seed:fangzheng",
        "方正证券股份有限公司",
        ("方正证券",),
        "brokerage",
        "group:fangzheng",
    ),
    SeedEntity(
        "inst:seed:changjiang",
        "长江证券股份有限公司",
        ("长江证券",),
        "brokerage",
        "group:changjiang",
    ),
    SeedEntity(
        "inst:seed:guojin",
        "国金证券股份有限公司",
        ("国金证券",),
        "brokerage",
        "group:guojin",
    ),
    SeedEntity(
        "inst:seed:guoyuan",
        "国元证券股份有限公司",
        ("国元证券",),
        "brokerage",
        "group:guoyuan",
    ),
    SeedEntity(
        "inst:seed:dongfang",
        "东方证券股份有限公司",
        ("东方证券",),
        "brokerage",
        "group:dongfang",
    ),
    SeedEntity(
        "inst:seed:xingye",
        "兴业证券股份有限公司",
        ("兴业证券",),
        "brokerage",
        "group:xingye",
    ),
    SeedEntity(
        "inst:seed:ebscn",
        "光大证券股份有限公司",
        ("光大证券",),
        "brokerage",
        "group:ebscn",
    ),
    SeedEntity(
        "inst:seed:tianfeng",
        "天风证券股份有限公司",
        ("天风证券",),
        "brokerage",
        "group:tianfeng",
    ),
    SeedEntity(
        "inst:seed:huafu",
        "华福证券有限责任公司",
        ("华福证券",),
        "brokerage",
        "group:huafu",
    ),
    SeedEntity(
        "inst:seed:kayuan",
        "开源证券股份有限公司",
        ("开源证券",),
        "brokerage",
        "group:kayuan",
    ),
    SeedEntity(
        "inst:seed:dongwu",
        "东吴证券股份有限公司",
        ("东吴证券",),
        "brokerage",
        "group:dongwu",
    ),
    SeedEntity(
        "inst:seed:zheshang",
        "浙商证券股份有限公司",
        ("浙商证券",),
        "brokerage",
        "group:zheshang",
    ),
    SeedEntity(
        "inst:seed:guolian_minsheng",
        "国联民生证券股份有限公司",
        ("国联民生", "国联民生证券"),
        "brokerage",
        "group:guolian_minsheng",
    ),
    SeedEntity(
        "inst:seed:citic_jiantou",
        "中信建投证券股份有限公司",
        ("中信建投", "中信建投证券"),
        "brokerage",
        "group:citic_jiantou",
    ),
    SeedEntity(
        "inst:seed:sdic",
        "国投证券股份有限公司",
        ("国投证券",),
        "brokerage",
        "group:sdic",
    ),
    SeedEntity(
        "inst:seed:guohai",
        "国海证券股份有限公司",
        ("国海证券",),
        "brokerage",
        "group:guohai",
    ),
    SeedEntity(
        "inst:seed:boci",
        "中银国际证券股份有限公司",
        ("中银证券",),
        "brokerage",
        "group:boci",
    ),
    SeedEntity(
        "inst:seed:xibu",
        "西部证券股份有限公司",
        ("西部证券",),
        "brokerage",
        "group:xibu",
    ),
    SeedEntity(
        "inst:seed:huaan",
        "华安证券股份有限公司",
        ("华安证券",),
        "brokerage",
        "group:huaan",
    ),
    SeedEntity(
        "inst:seed:catong",
        "财通证券股份有限公司",
        ("财通证券",),
        "brokerage",
        "group:catong",
    ),
    SeedEntity(
        "inst:seed:shanxi",
        "山西证券股份有限公司",
        ("山西证券",),
        "brokerage",
        "group:shanxi",
    ),
    SeedEntity(
        "inst:seed:taipingyang",
        "太平洋证券股份有限公司",
        ("太平洋证券",),
        "brokerage",
        "group:taipingyang",
    ),
    SeedEntity(
        "inst:seed:dongbei",
        "东北证券股份有限公司",
        ("东北证券",),
        "brokerage",
        "group:dongbei",
    ),
    SeedEntity(
        "inst:seed:changcheng",
        "长城证券股份有限公司",
        ("长城证券",),
        "brokerage",
        "group:changcheng",
    ),
    SeedEntity(
        "inst:seed:huaxi",
        "华西证券股份有限公司",
        ("华西证券",),
        "brokerage",
        "group:huaxi",
    ),
    SeedEntity(
        "inst:seed:caixin",
        "财信证券股份有限公司",
        ("财信证券",),
        "brokerage",
        "group:caixin",
    ),
    SeedEntity(
        "inst:seed:huachuang",
        "华创证券有限责任公司",
        ("华创证券",),
        "brokerage",
        "group:huachuang",
    ),
    SeedEntity(
        "inst:seed:xinda",
        "信达证券股份有限公司",
        ("信达证券",),
        "brokerage",
        "group:xinda",
    ),
    SeedEntity(
        "inst:seed:zhongyou",
        "中邮证券有限责任公司",
        ("中邮证券",),
        "brokerage",
        "group:zhongyou",
    ),
    SeedEntity(
        "inst:seed:guosheng",
        "国盛证券有限责任公司",
        ("国盛证券",),
        "brokerage",
        "group:guosheng",
    ),
    SeedEntity(
        "inst:seed:minsheng",
        "民生证券股份有限公司",
        ("民生证券",),
        "brokerage",
        "group:minsheng",
    ),
    SeedEntity(
        "inst:seed:pingan_sec",
        "平安证券股份有限公司",
        ("平安证券",),
        "brokerage",
        "group:pingan",
    ),
    SeedEntity(
        "inst:seed:galaxy",
        "中国银河证券股份有限公司",
        ("银河证券", "中国银河"),
        "brokerage",
        "group:galaxy",
    ),
    SeedEntity(
        "inst:seed:tianhong",
        "天弘基金管理有限公司",
        ("天弘基金",),
        "public_fund",
        "group:tianhong",
    ),
    SeedEntity(
        "inst:seed:boshi",
        "博时基金管理有限公司",
        ("博时基金",),
        "public_fund",
        "group:boshi",
    ),
    SeedEntity(
        "inst:seed:pengyang",
        "鹏华基金管理有限公司",
        ("鹏华基金",),
        "public_fund",
        "group:pengyang",
    ),
    SeedEntity(
        "inst:seed:bosera",
        "交银施罗德基金管理有限公司",
        ("交银施罗德", "交银施罗德基金"),
        "public_fund",
        "group:bosera",
    ),
    SeedEntity(
        "inst:seed:jing_shi_chang_cheng",
        "景顺长城基金管理有限公司",
        ("景顺长城",),
        "public_fund",
        "group:jing_shi_chang_cheng",
    ),
    SeedEntity(
        "inst:seed:yinhua",
        "银华基金管理股份有限公司",
        ("银华基金",),
        "public_fund",
        "group:yinhua",
    ),
    SeedEntity(
        "inst:seed:icbc_cs",
        "工银瑞信基金管理有限公司",
        ("工银瑞信",),
        "public_fund",
        "group:icbc_cs",
    ),
    SeedEntity(
        "inst:seed:ccb",
        "建信基金管理有限责任公司",
        ("建信基金",),
        "public_fund",
        "group:ccb",
    ),
    SeedEntity(
        "inst:seed:lions",
        "中欧基金管理有限公司",
        ("中欧基金",),
        "public_fund",
        "group:lions",
    ),
    SeedEntity(
        "inst:seed:yongying",
        "永赢基金管理有限公司",
        ("永赢基金",),
        "public_fund",
        "group:yongying",
    ),
    SeedEntity(
        "inst:seed:yuanxin",
        "圆信永丰基金管理有限公司",
        ("圆信永丰",),
        "public_fund",
        "group:yuanxin",
    ),
    SeedEntity(
        "inst:seed:huabao",
        "华宝基金管理有限公司",
        ("华宝基金",),
        "public_fund",
        "group:huabao",
    ),
    SeedEntity(
        "inst:seed:hongde",
        "泓德基金管理有限公司",
        ("泓德基金",),
        "public_fund",
        "group:hongde",
    ),
    SeedEntity(
        "inst:seed:guoshou_anbao",
        "国寿安保基金管理有限公司",
        ("国寿安保基金", "国寿安保"),
        "public_fund",
        "group:chinalife",
    ),
    SeedEntity(
        "inst:seed:huatai_baoxing",
        "华泰保兴基金管理有限公司",
        ("华泰保兴",),
        "public_fund",
        "group:huatai_baoxing",
    ),
    SeedEntity(
        "inst:seed:puyin_ansheng",
        "浦银安盛基金管理有限公司",
        ("浦银安盛", "浦银安盛基金"),
        "public_fund",
        "group:puyin_ansheng",
    ),
    SeedEntity(
        "inst:seed:changxin",
        "长信基金管理有限责任公司",
        ("长信基金",),
        "public_fund",
        "group:changxin",
    ),
    SeedEntity(
        "inst:seed:shenwan_lingxin",
        "申万菱信基金管理有限公司",
        ("申万菱信",),
        "public_fund",
        "group:shenwan_lingxin",
    ),
    SeedEntity(
        "inst:seed:xinda_aoya",
        "信达澳亚基金管理有限公司",
        ("信达澳亚",),
        "public_fund",
        "group:xinda_aoya",
    ),
    SeedEntity(
        "inst:seed:guohai_franklin",
        "国海富兰克林基金管理有限公司",
        ("国海富兰克林",),
        "public_fund",
        "group:guohai_franklin",
    ),
    SeedEntity(
        "inst:seed:nuoan",
        "诺安基金管理有限公司",
        ("诺安基金",),
        "public_fund",
        "group:nuoan",
    ),
    SeedEntity(
        "inst:seed:zhaoshang_fund",
        "招商基金管理有限公司",
        ("招商基金",),
        "public_fund",
        "group:zhaoshang_fund",
    ),
    SeedEntity(
        "inst:seed:abc_ca",
        "农银汇理基金管理有限公司",
        ("农银汇理", "农银汇理基金"),
        "public_fund",
        "group:abc_ca",
    ),
    SeedEntity(
        "inst:seed:changsheng",
        "长盛基金管理有限公司",
        ("长盛基金",),
        "public_fund",
        "group:changsheng",
    ),
    SeedEntity(
        "inst:seed:galaxy_fund",
        "银河基金管理有限公司",
        ("银河基金",),
        "public_fund",
        "group:galaxy_fund",
    ),
    SeedEntity(
        "inst:seed:boc_fund",
        "中银基金管理有限公司",
        ("中银基金",),
        "public_fund",
        "group:boc_fund",
    ),
    SeedEntity(
        "inst:seed:huaan_fund",
        "华安基金管理有限公司",
        ("华安基金",),
        "public_fund",
        "group:huaan_fund",
    ),
    SeedEntity(
        "inst:seed:wanjia",
        "万家基金管理有限公司",
        ("万家基金",),
        "public_fund",
        "group:wanjia",
    ),
    SeedEntity(
        "inst:seed:xxqg",
        "兴证全球基金管理有限公司",
        ("兴证全球", "兴全基金"),
        "public_fund",
        "group:xxqg",
    ),
    SeedEntity(
        "inst:seed:pingan_fund",
        "平安基金管理有限公司",
        ("平安基金",),
        "public_fund",
        "group:pingan",
    ),
    SeedEntity(
        "inst:seed:msjy",
        "民生加银基金管理有限公司",
        ("民生加银",),
        "public_fund",
        "group:msjy",
    ),
    SeedEntity(
        "inst:seed:jpm_fund",
        "摩根基金管理（中国）有限公司",
        ("摩根基金",),
        "public_fund",
        "group:jpm_fund",
    ),
    SeedEntity(
        "inst:seed:neuburger",
        "路博迈基金管理（中国）有限公司",
        ("路博迈基金", "路博迈"),
        "public_fund",
        "group:neuburger",
    ),
    SeedEntity(
        "inst:seed:blackrock_fund",
        "贝莱德基金管理有限公司",
        ("贝莱德基金",),
        "public_fund",
        "group:blackrock",
    ),
    SeedEntity(
        "inst:seed:huisheng",
        "惠升基金管理有限责任公司",
        ("惠升基金",),
        "public_fund",
        "group:huisheng",
    ),
    SeedEntity(
        "inst:seed:dongfang_fund",
        "东方基金管理股份有限公司",
        ("东方基金",),
        "public_fund",
        "group:dongfang_fund",
    ),
    SeedEntity(
        "inst:seed:catong_fund",
        "财通基金管理有限公司",
        ("财通基金",),
        "public_fund",
        "group:catong_fund",
    ),
    SeedEntity(
        "inst:seed:debon",
        "德邦基金管理有限公司",
        ("德邦基金",),
        "public_fund",
        "group:debon",
    ),
    SeedEntity(
        "inst:seed:everbright",
        "光大保德信基金管理有限公司",
        ("光大保德信",),
        "public_fund",
        "group:everbright",
    ),
    SeedEntity(
        "inst:seed:ruiyuan",
        "睿远基金管理有限公司",
        ("睿远基金",),
        "public_fund",
        "group:ruiyuan",
    ),
    SeedEntity(
        "inst:seed:quanguo",
        "泉果基金管理有限公司",
        ("泉果基金",),
        "public_fund",
        "group:quanguo",
    ),
    SeedEntity(
        "inst:seed:picc_am",
        "中国人保资产管理有限公司",
        ("人保资产",),
        "asset_management",
        "group:picc",
    ),
    SeedEntity(
        "inst:seed:picc_pension",
        "中国人保养老保险股份有限公司",
        ("人保养老",),
        "insurance",
        "group:picc",
    ),
    SeedEntity(
        "inst:seed:pingan_am",
        "平安资产管理有限责任公司",
        ("平安资管", "平安资产管理"),
        "asset_management",
        "group:pingan",
    ),
    SeedEntity(
        "inst:seed:pingan_pension",
        "平安养老保险股份有限公司",
        ("平安养老",),
        "insurance",
        "group:pingan",
    ),
    SeedEntity(
        "inst:seed:changjiang_pension",
        "长江养老保险股份有限公司",
        ("长江养老",),
        "insurance",
        "group:changjiang_pension",
    ),
    SeedEntity(
        "inst:seed:taiping_am",
        "太平资产管理有限公司",
        ("太平资产",),
        "asset_management",
        "group:taiping",
    ),
    SeedEntity(
        "inst:seed:chinalife_am",
        "中国人寿资产管理有限公司",
        ("国寿资产",),
        "asset_management",
        "group:chinalife",
    ),
    SeedEntity(
        "inst:seed:nci",
        "新华人寿保险股份有限公司",
        ("新华保险",),
        "insurance",
        "group:nci",
    ),
    SeedEntity(
        "inst:seed:aia",
        "友邦保险有限公司",
        ("友邦保险",),
        "insurance",
        "group:aia",
    ),
    SeedEntity(
        "inst:seed:sunshine",
        "阳光保险集团股份有限公司",
        ("阳光保险",),
        "insurance",
        "group:sunshine",
    ),
    SeedEntity(
        "inst:seed:taikang_ins",
        "泰康保险集团股份有限公司",
        ("泰康保险",),
        "insurance",
        "group:taikang",
    ),
    SeedEntity(
        "inst:seed:nci_pension",
        "新华养老保险股份有限公司",
        ("新华养老",),
        "insurance",
        "group:nci",
    ),
    SeedEntity(
        "inst:seed:sunshine_life",
        "光大永明人寿保险有限公司",
        ("光大永明",),
        "insurance",
        "group:sunshine_life",
    ),
    SeedEntity(
        "inst:seed:cpic_am",
        "太平洋资产管理有限责任公司",
        ("太平洋资管",),
        "asset_management",
        "group:cpic",
    ),
    SeedEntity(
        "inst:seed:gf_am",
        "广发证券资产管理（广东）有限公司",
        ("广发资管",),
        "asset_management",
        "group:guangfa",
    ),
    SeedEntity(
        "inst:seed:huatai_am",
        "华泰资产管理有限公司",
        ("华泰资管", "华泰资产"),
        "asset_management",
        "group:huatai",
    ),
    SeedEntity(
        "inst:seed:cmb_wm",
        "招银理财有限责任公司",
        ("招银理财",),
        "asset_management",
        "group:cmb",
    ),
    SeedEntity(
        "inst:seed:citic_wm",
        "信银理财有限责任公司",
        ("信银理财",),
        "asset_management",
        "group:citic",
    ),
    SeedEntity(
        "inst:seed:ccb_wm",
        "建信理财有限责任公司",
        ("建信理财",),
        "asset_management",
        "group:ccb",
    ),
    SeedEntity(
        "inst:seed:cib_wm",
        "兴银理财有限责任公司",
        ("兴银理财",),
        "asset_management",
        "group:cib",
    ),
    SeedEntity(
        "inst:seed:ceb_wm",
        "光大理财有限责任公司",
        ("光大理财",),
        "asset_management",
        "group:ceb",
    ),
    SeedEntity(
        "inst:seed:cmbc_wm",
        "民生理财有限责任公司",
        ("民生理财",),
        "asset_management",
        "group:cmbc",
    ),
    SeedEntity(
        "inst:seed:boc_wm",
        "中银理财有限责任公司",
        ("中银理财",),
        "asset_management",
        "group:boc",
    ),
    SeedEntity(
        "inst:seed:icbc_wm",
        "工银理财有限责任公司",
        ("工银理财",),
        "asset_management",
        "group:icbc",
    ),
    SeedEntity(
        "inst:seed:bocom_wm",
        "交银理财有限责任公司",
        ("交银理财",),
        "asset_management",
        "group:bocom",
    ),
    SeedEntity(
        "inst:seed:icbc",
        "中国工商银行股份有限公司",
        ("工商银行", "中国工商银行"),
        "other",
        "group:icbc",
    ),
    SeedEntity(
        "inst:seed:panjing",
        "上海盘京投资管理中心（有限合伙）",
        ("盘京投资",),
        "private_fund",
        "group:panjing",
    ),
    SeedEntity(
        "inst:seed:danshui",
        "淡水泉（北京）投资管理有限公司",
        ("淡水泉", "淡水泉投资"),
        "private_fund",
        "group:danshui",
    ),
    SeedEntity(
        "inst:seed:gaoyi",
        "上海高毅资产管理合伙企业（有限合伙）",
        ("高毅资产",),
        "private_fund",
        "group:gaoyi",
    ),
    SeedEntity(
        "inst:seed:jinglin",
        "上海景林资产管理有限公司",
        ("景林资产",),
        "private_fund",
        "group:jinglin",
    ),
    SeedEntity(
        "inst:seed:chongyang",
        "上海重阳投资管理股份有限公司",
        ("重阳投资",),
        "private_fund",
        "group:chongyang",
    ),
    SeedEntity(
        "inst:seed:xiangju",
        "相聚资本管理有限公司",
        ("相聚资本",),
        "private_fund",
        "group:xiangju",
    ),
    SeedEntity(
        "inst:seed:yuanyue",
        "北京源乐晟资产管理有限公司",
        ("源乐晟",),
        "private_fund",
        "group:yuanyue",
    ),
    SeedEntity(
        "inst:seed:ningquan",
        "上海宁泉资产管理有限公司",
        ("宁泉资产",),
        "private_fund",
        "group:ningquan",
    ),
    SeedEntity(
        "inst:seed:shicheng",
        "上海世诚投资管理有限公司",
        ("世诚投资",),
        "private_fund",
        "group:shicheng",
    ),
    SeedEntity(
        "inst:seed:zhengxingu",
        "上海正心谷投资管理有限公司",
        ("正心谷",),
        "private_fund",
        "group:zhengxingu",
    ),
    SeedEntity(
        "inst:seed:temasek",
        "淡马锡控股（私人）有限公司",
        ("淡马锡", "Temasek"),
        "foreign_institution",
        "group:temasek",
    ),
    # v2 评估回归：裸名实体被 LLM 判“名称不完整”但实体真实（全称取自
    # LLM 标注 rationale，LLM 标注口径）——种子化后 canonical 为全称，
    # entity_ok=true，研究机构召回计入。
    SeedEntity(
        "inst:seed:shangcheng_am",
        "深圳市尚诚资产管理有限责任公司",
        ("尚诚资产", "深圳市尚诚资产管理"),
        "private_fund",
        "group:shangcheng_am",
    ),
    SeedEntity(
        "inst:seed:panhou_am",
        "磐厚动量（上海）资本管理有限公司",
        ("磐厚动量", "磐厚动量(上海)资本管理"),
        "private_fund",
        "group:panhou_am",
    ),
    SeedEntity(
        "inst:seed:miyuan_am",
        "上海弥远投资管理有限公司",
        ("弥远投资", "上海弥远投资管理"),
        "private_fund",
        "group:miyuan_am",
    ),
    SeedEntity(
        "inst:seed:huaxia_jiuying",
        "华夏久盈资产管理有限责任公司",
        ("华夏久盈", "华夏久盈资产管理"),
        "asset_management",
        "group:huaxia_jiuying",
    ),
    SeedEntity(
        "inst:seed:yuanhong_am",
        "上海元泓投资管理有限公司",
        ("元泓投资", "上海元泓投资"),
        "private_fund",
        "group:yuanhong_am",
    ),
    SeedEntity(
        "inst:seed:yuanxin_zhuhai",
        "远信（珠海）私募基金管理有限公司",
        ("远信（珠海）私募基金管理", "远信(珠海)私募基金管理"),
        "private_fund",
        "group:yuanxin_zhuhai",
    ),
    SeedEntity(
        "inst:seed:fenggu_capital",
        "北京峰谷资本管理有限公司",
        ("峰谷资本", "北京峰谷资本管理"),
        "private_fund",
        "group:fenggu_capital",
    ),
    SeedEntity(
        "inst:seed:juming_am",
        "上海聚鸣投资管理有限公司",
        ("聚鸣投资", "上海聚鸣投资管理"),
        "private_fund",
        "group:juming_am",
    ),
    SeedEntity(
        "inst:seed:xishirun_am",
        "上海喜世润投资管理有限公司",
        ("喜世润投资", "上海喜世润投资管理"),
        "private_fund",
        "group:xishirun_am",
    ),
    SeedEntity(
        "inst:seed:bopu_am",
        "深圳前海博普资产管理有限公司",
        ("博普资产", "深圳前海博普资产管理"),
        "asset_management",
        "group:bopu_am",
    ),
    SeedEntity(
        "inst:seed:xingshi_am",
        "北京市星石投资管理有限公司",
        ("星石投资", "北京市星石投资管理"),
        "private_fund",
        "group:xingshi_am",
    ),
    SeedEntity(
        "inst:seed:hongdao_am",
        "北京鸿道投资管理有限责任公司",
        ("鸿道投资", "北京鸿道投资管理有限责公司"),
        "private_fund",
        "group:hongdao_am",
    ),
)


# Same-group aliases for entities that are not seed-listed themselves but
# belong to a seeded group.  Used only for ``group_id`` aggregation; every
# alias still resolves to its own institution entity.
GROUP_ALIASES: dict[str, str] = {
    "易方达资产": "group:yifangda",
    "易方达资产管理": "group:yifangda",
    "易方达香港": "group:yifangda",
    "易方达资产香港": "group:yifangda",
    "平安资管": "group:pingan",
    "平安养老": "group:pingan",
    "国寿资产": "group:chinalife",
    "泰康基金": "group:taikang",
    "泰康人寿": "group:taikang",
}


SEED_ALIASES: dict[str, Institution] = {}
for _entity in SEED_ENTITIES:
    _institution = Institution(
        institution_id=_entity.institution_id,
        canonical_name=_entity.canonical_name,
        group_id=_entity.group_id,
        institution_type=_entity.institution_type,
        verification_status="verified",
    )
    for _raw in (_entity.canonical_name, *_entity.short_names):
        SEED_ALIASES[normalize_institution_name(_raw)] = _institution
del _entity, _institution, _raw


_FOREIGN_KEYWORDS = (
    "摩根",
    "高盛",
    "瑞银",
    "瑞信",
    "花旗",
    "贝莱德",
    "富达",
    "景顺",
    "安联",
    "野村",
    "大和",
    "德意志银行",
    "巴克莱",
    "淡马锡",
    "新加坡",
    "外资",
    "境外",
    "qfii",
)

# v2 外文机构识别（plan.md 第三部分）：英文后缀与品牌名归入境外投资机构，
# 使外文名单（DM Capital Limited、Point72 等）计入研究机构主指标。
_ENGLISH_RESEARCH_SUFFIXES = (
    "asset management",
    "investment management",
    "securities",
    "capital",
    "fund",
    "funds",
    "partners",
    "insurance",
    "advisors",
    "advisory",
    "investments",
    "investment",
    "global investors",
    "ventures",
    "equity",
    "private equity",
    "hong kong",
)
_ENGLISH_FOREIGN_BRANDS = (
    "point72",
    "ubs",
    "gic",
    "morganstanley",
    "citadel",
    "blackrock",
    "fidelity",
    "schroders",
    "temasek",
    "bridgewater",
    "twosigma",
    "millennium",
    "hsbc",
    "deutschebank",
    "barclays",
    "nomura",
    "daiwa",
    "mizuho",
    "creditsuisse",
    "goldmansachs",
)


def infer_institution_type(normalized_alias: str) -> str:
    """Map a normalized alias to one of the fixed institution types."""

    value = normalized_alias.lower()
    # warming v2: a bank is eligible only when the participant context names
    # a research department, and a trust requires an explicit investment/
    # research role.  The entity type alone therefore stays ``other``; the
    # occurrence-level eligibility classifier makes the contextual decision.
    if "银行" in value or re.search(r"\bbank\b", value):
        return "other"
    if "信托" in value or re.search(r"\btrust\b", value):
        return "other"
    if any(keyword in value for keyword in _FOREIGN_KEYWORDS):
        return "foreign_institution"
    if any(keyword in value for keyword in _ENGLISH_RESEARCH_SUFFIXES):
        return "foreign_institution"
    if value in _ENGLISH_FOREIGN_BRANDS:
        return "foreign_institution"
    if "证券" in value or "投行" in value:
        return "brokerage"
    if "保险" in value or "人寿" in value:
        return "insurance"
    if (
        "资产管理" in value
        or value.endswith(("资管", "资产", "资本"))
        or value.startswith("资管")
    ):
        return "asset_management"
    if any(
        keyword in value
        for keyword in (
            "私募",
            "创投",
            "创业投资",
            "风险投资",
            "投资管理",
            "投资合伙企业",
        )
    ) or value.endswith(("投资", "资本管理")):
        return "private_fund"
    if "证券投资基金" in value or value.endswith(
        ("基金", "基金管理")
    ):
        return "public_fund"
    return "other"


def levenshtein(a: str, b: str) -> int:
    """Classic Levenshtein distance used only for needs_review candidates."""

    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (char_a != char_b),
                )
            )
        previous = current
    return previous[-1]


def find_needs_review_candidates(
    normalized_alias: str,
    existing_aliases: Iterable[str],
    *,
    max_distance: int = 2,
) -> list[str]:
    """Fuzzy-similar aliases; callers must treat them as review candidates
    only and must never auto-merge them into an existing entity."""

    return sorted(
        {
            alias
            for alias in existing_aliases
            if alias != normalized_alias
            and levenshtein(normalized_alias, alias) <= max_distance
        }
    )


@dataclass(frozen=True, slots=True)
class InstitutionReviewCandidate:
    """A name that could not be safely merged (plan.md 12.1)."""

    raw_name: str
    normalized_alias: str
    existing_institution_id: str
    existing_canonical_name: str
    reason: str  # name_conflict | fuzzy


class InstitutionRegistry:
    """Storage-backed conservative institution resolver."""

    def __init__(self, storage: Storage, *, now: datetime | None = None) -> None:
        self.storage = storage
        self.now = now or datetime.now(SHANGHAI_TZ)
        self.needs_review: list[InstitutionReviewCandidate] = []
        self.created_count = 0

    @staticmethod
    def normalize(raw: str) -> str:
        return normalize_institution_name(raw)

    def resolve(self, raw: str) -> Institution:
        """Resolve one raw institution mention to a persisted entity.

        Exact alias matches (seed or previously persisted) win.  Unknown
        names create a conservative ``needs_review`` entity with a stable
        hash-derived id; no fuzzy merging is ever performed.
        """

        alias = self.normalize(raw)
        if not alias:
            raise ValueError("机构名称规范化后为空")

        # 种子别名优先于历史 needs_review 实体（plan.md 12.1 保守归一）：
        # 重算会累积裸名实体（如“国泰海通”），若先查库会命中旧实体而
        # 无法升级为种子 canonical/类型（v2 里程碑 4 评估发现的类型偏差）。
        seed = SEED_ALIASES.get(alias)
        if seed is not None:
            self.storage.upsert_institution(seed, self.now)
            self.storage.upsert_institution_alias(
                InstitutionAlias(alias, seed.institution_id, "seed")
            )
            return seed

        matched = self.storage.resolve_institution_alias(alias)
        if matched is not None:
            institution = self.storage.get_institution(matched.institution_id)
            if institution is not None:
                self._check_name_conflict(raw, alias, institution)
                # 历史 needs_review 实体按当前推断刷新类型（重算累积的
                # 裸名实体类型可能过时——v2 里程碑 4 类型偏差修复）。
                if institution.verification_status == "needs_review":
                    inferred = infer_institution_type(alias)
                    if inferred != institution.institution_type:
                        updated = replace(
                            institution, institution_type=inferred
                        )
                        self.storage.upsert_institution(updated, self.now)
                        institution = updated
                return institution

        institution_id = "inst:" + sha1(
            ("institution|" + alias).encode("utf-8")
        ).hexdigest()[:16]
        existing = self.storage.get_institution(institution_id)
        if existing is not None:
            self.storage.upsert_institution_alias(
                InstitutionAlias(alias, institution_id, "exact_rule")
            )
            self._check_name_conflict(raw, alias, existing)
            return existing

        group_id = GROUP_ALIASES.get(alias, institution_id)
        institution = Institution(
            institution_id=institution_id,
            canonical_name=_display_name(raw),
            group_id=group_id,
            institution_type=infer_institution_type(alias),
            verification_status="needs_review",
        )
        self.created_count += 1
        self.storage.upsert_institution(institution, self.now)
        self.storage.upsert_institution_alias(
            InstitutionAlias(alias, institution_id, "exact_rule")
        )
        return institution

    def _check_name_conflict(
        self, raw: str, alias: str, institution: Institution
    ) -> None:
        """Flag canonical-name mismatches for the same normalized alias.

        The entity is still returned (single-alias policy); the conflict is
        surfaced as a ``needs_review`` candidate instead of auto-merging.
        Only legal-form raw names (containing 公司/合伙) are compared so that
        short-name mentions never spam the review list.
        """

        raw_identity = _identity(raw)
        canonical_identity = _identity(institution.canonical_name)
        if raw_identity != canonical_identity and (
            "公司" in raw or "合伙" in raw
        ):
            self.needs_review.append(
                InstitutionReviewCandidate(
                    raw_name=_display_name(raw),
                    normalized_alias=alias,
                    existing_institution_id=institution.institution_id,
                    existing_canonical_name=institution.canonical_name,
                    reason="name_conflict",
                )
            )

    def fuzzy_candidates(
        self, raw: str, existing_aliases: Iterable[str], *, max_distance: int = 2
    ) -> list[InstitutionReviewCandidate]:
        """Surface fuzzy matches as review candidates without merging."""

        alias = self.normalize(raw)
        return [
            InstitutionReviewCandidate(
                raw_name=_display_name(raw),
                normalized_alias=candidate,
                existing_institution_id="",
                existing_canonical_name=candidate,
                reason="fuzzy",
            )
            for candidate in find_needs_review_candidates(
                alias, existing_aliases, max_distance=max_distance
            )
        ]


_MEDIA_KEYWORDS = (
    "日报",
    "晚报",
    "时报",
    "晨报",
    "周刊",
    "月刊",
    "电视台",
    "广播",
    "杂志社",
    "传媒",
    "新媒体",
    "通讯社",
    "财经网",
    "证券时报",
    "上海证券报",
    "中国证券报",
)


def participant_qualifies(raw: str) -> bool:
    """plan.md 12.2: media, individuals and generic investor labels never
    count towards independent institution breadth."""

    value = normalize_institution_name(raw)
    if not value:
        return False
    if any(keyword in value for keyword in _MEDIA_KEYWORDS):
        return False
    if value in {
        "个人投资者",
        "投资者",
        "个人",
        "自然人",
        "本公司",
        "公司",
        "共同基金",
        "机构投资者",
    }:
        return False
    if value.endswith(("先生", "女士", "经理", "总", "董事长", "总监")):
        return False
    return True
