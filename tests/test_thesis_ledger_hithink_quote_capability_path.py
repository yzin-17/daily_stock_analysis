"""HiThink 实际来源工厂、原 SDK、同次快照与标准结果的完整查询接缝。"""

from datetime import datetime, timezone

import pytest

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ThesisLedgerGatewayError, ThesisLedgerProviderRuntime
from tests.test_thesis_ledger_hithink_quote import Response, item
from tests.test_thesis_ledger_hithink_quote_runtime import route, _request, _policy


def install_sdk(monkeypatch, get):
    from src.services import thesis_ledger_hithink_quote as sdk
    original = sdk.HiThinkSnapshotAdapter
    constructors = []

    def build(**kwargs):
        constructors.append(kwargs.copy())
        return original(**kwargs, http_get=get,
                        clock=lambda: datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(sdk, 'HiThinkSnapshotAdapter', build)
    return constructors


def test_actual_source_factory_and_original_sdk_preserve_one_call_and_standard_result(route, monkeypatch):
    store, asset, symbol, source = route
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response({'code': 0, 'data': {'timestamp': None, 'item': [item(symbol)]}})
    constructors = install_sdk(monkeypatch, get)
    result = ThesisLedgerProviderRuntime(store).execute_request(_request(asset, symbol))
    assert constructors == [{'api_key': 'synthetic-quote-key-v1', 'timeout_seconds': 4.5}]
    assert len(calls) == 1 and calls[0][1]['timeout'] == 4.5
    assert calls[0][1]['params'] == {('thscodes' if asset == 'STOCK' else 'thscode'): symbol}
    assert calls[0][1]['headers']['X-api-key'] == 'synthetic-quote-key-v1'
    assert calls[0][1]['allow_redirects'] is False
    assert result.value.price == 10.5 and result.value.pre_close == 10.7
    assert result.value.source == 'hithink/' + source and result.upstream_source == source
    assert result.value.provider_timestamp is None and result.value.fetched_at == '2026-09-28T10:00:00+00:00'
    assert result.value.units.volume == ('share' if asset == 'STOCK' else 'unknown')
    assert store.get_route_admission_v3(key={'kind': 'data', 'market': 'CN', 'assetType': asset, 'capability': 'REALTIME_QUOTE'},
                                      target={'providerId': 'hithink', 'upstreamSource': source}) is None


@pytest.mark.parametrize('mutation', ['credential', 'policy'])
def test_actual_factory_uses_guard_snapshot_and_rejects_late_generation(route, monkeypatch, mutation):
    store, asset, symbol, _ = route
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs['headers']['X-api-key'])
        if mutation == 'credential':
            monkeypatch.setenv('HITHINK_API_KEY', 'synthetic-quote-key-v2')
        else:
            store.apply_policy_v3(_policy(asset, 'a-share-prices-snapshot' if asset == 'STOCK' else 'fund-market-snapshot', revision=2))
        return Response({'code': 0, 'data': {'timestamp': None, 'item': [item(symbol)]}})
    constructors = install_sdk(monkeypatch, get)
    with pytest.raises(ThesisLedgerGatewayError) as error:
        ThesisLedgerProviderRuntime(store).execute_request(_request(asset, symbol))
    assert error.value.code == 'not_admitted' and calls == ['synthetic-quote-key-v1']
    assert constructors == [{'api_key': 'synthetic-quote-key-v1', 'timeout_seconds': 4.5}]


@pytest.mark.parametrize('failure', ['http', 'identity'])
def test_original_sdk_errors_keep_retryable_and_exactly_one_http(route, monkeypatch, failure):
    store, asset, symbol, _ = route
    calls = []

    def get(*args, **kwargs):
        calls.append(True)
        if failure == 'http':
            return Response({}, status_code=503)
        return Response({'code': 0, 'data': {'timestamp': None, 'item': [item('000995.SZ')]}})
    install_sdk(monkeypatch, get)
    with pytest.raises(ThesisLedgerGatewayError) as error:
        ThesisLedgerProviderRuntime(store).execute_request(_request(asset, symbol))
    assert error.value.code == ('upstream_failure' if failure == 'http' else 'invalid_response')
    assert error.value.retryable is (failure == 'http') and calls == [True]


def test_quote_inventory_and_legacy_helpers_derive_the_same_bindings():
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_hithink_quote_admission import iter_hithink_quote_routes, hithink_quote_revisions
    routes = list(iter_hithink_quote_routes())
    assert len(routes) == 2
    inventory = list(exact_route_inventory())
    for key, target in routes:
        assert sum(k == key and t == target for k, t, _ in inventory) == 1
        binding = CAPABILITY_SOURCES.resolve(key, target)
        assert hithink_quote_revisions(key['assetType'], target['upstreamSource']) == {
            'adapterRevision': binding.adapter_revision, 'sourceRevision': binding.source_revision}
    assert hithink_quote_revisions('STOCK', 'fund-market-snapshot') is None


def test_public_quote_dispatch_cannot_forward_unregistered_sdk_method():
    from types import SimpleNamespace
    from src.services.thesis_ledger_quote_dispatch import realtime_quote
    adapter = SimpleNamespace(get_realtime_quote=lambda *args: pytest.fail('不能透传未登记 SDK 方法'))
    with pytest.raises(CapabilityContractError) as error:
        realtime_quote(adapter, 'unregistered', '600519.SH', 'STOCK', 'unregistered', CapabilityContractError)
    assert error.value.code == 'unsupported_source'
