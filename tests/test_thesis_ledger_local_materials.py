"""统一入口使用原解析器，验证重叠费用及冻结兼容。"""

from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from data_provider.thesis_ledger_sources import local_materials as local
from src.services import thesis_ledger_local_material_selection as selection
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_public_fees_contract_v1 import FeeRequest
from tests.test_thesis_ledger_notice_events_v3 import configured_client, admit, request as cash_request


def query(symbol='00147.HK', start='2025-08-06', end='2025-08-11'):
    return FeeRequest.model_validate({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'local-test',
        'routeKey': {'kind': 'data', 'market': 'HK', 'assetType': 'STOCK', 'capability': 'HISTORICAL_PUBLIC_FEES'},
        'routeTarget': {'providerId': 'local-materials', 'upstreamSource': 'local-materials', 'routeIndex': 0},
        'symbol': symbol, 'range': {'start': start, 'end': end}})


def test_complete_hk_package_selected_once_and_legacy_frozen_read_unchanged():
    request = query()
    candidates = local.origin_candidates(request.routeKey.model_dump())
    source, binding = selection.select_material(candidates, request)
    assert binding.upstream_source == 'hk-stock-public-fees'
    path = selection.material_path(source, binding)
    before = path.read_bytes()
    value = local.read(request, ReadContext(object()))
    assert value.routeTarget == request.routeTarget
    assert 'official-hk-rules:hk-stock-public-fees:' in value.provenance.providerRevision
    assert {charge.code for segment in value.segments for charge in segment.charges} == {'handlingFee', 'stampDuty', 'regulatoryFee', 'afrcLevy'}
    assert all(len({(charge.code, charge.side) for charge in segment.charges}) == len(segment.charges) for segment in value.segments)
    legacy_request = request.model_copy(update={'routeTarget': request.routeTarget.model_copy(update={
        'providerId': source.provider_id, 'upstreamSource': binding.upstream_source})})
    legacy = binding.read(legacy_request, ReadContext(source.factory(None)))
    assert value.segments == legacy.segments
    assert value.sourceDocuments == legacy.sourceDocuments
    assert path.read_bytes() == before


@pytest.mark.parametrize('sample,reason', [
    (query(symbol='00700.HK'), 'not_covered'),
    (query(start='2020-01-01'), 'insufficient_coverage'),
])
def test_outside_symbol_or_date_refused(sample, reason):
    with pytest.raises(CapabilityContractError) as error:
        local.read(sample, ReadContext(object()))
    assert error.value.code == reason


def test_conflicting_overlap_rejected(monkeypatch, tmp_path):
    request = query()
    candidates = local.origin_candidates(request.routeKey.model_dump())
    source, binding = candidates[0]
    pack = json.loads(selection.material_path(source, binding).read_bytes())
    first, second = tmp_path / 'first.json', tmp_path / 'second.json'
    first.write_text(json.dumps(pack))
    altered = deepcopy(pack)
    altered['coverageDescription'] = 'conflicting version'
    second.write_text(json.dumps(altered))
    alternate = SimpleNamespace(upstream_source='different', key=binding.key)
    monkeypatch.setattr(selection, 'material_path', lambda _source, item: first if item is binding else second)
    with pytest.raises(CapabilityContractError) as error:
        selection.select_material([(source, binding), (source, alternate)], request)
    assert error.value.code == 'basis_incompatible'


def test_cash_notices_do_not_gain_admission_through_facade():
    binding = next(item for item in local.SOURCE.bindings if item.key.capability == 'CASH_DISTRIBUTION')
    assert binding.admission_required is True
    assert local.catalog_readiness(SimpleNamespace(key=binding.key.route_key())) is None


def test_cash_facade_uses_original_admission_and_original_bytes(configured_client):
    from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
    from src.services import thesis_ledger_notice_dividends as notices
    _, runtime, store = configured_client
    key = notices.ROUTE_KEY
    target = {'providerId': 'local-materials', 'upstreamSource': 'local-materials'}
    assert local.current_cash_admission(store, key, target, {}) is None
    admit(store)
    legacy = execute_event_request(runtime, cash_request(runtime))
    admission_before = store.get_route_admission_v3(key=key, target=notices.ROUTE_TARGET)
    store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'facade-policy',
        'revision': 2, 'enabled': True, 'routes': [{'key': key, 'targets': [target]}]})
    payload = {**cash_request(runtime), 'desiredRevision': 2, 'effectivePolicyRevision': 2,
               'routeTarget': {**target, 'routeIndex': 0}}
    result = execute_event_request(runtime, payload)
    assert result['noticeDividendEvidence'] == legacy['noticeDividendEvidence']
    assert result['facts'][0]['cashAmount'] == legacy['facts'][0]['cashAmount']
    assert store.get_route_admission_v3(key=key, target=notices.ROUTE_TARGET) == admission_before
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, {**payload, 'symbol': '159516.SZ'})


@pytest.mark.parametrize('binding', [item for item in local.SOURCE.bindings if item.key.capability == 'HISTORICAL_PUBLIC_FEES'])
def test_every_unified_fee_scope_uses_original_parser(binding, configured_client):
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest
    _, runtime, store = configured_client
    candidates = local.origin_candidates(binding.key.route_key())
    source, original = candidates[-1]
    pack = json.loads(selection.material_path(source, original).read_bytes())
    sample = FeeRequest.model_validate({
        'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'all-fee-scopes',
        'routeKey': binding.key.route_key(), 'routeTarget': {
            'providerId': 'local-materials', 'upstreamSource': 'local-materials', 'routeIndex': 0},
        'symbol': pack.get('symbol', '600000.SH'), 'range': pack['coverage'],
    })
    result = local.read(sample, ReadContext(object()))
    assert result.range == sample.range
    assert result.routeKey == sample.routeKey
    assert result.provenance.historicalAvailability == 'notVerified'
    store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'fee-facade-policy',
        'revision': 1, 'enabled': True, 'routes': [{'key': binding.key.route_key(),
            'targets': [{'providerId': 'local-materials', 'upstreamSource': 'local-materials'}]}]})
    outer = ThesisLedgerDataRequest('HISTORICAL_PUBLIC_FEES', sample.symbol,
        instrument_type=binding.key.asset_type, start=sample.range.start, end=sample.range.end,
        request_id=sample.requestId, parameters=sample.model_dump(mode='json'))
    actual = runtime.execute_request(outer)
    assert actual.value['segments'] == result.model_dump(mode='json', exclude_unset=True)['segments']


@pytest.mark.parametrize('binding', [item for item in local.SOURCE.bindings if item.key.capability == 'HISTORICAL_TRADING_STATUS'])
def test_unified_historical_status_preserves_original_evidence(binding):
    source, original = local.origin_candidates(binding.key.route_key())[-1]
    pack = json.loads(selection.material_path(source, original).read_bytes())
    sample = binding.contract.request_schema.model_validate({
        'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'all-status-scopes',
        'routeKey': binding.key.route_key(), 'routeTarget': {
            'providerId': 'local-materials', 'upstreamSource': 'local-materials', 'routeIndex': 0},
        'symbol': pack['symbol'], 'range': pack['coverage'],
    })
    result = local.read(sample, ReadContext(object()))
    assert result.routeTarget == sample.routeTarget
    assert result.sourceDocuments
