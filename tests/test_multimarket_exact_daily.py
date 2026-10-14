"""多市场 SDK/HTTP 接缝及真实策略选择；不使用外部账号。"""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pandas as pd
import pytest

from tests.test_configured_market_routes import store, apply, no_admissions
from tests.test_native_exact_daily import frame
from data_provider.multimarket_exact_daily import read_multimarket_exact_daily
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime


@pytest.mark.parametrize("market,asset,symbol,ticker", [("CN", "STOCK", "600519.SH", "600519.SS"),
    ("HK", "STOCK", "00700.HK", "0700.HK"), ("US", "STOCK", "AAPL.US", "AAPL"),
    ("CN", "ETF", "510300.SH", "510300.SS"), ("HK", "ETF", "02800.HK", "2800.HK"),
    ("US", "ETF", "SPY.US", "SPY")])
def test_yahoo_exact_market_symbol_inclusive_end_and_missing_amount(monkeypatch, store, market, asset, symbol, ticker):
    import yfinance
    calls = []
    def download(**options):
        calls.append(options)
        raw = frame().drop(columns="amount").rename(columns={"date": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
        return raw.assign(Date=lambda df: pd.to_datetime(df.Date)).set_index("Date")
    monkeypatch.setattr(yfinance, "download", download)
    key = {"kind": "bar", "market": market, "assetType": asset, "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"}
    target = {"providerId": "yfinance", "upstreamSource": "yfinance"}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    request = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type=asset, timeframe="1d", adjustment="qfq", start="2026-05-18", end="2026-05-18")
    result = ThesisLedgerProviderRuntime(store).execute_market_bars_v3(request, key)
    assert result.value.iloc[0]["amount"] is None
    assert calls[0]["tickers"] == ticker and calls[0]["auto_adjust"] is True
    assert calls[0]["end"] == "2026-05-19"
    assert calls == [{'tickers': ticker, 'start': '2026-05-18', 'end': '2026-05-19',
        'auto_adjust': True, 'progress': False, 'threads': False, 'timeout': 4.5, 'multi_level_index': False}]
    no_admissions(store)


@pytest.mark.parametrize("market,symbol", [("HK", "00700.HK"), ("US", "AAPL.US")])
@pytest.mark.parametrize("adjustment", ["none", "qfq"])
def test_longbridge_uses_explicit_adjustment_and_local_trading_date(market, symbol, adjustment):
    from longbridge.openapi import AdjustType
    calls = []
    zone = ZoneInfo("Asia/Hong_Kong" if market == "HK" else "America/New_York")
    candle = SimpleNamespace(timestamp=datetime(2026, 5, 18, 16, tzinfo=zone), open=1., high=1.2, low=.9, close=1.1, volume=100., turnover=110.)
    def read(*args): calls.append(args); return [candle]
    adapter = SimpleNamespace(_get_ctx=lambda: SimpleNamespace(history_candlesticks_by_date=read))
    request = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type="STOCK", timeframe="1d", adjustment=adjustment, start="2026-05-18", end="2026-05-18")
    result = read_multimarket_exact_daily("longbridge", adapter, request, market)
    assert result["date"].tolist() == ["2026-05-18"]
    assert calls[0][0] == symbol
    assert calls[0][2] == (AdjustType.NoAdjust if adjustment == "none" else AdjustType.ForwardAdjust)


@pytest.mark.parametrize("provider,adjustment", [("finnhub", "qfq"), ("alphavantage", "none")])
def test_http_daily_is_source_pinned_and_never_estimates_amount(monkeypatch, store, provider, adjustment):
    import data_provider.multimarket_exact_daily as native
    calls = []
    def get(url, params, timeout):
        calls.append((url, params, timeout))
        if provider == "finnhub":
            payload = {"s": "ok", "t": [int(datetime(2026, 5, 18, tzinfo=ZoneInfo("America/New_York")).timestamp())],
                       "o": [1.], "h": [1.2], "l": [.9], "c": [1.1], "v": [100.]}
        else:
            payload = {"Time Series (Daily)": {"2026-05-18": {"1. open": "1", "2. high": "1.2", "3. low": ".9", "4. close": "1.1", "5. volume": "100"}}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr(native.requests, "get", get)
    store.save_provider_config(provider, {"requestId": "synthetic", "credentials": {"method": "api_key", "values": {"apiKey": "synthetic"}}})
    key = {"kind": "bar", "market": "US", "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": adjustment}
    target = {"providerId": provider, "upstreamSource": provider}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    request = ThesisLedgerDataRequest("DAILY_BAR", "AAPL.US", instrument_type="STOCK", timeframe="1d", adjustment=adjustment, start="2026-05-18", end="2026-05-18")
    result = ThesisLedgerProviderRuntime(store, adapters={provider: SimpleNamespace(_api_key="synthetic")}).execute_market_bars_v3(request, key)
    assert result.value.iloc[0]["amount"] is None
    assert calls[0][1]["symbol"] == "AAPL"
    if provider == "alphavantage": assert calls[0][1]["outputsize"] == "full"
    assert calls[0][2] == 4.5 and result.value.iloc[0]["volume"] == 100
    if provider == "alphavantage":
        assert calls == [("https://www.alphavantage.co/query", {'function': 'TIME_SERIES_DAILY',
            'symbol': 'AAPL', 'outputsize': 'full', 'apikey': 'synthetic'}, 4.5)]
    no_admissions(store)


def test_full_history_permission_message_is_explicit(monkeypatch):
    import data_provider.multimarket_exact_daily as native
    from data_provider.exact_daily_contract import ExactDailyError
    monkeypatch.setattr(native.requests, "get", lambda *args, **kwargs: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"Information": "premium required"}))
    request = ThesisLedgerDataRequest("DAILY_BAR", "AAPL.US", instrument_type="STOCK", timeframe="1d", adjustment="none", start="2026-05-18", end="2026-05-18")
    with pytest.raises(ExactDailyError) as error:
        read_multimarket_exact_daily("alphavantage", SimpleNamespace(_api_key="synthetic"), request, "US")
    assert error.value.code == "quota_unavailable"


def test_distinct_http_payloads_project_to_same_daily_contract(monkeypatch):
    from data_provider.thesis_ledger_sources.alphavantage import SOURCE as ALPHA
    from data_provider.thesis_ledger_sources.finnhub import SOURCE as FINN
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_capability_contracts import ReadContext
    timestamp = int(datetime(2026, 5, 18, tzinfo=ZoneInfo('America/New_York')).timestamp())
    payloads = {'https://www.alphavantage.co/query': {'Time Series (Daily)': {'2026-05-18': {
        '1. open': '1', '2. high': '1.2', '3. low': '.9', '4. close': '1.1', '5. volume': '100'}}},
        'https://finnhub.io/api/v1/stock/candle': {'s': 'ok', 't': [timestamp], 'o': [1.], 'h': [1.2],
            'l': [.9], 'c': [1.1], 'v': [100.]}}
    monkeypatch.setattr('data_provider.multimarket_exact_daily.requests.get', lambda url, **kwargs:
        SimpleNamespace(raise_for_status=lambda: None, json=lambda: payloads[url]))
    results = []
    for source in (ALPHA, FINN):
        key = source.bindings[0].key.route_key()
        request = ThesisLedgerDataRequest('DAILY_BAR', 'AAPL.US', instrument_type='STOCK', timeframe='1d',
            adjustment=key['adjustment'], start='2026-05-18', end='2026-05-18')
        read = CAPABILITY_SOURCES.daily(request, key, {'providerId': source.provider_id, 'upstreamSource': source.provider_id},
            ReadContext(SimpleNamespace(_api_key='synthetic')))
        assert read.frame.attrs['upstream_source'] == source.provider_id
        results.append(read.frame.to_dict(orient='records'))
    assert results[0] == results[1]
    # 结构适配等价不扩大真实价格口径或消费资格。
    assert ALPHA.bindings[0].key.adjustment == 'none' and FINN.bindings[0].key.adjustment == 'qfq'
