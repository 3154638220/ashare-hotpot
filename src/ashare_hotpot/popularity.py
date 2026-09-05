from __future__ import annotations

import logging
import math
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from .models import OfficialPopularitySnapshot, PopularityRankRow
from .parsing import is_a_share_code
from .sources import PoliteHttpClient, RefreshCancelled


logger = logging.getLogger(__name__)

EASTMONEY_RANK_PAGE = "https://guba.eastmoney.com/rank/"
EASTMONEY_RANK_CURRENT_ENDPOINT = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
EASTMONEY_RANK_SURGE_ENDPOINT = "https://emappdata.eastmoney.com/stockrank/getAllHisRcList"
EASTMONEY_QUOTE_ENDPOINT = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EASTMONEY_QUOTE_UT = "f057cbcbce2a86e2866ab8877db1d059"
QUOTE_FIELDS = "f14,f3,f12,f2"
QUOTE_BATCH_SIZE = 50

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
        number = float(str(value))
        return number if math.isfinite(number) else None
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
        if not is_a_share_code(code):
            continue
        name = str(item.get("f14") or "").strip()
        quotes[code] = (
            name if name and name != "-" else code,
            _float_or_none(item.get("f2")),
            _float_or_none(item.get("f3")),
        )
    return quotes


def _attach_quotes(
    rows: list[PopularityRankRow],
    quotes: dict[str, tuple[str, float | None, float | None]],
) -> list[PopularityRankRow]:
    return [
        replace(
            row,
            name=quotes[row.code][0] if row.code in quotes and quotes[row.code][0] != row.code else row.name,
            current_price=quotes[row.code][1] if row.code in quotes and quotes[row.code][1] is not None else row.current_price,
            change_percent=quotes[row.code][2] if row.code in quotes and quotes[row.code][2] is not None else row.change_percent,
            name_from_cache=False if row.code in quotes and quotes[row.code][0] != row.code else row.name_from_cache,
        )
        for row in rows
    ]


def restore_popularity_names(snapshot: OfficialPopularitySnapshot, names: dict[str, str]) -> None:
    """Restore display identities only; never carry historical prices forward."""
    def restore(row: PopularityRankRow) -> PopularityRankRow:
        name = names.get(row.code)
        if "股票名称" in row.missing_quote_fields and name and name not in (row.code, "-"):
            return replace(row, name=name, name_from_cache=True)
        return row

    snapshot.popularity = [restore(row) for row in snapshot.popularity]
    snapshot.surging = [restore(row) for row in snapshot.surging]


def refresh_popularity_quotes(
    client: PoliteHttpClient,
    popularity: list[PopularityRankRow],
    surging: list[PopularityRankRow],
) -> tuple[list[PopularityRankRow], list[PopularityRankRow]]:
    """Batch missing quotes, then retry incomplete JSON once on the same endpoint.

    Transport errors already receive bounded retries in PoliteHttpClient. No
    alternate hosts, credentials or access-control workarounds are used.
    """
    pending = sorted({row.code for row in (*popularity, *surging) if row.quote_incomplete})
    quotes: dict[str, tuple[str, float | None, float | None]] = {}
    for attempt in range(2):
        retry: list[str] = []
        for start in range(0, len(pending), QUOTE_BATCH_SIZE):
            batch = pending[start:start + QUOTE_BATCH_SIZE]
            secids = [f"{'1' if code.startswith('6') else '0'}.{code}" for code in batch]
            try:
                received = parse_quotes(client.get_json(_quote_url(secids)))
            except RefreshCancelled:
                raise
            except Exception:
                logger.warning("popularity quote batch unavailable (%s stocks)", len(batch))
                continue
            for code in batch:
                incoming = received.get(code)
                previous = quotes.get(code, (code, None, None))
                if incoming:
                    # A partial repair must not erase fields already obtained
                    # during this refresh, including a legitimate zero change.
                    quotes[code] = (
                        incoming[0] if incoming[0] != code else previous[0],
                        incoming[1] if incoming[1] is not None else previous[1],
                        incoming[2] if incoming[2] is not None else previous[2],
                    )
                value = quotes.get(code, (code, None, None))
                if value[0] == code or value[1] is None or value[2] is None:
                    retry.append(code)
        if not retry or attempt == 1:
            break
        pending = retry
    return _attach_quotes(popularity, quotes), _attach_quotes(surging, quotes)


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

    return refresh_popularity_quotes(client, popularity, surging)
