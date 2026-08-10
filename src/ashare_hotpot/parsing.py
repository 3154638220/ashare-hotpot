from __future__ import annotations

import html as html_module
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from .config import SHANGHAI_TZ
from .filtering import filter_brokerage_research_mentions
from .models import (
    ArticleCandidate,
    InteractionRecord,
    ParsedArticle,
    ResearchCandidate,
    StockMention,
)


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
    candidates: list[str] = []
    for encoding in encodings:
        normalized = encoding.lower().replace("gbk", "gb18030")
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
        try:
            return content.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    # Some list pages (e.g. yuanchuang.10jqka.com.cn pagination) declare
    # gbk/gb18030 but contain a few invalid byte sequences, so the strict
    # pass above fails and the old fallback produced a fully garbled page.
    # A lenient pass in the declared encoding keeps the Chinese text intact;
    # only the broken bytes become U+FFFD.
    lenient_seen: set[str] = set()
    for encoding in candidates + ["utf-8", "gb18030"]:
        if encoding in lenient_seen:
            continue
        lenient_seen.add(encoding)
        try:
            return content.decode(encoding, errors="replace")
        except LookupError:
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
    provider_key: str = "ths",
    provider_name: str = "同花顺",
    content_type: str = "新闻",
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
                provider_key=provider_key,
                provider_name=provider_name,
                content_type=content_type,
            )
        )
        seen.add(url)
    return candidates


def _epoch_ms(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


IRM_QUESTION_URL_TEMPLATE = "http://irm.cninfo.com.cn/ircs/question/questionDetail?questionId={question_id}"


def parse_irm_page(
    payload: dict[str, object],
    *,
    source_key: str,
    source_name: str,
    now: datetime,
) -> tuple[list[InteractionRecord], int]:
    """Parse one page of the 深交所互动易 full-market latest Q&A stream."""

    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("互动易接口返回结构异常")
    records: list[InteractionRecord] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("indexId") or item.get("esId") or "").strip()
        code = str(item.get("stockCode") or "").strip()
        question = str(item.get("mainContent") or "").strip()
        if not question_id or not code or not question:
            continue
        pub_ms = _epoch_ms(item.get("pubDate"))
        if pub_ms is None:
            continue
        question_time = datetime.fromtimestamp(pub_ms / 1000, tz=SHANGHAI_TZ)
        reply = str(item.get("attachedContent") or "").strip() or None
        reply_time = None
        reply_ms = _epoch_ms(item.get("attachedPubDate"))
        if reply_ms is not None:
            reply_time = datetime.fromtimestamp(reply_ms / 1000, tz=SHANGHAI_TZ)
        trade = item.get("trade")
        industry_tags = tuple(
            sorted({str(tag).strip() for tag in trade if str(tag).strip()})
            if isinstance(trade, list)
            else ()
        )
        records.append(
            InteractionRecord(
                record_id=f"irm:{question_id}",
                platform_key=source_key,
                platform_name=source_name,
                code=code,
                stock_name=str(item.get("companyShortName") or code).replace(" ", "").strip() or code,
                question=question,
                question_time=question_time,
                question_url=IRM_QUESTION_URL_TEMPLATE.format(question_id=question_id),
                reply=reply,
                reply_time=reply_time,
                industry_tags=industry_tags,
            )
        )
    try:
        total = int(float(str(payload.get("totalRecord") or 0)))
    except (TypeError, ValueError):
        total = 0
    return records, total


SSE_QUESTION_URL_TEMPLATE = "https://sns.sseinfo.com/qadetail.do?weiboId={question_id}"
SSE_FEEDS_BASE_URL = "https://sns.sseinfo.com/ajax/feeds.do"
SSE_AVATAR_CODE_PATTERN = re.compile(
    r"/company/(?P<code>\d{6})\.(?:png|jpg|jpeg|gif)", re.IGNORECASE
)
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn"


def parse_irm_ircs_page(
    payload: dict[str, object],
    *,
    source_key: str,
    source_name: str,
    provider_key: str,
    provider_name: str,
    now: datetime,
    base_url: str,
) -> tuple[list[ResearchCandidate], int]:
    """Parse one page of the 互动易投资者关系活动流 (``searchTypes=4``).

    The API is public and paginated; every row is a 投资者关系活动记录 with an
    attachment PDF on ``static.cninfo.com.cn``.  Any unexpected payload shape
    (identity page, gateway error, structure change) raises
    :class:`RuntimeError` so the caller fails closed and never fabricates an
    empty board.
    """

    results = payload.get("results")
    if not isinstance(results, list):
        raise RuntimeError("互动易投资者关系接口返回结构异常")
    candidates: list[ResearchCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index_id = str(item.get("indexId") or item.get("esId") or "").strip()
        title = str(item.get("mainContent") or "").strip()
        code = str(item.get("stockCode") or "").strip()
        pub_ms = _epoch_ms(item.get("pubDate"))
        if not index_id or not title or not code or pub_ms is None:
            continue
        attachment_url = str(item.get("attachmentUrl") or "").strip()
        document_url = (
            urljoin(CNINFO_STATIC_BASE + "/", attachment_url)
            if attachment_url
            else None
        )
        attachment_type = (
            str(item.get("filetype") or "").strip().upper() or None
        )
        published_at = datetime.fromtimestamp(pub_ms / 1000, tz=SHANGHAI_TZ)
        stock_name = str(item.get("companyShortName") or code).strip() or code
        candidates.append(
            ResearchCandidate(
                document_id=f"irm_ircs:{index_id}",
                provider_key=provider_key,
                provider_name=provider_name,
                kind="research_activity",
                source_url=base_url,
                document_url=document_url,
                title=title,
                published_at=published_at,
                stock_codes=(code,) if is_a_share_code(code) else (),
                stock_names={code: stock_name} if code else {},
                attachment_type=attachment_type,
                description=title,
            )
        )
    try:
        total = int(float(str(payload.get("totalRecord") or 0)))
    except (TypeError, ValueError):
        total = 0
    return candidates, total


def parse_sse_relative_time(value: str, now: datetime) -> datetime | None:
    """Convert the e互动 relative labels into an absolute Shanghai time."""

    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI_TZ)
    value = value.strip()
    if not value:
        return None
    if value == "刚刚":
        return now
    match = re.fullmatch(r"(\d+)\s*(分钟|小时|天|周)前", value)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "分钟":
            return now - timedelta(minutes=amount)
        if unit == "小时":
            return now - timedelta(hours=amount)
        if unit == "天":
            return now - timedelta(days=amount)
        return now - timedelta(weeks=amount)
    match = re.fullmatch(r"昨天\s*(\d{1,2}):(\d{2})", value)
    if match:
        try:
            return datetime(
                now.year,
                now.month,
                now.day,
                int(match.group(1)),
                int(match.group(2)),
                tzinfo=SHANGHAI_TZ,
            ) - timedelta(days=1)
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", value)
    if match:
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                tzinfo=SHANGHAI_TZ,
            )
        except ValueError:
            return None
    return None


SSE_STOCK_MENTION_PATTERN = re.compile(r":([\u4e00-\u9fa5A-Za-z0-9]+)\((\d{6})\)")


def parse_sse_feed(
    html: str,
    *,
    source_key: str,
    source_name: str,
    now: datetime,
) -> list[InteractionRecord]:
    """Parse one page of the 上证e互动 HTML feed.

    The feed serves both ``type=10`` (latest questions) and ``type=11``
    (latest replies) with the same markup; the reply block is present only
    for answered questions.
    """

    soup = BeautifulSoup(html, "html.parser")
    records: list[InteractionRecord] = []
    seen: set[str] = set()
    for item_node in soup.select(".m_feed_item"):
        item_id = ""
        for attr in ("id", "data-id"):
            value = str(item_node.get(attr, "")).strip()
            if value.startswith("item-"):
                item_id = value[5:]
                break
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)

        question_node = item_node.select_one(".m_feed_detail.m_qa_detail .m_feed_txt")
        if question_node is None:
            question_node = item_node.select_one(".m_feed_txt")
        if question_node is None:
            continue
        question_text = question_node.get_text(" ", strip=True)
        mention = SSE_STOCK_MENTION_PATTERN.search(question_text)
        if mention is None:
            continue
        code = mention.group(2)
        if not is_a_share_code(code):
            continue
        stock_name = mention.group(1)
        question = SSE_STOCK_MENTION_PATTERN.sub("", question_text).strip(" :：")

        time_node = item_node.select_one(".m_feed_detail.m_qa_detail .m_feed_from span")
        if time_node is None:
            time_node = item_node.select_one(".m_feed_from span")
        question_time = (
            parse_sse_relative_time(time_node.get_text(" ", strip=True), now)
            if time_node is not None
            else None
        )
        if question_time is None:
            continue

        answer_node = item_node.select_one(".m_feed_detail.m_qa .m_feed_txt")
        reply: str | None = None
        reply_time: datetime | None = None
        if answer_node is not None:
            reply = answer_node.get_text(" ", strip=True) or None
            answer_time_node = item_node.select_one(".m_feed_detail.m_qa .m_feed_from span")
            if answer_time_node is not None:
                reply_time = parse_sse_relative_time(
                    answer_time_node.get_text(" ", strip=True), now
                )

        records.append(
            InteractionRecord(
                record_id=f"sse:{item_id}",
                platform_key=source_key,
                platform_name=source_name,
                code=code,
                stock_name=stock_name,
                question=question,
                question_time=question_time,
                question_url=SSE_QUESTION_URL_TEMPLATE.format(question_id=item_id),
                reply=reply,
                reply_time=reply_time,
            )
        )
    return records


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


def _cninfo_stock_codes(item: dict[str, object]) -> tuple[str, ...]:
    """Collect all A-share codes referenced by one 巨潮 list item."""

    codes: set[str] = set()
    for raw in (item.get("secCode"), item.get("secCodeList")):
        for code in str(raw or "").split(","):
            code = code.strip()
            if is_a_share_code(code):
                codes.add(code)
    return tuple(sorted(codes))


def _cninfo_stock_names(item: dict[str, object]) -> dict[str, str]:
    """Map each A-share code referenced by one 巨潮 list item to its short
    name (``secName``/``tileSecName`` for the primary code and the
    comma-separated ``secCodeList``/``secNameList`` pairs when present)."""

    names: dict[str, str] = {}
    sec_code = str(item.get("secCode") or "").strip()
    if sec_code and is_a_share_code(sec_code):
        name = str(item.get("secName") or item.get("tileSecName") or "").strip()
        if name and name != sec_code:
            names[sec_code] = name
    listed_codes = [
        code.strip()
        for code in str(item.get("secCodeList") or "").split(",")
        if code.strip()
    ]
    listed_names = [
        name.strip()
        for name in str(item.get("secNameList") or "").split(",")
        if name.strip()
    ]
    for code, name in zip(listed_codes, listed_names):
        if is_a_share_code(code) and name and name != code:
            names[code] = name
    return names


def parse_cninfo_page(
    payload: dict[str, object],
    *,
    source_key: str,
    source_name: str,
    provider_key: str,
    provider_name: str,
    kind: str,
    now: datetime,
    base_url: str,
) -> tuple[list[ResearchCandidate], int]:
    """Parse one page of the 巨潮资讯 announcement/调研 list API.

    The API serves both the full-text announcement stream
    (``hisAnnouncement/query``) and the 调研/投资者关系 stream
    (``disclosure``) with the same JSON shape.  Any unexpected payload shape
    (identity page, structure change, gateway error) raises
    :class:`RuntimeError` so the caller can fail closed.
    """

    announcements = payload.get("announcements")
    if not isinstance(announcements, list):
        raise RuntimeError("巨潮接口返回结构异常")
    candidates: list[ResearchCandidate] = []
    for item in announcements:
        if not isinstance(item, dict):
            continue
        announcement_id = str(item.get("announcementId") or "").strip()
        title = str(
            item.get("announcementTitle") or item.get("shortTitle") or ""
        ).strip()
        time_ms = _epoch_ms(item.get("announcementTime"))
        if not announcement_id or not title or time_ms is None:
            continue
        published_at = datetime.fromtimestamp(time_ms / 1000, tz=SHANGHAI_TZ)
        adjunct_url = str(item.get("adjunctUrl") or "").strip()
        document_url = (
            urljoin(CNINFO_STATIC_BASE + "/", adjunct_url) if adjunct_url else None
        )
        attachment_type = (
            str(item.get("adjunctType") or "").strip().upper() or None
        )
        candidates.append(
            ResearchCandidate(
                document_id=f"cninfo:{announcement_id}",
                provider_key=provider_key,
                provider_name=provider_name,
                kind=kind,
                source_url=base_url,
                document_url=document_url,
                title=title,
                published_at=published_at,
                stock_codes=_cninfo_stock_codes(item),
                stock_names=_cninfo_stock_names(item),
                attachment_type=attachment_type,
                description=str(item.get("announcementContent") or "").strip(),
            )
        )
    try:
        total = int(float(str(payload.get("totalAnnouncement") or 0)))
    except (TypeError, ValueError):
        total = 0
    return candidates, total


def parse_jsonp_payload(text: str, *, source_label: str) -> object:
    """Strip a JSONP wrapper (``callback({...})``) and return the object.

    Raises :class:`RuntimeError` for non-JSONP / invalid JSON / non-object
    payloads so the caller can fail closed instead of treating an error page
    as an empty board.
    """

    cleaned = (text or "").strip()
    start = cleaned.find("(")
    end = cleaned.rfind(")")
    if start < 0 or end <= start:
        raise RuntimeError(f"{source_label}接口返回的不是 JSONP")
    body = cleaned[start + 1 : end]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{source_label}接口返回非法 JSON：{exc}") from exc
    if isinstance(payload, (dict, list)):
        return payload
    raise RuntimeError(f"{source_label}接口返回结构异常")


def parse_sse_announcement_page(
    payload: object,
    *,
    source_key: str,
    source_name: str,
    provider_key: str,
    provider_name: str,
    kind: str,
    now: datetime,
    base_url: str,
) -> tuple[list[ResearchCandidate], int]:
    """Parse one page of the 上交所公司公告 JSONP API.

    Response shape (locked by fixture ``sse_announcement_page.json``):
    ``pageHelp.data`` is a list of single-item lists; each item carries
    ``ORG_BULLETIN_ID`` / ``TITLE`` / ``SECURITY_CODE`` / ``SECURITY_NAME`` /
    ``SSEDATE`` / ``URL``.  ``success=false`` (gateway/rate-limit error),
    missing ``pageHelp`` or a malformed ``data`` shape raises
    :class:`RuntimeError` so the sync service fails closed.
    """

    if not isinstance(payload, dict):
        raise RuntimeError("上交所公告接口返回结构异常")
    if str(payload.get("success") or "").lower() == "false":
        error = str(payload.get("error") or "未知错误")[:200]
        raise RuntimeError(f"上交所公告接口返回失败：{error}")
    page_help = payload.get("pageHelp")
    if not isinstance(page_help, dict):
        raise RuntimeError("上交所公告接口结构异常")
    rows = page_help.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("上交所公告接口结构异常")
    candidates: list[ResearchCandidate] = []
    for row in rows:
        inner = row if isinstance(row, list) else [row]
        for item in inner:
            if not isinstance(item, dict):
                continue
            org_id = str(item.get("ORG_BULLETIN_ID") or "").strip()
            title = str(item.get("TITLE") or "").strip()
            date_str = str(item.get("SSEDATE") or "").strip()
            code = str(item.get("SECURITY_CODE") or "").strip()
            if not org_id or not title or not date_str:
                continue
            try:
                published_at = datetime.strptime(
                    date_str, "%Y-%m-%d"
                ).replace(tzinfo=SHANGHAI_TZ)
            except ValueError:
                continue
            url_path = str(item.get("URL") or "").strip()
            document_url = (
                urljoin("https://www.sse.com.cn/", url_path.lstrip("/"))
                if url_path
                else None
            )
            attachment_type = (
                "PDF"
                if document_url and document_url.lower().endswith(".pdf")
                else None
            )
            stock_codes = (code,) if code else ()
            stock_name = str(item.get("SECURITY_NAME") or "").strip()
            candidates.append(
                ResearchCandidate(
                    document_id=f"sse_ann:{org_id}",
                    provider_key=provider_key,
                    provider_name=provider_name,
                    kind=kind,
                    source_url=base_url,
                    document_url=document_url,
                    title=title,
                    published_at=published_at,
                    stock_codes=stock_codes,
                    stock_names={code: stock_name} if code and stock_name else {},
                    attachment_type=attachment_type,
                    description=str(
                        item.get("BULLETIN_TYPE_DESC") or ""
                    ).strip(),
                )
            )
    try:
        total = int(float(str(page_help.get("total") or 0)))
    except (TypeError, ValueError):
        total = 0
    return candidates, total


def parse_bse_announcement_page(
    payload: object,
    *,
    source_key: str,
    source_name: str,
    provider_key: str,
    provider_name: str,
    kind: str,
    now: datetime,
    base_url: str,
) -> tuple[list[ResearchCandidate], int]:
    """Parse one page of the 北交所上市公司公告 JSONP API.

    The official announcement page calls ``initDisclosureList.do``.  Its
    pagination is nested: ``data.content`` contains one wrapper for the BSE
    market, ``wrapper.disclosures`` contains every announcement on the page,
    and ``wrapper.totalElements`` is the announcement total used for manifest
    reconciliation.  The outer ``data.totalElements`` counts grouped rows and
    is deliberately not used as the document total.

    Malformed list items fail closed.  Silently skipping one would make the
    source manifest impossible to reconcile with the public list.
    """

    if not isinstance(payload, dict):
        raise RuntimeError("北交所公告接口返回结构异常")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("北交所公告接口返回结构异常")
    wrappers = data.get("content")
    if not isinstance(wrappers, list):
        raise RuntimeError("北交所公告接口返回结构异常")
    if not wrappers:
        return [], 0

    candidates: list[ResearchCandidate] = []
    total = 0
    for wrapper in wrappers:
        if not isinstance(wrapper, dict):
            raise RuntimeError("北交所公告接口返回结构异常")
        rows = wrapper.get("disclosures")
        if not isinstance(rows, list):
            raise RuntimeError("北交所公告接口返回结构异常")
        try:
            total += int(float(str(wrapper["totalElements"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("北交所公告接口返回结构异常") from exc

        for item in rows:
            if not isinstance(item, dict):
                raise RuntimeError("北交所公告接口返回结构异常")
            disclosure_code = str(item.get("disclosureCode") or "").strip()
            title = (
                str(item.get("disclosureTitle") or "").strip()
                + str(item.get("disclosurePostTitle") or "").strip()
            )
            date_str = str(item.get("publishDate") or "").strip()
            if not disclosure_code or not title or not date_str:
                raise RuntimeError("北交所公告接口返回结构异常")
            try:
                published_at = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=SHANGHAI_TZ
                )
            except ValueError as exc:
                raise RuntimeError("北交所公告接口返回结构异常") from exc

            code = str(item.get("companyCd") or "").strip()
            stock_name = str(item.get("companyName") or "").strip()
            document_path = str(item.get("destFilePath") or "").strip()
            document_url = (
                urljoin("https://www.bse.cn/", document_path.lstrip("/"))
                if document_path
                else None
            )
            file_ext = str(item.get("fileExt") or "").strip().upper()
            if not file_ext and document_url and "." in document_url:
                file_ext = document_url.rsplit(".", 1)[-1].upper()
            disclosure_type = str(item.get("disclosureType") or "").strip()
            disclosure_subtype = str(
                item.get("disclosureSubType") or ""
            ).strip()
            description = "/".join(
                part for part in (disclosure_type, disclosure_subtype) if part
            )
            candidates.append(
                ResearchCandidate(
                    document_id=f"bse_ann:{disclosure_code}",
                    provider_key=provider_key,
                    provider_name=provider_name,
                    kind=kind,
                    source_url=base_url,
                    document_url=document_url,
                    title=title,
                    published_at=published_at,
                    stock_codes=(code,) if code else (),
                    stock_names={code: stock_name} if code and stock_name else {},
                    attachment_type=file_ext or None,
                    description=description,
                )
            )
    return candidates, total


def _bse_performance_datetime(value: object) -> datetime | None:
    """Convert the 北交所 Java ``showDate`` epoch-ms object to a datetime."""

    if not isinstance(value, dict):
        return None
    try:
        epoch_ms = int(value.get("time") or 0)
    except (TypeError, ValueError):
        return None
    if epoch_ms <= 0:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=SHANGHAI_TZ)


def parse_bse_performance_page(
    payload: object,
    *,
    source_key: str,
    source_name: str,
    provider_key: str,
    provider_name: str,
    kind: str,
    now: datetime,
    base_url: str,
) -> tuple[list[ResearchCandidate], int]:
    """Parse one page of the 北交所业绩说明会/投资者关系活动 JSONP API.

    Response shape (locked by fixture ``bse_performance_page.json``):
    a single-element list whose ``[0].listInfo.content`` carries
    ``id`` / ``title`` / ``stockCode`` / ``stockName`` / ``showDate`` /
    ``beginTime`` / ``endTime`` / ``type`` / ``url1`` / ``uploadReport``.
    """

    if not isinstance(payload, list) or not payload or not isinstance(
        payload[0], dict
    ):
        raise RuntimeError("北交所业绩说明会接口返回结构异常")
    list_info = payload[0].get("listInfo")
    if not isinstance(list_info, dict):
        raise RuntimeError("北交所业绩说明会接口返回结构异常")
    content = list_info.get("content")
    if not isinstance(content, list):
        raise RuntimeError("北交所业绩说明会接口返回结构异常")
    candidates: list[ResearchCandidate] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        perf_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not perf_id or not title:
            continue
        published_at = _bse_performance_datetime(item.get("showDate"))
        if published_at is None:
            continue
        code = str(item.get("stockCode") or "").strip()
        stock_name = str(item.get("stockName") or "").strip()
        report_path = str(item.get("uploadReport") or "").strip()
        stream_url = str(item.get("url1") or "").strip()
        document_url = report_path if report_path else (stream_url or None)
        attachment_type = None
        if report_path:
            lowered = report_path.lower()
            if lowered.endswith(".pdf"):
                attachment_type = "PDF"
            elif lowered.endswith(".doc"):
                attachment_type = "DOC"
            elif lowered.endswith(".docx"):
                attachment_type = "DOCX"
        begin = str(item.get("beginTime") or "").strip()
        end = str(item.get("endTime") or "").strip()
        activity_type = str(item.get("type") or "").strip()
        description = " ".join(
            part
            for part in (
                f"{activity_type}",
                f"{begin}-{end}" if begin and end else "",
                f"直播：{stream_url}" if stream_url else "",
            )
            if part
        ).strip()
        candidates.append(
            ResearchCandidate(
                document_id=f"bse_perf:{perf_id}",
                provider_key=provider_key,
                provider_name=provider_name,
                kind=kind,
                source_url=base_url,
                document_url=document_url,
                title=title,
                published_at=published_at,
                stock_codes=(code,) if code else (),
                stock_names={code: stock_name} if code and stock_name else {},
                attachment_type=attachment_type,
                description=description,
            )
        )
    try:
        total = int(float(str(list_info.get("totalElements") or 0)))
    except (TypeError, ValueError):
        total = 0
    return candidates, total


def _sse_publish_title(text: str, attachment_name: str | None) -> str:
    """Prefer the attachment file name (without extension) as the title."""

    if attachment_name:
        name = re.sub(
            r"\.(docx?|pdf|xlsx?|txt)$", "", attachment_name, flags=re.IGNORECASE
        ).strip()
        if name:
            return name
    cleaned = re.sub(r"\s+", " ", text).strip(" ：:，,。")
    return cleaned[:200] or "上市公司发布"


def parse_sse_publish_feed(
    html: str,
    *,
    source_key: str,
    source_name: str,
    provider_key: str,
    provider_name: str,
    now: datetime,
) -> list[ResearchCandidate]:
    """Parse one page of the 上证e互动“上市公司发布” feed (type=30).

    Items are investor-relations activity records (调研/说明会/路演) with an
    optional attachment (PDF/DOC/DOCX).  The stock code is read from the
    company avatar URL; items without a code are kept as metadata-only.
    """

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[ResearchCandidate] = []
    seen: set[str] = set()
    for item_node in soup.select(".m_feed_item"):
        item_id = ""
        for attr in ("id", "data-id"):
            value = str(item_node.get(attr, "")).strip()
            if value.startswith("item-"):
                item_id = value[5:]
                break
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)

        txt_node = item_node.select_one(".m_feed_txt")
        if txt_node is None:
            continue
        text = txt_node.get_text(" ", strip=True)
        attachment = txt_node.select_one("a[href]")
        document_url: str | None = None
        attachment_name: str | None = None
        attachment_type: str | None = None
        if attachment is not None:
            href = str(attachment.get("href", "")).strip()
            if href.startswith(("http://", "https://")):
                document_url = href
            attachment_name = str(attachment.get_text(" ", strip=True)).strip()
            extension_match = re.search(
                r"\.([A-Za-z0-9]+)$", document_url or attachment_name or ""
            )
            attachment_type = (
                extension_match.group(1).upper() if extension_match else None
            )

        face_node = item_node.select_one(".m_feed_face")
        stock_name = ""
        code = ""
        if face_node is not None:
            name_node = face_node.select_one("p")
            stock_name = name_node.get_text(" ", strip=True) if name_node else ""
            avatar = face_node.select_one("img")
            if avatar is not None:
                avatar_match = SSE_AVATAR_CODE_PATTERN.search(
                    str(avatar.get("src", ""))
                )
                if avatar_match:
                    code = avatar_match.group("code")

        time_node = item_node.select_one(".m_feed_from span")
        published_at = (
            parse_sse_relative_time(time_node.get_text(" ", strip=True), now)
            if time_node is not None
            else None
        )
        if published_at is None:
            continue

        candidates.append(
            ResearchCandidate(
                document_id=f"ssepub:{item_id}",
                provider_key=provider_key,
                provider_name=provider_name,
                kind="research_activity",
                source_url=SSE_FEEDS_BASE_URL,
                document_url=document_url,
                title=_sse_publish_title(text, attachment_name),
                published_at=published_at,
                stock_codes=(code,) if is_a_share_code(code) else (),
                stock_names=(
                    {code: _clean_stock_name(stock_name, code)}
                    if code and stock_name and stock_name != code
                    else {}
                ),
                attachment_type=attachment_type,
                description=text,
            )
        )
    return candidates


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


def extract_body_text(html: str) -> str:
    """Extract the plain article body text from a detail page.

    Used by signal-feed sources (e.g. the 同花顺 company-interaction channel)
    so their company statements can be persisted as ``SourceDocument`` body
    text for the short-term signal pipeline.  Whitespace is normalised only;
    numbers, units, percentages and negation words are never altered.
    """

    return _article_body(BeautifulSoup(html, "html.parser")).get_text(
        " ", strip=True
    )


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
        if _is_newsroom_byline_anchor(anchor):
            continue
        name = _clean_stock_name(anchor.get_text(" ", strip=True), code)
        stocks.setdefault(code, name)
    return stocks


def _is_newsroom_byline_anchor(anchor: Tag) -> bool:
    """True when the anchor names the newsroom rather than article content.

    Articles on the 原创/独家公司互动 channel start with a fixed byline such as
    "同花顺（300033）金融研究中心08月05日讯…" where 同花顺 is linked as a
    stock.  That boilerplate line would otherwise count 同花顺 as a mention in
    every single interaction article and skew the heat ranking.
    """

    node: Tag | None = anchor
    for _ in range(4):
        if node is None:
            return False
        nxt = node.next_sibling
        if nxt is not None:
            return str(nxt).strip().startswith("金融研究中心")
        node = node.parent
    return False


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
        provider_key=candidate.provider_key,
        provider_name=candidate.provider_name,
        content_type=candidate.content_type,
        stocks=ordered_stocks,
        industry_tags=industry_tags,
    )
