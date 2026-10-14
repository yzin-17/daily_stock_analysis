"""精确日线 SDK 参数、真实返回形状与空准入表策略接缝。"""

from contextlib import contextmanager
from types import SimpleNamespace
import pandas as pd
import pytest

from data_provider.cn_exact_daily import read_cn_exact_daily
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions


def frame():
    return pd.DataFrame({"date": ["2026-05-18"], "open": [1.], "high": [1.2],
                         "low": [.9], "close": [1.1], "volume": [100.], "amount": [110.]})


def request(adjustment="none"):
    return ThesisLedgerDataRequest("DAILY_BAR", "600519.SH", instrument_type="STOCK", timeframe="1d",
                                  adjustment=adjustment, start="2026-05-18", end="2026-05-18")


@pytest.mark.parametrize("adjustment,flag", [("none", 0), ("qfq", 1), ("hfq", 2)])
@pytest.mark.parametrize("asset,symbol", [("STOCK", "600519.SH"), ("ETF", "510300.SH")])
def test_efinance_sdk_receives_exact_identifier_and_adjustment(monkeypatch, store, adjustment, flag, asset, symbol):
    import efinance
    calls = []
    def read(**options):
        calls.append(options)
        return frame().rename(columns={"date": "日期", "open": "开盘", "high": "最高", "low": "最低",
                                       "close": "收盘", "volume": "成交量", "amount": "成交额"})
    monkeypatch.setattr(efinance.stock, "get_quote_history", read)
    key = {"kind": "bar", "market": "CN", "assetType": asset, "capability": "DAILY_BAR",
           "timeframe": "1d", "adjustment": adjustment}
    target = {"providerId": "efinance", "upstreamSource": "eastmoney"}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    query = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type=asset, timeframe="1d", adjustment=adjustment,
                                   start="2026-05-18", end="2026-05-18")
    result = ThesisLedgerProviderRuntime(store, adapters={"efinance": object()}).execute_market_bars_v3(query, key)
    assert result.value["close"].tolist() == [1.1]
    assert calls == [{"stock_codes": "1." + symbol.split('.')[0], "beg": "20260518", "end": "20260518", "klt": 101,
                      "fqt": flag, "quote_id_mode": True, "use_id_cache": False}]
    no_admissions(store)


@pytest.mark.parametrize('failure,code', [('identity', 'invalid_response'), ('empty', 'not_covered')])
def test_efinance_source_binding_preserves_identity_and_empty_errors(monkeypatch, failure, code):
    import efinance
    from data_provider.thesis_ledger_sources.efinance import daily
    from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyQuery, ReadContext
    raw = frame().rename(columns={"date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘"})
    if failure == 'identity':
        raw['股票代码'] = '000001'
    else:
        raw = raw.iloc[:0]
    monkeypatch.setattr(efinance.stock, 'get_quote_history', lambda **options: raw)
    with pytest.raises(CapabilityContractError) as error:
        daily(DailyQuery.from_request(request()), ReadContext(None))
    assert error.value.code == code


def test_efinance_migrated_routes_and_revisions_have_one_fact_source():
    from data_provider.thesis_ledger_sources.efinance import SOURCE, PROTOCOL
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    from src.services.thesis_ledger_provider_manifests import PROVIDER_MANIFESTS
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    assert len([b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR']) == 6
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    registered = [(key, target, gated) for key, target, gated in exact_route_inventory() if target['providerId'] == 'efinance' and key['capability'] == 'DAILY_BAR']
    assert len(registered) == 6 and all(gated for _, _, gated in registered)
    assert not any(provider == 'efinance' for _, _, provider, _ in NATIVE_DAILY_ROUTES)
    for binding in (b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR'):
        key, target = binding.key.route_key(), {'providerId': 'efinance', 'upstreamSource': 'eastmoney'}
        assert CAPABILITY_SOURCES.resolve(key, target) is binding
        assert market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS['efinance']) == {
            'adapterRevision': f"dsa-v3-cn-{key['assetType'].lower()}-efinance-{key['adjustment']}-exact-daily-v1",
            'sourceRevision': PROTOCOL, 'credentialRevision': 'not-required'}


@pytest.mark.parametrize("adjustment,flag", [("none", "3"), ("qfq", "2"), ("hfq", "1")])
def test_baostock_adjustment_and_fields(store, adjustment, flag):
    calls = []
    rows = iter(frame().assign(code="sh.600519")[["date", "code", "open", "high", "low", "close", "volume", "amount"]].values.tolist())
    response = SimpleNamespace(error_code="0", fields=["date", "code", "open", "high", "low", "close", "volume", "amount"])
    def next_row():
        try: response.current = next(rows); return True
        except StopIteration: return False
    response.next, response.get_row_data = next_row, lambda: response.current
    def query(**options): calls.append(options); return response
    @contextmanager
    def session(): yield SimpleNamespace(query_history_k_data_plus=query)
    key = {"kind": "bar", "market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR",
           "timeframe": "1d", "adjustment": adjustment}
    target = {"providerId": "baostock", "upstreamSource": "baostock"}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    result = ThesisLedgerProviderRuntime(store, adapters={"baostock": SimpleNamespace(_baostock_session=session)}).execute_market_bars_v3(request(adjustment), key).value
    assert result["close"].tolist() == [1.1]
    assert calls == [{"code": "sh.600519", "adjustflag": flag, "frequency": "d",
                      "fields": "date,code,open,high,low,close,volume,amount",
                      "start_date": "2026-05-18", "end_date": "2026-05-18"}]
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    binding = CAPABILITY_SOURCES.resolve(key, target)
    assert binding.gated and binding.maximum_rows is None
    assert market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS['baostock']) == {
        'adapterRevision': f'dsa-v3-cn-stock-baostock-{adjustment}-exact-daily-v1',
        'sourceRevision': 'dsa-baostock-exact-daily-single-response-v1', 'credentialRevision': 'not-required'}
    no_admissions(store)


@pytest.mark.parametrize("adjustment,flag", [("none", "none"), ("qfq", "forward"), ("hfq", "backward")])
@pytest.mark.parametrize('date_field', ['date', 'timestamp', 'time', 'ts'])
def test_tickflow_compact_response_uses_sdk_adjustment(store, adjustment, flag, date_field):
    from data_provider.tickflow_fetcher import TickFlowFetcher
    store.save_provider_config('tickflow', {'requestId': 'fixture',
        'credentials': {'method': 'api_key', 'values': {'apiKey': 'synthetic'}}})
    calls = []
    raw = frame().to_dict(orient='list')
    if date_field != 'date':
        raw.pop('date')
        raw[date_field] = [int(pd.Timestamp('2026-05-18', tz='Asia/Shanghai').timestamp() * 1000)]
    def read(symbol, **options): calls.append((symbol, options)); return raw
    adapter = SimpleNamespace(_get_client=lambda: SimpleNamespace(klines=SimpleNamespace(get=read)),
                              _daily_kline_count=TickFlowFetcher._daily_kline_count,
                              _date_to_ms=TickFlowFetcher._date_to_ms)
    key = {'kind': 'bar', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'DAILY_BAR',
           'timeframe': '1d', 'adjustment': adjustment}
    target = {'providerId': 'tickflow', 'upstreamSource': 'tickflow'}
    assert apply(store, key, target)['effective']['routes'][0]['targets'][0]['eligible']
    result = ThesisLedgerProviderRuntime(store, adapters={'tickflow': adapter}).execute_market_bars_v3(request(adjustment), key).value
    assert result["date"].tolist() == ["2026-05-18"]
    assert result['volume'].tolist() == [100.]
    assert calls == [('600519.SH', {'period': '1d', 'count': 30,
        'start_time': int(pd.Timestamp('2026-05-18', tz='Asia/Shanghai').timestamp() * 1000),
        'end_time': int(pd.Timestamp('2026-05-19', tz='Asia/Shanghai').timestamp() * 1000) - 1,
        'adjust': flag, 'as_dataframe': False})]
    no_admissions(store)


@pytest.mark.parametrize('symbol', ['600519.SH', '000995.SZ'])
@pytest.mark.parametrize('date_field', ['date', 'datetime'])
def test_pytdx_keeps_raw_metrics_and_date(store, symbol, date_field):
    calls = []
    def read(*args):
        calls.append(args)
        return frame().rename(columns={"volume": "vol", "date": date_field})
    key = {"kind": "bar", "market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR",
           "timeframe": "1d", "adjustment": "none"}
    target = {"providerId": "pytdx", "upstreamSource": "pytdx"}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    query = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type="STOCK", timeframe="1d",
                                   adjustment="none", start="2026-05-18", end="2026-05-18")
    runtime = ThesisLedgerProviderRuntime(store, adapters={"pytdx": SimpleNamespace(_fetch_raw_data=read)})
    result = runtime.execute_market_bars_v3(query, key).value
    assert result["volume"].tolist() == [100.]
    assert calls == [(symbol, "2026-05-18", "2026-05-18")]
    from data_provider.thesis_ledger_sources.pytdx import SOURCE, PROTOCOL
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    assert len([b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR']) == 1 and SOURCE.bindings[0].gated
    registered = [(k, t, gated) for k, t, gated in exact_route_inventory()
                  if t['providerId'] == 'pytdx' and k['capability'] == 'DAILY_BAR']
    assert registered == [(key, target, True)]
    assert not any(provider == 'pytdx' for _, _, provider, _ in NATIVE_DAILY_ROUTES)
    assert market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS['pytdx']) == {
        'adapterRevision': 'dsa-v3-cn-stock-pytdx-none-exact-daily-v1',
        'sourceRevision': PROTOCOL, 'credentialRevision': 'not-required'}
    from src.services.thesis_ledger_provider_runtime import ProviderCallError
    from dataclasses import replace
    with pytest.raises(ProviderCallError) as unsupported:
        runtime.execute_market_bars_v3(replace(query, symbol='430047.BJ'), key)
    assert unsupported.value.code == 'not_covered'
    with pytest.raises(ProviderCallError):
        runtime.execute_market_bars_v3(replace(query, adjustment='qfq'), {**key, 'adjustment': 'qfq'})
    assert calls == [(symbol, "2026-05-18", "2026-05-18")]
    no_admissions(store)


def test_missing_amount_stays_unknown_and_invalid_price_is_rejected():
    from data_provider.exact_daily_contract import normalize_exact_daily
    from src.services.thesis_ledger_provider_runtime import ProviderCallError
    result = normalize_exact_daily(frame().drop(columns="amount"), start="2026-05-18", end="2026-05-18", source="synthetic")
    assert result.iloc[0]["amount"] is None
    ThesisLedgerProviderRuntime._validate_bars(result)
    result.loc[0, "high"] = .5
    with pytest.raises(ProviderCallError): ThesisLedgerProviderRuntime._validate_bars(result)


@pytest.mark.parametrize("market,symbol,endpoint", [("CN", "600519.SH", "daily"), ("HK", "00700.HK", "hk_daily")])
def test_tushare_stock_endpoint_is_bound_to_market_and_symbol(store, market, symbol, endpoint):
    store.save_provider_config("tushare", {"requestId": "synthetic", "credentials": {"method": "token", "values": {"token": "synthetic"}}})
    calls = []
    def query(actual_endpoint, **params):
        calls.append((actual_endpoint, params))
        return frame().rename(columns={"date": "trade_date", "volume": "vol"}).assign(trade_date="20260518", ts_code=symbol)
    adapter = SimpleNamespace(_token="synthetic", _api=object(), _minute_start=None,
                              _check_rate_limit=lambda: None, _build_api_client=lambda _: SimpleNamespace(query=query))
    key = {"kind": "bar", "market": market, "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "none"}
    target = {"providerId": "tushare", "upstreamSource": "tushare"}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    req = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type="STOCK", timeframe="1d", adjustment="none", start="2026-05-18", end="2026-05-18")
    result = ThesisLedgerProviderRuntime(store, adapters={"tushare": adapter}).execute_market_bars_v3(req, key)
    assert result.value["date"].tolist() == ["2026-05-18"]
    assert calls == [(endpoint, {"ts_code": symbol, "start_date": "20260518", "end_date": "20260518"})]
    no_admissions(store)
