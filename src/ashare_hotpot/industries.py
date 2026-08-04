from __future__ import annotations

import re
import logging
from typing import Any
from urllib.parse import urlencode

from .sources import PoliteHttpClient, RefreshCancelled


STOCK_INDUSTRY_ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
STOCK_INDUSTRY_BATCH_SIZE = 100
_STOCK_CODE_PATTERN = re.compile(r"\d{6}")
_EMPTY_INDUSTRIES = {"", "-", "--", "none", "null"}
logger = logging.getLogger(__name__)


def parse_stock_industries(payload: dict[str, Any]) -> dict[str, str]:
    """Return code-to-primary-industry mappings from the public data response."""

    result = payload.get("result")
    if not isinstance(result, dict):
        return {}
    items = result.get("data")
    if not isinstance(items, list):
        return {}

    industries: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("SECURITY_CODE") or "").strip()
        industry = str(item.get("EM2016") or "").strip()
        if not _STOCK_CODE_PATTERN.fullmatch(code) or industry.lower() in _EMPTY_INDUSTRIES:
            continue
        industries[code] = industry.split("-", maxsplit=1)[0].strip()
    return industries


def _industry_url(codes: list[str]) -> str:
    quoted_codes = ",".join(f'"{code}"' for code in codes)
    parameters = {
        "reportName": "RPT_F10_BASIC_ORGINFO",
        "columns": "SECURITY_CODE,EM2016",
        "filter": f"(SECURITY_CODE in ({quoted_codes}))",
        "pageNumber": "1",
        "pageSize": str(len(codes)),
        "source": "WEB",
        "client": "WEB",
    }
    return f"{STOCK_INDUSTRY_ENDPOINT}?{urlencode(parameters)}"


def fetch_stock_industries(client: PoliteHttpClient, codes: set[str]) -> dict[str, str]:
    """Fetch industry labels in small batches, retaining successful responses."""

    ordered_codes = sorted(code for code in codes if _STOCK_CODE_PATTERN.fullmatch(code))
    industries: dict[str, str] = {}
    for start in range(0, len(ordered_codes), STOCK_INDUSTRY_BATCH_SIZE):
        batch = ordered_codes[start : start + STOCK_INDUSTRY_BATCH_SIZE]
        try:
            industries.update(parse_stock_industries(client.get_json(_industry_url(batch))))
        except RefreshCancelled:
            raise
        except Exception as exc:
            logger.warning("stock industry batch lookup failed for %s stocks: %s", len(batch), exc)
    return industries
