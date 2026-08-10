from __future__ import annotations

import re

from .models import StockMention


TEMPLATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "融资融券模板稿",
        re.compile(r"获融资买入|获融资卖出|融资余额|融资净买入|融资净卖出"),
    ),
    (
        "ETF资金模板稿",
        re.compile(r"持仓该股ETF资金|ETF资金.{0,12}(?:净流入|净流出)"),
    ),
    (
        "固定格式资金流稿",
        re.compile(r"主力资金.{0,20}(?:净流入|净流出)|主力净流(?:入|出)"),
    ),
    (
        "股东户数模板稿",
        re.compile(r"股东户数|股东总户数"),
    ),
    (
        "批量机构调研稿",
        re.compile(r"机构调研记录|(?:证券|基金|资管|投资者).{0,18}调研我司|召开.{0,12}调研"),
    ),
    (
        "大宗交易模板稿",
        re.compile(r"发生.{0,12}大宗交易|大宗交易.{0,16}成交"),
    ),
)


# Most listed brokers include “证券” in their short name. These are the
# notable listed-broker names which do not; keeping the exception list short
# makes the rule conservative and avoids classifying every financial company as
# a broker.
BROKERAGE_SPECIAL_NAMES = frozenset(
    {
        "中信建投",
        "中国银河",
        "中金公司",
        "国泰海通",
        "国联民生",
        "第一创业",
        "东方财富",
    }
)

# These words are specific enough to show that a named brokerage is acting as
# an analyst or research publisher. Broad words such as “表示” and “预计” are
# intentionally excluded because a broker can use them in reporting its own
# business.
BROKERAGE_RESEARCH_CUE_PATTERN = re.compile(
    r"研报|分析师|评级|投资建议|给予|维持|首次覆盖|目标价|买入|增持|推荐|看好|"
    r"上调|下调|调高|调低|唱多|唱空|跑赢|优于大市"
)


def is_brokerage_stock(stock: StockMention) -> bool:
    """Return whether a stock is a listed brokerage for this narrow filter."""

    name = re.sub(r"\s+", "", stock.name)
    return name.endswith("证券") or name in BROKERAGE_SPECIAL_NAMES


def _brokerage_aliases(stock: StockMention) -> tuple[str, ...]:
    """Return names likely used for the brokerage in article text."""

    name = re.sub(r"\s+", "", stock.name)
    aliases = [name]
    if not name.endswith("证券"):
        aliases.append(f"{name}证券")
    return tuple(dict.fromkeys(aliases))


def is_brokerage_research_mention(
    stock: StockMention,
    *,
    title: str,
    summary: str = "",
    body_text: str = "",
) -> bool:
    """Identify the high-confidence case where a brokerage is a rater, not news.

    The broker name and a research/ratings cue must occur within 48 characters.
    We inspect the title, summary, and only the leading body text because that is
    where the article's subject and attribution are normally introduced.
    """

    if not is_brokerage_stock(stock):
        return False

    contexts = (title, summary, body_text[:1600])
    for context in contexts:
        if not context:
            continue
        for alias in _brokerage_aliases(stock):
            for match in re.finditer(re.escape(alias), context):
                following_text = context[match.end() : match.end() + 48]
                if BROKERAGE_RESEARCH_CUE_PATTERN.search(following_text):
                    return True
    return False


def filter_brokerage_research_mentions(
    stocks: tuple[StockMention, ...] | list[StockMention],
    *,
    title: str,
    summary: str = "",
    body_text: str = "",
) -> tuple[StockMention, ...]:
    """Remove brokerages that are explicitly acting as research/ratings sources.

    Other stocks in the same article are retained, including the rated stock.
    Non-matching and ambiguous brokerage mentions are kept by design.
    """

    return tuple(
        stock
        for stock in stocks
        if not is_brokerage_research_mention(
            stock,
            title=title,
            summary=summary,
            body_text=body_text,
        )
    )


def template_filter_reason(title: str, summary: str = "") -> str | None:
    haystack = f"{title}\n{summary}"
    for reason, pattern in TEMPLATE_PATTERNS:
        if pattern.search(haystack):
            return reason
    return None


# 明显的垃圾/纯走势类提问。纯走势/庄家/盘口问题只有在不包含经营、
# 财务、治理或重大事项语义时才被过滤，避免误伤基本面讨论。
JUNK_QUESTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("纯走势/庄家/盘口", re.compile(r"(?:庄家|主力|游资|盘口|拉升|打压|出货|吸筹|洗盘|砸盘|诱多|诱空|挂单|买卖盘|K线|分时)")),
    ("垃圾内容", re.compile(r"(?:涨停|跌停|翻倍|暴涨|暴跌|梭哈|满仓干|加仓干|稳赚|内幕|荐股|加群|私聊|V信|微信号|扣扣|红包)")),
    ("无意义重复", re.compile(r"(.)\1{9,}")),
)

FUNDAMENTAL_SEMANTICS_PATTERN = re.compile(
    r"(?:业绩|利润|营收|收入|成本|毛利率|净利率|分红|派息|回购|减持|增持|订单|合同|中标|产能|产量|"
    r"产品|研发|专利|客户|供应商|股东|股权|重组|收购|并购|出售|投资|扩产|诉讼|仲裁|处罚|风险|"
    r"管理层|董事会|监事会|治理|募集资金|募投|担保|质押|解禁|分红方案|业绩预告|业绩快报|经营|"
    r"现金流|负债|存货|应收账款|商誉|减值|税率|补贴|政策|中标|合作|签约|投产|量产|交付)"
)


def interaction_noise_reason(question: str) -> str | None:
    """Return the filter reason for empty/junk/pure price-action questions."""

    text = (question or "").strip()
    if not text:
        return "空内容"
    if len(text) < 4:
        return "空内容"
    for reason, pattern in JUNK_QUESTION_PATTERNS:
        if pattern.search(text):
            if reason == "纯走势/庄家/盘口" and FUNDAMENTAL_SEMANTICS_PATTERN.search(text):
                continue
            return reason
    return None
