"""境外报价固定实际来源、标的、资产及真实缺失字段。"""

from datetime import datetime, timezone
from types import SimpleNamespace
import sys

import pytest

from data_provider.exact_foreign_quotes import yahoo_quote, longbridge_quote
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, ProviderCallError


def info():
    return {"symbol": "SPY", "quoteType": "ETF", "regularMarketPrice": 10, "regularMarketPreviousClose": 9,
            "regularMarketOpen": 9, "regularMarketDayHigh": 11, "regularMarketDayLow": 8,
            "regularMarketVolume": None, "regularMarketTime": 1791201600}


def test_yahoo_only_uses_single_symbol_quote_response_preserving_missing_metrics(monkeypatch):
    calls = []
    class Ticker:
        def __init__(self, symbol):
            calls.append(symbol)
        def get_info(self):
            return info()
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=Ticker))
    quote = yahoo_quote(SimpleNamespace(_convert_stock_code=lambda code: code), "SPY", "ETF")
    assert calls == ["SPY"]
    assert quote.volume is None and quote.amount is None
    assert quote.provider_timestamp and quote.fetched_at
    ThesisLedgerProviderRuntime._validate_quote(quote)


def test_yahoo_failure_never_calls_stooq_or_history(monkeypatch):
    class Ticker:
        def __init__(self, _symbol):
            pass
        def get_info(self):
            raise ConnectionError("synthetic failure")
        def history(self, **kwargs):
            pytest.fail("historical fallback")
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=Ticker))
    adapter = SimpleNamespace(_convert_stock_code=lambda code: code, _get_us_stock_quote_from_stooq=lambda *_: pytest.fail("publisher fallback"))
    with pytest.raises(ConnectionError):
        yahoo_quote(adapter, "SPY", "ETF")


@pytest.mark.parametrize("change", [{"symbol": "OTHER"}, {"quoteType": "EQUITY"}])
def test_yahoo_wrong_identity_or_asset_rejected(monkeypatch, change):
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda _: SimpleNamespace(get_info=lambda: {**info(), **change})))
    with pytest.raises(ValueError):
        yahoo_quote(SimpleNamespace(_convert_stock_code=lambda code: code), "SPY", "ETF")


@pytest.mark.parametrize("volume", [None, 0])
def test_longbridge_preserves_missing_and_real_zero_without_extra_reads(volume):
    calls = []
    value = SimpleNamespace(symbol="AAPL.US", last_done=10, prev_close=9, open=9, high=11, low=8,
                            volume=volume, turnover=None, timestamp=datetime.now(timezone.utc))
    def read(symbols):
        calls.append(symbols)
        return [value]
    quote = longbridge_quote(SimpleNamespace(_get_ctx=lambda: SimpleNamespace(quote=read)), "AAPL.US")
    assert calls == [["AAPL.US"]]
    assert quote.volume == volume and quote.amount is None
    ThesisLedgerProviderRuntime._validate_quote(quote)


def test_longbridge_wrong_symbol_rejected():
    adapter = SimpleNamespace(_get_ctx=lambda: SimpleNamespace(quote=lambda _: [SimpleNamespace(symbol="OTHER.US")]))
    with pytest.raises(ValueError, match="identity"):
        longbridge_quote(adapter, "AAPL.US")


def test_boolean_price_is_not_a_numeric_fact():
    with pytest.raises(ProviderCallError):
        ThesisLedgerProviderRuntime._validate_quote(SimpleNamespace(price=True, pre_close=1, open_price=1, high=1, low=1))
