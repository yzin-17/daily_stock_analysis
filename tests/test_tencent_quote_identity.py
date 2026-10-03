"""腾讯报价不得以请求代码掩盖响应身份错误。"""

from unittest.mock import Mock

import pytest

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.base import DataFetcherManager
from data_provider.realtime_types import UnifiedRealtimeQuote
from data_provider.tencent_quote_identity import quote_timestamp


def payload(symbol='sh600519', code='600519', name='贵州茅台'):
    fields = ['0'] * 50
    fields[1:4] = [name, code, '1500']
    return f'v_{symbol}="{"~".join(fields)}";'


@pytest.mark.parametrize('response', [
    payload(symbol='sz600519'),
    payload(code='000001'),
    payload(name=' '),
    payload() + payload(),
    payload() + 'alert(1);',
    'v_sh600519="short";',
    payload().replace('v_sh600519', 'v_sh６００５１９'),
])
def test_invalid_quote_never_records_success(monkeypatch, response):
    breaker = Mock()
    monkeypatch.setattr('data_provider.akshare_fetcher.get_realtime_circuit_breaker', lambda: breaker)
    monkeypatch.setattr('data_provider.akshare_fetcher.requests.get', lambda *a, **k: Mock(status_code=200, text=response))
    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, '_enforce_rate_limit', lambda: None)
    assert fetcher._get_stock_realtime_quote_tencent('600519') is None
    breaker.record_success.assert_not_called()
    breaker.record_failure.assert_called_once()


def test_quote_construction_failure_is_not_source_success(monkeypatch):
    breaker = Mock()
    monkeypatch.setattr('data_provider.akshare_fetcher.get_realtime_circuit_breaker', lambda: breaker)
    monkeypatch.setattr('data_provider.akshare_fetcher.requests.get', lambda *a, **k: Mock(status_code=200, text=payload()))
    monkeypatch.setattr('data_provider.akshare_fetcher.UnifiedRealtimeQuote', Mock(side_effect=ValueError('invalid quote')))
    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, '_enforce_rate_limit', lambda: None)
    assert fetcher._get_stock_realtime_quote_tencent('600519') is None
    breaker.record_success.assert_not_called()
    breaker.record_failure.assert_called_once()


@pytest.mark.parametrize('value', ['', '0', '20260230150000', '20260925156000',
                                   '20260925', '２０２６０９２５１５００００', '20260925150000Z'])
def test_invalid_provider_time_remains_unknown(value):
    fields = ['0'] * 50
    fields[30] = value
    assert quote_timestamp(fields) is None


def test_provider_time_survives_manager_as_old_quote(monkeypatch):
    fields = ['0'] * 50
    fields[30] = '20260925150000'
    provider_time = quote_timestamp(fields)
    assert provider_time == '2026-09-25T15:00:00+08:00'
    manager = DataFetcherManager.__new__(DataFetcherManager)
    monkeypatch.setattr(manager, '_utc_now_iso', lambda: '2026-09-27T07:00:00+00:00')
    quote = UnifiedRealtimeQuote(code='600519', provider_timestamp=provider_time)
    manager._enrich_realtime_quote(quote, realtime_cache_ttl=600)
    assert quote.provider_timestamp == '2026-09-25T07:00:00+00:00'
    assert quote.stale_seconds == 172800
    assert quote.is_stale is True
    assert quote.fetched_at == '2026-09-27T07:00:00+00:00'


def test_adapter_exposes_provider_time(monkeypatch):
    fields = ['0'] * 50
    fields[1:4] = ['贵州茅台', '600519', '1500']
    fields[30] = '20260925150000'
    response = 'v_sh600519="' + '~'.join(fields) + '";'
    monkeypatch.setattr('data_provider.akshare_fetcher.get_realtime_circuit_breaker', Mock())
    monkeypatch.setattr('data_provider.akshare_fetcher.requests.get', lambda *a, **k: Mock(status_code=200, text=response))
    fetcher = AkshareFetcher()
    monkeypatch.setattr(fetcher, '_enforce_rate_limit', lambda: None)
    quote = fetcher._get_stock_realtime_quote_tencent('600519')
    assert quote.provider_timestamp == '2026-09-25T15:00:00+08:00'
