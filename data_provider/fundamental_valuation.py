"""CN 股票研究估值编排；来源日期、观察时刻和历史可见性分开。"""

import re

from .eastmoney_stock_valuation_reader import read_current_stock_valuation


_REQUEST = re.compile(r"[0-9]{6}(?:\.(?:SH|SZ|BJ))?\Z")
_PREFIX = re.compile(r"(SH|SZ|BJ|SS)\.?([0-9]{6})\Z")


def _reader_symbol(original, normalized):
    requested = original.strip().upper()
    if _REQUEST.fullmatch(requested):
        return requested
    prefix = _PREFIX.fullmatch(requested)
    if prefix and prefix.group(2) == normalized:
        market = "SH" if prefix.group(1) == "SS" else prefix.group(1)
        return f"{normalized}.{market}"
    if "." not in requested and _REQUEST.fullmatch(normalized):
        return normalized
    return None


def build_cn_valuation(manager, *, stock_code, original_stock_code, is_etf, timeout_seconds):
    """保留实时报价供股息率使用，估值原文读取最多另占 1.5 秒。"""
    if timeout_seconds > 0:
        quote, quote_error, quote_ms = manager._run_with_retry(
            lambda: manager.get_realtime_quote(stock_code), timeout_seconds,
            "fundamental_valuation")
    else:
        quote, quote_error, quote_ms = None, "fundamental stage timeout", 0

    source_symbol = _reader_symbol(original_stock_code, stock_code) if not is_etf else None
    remaining = max(0.0, timeout_seconds - quote_ms / 1000.0)
    reader_ms = 0
    reader_error = None
    observation = None
    if source_symbol and remaining > 0:
        reader_timeout = min(1.5, remaining)
        observation, reader_error, reader_ms = manager._run_with_timeout(
            lambda: read_current_stock_valuation(source_symbol,
                                                 timeout_seconds=reader_timeout),
            reader_timeout, "fundamental_stock_valuation")

    if isinstance(observation, dict):
        data = dict(observation["data"])
        data.update({"symbol": observation["symbol"],
                     "trade_date": observation["tradeDate"],
                     "observed_at": observation["observedAt"],
                     "source_available_at": None,
                     "historical_visibility_verified": False,
                     "market_cap_unit": observation["marketCapUnit"],
                     "currency": observation["currency"],
                     "content_fingerprint": observation["contentFingerprint"]})
        source_chain = manager._normalize_source_chain(
            [{"provider": "eastmoney_stock_value", "result": "partial",
              "duration_ms": reader_ms}], "eastmoney_stock_value", "partial", reader_ms)
        return quote, manager._build_fundamental_block("partial", data, source_chain, []), quote_ms + reader_ms

    data = {"pe_ratio": getattr(quote, "pe_ratio", None) if quote else None,
            "pb_ratio": getattr(quote, "pb_ratio", None) if quote else None,
            "total_mv": getattr(quote, "total_mv", None) if quote else None,
            "circ_mv": getattr(quote, "circ_mv", None) if quote else None}
    status = manager._infer_block_status(data, "partial" if quote is not None else "not_supported")
    if status == "partial" and quote_error and not manager._has_meaningful_payload(data):
        status = "failed"
    source_chain = [{"provider": "realtime_quote", "result": status, "duration_ms": quote_ms}]
    if reader_error:
        source_chain.append({"provider": "eastmoney_stock_value", "result": "failed",
                             "duration_ms": reader_ms})
    errors = [error for error in (quote_error, reader_error) if error]
    block = manager._build_fundamental_block(
        status, data, manager._normalize_source_chain(source_chain, "realtime_quote",
                                                      status, quote_ms), errors)
    return quote, block, quote_ms + reader_ms
