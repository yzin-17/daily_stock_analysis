"""每手当前参考的标准模型、规范化、摘要与新来源隔离。"""

from types import SimpleNamespace
from unittest.mock import Mock

from pydantic import ValidationError
import pytest

from data_provider.security_lot_size import lot_reference
from src.services.thesis_ledger_security_lot_contract_v1 import CONTRACT, LotRequest, LotResult
from src.services.thesis_ledger_security_lot_values import reference_digest
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, QueryBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions
from tests.test_security_lot_size_routes import setup
from tests.test_thesis_ledger_futu_capability_path import configure


def test_standard_schema_keeps_legacy_digest_and_aliases():
    for symbol in ('700.HK', 'HK.00700', '00700.HK'):
        assert LotRequest(symbol=symbol).symbol == '00700.HK'
    old = lot_reference('700.HK', '100', 'futu', '2026-07-01T00:00:00Z')
    assert LotResult.model_validate(old).model_dump() == old
    assert LotResult.model_json_schema()['properties']['sharesPerLot']['type'] == 'string'


@pytest.mark.parametrize('field,value', [('sharesPerLot', 100), ('sharesPerLot', '0'), ('sharesPerLot', '100.5'),
    ('sharesPerLot', '1000000000000000'), ('historicalChanges', 'verified'), ('symbol', '00147.HK')])
def test_bad_lot_or_history_or_identity_cannot_be_hidden_by_old_digest(field, value):
    data = lot_reference('00700.HK', 100, 'futu', '2026-07-01T00:00:00Z')
    data[field] = value
    with pytest.raises(ValidationError):
        LotResult.model_validate(data)


@pytest.mark.parametrize('changes', [{'start': '2026-07-01'}, {'end': '2026-07-01'},
    {'adjustment': 'none'}, {'timeframe': '1d'}, {'parameters': {'asOf': '2026-07-01'}}])
def test_parameters_are_current_only_and_capability_specific(changes):
    with pytest.raises(ValueError):
        LotRequest.from_request(ThesisLedgerDataRequest('SECURITY_LOT_SIZE', '00700.HK', instrument_type='STOCK', **changes))


def test_new_provider_reuses_contract_without_provider_list_or_execution_change():
    stamp = '2026-07-01T00:00:00Z'
    data = {'symbol': '00700.HK', 'sharesPerLot': '200', 'historicalChanges': 'unverified',
        'source': {'provider': 'fixture-lot', 'sourceUrl': 'https://example.test/static', 'observedAt': stamp,
                   'sha256': reference_digest('00700.HK', '200', 'fixture-lot', stamp)}}
    binding = QueryBinding(CONTRACT, ExactKey('HK', 'STOCK', 'SECURITY_LOT_SIZE'), 'fixture-lot',
        'adapter-v1', 'source-v1', lambda request, context: data, admission_required=False)
    registry = CapabilityRegistry((SourceDefinition('fixture-lot', lambda: {}, lambda _: object(), (binding,)),))
    value = registry.query(CONTRACT, LotRequest(symbol='700.HK'), binding.key.route_key(),
        {'providerId': 'fixture-lot', 'upstreamSource': 'fixture-lot'}, ReadContext(None))
    assert value.sharesPerLot == '200'
    with pytest.raises(CapabilityContractError):
        registry.query(CONTRACT, LotRequest(symbol='00147.HK'), binding.key.route_key(),
            {'providerId': 'fixture-lot', 'upstreamSource': 'fixture-lot'}, ReadContext(None))


def test_two_bindings_keep_revisions_and_configuration_semantics():
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS, current_data_adapter_revisions
    from src.services.thesis_ledger_price_route_access import configured_data_route
    bindings = [(k, t, b) for k, t, b in CAPABILITY_SOURCES.routes('SECURITY_LOT_SIZE')]
    assert len(bindings) == 2
    for key, target, binding in bindings:
        provider = target['providerId']
        assert binding.gated and binding.admission_required is False and configured_data_route(key, target)
        assert (key['market'], key['assetType'], 'SECURITY_LOT_SIZE', provider, provider) not in _DATA_ADAPTERS
        assert current_data_adapter_revisions(key, target) == {'adapterRevision': f'{provider}-security-lot-size-v1',
            'sourceRevision': f'{provider}-static-info-v1', 'credentialRevision': 'not-required'}


def test_longbridge_actual_factory_keeps_ticker_board_and_original_cached_observation(monkeypatch, setup):
    from data_provider.longbridge_fetcher import LongbridgeFetcher
    info = SimpleNamespace(symbol='700.HK', board='HKEquity', lot_size=100)
    read = Mock(return_value=info)
    monkeypatch.setattr(LongbridgeFetcher, '_get_static_info', read)
    key = {'kind': 'data', 'market': 'HK', 'assetType': 'STOCK', 'capability': 'SECURITY_LOT_SIZE'}
    apply(setup, key, {'providerId': 'longbridge', 'upstreamSource': 'longbridge'})
    runtime = ThesisLedgerProviderRuntime(setup)
    adapter = runtime._adapter('longbridge')
    adapter._static_cache['700.HK'] = (info, 1000)
    value = runtime.execute_request(ThesisLedgerDataRequest('SECURITY_LOT_SIZE', '00700.HK', instrument_type='STOCK')).value
    assert value['source']['observedAt'] == '1970-01-01T00:16:40+00:00'
    read.assert_called_once_with('700.HK')
    no_admissions(setup)


def test_futu_actual_factory_uses_original_process_once_then_same_cached_reference(monkeypatch, store):
    import json
    import subprocess
    from data_provider.futu_lot_size import RESULT_PREFIX
    configure(store)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=RESULT_PREFIX + json.dumps(
        {'sharesPerLot': '100', 'observedAt': '2026-07-01T00:00:00Z'})))
    monkeypatch.setattr(subprocess, 'run', run)
    key = {'kind': 'data', 'market': 'HK', 'assetType': 'STOCK', 'capability': 'SECURITY_LOT_SIZE'}
    apply(store, key, {'providerId': 'futu', 'upstreamSource': 'futu'})
    runtime = ThesisLedgerProviderRuntime(store)
    request = ThesisLedgerDataRequest('SECURITY_LOT_SIZE', '700.HK', instrument_type='STOCK')
    first = runtime.execute_request(request).value
    second = runtime.execute_request(request).value
    assert first == second and first['symbol'] == '00700.HK'
    assert run.call_count == 1 and run.call_args.kwargs['timeout'] == 4.5
    assert run.call_args.args[0][-1] == 'data_provider.futu_lot_size'
    no_admissions(store)
