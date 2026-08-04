from __future__ import annotations

from ashare_hotpot.industries import fetch_stock_industries, parse_stock_industries


def test_parse_stock_industries_keeps_only_valid_codes_and_industries() -> None:
    payload = {
        "result": {
            "data": [
                {"SECURITY_CODE": "000001", "EM2016": "金融-银行-股份制与城商行"},
                {"SECURITY_CODE": "600519", "EM2016": "食品饮料-饮料-白酒"},
                {"SECURITY_CODE": "AAPL", "EM2016": "科技"},
                {"SECURITY_CODE": "300750", "EM2016": "-"},
            ]
        }
    }

    assert parse_stock_industries(payload) == {"000001": "金融", "600519": "食品饮料"}


def test_fetch_stock_industries_keeps_successful_batches() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def get_json(self, _url: str) -> dict[str, object]:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("temporary failure")
            return {"result": {"data": [{"SECURITY_CODE": "000001", "EM2016": "金融-银行"}]}}

    client = FakeClient()
    codes = {"000001"}.union({f"{index:06d}" for index in range(100001, 100101)})

    assert fetch_stock_industries(client, codes) == {"000001": "金融"}
    assert client.calls == 2
