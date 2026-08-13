from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ashare_hotpot.config import SHANGHAI_TZ
from ashare_hotpot.models import ArticleCandidate
from ashare_hotpot.parsing import (
    canonicalize_url,
    decode_html,
    is_a_share_code,
    parse_article_detail,
    parse_list_datetime,
    parse_list_page,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_decode_gbk_and_parse_list_page() -> None:
    source = (FIXTURES / "list_page.html").read_text(encoding="utf-8")
    content = source.encode("gb18030")
    html = decode_html(content, "text/html; charset=gbk")
    items = parse_list_page(
        html,
        source_key="companynews",
        source_name="公司资讯",
        base_url="https://stock.10jqka.com.cn/companynews_list/",
        now=datetime(2026, 8, 4, 19, 0, tzinfo=SHANGHAI_TZ),
    )
    assert [item.seq for item in items] == ["678663522", "678663520"]
    assert items[0].title == "长江证券：拟回购股份"
    assert items[0].summary == "公司拟使用自有资金回购股份。"
    assert items[0].url == "https://stock.10jqka.com.cn/20260804/c678663522.shtml"


def test_cross_year_list_datetime() -> None:
    now = datetime(2026, 1, 1, 9, 0, tzinfo=SHANGHAI_TZ)
    parsed = parse_list_datetime("12月31日 23:55", now)
    assert parsed == datetime(2025, 12, 31, 23, 55, tzinfo=SHANGHAI_TZ)


def _candidate(url: str = "https://stock.10jqka.com.cn/20260804/c1.shtml") -> ArticleCandidate:
    return ArticleCandidate(
        seq="1",
        url=url,
        title="测试文章",
        summary="摘要",
        published_at=datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ),
        channel_key="test",
        channel_name="测试栏目",
    )


def test_parse_next_article_extracts_only_a_share() -> None:
    html = (FIXTURES / "article_next.html").read_text(encoding="utf-8")
    article = parse_article_detail(_candidate(), html)
    assert [(stock.code, stock.name) for stock in article.stocks] == [("000783", "长江证券")]
    assert article.industry_tags == ("证券",)
    assert article.source_name == "上海证券报"
    assert article.published_at == datetime(2026, 8, 4, 18, 7, 54, tzinfo=SHANGHAI_TZ)


def test_parse_industry_research_article_preserves_explicit_industry_tag() -> None:
    html = (FIXTURES / "ths_industry_research_article.html").read_text(encoding="utf-8")
    candidate = ArticleCandidate(
        seq="industry-001",
        url="https://stock.10jqka.com.cn/20260812/cindustry001.shtml",
        title="Industry research fixture",
        summary="Industry research body",
        published_at=datetime(2026, 8, 12, 17, 0, tzinfo=SHANGHAI_TZ),
        channel_key="industry_research",
        channel_name="行业研究",
    )
    article = parse_article_detail(candidate, html)
    assert article.channel_key == "industry_research"
    assert article.industry_tags == ("证券",)
    assert [stock.code for stock in article.stocks] == ["000001"]


def test_parse_live_style_linked_concept_and_fixed_text_concepts() -> None:
    candidate = ArticleCandidate(
        seq="concept",
        url="https://stock.10jqka.com.cn/20260812/cconcept.shtml",
        title="内房股走高，AI ASIC与存储产业景气",
        summary="",
        published_at=datetime(2026, 8, 12, 17, 0, tzinfo=SHANGHAI_TZ),
        channel_key="industry_research",
        channel_name="行业研究",
    )
    html = """
    <html><body><div class="news-content-parsed">
      明确终端本地AI算力为
      <a href="https://q.10jqka.com.cn/gn/detail/code/301558/">消费电子（881124）</a>
      升级主线。
    </div></body></html>
    """

    article = parse_article_detail(candidate, html)

    assert article.industry_tags == ()
    assert article.industry_concepts == (
        "消费电子",
        "房地产",
        "AI ASIC",
        "存储产业",
    )


def test_parse_industry_research_fixture_list_page() -> None:
    html = (FIXTURES / "ths_industry_research_list_page.html").read_text(encoding="utf-8")
    items = parse_list_page(
        html,
        source_key="industry_research",
        source_name="行业研究",
        base_url="https://stock.10jqka.com.cn/bkfy_list/",
        now=datetime(2026, 8, 12, 18, 0, tzinfo=SHANGHAI_TZ),
    )
    assert len(items) == 1
    assert items[0].channel_key == "industry_research"
    assert items[0].seq == "678790294"


def test_parse_legacy_article_and_beijing_stock() -> None:
    html = (FIXTURES / "article_legacy.html").read_text(encoding="utf-8")
    article = parse_article_detail(_candidate(), html)
    assert {stock.code for stock in article.stocks} == {"688001", "920047"}


def test_parse_article_skips_newsroom_byline_stock_link() -> None:
    html = """
    <html><body>
      <div class="news-content-parsed">
        <p><span><a class="link-wrapper link-blue"
          href="https://stockpage.10jqka.com.cn/300033">同花顺（300033）</a></span>
          金融研究中心08月05日讯，有投资者向
          <span><a class="link-wrapper link-blue"
          href="https://stockpage.10jqka.com.cn/000858">五粮液（000858）</a></span>
          提问， 大国浓香，和美五粮的广告语公司是否考虑过修改。
        </p>
      </div>
    </body></html>
    """
    article = parse_article_detail(_candidate(), html)
    assert [(stock.code, stock.name) for stock in article.stocks] == [("000858", "五粮液")]


def test_parse_article_keeps_real_tonghuashun_mention() -> None:
    html = """
    <html><body>
      <div class="news-content-parsed">
        <p>某公司产品与<span><a class="link-wrapper link-blue"
          href="https://stockpage.10jqka.com.cn/300033">同花顺（300033）</a></span>
          达成合作，业务进展顺利。</p>
      </div>
    </body></html>
    """
    article = parse_article_detail(_candidate(), html)
    assert [stock.code for stock in article.stocks] == ["300033"]


def test_parse_article_excludes_brokerage_as_rating_source() -> None:
    candidate = ArticleCandidate(
        seq="rating",
        url="https://stock.10jqka.com.cn/20260804/crating.shtml",
        title="中信证券：维持贵州茅台买入评级",
        summary="",
        published_at=datetime(2026, 8, 4, 18, 0, tzinfo=SHANGHAI_TZ),
        channel_key="test",
        channel_name="测试栏目",
    )
    html = """
    <html><body><div class="news-content-parsed">
      <a data-code="600030" data-type="stock">中信证券</a>
      <a data-code="600519" data-type="stock">贵州茅台</a>
    </div></body></html>
    """

    article = parse_article_detail(candidate, html)

    assert [(stock.code, stock.name) for stock in article.stocks] == [("600519", "贵州茅台")]


def test_a_share_code_filter() -> None:
    included = {"000001", "002594", "300750", "600519", "688001", "920001"}
    excluded = {"200002", "900901", "510300", "430047", "832000", "850105", "00700", "AAPL"}
    assert all(is_a_share_code(code) for code in included)
    assert not any(is_a_share_code(code) for code in excluded)
    assert canonicalize_url("http://stock.10jqka.com.cn/a?x=1#top") == "https://stock.10jqka.com.cn/a"
