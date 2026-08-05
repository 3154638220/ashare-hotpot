from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from .models import PopularityRankRow
from .parsing import is_a_share_code
from .sources import PoliteHttpClient


logger = logging.getLogger(__name__)

EASTMONEY_RANK_PAGE = "https://guba.eastmoney.com/rank/"
EASTMONEY_RANK_CURRENT_ENDPOINT = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
EASTMONEY_RANK_SURGE_ENDPOINT = "https://emappdata.eastmoney.com/stockrank/getAllHisRcList"
EASTMONEY_QUOTE_ENDPOINT = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EASTMONEY_QUOTE_UT = "f057cbcbce2a86e2866ab8877db1d059"
QUOTE_FIELDS = "f14,f3,f12,f2"

RANK_REQUEST_BODY: dict[str, object] = {
    "appId": "appId01",
    "globalId": "786e4c21-70dc-435a-93bb-38",
    "marketType": "",
    "pageNo": 1,
    "pageSize": 100,
}


def stock_rank_url(code: str) -> str:
    """Official per-stock popularity page link provided by the source site."""

    return f"{EASTMONEY_RANK_PAGE}stock?code={code}"


def _code_from_sc(sc: str) -> str | None:
    sc = sc.strip()
    if len(sc) < 6:
        return None
    code = sc[-6:]
    return code if is_a_share_code(code) else None


def _int_or_none(value: object) -> int | None:
    try:
        if value is None or value == "" or value == "-":
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    try:
        if value is None or value == "" or value == "-":
            return None
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _rank_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("东方财富人气榜接口返回结构异常")
    return [item for item in data if isinstance(item, dict)]


def parse_rank_rows(payload: dict[str, Any], *, with_change: bool) -> list[PopularityRankRow]:
    """Parse one official rank response into deduplicated A-share rows.

    Only the officially published rank and rank change are kept; the attention
    score itself is not public and is never reconstructed.
    """

    rows: list[PopularityRankRow] = []
    seen: set[str] = set()
    for item in _rank_records(payload):
        sc = str(item.get("sc") or "").strip()
        code = _code_from_sc(sc)
        if not code:
            continue
        if code in seen:
            continue
        rank = _int_or_none(item.get("rk"))
        if rank is None:
            raise RuntimeError("东方财富人气榜缺少有效排名")
        seen.add(code)
        rows.append(
            PopularityRankRow(
                rank=rank,
                code=code,
                name=code,
                change=_int_or_none(item.get("hrc")) if with_change else None,
                current_price=None,
                change_percent=None,
                url=stock_rank_url(code),
            )
        )
    if not rows:
        raise RuntimeError("东方财富人气榜返回空数据")
    return rows


def _collect_secids(payloads: list[dict[str, Any]]) -> list[str]:
    secids: list[str] = []
    seen: set[str] = set()
    for payload in payloads:
        for item in _rank_records(payload):
            sc = str(item.get("sc") or "").strip()
            code = _code_from_sc(sc)
            if not code:
                continue
            prefix = "0" if sc.startswith(("SZ", "BJ")) else "1"
            secid = f"{prefix}.{code}"
            if secid not in seen:
                seen.add(secid)
                secids.append(secid)
    return secids


def _quote_url(secids: list[str]) -> str:
    parameters = {
        "ut": EASTMONEY_QUOTE_UT,
        "fltt": "2",
        "invt": "2",
        "fields": QUOTE_FIELDS,
        "secids": ",".join(secids),
    }
    return f"{EASTMONEY_QUOTE_ENDPOINT}?{urlencode(parameters)}"


def parse_quotes(payload: dict[str, Any]) -> dict[str, tuple[str, float | None, float | None]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}
    diff = data.get("diff")
    if isinstance(diff, dict):
        records = [item for item in diff.values() if isinstance(item, dict)]
    elif isinstance(diff, list):
        records = [item for item in diff if isinstance(item, dict)]
    else:
        records = []
    quotes: dict[str, tuple[str, float | None, float | None]] = {}
    for item in records:
        code = str(item.get("f12") or "").strip()
        if not code:
            continue
        quotes[code] = (
            str(item.get("f14") or code),
            _float_or_none(item.get("f2")),
            _float_or_none(item.get("f3")),
        )
    return quotes


def _attach_quotes(
    rows: list[PopularityRankRow],
    quotes: dict[str, tuple[str, float | None, float | None]],
) -> list[PopularityRankRow]:
    return [
        PopularityRankRow(
            rank=row.rank,
            code=row.code,
            name=quotes[row.code][0] if row.code in quotes else row.name,
            change=row.change,
            current_price=quotes[row.code][1] if row.code in quotes else None,
            change_percent=quotes[row.code][2] if row.code in quotes else None,
            url=row.url,
        )
        for row in rows
    ]


def fetch_official_popularity(
    client: PoliteHttpClient,
) -> tuple[list[PopularityRankRow], list[PopularityRankRow]]:
    """Read the official popularity and surging boards at low frequency.

    Both boards must succeed: empty data, structurally changed responses or an
    identity-check page fail the whole read so a partial board is never shown.
    The quote lookup is supplementary and best-effort.
    """

    current_payload = client.post_json(EASTMONEY_RANK_CURRENT_ENDPOINT, RANK_REQUEST_BODY)
    surge_payload = client.post_json(EASTMONEY_RANK_SURGE_ENDPOINT, RANK_REQUEST_BODY)

    popularity = parse_rank_rows(current_payload, with_change=False)
    surging = parse_rank_rows(surge_payload, with_change=True)

    secids = _collect_secids([current_payload, surge_payload])
    quotes: dict[str, tuple[str, float | None, float | None]] = {}
    if secids:
        try:
            quotes = parse_quotes(client.get_json(_quote_url(secids)))
        except Exception as exc:  # quote lookup is supplementary
            logger.warning("popularity quote lookup failed: %s", exc)

    return _attach_quotes(popularity, quotes), _attach_quotes(surging, quotes)
