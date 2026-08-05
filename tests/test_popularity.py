from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from ashare_hotpot.popularity import (
    fetch_official_popularity,
    parse_quotes,
    parse_rank_rows,
    stock_rank_url,
)


CURRENT_PAYLOAD = {
    "data": [
        {"sc": "SZ000001", "rk": 1, "hrc": 3},
        {"sc": "SH600519", "rk": 2, "hrc": -1},
        {"sc": "SZ000001", "rk": 1, "hrc": 3},  # duplicate code
        {"sc": "BJ920001", "rk": 3, "hrc": 0},  # Beijing Stock Exchange A-share
        {"sc": "SH200001", "rk": 4, "hrc": 2},  # Shenzhen B-share -> filtered
        {"sc": "SH900901", "rk": 5, "hrc": 2},  # Shanghai B-share -> filtered
        {"sc": "SZ430001", "rk": 6, "hrc": 2},  # NEEQ -> filtered
    ],
    "rc": 0,
}

SURGE_PAYLOAD = {
    "data": [
        {"sc": "SH600519", "rk": 5, "hrc": 12},
        {"sc": "SZ000001", "rk": 9, "hrc": 8},
        {"sc": "SH688001", "rk": 11, "hrc": 6},  # STAR board
    ],
    "rc": 0,
}

QUOTE_PAYLOAD = {
    "data": {
        "diff": [
            {"f12": "000001", "f14": "平安银行", "f2": 11.25, "f3": 1.5},
            {"f12": "600519", "f14": "贵州茅台", "f2": 1600.0, "f3": 2.0},
            {"f12": "920001", "f14": "北交所样例", "f2": 9.8, "f3": "-"},
        ]
    }
}


def test_parse_current_rank_rows_filters_dedupes_and_builds_links() -> None:
    rows = parse_rank_rows(CURRENT_PAYLOAD, with_change=False)

    assert [row.code for row in rows] == ["000001", "600519", "920001"]
    assert [row.rank for row in rows] == [1, 2, 3]
    assert all(row.change is None for row in rows)
    assert all(row.current_price is None for row in rows)
    assert rows[0].url == stock_rank_url("000001")
    assert rows[0].url == "https://guba.eastmoney.com/rank/stock?code=000001"
    assert rows[1].name == "600519"


def test_parse_surge_rank_rows_keeps_change_and_current_rank() -> None:
    rows = parse_rank_rows(SURGE_PAYLOAD, with_change=True)

    assert [row.code for row in rows] == ["600519", "000001", "688001"]
    assert [row.rank for row in rows] == [5, 9, 11]
    assert [row.change for row in rows] == [12, 8, 6]


def test_empty_rank_data_fails_the_whole_board() -> None:
    with pytest.raises(RuntimeError):
        parse_rank_rows({"data": []}, with_change=False)


def test_missing_rank_field_fails_the_whole_board() -> None:
    with pytest.raises(RuntimeError):
        parse_rank_rows({"data": [{"sc": "SZ000001"}]}, with_change=False)


def test_structural_change_fails_the_whole_board() -> None:
    with pytest.raises(RuntimeError):
        parse_rank_rows({"data": {"diff": []}}, with_change=False)


def test_parse_quotes_handles_missing_values() -> None:
    quotes = parse_quotes(QUOTE_PAYLOAD)

    assert quotes["000001"] == ("平安银行", 11.25, 1.5)
    assert quotes["600519"][1] == 1600.0
    assert quotes["920001"][2] is None


def test_fetch_official_popularity_merges_quotes_and_builds_secids() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.post_calls: list[str] = []
            self.get_calls: list[str] = []

        def post_json(self, url: str, _payload):
            self.post_calls.append(url)
            if "getAllCurrentList" in url:
                return CURRENT_PAYLOAD
            if "getAllHisRcList" in url:
                return SURGE_PAYLOAD
            raise AssertionError(url)

        def get_json(self, url: str):
            self.get_calls.append(url)
            return QUOTE_PAYLOAD

    client = FakeClient()
    popularity, surging = fetch_official_popularity(client)

    assert [row.name for row in popularity] == ["平安银行", "贵州茅台", "北交所样例"]
    assert popularity[0].current_price == 11.25
    assert popularity[0].change_percent == 1.5
    assert surging[0].name == "贵州茅台"
    assert surging[0].change == 12
    assert surging[1].name == "平安银行"
    assert len(client.post_calls) == 2
    assert len(client.get_calls) == 1
    secids = parse_qs(urlparse(client.get_calls[0]).query)["secids"][0]
    assert "0.000001" in secids
    assert "1.600519" in secids
    assert "0.920001" in secids


def test_fetch_official_popularity_quote_failure_is_best_effort() -> None:
    class FailingQuoteClient:
        def post_json(self, url: str, _payload):
            if "getAllCurrentList" in url:
                return CURRENT_PAYLOAD
            return SURGE_PAYLOAD

        def get_json(self, _url: str):
            raise RuntimeError("行情接口失败")

    popularity, surging = fetch_official_popularity(FailingQuoteClient())

    assert popularity[0].name == "000001"
    assert popularity[0].current_price is None
    assert surging[0].change == 12
