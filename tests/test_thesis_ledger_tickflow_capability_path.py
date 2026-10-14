"""TickFlow 来源所有权、工厂预算及统一错误边界；不连接真实 SDK 服务。"""

from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.thesis_ledger_sources.tickflow import SOURCE, PROTOCOL, daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyQuery, ReadContext
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store
from tests.test_native_exact_daily import request


def test_tickflow_has_one_registration_and_original_revisions():
    from src.services.provider_credentials import credential_schema
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    registered = [(key, target, gated) for key, target, gated in exact_route_inventory()
                  if target['providerId'] == 'tickflow' and key['capability'] == 'DAILY_BAR']
    assert len(registered) == 3 and all(gated for _, _, gated in registered)
    assert not any(provider == 'tickflow' for _, _, provider, _ in NATIVE_DAILY_ROUTES)
    assert SOURCE.credentials == (('api_key', ('apiKey',)),)
    assert credential_schema('tickflow')['methods'][0]['method'] == 'api_key'
    for binding in (b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR'):
        key, target = binding.key.route_key(), {'providerId': 'tickflow', 'upstreamSource': 'tickflow'}
        assert CAPABILITY_SOURCES.resolve(key, target) is binding
        revisions = market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS['tickflow'], credential_version=1)
        assert revisions['adapterRevision'] == f"dsa-v3-cn-stock-tickflow-{key['adjustment']}-exact-daily-v1"
        assert revisions['sourceRevision'] == PROTOCOL
        assert binding.maximum_rows is None and binding.protocol == PROTOCOL


@pytest.mark.parametrize('probe,timeout,key_kind', [(False, 4.5, 'daily'), (True, 5, 'daily'), (False, 4.5, 'quote'), (True, 5, 'quote')])
def test_runtime_factory_keeps_snapshot_config_and_probe_budget(monkeypatch, store, probe, timeout, key_kind):
    store.save_provider_config('tickflow', {'requestId': 'fixture',
        'credentials': {'method': 'api_key', 'values': {'apiKey': 'synthetic-snapshot'}}})
    snapshot = store.provider_credential_snapshot('tickflow')
    calls = []
    monkeypatch.setattr('data_provider.tickflow_fetcher.TickFlowFetcher', lambda **kwargs: calls.append(kwargs) or object())
    monkeypatch.setattr('src.config.get_config', lambda: SimpleNamespace(tickflow_kline_adjust='hfq',
        tickflow_batch_daily_enabled=False, tickflow_batch_size=17, tickflow_priority=9))
    key = SOURCE.bindings[0].key.route_key() if key_kind == 'daily' else {
        'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    ThesisLedgerProviderRuntime(store)._adapter('tickflow', snapshot, probe=probe, market_v3_route_key=key)
    assert calls == [{'api_key': 'synthetic-snapshot', 'timeout': timeout, 'kline_adjust': 'hfq',
        'batch_daily_enabled': False, 'batch_size': 17, 'priority': 9}]


@pytest.mark.parametrize('failure,code', [('credential', 'credential_missing'), ('date', 'invalid_response'), ('empty', 'not_covered')])
def test_source_preserves_classified_errors(failure, code):
    raw = pd.DataFrame({'date': [], 'open': [], 'high': [], 'low': [], 'close': []})
    if failure == 'date':
        raw = pd.DataFrame({'open': [1], 'high': [1], 'low': [1], 'close': [1]})
    adapter = SimpleNamespace(_get_client=lambda: None)
    if failure != 'credential':
        adapter._get_client = lambda: SimpleNamespace(klines=SimpleNamespace(get=lambda *args, **kwargs: raw))
        adapter._daily_kline_count = lambda *args: 60
        adapter._date_to_ms = lambda *args, **kwargs: 0
    with pytest.raises(CapabilityContractError) as error:
        daily(DailyQuery.from_request(request()), ReadContext(adapter))
    assert error.value.code == code


def test_runtime_reuses_sdk_by_credential_snapshot_and_keeps_probe_isolated(monkeypatch, store):
    def configure(value):
        store.save_provider_config('tickflow', {'requestId': 'fixture',
            'credentials': {'method': 'api_key', 'values': {'apiKey': value}}})
        return store.provider_credential_snapshot('tickflow')

    calls = []
    def build(**options):
        adapter = SimpleNamespace(options=options)
        calls.append(adapter)
        return adapter
    monkeypatch.setattr('data_provider.tickflow_fetcher.TickFlowFetcher', build)
    runtime = ThesisLedgerProviderRuntime(store)
    key = SOURCE.bindings[0].key.route_key()
    first_snapshot = configure('synthetic-first')
    first = runtime._adapter('tickflow', first_snapshot, market_v3_route_key=key)
    assert runtime._adapter('tickflow', first_snapshot, market_v3_route_key=key) is first
    quote_key = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    assert runtime._adapter('tickflow', first_snapshot, market_v3_route_key=quote_key) is first
    assert len(calls) == 1
    second_snapshot = configure('synthetic-second')
    second = runtime._adapter('tickflow', second_snapshot, market_v3_route_key=key)
    assert second is not first and second.options['api_key'] == 'synthetic-second'
    assert first.options['api_key'] == 'synthetic-first'
    probe = runtime._adapter('tickflow', second_snapshot, cache=False, probe=True, market_v3_route_key=key)
    assert probe is not second and probe.options['timeout'] == 5
    assert runtime._adapter('tickflow', second_snapshot, market_v3_route_key=key) is second
    assert runtime._adapter_versions['tickflow'] == second_snapshot and len(calls) == 3
