"""腾讯 ETF 单标基础报价；未知量额不做股票单位换算。"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from .base import DataFetchError
from .tencent_quote_identity import quote_timestamp, verified_quote_fields


def _price(fields: list[str], index: int) -> float:
    try:
        value = float(fields[index])
    except (ValueError, TypeError, IndexError) as exc:
        raise DataFetchError("腾讯 ETF 报价缺少有效价格") from exc
    if not math.isfinite(value) or value <= 0:
        raise DataFetchError("腾讯 ETF 报价价格无效")
    return value


def fetch_tencent_etf_quote(
    symbol: str, *, http_get: Callable[..., Any] = requests.get,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    if not isinstance(symbol, str) or re.fullmatch(r"(?:5[0-9]{5}\.SH|1[0-9]{5}\.SZ)", symbol) is None:
        raise DataFetchError("腾讯 ETF 报价必须使用匹配市场的完整代码")
    code, market = symbol.split(".")
    wire_symbol = market.lower() + code
    response = http_get(f"https://qt.gtimg.cn/q={wire_symbol}", timeout=4.5, allow_redirects=False)
    if response.status_code != 200:
        raise DataFetchError("腾讯 ETF 报价 HTTP 请求失败")
    response.encoding = "gbk"
    try:
        fields = verified_quote_fields(response.text, wire_symbol)
    except ValueError as exc:
        raise DataFetchError("腾讯 ETF 报价响应身份不匹配") from exc
    price, previous, opened, high, low = (_price(fields, i) for i in (3, 4, 5, 33, 34))
    if high < max(price, opened, low) or low > min(price, opened, high):
        raise DataFetchError("腾讯 ETF 报价 OHLC 无效")
    observed = clock()
    if observed.tzinfo is None:
        raise DataFetchError("腾讯 ETF 报价抓取时刻缺少时区")
    return {
        "price": price, "pre_close": previous, "open_price": opened, "high": high, "low": low,
        "volume": None, "amount": None,
        "provider_timestamp": quote_timestamp(fields),
        "fetched_at": observed.astimezone(timezone.utc).isoformat(),
        "source": "tencent/tencent",
        "units": {"price_currency": "CNY", "volume": "unknown", "turnover_currency": "unknown"},
    }
