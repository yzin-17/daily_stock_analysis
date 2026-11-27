"""TickFlow 逐精确功能检查；匿名日线与账号接口共享一个来源身份。"""

from collections import Counter
from math import isfinite
import sqlite3

ANONYMOUS_URL = "https://free-api.tickflow.org"
ACCOUNT_URL = "https://api.tickflow.org"


def requires_credential(key, target):
    return key.get("capability") != "DAILY_BAR"


def failure_result(error):
    from tickflow._exceptions import AuthenticationError, PermissionError, RateLimitError, ConnectionError, TimeoutError
    state, code = "unknown", "upstream_failure"
    if isinstance(error, AuthenticationError):
        state, code = "credential_invalid", "authentication_failed"
    elif isinstance(error, PermissionError):
        state, code = "permission_missing", "permission_denied"
    elif isinstance(error, RateLimitError):
        code = "rate_limited"
    elif isinstance(error, (ConnectionError, TimeoutError)):
        code = "network_failure"
    elif isinstance(error, (ValueError, TypeError, KeyError)):
        code = "invalid_response"
    return {"state": state, "attempted": True, "errorCode": code}


def valid_quotes_for_symbols(quotes, symbols):
    """只接收本次请求中唯一匹配且有有效现价的报价。"""
    if isinstance(quotes, dict):
        quotes = [quotes]
    if not isinstance(quotes, list):
        return {}
    requested = set(symbols)
    rows = [(row.get("symbol"), row) for row in quotes if isinstance(row, dict)
            and isinstance(row.get("symbol"), str) and row["symbol"] in requested]
    counts = Counter(symbol for symbol, _ in rows)
    valid = {}
    for symbol, row in rows:
        price = row.get("last_price")
        if price is None:
            price = row.get("price")
        try:
            number = float(price)
            if not isinstance(price, bool) and isfinite(number) and number > 0 and counts[symbol] == 1:
                valid[symbol] = row
        except (ValueError, TypeError, OverflowError):
            continue
    return valid


def _quote_probe_symbols(context, symbol):
    previous = context.previous_access or {}
    if previous.get("state") != "allowed":
        return [symbol]
    capacity_samples = _quote_sample_universe()
    requested = getattr(context.adapter, "batch_size", 1)
    previous_size = previous.get("verifiedQuoteBatchSize", 1)
    rejected = previous.get("rejectedQuoteBatchSize")
    candidate = previous_size * 2 if rejected is None else max(previous_size + 1, (previous_size + rejected) // 2)
    size = min(requested, candidate, len(capacity_samples))
    return capacity_samples[:size] or [symbol]


def _quote_sample_universe():
    from src.data.stock_mapping import STOCK_NAME_MAP

    return [f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
            for code in STOCK_NAME_MAP if len(code) == 6 and code.isascii() and code.isdigit()
            and code.startswith(("60", "68", "00", "30"))]


def checked_quote_batch_size(api_key, endpoint):
    """读取已保存账号对现行报价接口的有效批大小。"""
    if not isinstance(endpoint, str) or endpoint.rstrip("/") != ACCOUNT_URL:
        return 1
    from src.services.thesis_ledger_account_access_store import load_record
    from src.services.thesis_ledger_control import ControlContractError
    from src.services.thesis_ledger_probe_store import ReadOnlyProbeStore

    store = ReadOnlyProbeStore()
    try:
        snapshot = store.provider_credential_snapshot("tickflow")
        if snapshot.values.get("apiKey") != api_key:
            return 1
        report = load_record(store, snapshot, None)
    except (ControlContractError, sqlite3.Error):
        return 1
    for route in (report or {}).get("routes", []):
        if (route["key"].get("capability") == "REALTIME_QUOTE"
                and route["key"].get("assetType") == "STOCK"
                and route["target"] == {"providerId": "tickflow", "upstreamSource": "tickflow"}):
            return route["access"].get("verifiedQuoteBatchSize", 1)
    return 1


def probe_account_access(context):
    from tickflow import TickFlow
    from tickflow._exceptions import BadRequestError
    key = context.key
    daily = key["capability"] == "DAILY_BAR"
    etf = key["assetType"] == "ETF"
    api_key = "" if daily else context.snapshot.values.get("apiKey", "")
    if not daily and not api_key:
        return {"state": "credential_missing", "attempted": False, "errorCode": "credential_missing"}
    client = TickFlow(api_key=api_key, base_url=ANONYMOUS_URL if daily else ACCOUNT_URL,
                      timeout=context.timeout_seconds, max_retries=0)
    try:
        symbol = "510300.SH" if etf else "600519.SH"
        if key["capability"] == "REALTIME_QUOTE":
            symbols = [symbol] if etf else _quote_probe_symbols(context, symbol)
            try:
                value = client.quotes.get(symbols=symbols)
            except BadRequestError:
                if len(symbols) == 1:
                    raise
                return {"state": "unknown", "attempted": True, "errorCode": "quote_batch_unverified",
                        "rejectedQuoteBatchSize": len(symbols)}
            if not isinstance(value, list):
                raise ValueError("行情接口响应无效")
            if etf and (len(value) != 1 or not valid_quotes_for_symbols(value, symbols)):
                raise ValueError("ETF 行情回包未确认请求标的")
            result = {"state": "allowed", "attempted": True}
            if len(symbols) > 1 and len(valid_quotes_for_symbols(value, symbols)) == len(symbols):
                result["verifiedQuoteBatchSize"] = len(symbols)
                rejected = (context.previous_access or {}).get("rejectedQuoteBatchSize")
                if rejected is not None and rejected > len(symbols):
                    result["rejectedQuoteBatchSize"] = rejected
            return result
        else:
            value = client.klines.get(symbol, period="1d" if daily else key["timeframe"], count=1,
                adjust={"none": "none", "qfq": "forward", "hfq": "backward"}.get(key.get("adjustment"), "none"),
                as_dataframe=False)
            if not isinstance(value, dict) or not isinstance(value.get("timestamp"), list):
                raise ValueError("K线接口响应无效")
        return {"state": "allowed", "attempted": True}
    except Exception as error:
        return failure_result(error)
    finally:
        client.close()
