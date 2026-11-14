"""HiThink 按具体检索词读取证券身份，单次请求与固定凭证域。"""

from datetime import datetime, timezone
import re
import requests

URL = "https://fuyao.aicubes.cn/api/meta/tickers/search"


class InstrumentSearchError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


ASSET_TYPES = {"STOCK": "a-share", "ETF": "fund-etf", "MUTUAL_FUND": "fund-otc"}
NATIVE_TYPES = {native: product for product, native in ASSET_TYPES.items()}


def search_instruments(api_key, query, *, current=False, market=None, instrument_type=None, http_get=requests.get):
    native_types = ["a-share", "fund-etf"]
    if current:
        native_types = list(NATIVE_TYPES) if market is None else (
            ["fund-otc"] if market == "OF" else ["a-share", "fund-etf"]
        )
        if instrument_type:
            selected = ASSET_TYPES.get(instrument_type)
            if selected not in native_types:
                raise InstrumentSearchError("unsupported_capability")
            native_types = [selected]
    params = {"q": query, "asset_type": ",".join(native_types), "limit": 20}
    if market in {"SH", "SZ", "BJ"}:
        params["exchange"] = market
    try:
        response = http_get(URL, params=params,
                            headers={"X-api-key": api_key}, timeout=8, allow_redirects=False)
        if response.status_code != 200:
            raise InstrumentSearchError("upstream_unavailable")
        body = response.json()
    except InstrumentSearchError:
        raise
    except Exception:
        raise InstrumentSearchError("upstream_unavailable") from None
    if not isinstance(body, dict) or type(body.get("code")) is not int:
        raise InstrumentSearchError("invalid_response")
    if body["code"] != 0:
        raise InstrumentSearchError({2001: "authentication_failed", 2003: "permission_denied", 4001: "rate_limited"}.get(body["code"], "upstream_failure"))
    data = body.get("data")
    if (not isinstance(data, dict) or type(data.get("timestamp")) is not int or data["timestamp"] <= 0
            or not isinstance(data.get("item"), list) or len(data["item"]) > 20):
        raise InstrumentSearchError("invalid_response")
    items, seen = [], set()
    for row in data["item"]:
        if not isinstance(row, dict):
            raise InstrumentSearchError("invalid_response")
        symbol, kind = row.get("thscode"), row.get("asset_type")
        match = re.fullmatch(r"([0-9]{6})\.(SH|SZ|BJ|OF)", symbol) if isinstance(symbol, str) else None
        if (not match or kind not in native_types or row.get("currency") != "CNY"
                or (match[2] == "OF") != (kind == "fund-otc")
                or row.get("exchange") != (None if match[2] == "OF" else match[2])
                or (market is not None and match[2] != market) or symbol in seen):
            raise InstrumentSearchError("invalid_response")
        seen.add(symbol)
        name = row.get("name") if current else symbol
        if not isinstance(name, str) or not name.strip():
            raise InstrumentSearchError("invalid_response")
        items.append({"symbol": symbol, "assetType": NATIVE_TYPES[kind], "name": name.strip()})
    return {"items": items, "raw": body, "sourceTimestamp": data["timestamp"], "observedAt": datetime.now(timezone.utc).isoformat()}
