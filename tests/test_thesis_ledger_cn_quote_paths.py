"""沪深单标报价：实际 SDK 接缝、缓存、身份与未知，不扩大或隐藏备用。"""

from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, QuoteQuery, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions


def key(asset='STOCK'):
    return {'kind': 'data', 'market': 'CN', 'assetType': asset, 'capability': 'REALTIME_QUOTE'}


@pytest.mark.parametrize('asset,symbol,code', [('STOCK', '600519.SH', '600519'), ('ETF', '510300.SH', '510300')])
def test_efinance_actual_snapshot_factory_and_cache_do_not_read_universe(monkeypatch, store, asset, symbol, code):
    import efinance as ef
    import data_provider.efinance_fetcher as module
    calls = []
    def snapshot(actual):
        calls.append(actual)
        return pd.DataFrame([{'代码': code, '最新价': 10, '今开': 9, '最高': 11, '最低': 8, '昨收': 9}])
    monkeypatch.setattr(ef.stock, 'get_quote_snapshot', snapshot)
    monkeypatch.setattr(ef.stock, 'get_realtime_quotes', lambda *args, **kwargs: pytest.fail('不能进入全市场'))
    monkeypatch.setattr(module, '_realtime_snapshot_cache', {})
    monkeypatch.setattr(module.EfinanceFetcher, '_enforce_rate_limit', lambda self: None)
    monkeypatch.setattr(module.EfinanceFetcher, '_set_random_user_agent', lambda self: None)
    assert apply(store, key(asset), {'providerId': 'efinance', 'upstreamSource': 'eastmoney'})['effective']['routes'][0]['targets'][0]['eligible']
    runtime = ThesisLedgerProviderRuntime(store)
    request = ThesisLedgerDataRequest('REALTIME_QUOTE', symbol, instrument_type=asset)
    first = runtime.execute_request(request)
    if asset == 'ETF':
        with pytest.raises(ProviderCallError) as error:
            runtime.execute_request(request)
        assert error.value.code == 'request_budget_cooldown'
        adapter = runtime._adapter('efinance')
        second = SimpleNamespace(value=adapter.get_realtime_quote_single_symbol(code))
    else:
        second = runtime.execute_request(request)
    assert calls == [code] and first.value.price == second.value.price == 10
    assert first.value.volume is None and first.value.amount is None
    assert first.provider == 'efinance' and first.upstream_source == 'eastmoney'
    no_admissions(store)


def test_efinance_stock_empty_snapshot_never_uses_universe_or_another_source(monkeypatch, store):
    import efinance as ef
    import data_provider.efinance_fetcher as module
    monkeypatch.setattr(module, '_realtime_snapshot_cache', {})
    monkeypatch.setattr(module.EfinanceFetcher, '_enforce_rate_limit', lambda self: None)
    monkeypatch.setattr(module.EfinanceFetcher, '_set_random_user_agent', lambda self: None)
    monkeypatch.setattr(ef.stock, 'get_quote_snapshot', lambda _: pd.DataFrame())
    monkeypatch.setattr(ef.stock, 'get_realtime_quotes', lambda *args, **kwargs: pytest.fail('不能进入全市场'))
    apply(store, key(), {'providerId': 'efinance', 'upstreamSource': 'eastmoney'})
    with pytest.raises(ProviderCallError) as error:
        ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK'))
    assert error.value.code == 'upstream_unavailable'
    no_admissions(store)


@pytest.mark.parametrize('symbol,market,code', [('600519.SH', 1, '600519'), ('000995.SZ', 0, '000995')])
def test_pytdx_actual_factory_one_target_and_context_release(monkeypatch, store, symbol, market, code):
    from data_provider.pytdx_fetcher import PytdxFetcher
    calls, closed = [], []
    def read(targets):
        calls.append(targets)
        return [{'code': code, 'market': market, 'price': 10, 'open': 9, 'high': 11, 'low': 8, 'last_close': 9, 'vol': 0}]
    @contextmanager
    def session(self):
        try:
            yield SimpleNamespace(get_security_quotes=read)
        finally:
            closed.append(True)
    monkeypatch.setattr(PytdxFetcher, '_pytdx_session', session)
    apply(store, key(), {'providerId': 'pytdx', 'upstreamSource': 'pytdx'})
    result = ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('REALTIME_QUOTE', symbol, instrument_type='STOCK'))
    assert calls == [[(market, code)]] and closed == [True]
    assert result.value.volume == 0 and result.value.amount is None and result.value.provider_timestamp is None
    no_admissions(store)


@pytest.mark.parametrize('values', [[{'code': 'OTHER'}], [{'code': '600519', 'market': 0}],
    [{'code': '600519', 'market': True}], [{'code': '600519'}, {'code': '600519'}]])
def test_pytdx_mismatch_is_rejected_after_releasing_context(values):
    closed = []
    @contextmanager
    def session():
        try:
            yield SimpleNamespace(get_security_quotes=lambda _: values)
        finally:
            closed.append(True)
    adapter = SimpleNamespace(_get_market_code=lambda _: (1, '600519'), _pytdx_session=session)
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.quote(QuoteQuery(symbol='600519.SH', instrument_type='STOCK'), key(),
            {'providerId': 'pytdx', 'upstreamSource': 'pytdx'}, ReadContext(adapter))
    assert error.value.code == 'invalid_response' and closed == [True]


def test_four_bindings_keep_source_revisions_and_single_registration():
    from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS
    targets = [('efinance', 'eastmoney', 'STOCK'), ('efinance', 'eastmoney', 'ETF'),
        ('pytdx', 'pytdx', 'STOCK'), ('akshare', 'eastmoney', 'ETF')]
    for provider, upstream, asset in targets:
        assert CAPABILITY_SOURCES.resolve(key(asset), {'providerId': provider, 'upstreamSource': upstream}) is not None
        assert ('CN', asset, 'REALTIME_QUOTE', provider, upstream) not in _DATA_ADAPTERS
