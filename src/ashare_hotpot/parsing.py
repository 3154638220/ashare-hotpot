from __future__ import annotations

import html as html_module
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from .config import SHANGHAI_TZ
from .filtering import filter_brokerage_research_mentions
from .models import ArticleCandidate, ParsedArticle, StockMention


TIME_PATTERN = re.compile(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})")
FULL_TIME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?P<year>20\d{2})-(?P<month>\d{1,2})-(?P<day>\d{1,2})[ T](?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"),
    re.compile(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"),
)
SEQ_PATTERN = re.compile(r"/c(?P<seq>\d+)\.shtml", re.IGNORECASE)
CODE_IN_URL_PATTERN = re.compile(r"stockpage\.10jqka\.com\.cn/(?P<code>\d{6})(?:/|\b)", re.IGNORECASE)
ESCAPED_STOCK_PAIR_PATTERN = re.compile(
    r'(?:stockName|name)\\?"\s*:\s*\\?"(?P<name>[^"\\]{1,30})\\?".{0,180}?'
    r'(?:stockCode|code)\\?"\s*:\s*\\?"(?P<code>\d{6})\\?"',
    re.DOTALL,
)
ESCAPED_CODE_PATTERN = re.compile(r'data-code=\\?"(?P<code>\d{6})\\?"')


def decode_html(content: bytes, content_type: str = "") -> str:
    header_match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    encodings = [header_match.group(1)] if header_match else []
    head = content[:4096].decode("ascii", errors="ignore")
    meta_match = re.search(r"charset=[\"']?([\w-]+)", head, re.IGNORECASE)
    if meta_match:
        encodings.append(meta_match.group(1))
    encodings.extend(["utf-8", "gb18030"])
    seen: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower().replace("gbk", "gb18030")
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            return content.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def parse_list_datetime(value: str, now: datetime) -> datetime | None:
    match = TIME_PATTERN.search(value)
    if not match:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI_TZ)
    values = {key: int(number) for key, number in match.groupdict().items()}
    candidates: list[datetime] = []
    for year in (now.year, now.year - 1):
        try:
            candidates.append(datetime(year=year, tzinfo=SHANGHAI_TZ, **values))
        except ValueError:
            pass
    viable = [candidate for candidate in candidates if candidate <= now + timedelta(days=1)]
    return max(viable, default=None)


def parse_full_datetime(value: str) -> datetime | None:
    for pattern in FULL_TIME_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        parts = {key: int(number) for key, number in match.groupdict(default="0").items()}
        try:
            return datetime(tzinfo=SHANGHAI_TZ, **parts)
        except ValueError:
            continue
    return None


def canonicalize_url(url: str, base_url: str = "") -> str:
    resolved = urljoin(base_url, url.strip())
    parsed = urlparse(resolved)
    scheme = "https" if parsed.scheme in {"", "http", "https"} else parsed.scheme
    host = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", parsed.path)
    return urlunparse((scheme, host, path, "", "", ""))


def extract_seq(url: str, data_seq: str = "") -> str:
    if data_seq.strip().isdigit():
        return data_seq.strip()
    match = SEQ_PATTERN.search(url)
    return match.group("seq") if match else url


def _candidate_container(anchor: Tag) -> Tag:
    return anchor.find_parent("li") or anchor.find_parent(class_="list-con") or anchor.parent


def parse_list_page(
    html: str,
    *,
    source_key: str,
    source_name: str,
    base_url: str,
    now: datetime,
) -> list[ArticleCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.news-link")
    if not anchors:
        anchors = [
            anchor
            for anchor in soup.select(".list-con .arc-title a, ul.list .arc-title a")
            if anchor.get("href")
        ]
    candidates: list[ArticleCandidate] = []
    seen: set[str] = set()
    for anchor in anchors:
        href = str(anchor.get("href", "")).strip()
        if not href:
            continue
        url = canonicalize_url(href, base_url)
        if url in seen:
            continue
        container = _candidate_container(anchor)
        published_at = parse_list_datetime(container.get_text(" ", strip=True), now)
        if not published_at:
            continue
        title = str(anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
        if not title:
            continue
        summary_node = container.select_one(".arc-cont, .arc-content, .summary, p")
        summary = summary_node.get_text(" ", strip=True) if summary_node else ""
        candidates.append(
            ArticleCandidate(
                seq=extract_seq(url, str(anchor.get("data-seq", ""))),
                url=url,
                title=title,
                summary=summary,
                published_at=published_at,
                channel_key=source_key,
                channel_name=source_name,
            )
        )
        seen.add(url)
    return candidates


def is_a_share_code(code: str) -> bool:
    if not re.fullmatch(r"\d{6}", code):
        return False
    if code.startswith(("200", "900")):
        return False
    if code.startswith("6"):
        return True
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return True
    # Since 2025-10-09 all Beijing Stock Exchange listed shares use 920xxx.
    # Other 4xxxxx/8xxxxx values on 10jqka pages can be NEEQ, industry or concept codes.
    return code.startswith("920")


def _clean_stock_name(name: str, code: str) -> str:
    cleaned = html_module.unescape(name)
    cleaned = re.sub(r"[（(]\s*" + re.escape(code) + r"\s*[)）]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:，,[]【】")
    return cleaned[:40] or code


def _article_body(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    selectors = (
        ".news-content-parsed",
        ".news-content.article-content",
        ".article-content",
        ".main-text",
        ".art_main",
        "#content",
        "article",
    )
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            return node
    return soup


def _extract_stocks_from_dom(body: Tag | BeautifulSoup) -> dict[str, str]:
    stocks: dict[str, str] = {}
    for anchor in body.select("a[data-code], a[href*='stockpage.10jqka.com.cn/']"):
        code = str(anchor.get("data-code", "")).strip()
        if not code:
            match = CODE_IN_URL_PATTERN.search(str(anchor.get("href", "")))
            code = match.group("code") if match else ""
        data_type = str(anchor.get("data-type", "stock")).lower()
        if data_type not in {"", "stock"} or not is_a_share_code(code):
            continue
        name = _clean_stock_name(anchor.get_text(" ", strip=True), code)
        stocks.setdefault(code, name)
    return stocks


def _extract_industry_tags_from_dom(body: Tag | BeautifulSoup) -> tuple[str, ...]:
    """Extract article-level industry labels without treating them as stock codes."""

    tags: list[str] = []
    seen: set[str] = set()
    for anchor in body.select("a[data-type]"):
        data_type = str(anchor.get("data-type", "")).strip().lower()
        if data_type not in {"industry", "hy"} and "industry" not in data_type:
            continue
        tag = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip(" ：:，,[]【】")
        code = str(anchor.get("data-code", "")).strip()
        if code:
            tag = _clean_stock_name(tag, code)
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tuple(tags)


def _extract_stocks_from_embedded_data(html: str) -> dict[str, str]:
    stocks: dict[str, str] = {}
    unescaped = html.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\\"", '"')
    embedded_soup = BeautifulSoup(unescaped, "html.parser")
    stocks.update(_extract_stocks_from_dom(_article_body(embedded_soup)))
    for match in ESCAPED_STOCK_PAIR_PATTERN.finditer(html):
        code = match.group("code")
        if is_a_share_code(code):
            stocks.setdefault(code, _clean_stock_name(match.group("name"), code))
    if not stocks:
        for match in ESCAPED_CODE_PATTERN.finditer(html):
            code = match.group("code")
            if is_a_share_code(code):
                stocks.setdefault(code, code)
    return stocks


def _extract_source_name(soup: BeautifulSoup, html: str) -> str:
    source_node = soup.find(string=re.compile(r"来源[：:]"))
    if source_node:
        parent_text = source_node.parent.get_text(" ", strip=True) if source_node.parent else str(source_node)
        match = re.search(r"来源[：:]\s*([^\s|]{1,40})", parent_text)
        if match:
            return match.group(1).strip()
    for pattern in (
        re.compile(r'"media"\s*:\s*\{[^{}]{0,200}?"name"\s*:\s*"([^"]+)"'),
        re.compile(r'来源[：:]\s*</?[^>]*>?\s*([^<\s]{1,40})'),
    ):
        match = pattern.search(html)
        if match:
            return html_module.unescape(match.group(1)).strip()
    return "同花顺财经"


def _extract_precise_time(soup: BeautifulSoup, html: str) -> datetime | None:
    for selector in ("time", ".time", ".publish-time", "[class*='time']"):
        node = soup.select_one(selector)
        if node:
            parsed = parse_full_datetime(node.get_text(" ", strip=True))
            if parsed:
                return parsed
    return parse_full_datetime(html)


def parse_article_detail(candidate: ArticleCandidate, html: str) -> ParsedArticle:
    soup = BeautifulSoup(html, "html.parser")
    body = _article_body(soup)
    stocks = _extract_stocks_from_dom(body)
    if not stocks:
        stocks.update(_extract_stocks_from_embedded_data(html))
    published_at = _extract_precise_time(soup, html) or candidate.published_at
    extracted_stocks = tuple(StockMention(code, stocks[code]) for code in sorted(stocks))
    ordered_stocks = filter_brokerage_research_mentions(
        extracted_stocks,
        title=candidate.title,
        summary=candidate.summary,
        body_text=body.get_text(" ", strip=True),
    )
    industry_tags = _extract_industry_tags_from_dom(body)
    return ParsedArticle(
        seq=candidate.seq,
        url=candidate.url,
        title=candidate.title,
        summary=candidate.summary,
        published_at=published_at,
        channel_key=candidate.channel_key,
        channel_name=candidate.channel_name,
        source_name=_extract_source_name(soup, html),
        stocks=ordered_stocks,
        industry_tags=industry_tags,
    )
