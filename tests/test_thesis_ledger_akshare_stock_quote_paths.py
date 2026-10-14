"""AKShare 两项股票报价：真实工厂/缓存/HTTP 接缝，不隐含跨来源读取。"""

from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, QuoteQuery, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions


KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}


@pytest.fixture
def sdk(monkeypatch):
    import data_provider.akshare_fetcher as module
    import data_provider.realtime_types as types
    breaker = SimpleNamespace(is_available=lambda _: True, record_success=lambda _: None,
                              record_failure=lambda *args: None)
    monkeypatch.setattr(module, 'get_realtime_circuit_breaker', lambda: breaker)
    monkeypatch.setattr(types, 'get_realtime_circuit_breaker', lambda: breaker)
    monkeypatch.setattr(module, '_realtime_cache', {'data': None, 'timestamp': 0, 'ttl': 1200})
    monkeypatch.setattr(module.AkshareFetcher, '_enforce_rate_limit', lambda self: None)
    monkeypatch.setattr(module.AkshareFetcher, '_set_random_user_agent', lambda self: None)
    return module, breaker


def configured(store, upstream):
    apply(store, KEY, {'providerId': 'akshare', 'upstreamSource': upstream})
    return ThesisLedgerProviderRuntime(store)


def test_eastmoney_actual_market_snapshot_reuses_cache_and_selects_identity(monkeypatch, store, sdk):
    import akshare as ak
    calls = []

    def snapshot():
        calls.append(True)
        return pd.DataFrame([{'代码': '000995', '最新价': 90, '今开': 90, '最高': 91, '最低': 89},
                             {'代码': '600519', '最新价': 10, '今开': 9, '最高': 11, '最低': 8, '成交量': 0, '涨跌额': 1}])
    monkeypatch.setattr(ak, 'stock_zh_a_spot_em', snapshot)
    runtime = configured(store, 'eastmoney')
    request = ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK')
    first, second = runtime.execute_request(request), runtime.execute_request(request)
    assert calls == [True] and first.value.price == second.value.price == 10
    assert first.value.volume == 0 and first.value.amount is None and first.value.provider_timestamp is None
    assert first.provider == 'akshare' and first.upstream_source == 'eastmoney'
    no_admissions(store)


def test_eastmoney_original_retry_is_bounded_and_failure_cache_prevents_new_calls(monkeypatch, store, sdk):
    import akshare as ak
    module, _ = sdk
    calls, sleeps = [], []

    def fail():
        calls.append(True)
        raise TimeoutError('controlled fixture')
    monkeypatch.setattr(ak, 'stock_zh_a_spot_em', fail)
    monkeypatch.setattr(module.time, 'sleep', sleeps.append)
    runtime = configured(store, 'eastmoney')
    request = ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK')
    for _ in range(2):
        with pytest.raises(ProviderCallError) as error:
            runtime.execute_request(request)
        assert error.value.code == 'upstream_unavailable'
    assert calls == [True, True] and sleeps == [2, 4]
    no_admissions(store)


def payload(symbol='sh600519', code='600519', clock='20241008103000'):
    fields = [''] * 55
    for i, value in {1: '贵州茅台', 2: code, 3: '10', 4: '9', 5: '9', 6: '12',
                     30: clock, 31: '1', 32: '11.1', 33: '11', 34: '8', 37: '1.5'}.items():
        fields[i] = value
    return f'v_{symbol}="' + '~'.join(fields) + '";'


def test_tencent_actual_http_retains_symbol_timeout_units_and_source_clock(monkeypatch, store, sdk):
    module, _ = sdk
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, text=payload())
    monkeypatch.setattr(module.requests, 'get', get)
    result = configured(store, 'tencent').execute_request(
        ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK'))
    assert len(calls) == 1 and calls[0][0] == 'http://qt.gtimg.cn/q=sh600519'
    assert calls[0][1]['timeout'] == 10
    assert result.value.volume == 1200 and result.value.amount == 15000
    assert result.value.provider_timestamp == '2024-10-08T10:30:00+08:00'
    no_admissions(store)


@pytest.mark.parametrize('raw', [payload(symbol='sz600519'), payload(code='000995'), ''])
def test_tencent_mismatch_or_empty_has_one_call_and_original_failure(monkeypatch, store, sdk, raw):
    module, _ = sdk
    calls = []

    def get(*args, **kwargs):
        calls.append(True)
        return SimpleNamespace(status_code=200, text=raw)
    monkeypatch.setattr(module.requests, 'get', get)
    with pytest.raises(ProviderCallError) as error:
        configured(store, 'tencent').execute_request(
            ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK'))
    assert error.value.code == 'upstream_unavailable' and calls == [True]
    no_admissions(store)


@pytest.mark.parametrize('upstream', ['eastmoney', 'tencent'])
def test_stock_contract_cannot_select_etf_method_or_read_through_open_circuit(upstream, sdk):
    _, breaker = sdk
    adapter = SimpleNamespace(_get_stock_realtime_quote_em=lambda _: pytest.fail('不能读取'),
                              _get_stock_realtime_quote_tencent=lambda _: pytest.fail('不能读取'))
    target = {'providerId': 'akshare', 'upstreamSource': upstream}
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.quote(QuoteQuery(symbol='510300.SH', instrument_type='STOCK'), KEY, target, ReadContext(adapter))
    assert error.value.code == 'invalid_request'
    breaker.is_available = lambda _: False
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.quote(QuoteQuery(symbol='600519.SH', instrument_type='STOCK'), KEY, target, ReadContext(adapter))
    assert error.value.code == 'not_covered'


def sina_payload(identity='sh600519', volume='', amount='0'):
    fields = [''] * 32
    for i, value in {0: '贵州茅台', 1: '9', 2: '9', 3: '10', 4: '11', 5: '8',
                     8: volume, 9: amount, 30: '2024-10-08', 31: '10:30:00'}.items():
        fields[i] = value
    return f'var hq_str_{identity}="' + ','.join(fields) + '";'


def test_sina_actual_factory_and_http_preserve_unknown_clock_and_missing(monkeypatch, store, sdk):
    module, _ = sdk
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, text=sina_payload())
    monkeypatch.setattr(module.requests, 'get', get)
    result = configured(store, 'sina').execute_request(
        ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK'))
    assert len(calls) == 1 and calls[0][0] == 'http://hq.sinajs.cn/list=sh600519'
    assert calls[0][1]['timeout'] == 10 and calls[0][1]['headers']['Referer'] == 'http://finance.sina.com.cn'
    assert result.value.price == 10 and result.value.pre_close == 9
    assert result.value.volume is None and result.value.amount == 0 and result.value.provider_timestamp is None
    assert result.provider == 'akshare' and result.upstream_source == 'sina'
    no_admissions(store)


@pytest.mark.parametrize('raw', [sina_payload(identity='sz600519'), sina_payload() + sina_payload(),
    'var hq_str_sh600519="name,9,9,10";', 'var hq_str_sz600519="";'])
def test_sina_wrong_identity_or_structure_never_reaches_sdk_generic(monkeypatch, store, sdk, raw):
    module, _ = sdk
    calls = []

    def get(*args, **kwargs):
        calls.append(True)
        return SimpleNamespace(status_code=200, text=raw)
    monkeypatch.setattr(module.requests, 'get', get)
    monkeypatch.setattr(module.AkshareFetcher, 'get_realtime_quote', lambda *args, **kwargs: pytest.fail('不能走通用 SDK'))
    with pytest.raises(ProviderCallError) as error:
        configured(store, 'sina').execute_request(
            ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK'))
    assert error.value.code == 'invalid_response' and calls == [True]
    no_admissions(store)


@pytest.mark.parametrize('failure', ['timeout', 'http', 'empty', 'missing'])
def test_sina_original_unavailable_errors_have_one_http_and_no_retry(monkeypatch, store, sdk, failure):
    module, _ = sdk
    calls = []

    def get(*args, **kwargs):
        calls.append(True)
        if failure == 'timeout':
            raise TimeoutError('controlled fixture')
        text = sina_payload()
        if failure == 'empty':
            text = ''
        elif failure == 'missing':
            text = 'var hq_str_sh600519="";'
        return SimpleNamespace(status_code=503 if failure == 'http' else 200, text=text)
    monkeypatch.setattr(module.requests, 'get', get)
    with pytest.raises(ProviderCallError) as error:
        configured(store, 'sina').execute_request(
            ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK'))
    assert error.value.code == 'upstream_unavailable' and calls == [True]
    no_admissions(store)


def test_sina_raw_zero_and_asset_circuit_parameter_isolation(monkeypatch, sdk):
    from data_provider.thesis_ledger_sources.akshare import sina_stock_quote
    module, breaker = sdk
    calls = []
    def get(*args, **kwargs):
        calls.append(True)
        return SimpleNamespace(status_code=200, text=sina_payload(volume='0', amount=''))
    monkeypatch.setattr(module.requests, 'get', get)
    adapter = SimpleNamespace(_enforce_rate_limit=lambda: None)
    value = sina_stock_quote(QuoteQuery(symbol='600519.SH', instrument_type='STOCK'), ReadContext(adapter))
    assert value.volume == 0 and value.amount is None and calls == [True]
    with pytest.raises(CapabilityContractError) as error:
        sina_stock_quote(QuoteQuery(symbol='510300.SH', instrument_type='STOCK'), ReadContext(adapter))
    assert error.value.code == 'invalid_request' and calls == [True]
    breaker.is_available = lambda _: False
    with pytest.raises(CapabilityContractError) as error:
        sina_stock_quote(QuoteQuery(symbol='600519.SH', instrument_type='STOCK'), ReadContext(adapter))
    assert error.value.code == 'not_covered' and calls == [True]
