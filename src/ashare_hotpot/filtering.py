from __future__ import annotations

import re


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


def template_filter_reason(title: str, summary: str = "") -> str | None:
    haystack = f"{title}\n{summary}"
    for reason, pattern in TEMPLATE_PATTERNS:
        if pattern.search(haystack):
            return reason
    return None

