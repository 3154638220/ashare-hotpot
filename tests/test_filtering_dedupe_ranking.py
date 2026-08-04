from __future__ import annotations

from datetime import datetime, timedelta

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.dedupe import Deduplicator, normalize_title
from ashare_hotpot.filtering import template_filter_reason
from ashare_hotpot.models import ParsedArticle, StockMention
from ashare_hotpot.ranking import RankingService


BASE_TIME = datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ)


def article(
    seq: str,
    title: str,
    stocks: tuple[StockMention, ...],
    *,
    minutes_ago: int = 0,
) -> ParsedArticle:
    return ParsedArticle(
        seq=seq,
        url=f"https://stock.10jqka.com.cn/20260804/c{seq}.shtml",
        title=title,
        summary="",
        published_at=BASE_TIME - timedelta(minutes=minutes_ago),
        channel_key="companynews",
        channel_name="公司资讯",
        source_name="测试来源",
        stocks=stocks,
    )


def test_template_filters_are_transparent() -> None:
    assert template_filter_reason("平安银行：8月3日获融资买入1000万元") == "融资融券模板稿"
    assert template_filter_reason("贵州茅台股东户数下降") == "股东户数模板稿"
    assert template_filter_reason("比亚迪发布新车型") is None


def test_deduplicate_near_titles_and_rank_once_per_event() -> None:
    stock_a = StockMention("000783", "长江证券")
    stock_b = StockMention("600519", "贵州茅台")
    articles = [
        article("1", "长江证券：拟斥资1亿元至2亿元回购股份", (stock_a,)),
        article("2", "【公告】长江证券拟斥资1亿元至2亿元回购股份", (stock_a,), minutes_ago=20),
        article("3", "长江证券与贵州茅台举行交流活动", (stock_a, stock_b), minutes_ago=60),
        article("4", "贵州茅台发布年度新品", (stock_b,), minutes_ago=90),
    ]
    events = Deduplicator(similarity_threshold=80).group(articles)
    assert len(events) == 3
    merged = next(event for event in events if len(event.articles) == 2)
    assert {item.seq for item in merged.articles} == {"1", "2"}

    rows = RankingService().build_rankings(events)
    assert rows[0].code == "600519" or rows[0].code == "000783"
    by_code = {row.code: row for row in rows}
    assert by_code["000783"].event_count == 2
    assert by_code["000783"].raw_article_count == 3
    assert by_code["600519"].event_count == 2
    assert by_code["600519"].raw_article_count == 2


def test_no_merge_without_shared_stock_or_outside_six_hours() -> None:
    stock_a = StockMention("000783", "长江证券")
    stock_b = StockMention("600519", "贵州茅台")
    articles = [
        article("1", "公司发布重大公告", (stock_a,)),
        article("2", "公司发布重大公告", (stock_b,), minutes_ago=10),
        article("3", "公司发布重大公告", (stock_a,), minutes_ago=7 * 60),
    ]
    assert len(Deduplicator().group(articles)) == 3
    assert normalize_title("【快讯】 公司：发布公告！") == "公司发布公告"

