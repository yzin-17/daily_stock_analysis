"""日线单位纯投影共享标准结果，来源私有合同不进入公共API。"""

from dataclasses import replace
from copy import deepcopy

import pytest

from api import thesis_ledger_source_basis as api
from data_provider.thesis_ledger_sources.akshare import SOURCE
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, SourceDefinition
from src.services.thesis_ledger_daily_units_contract import DailyFieldUnits, DailyUnitsPolicy
from tests.test_thesis_ledger_market_v3 import REQUEST


def test_different_source_private_structure_uses_same_projection_entry(monkeypatch):
    calls = []
    original = next(binding for binding in SOURCE.bindings
                    if binding.key.route_key() == REQUEST['routeKey'] and binding.upstream_source == 'eastmoney')

    def project(context):
        calls.append(context)
        assert not hasattr(context, 'runtime') and not hasattr(context, 'adapter')
        assert context.request == REQUEST
        wire = context.metadata['venue-measures']
        assert wire == {'quantity': ['lot', 100], 'cash': {'currency': 'CNY'}}
        return DailyFieldUnits(volume='hand', amount='CNY')

    binding = replace(original, upstream_source='venue-wire',
                      public_units_policy=DailyUnitsPolicy(('venue-measures',), project))
    registry = CapabilityRegistry((SourceDefinition('unit-fixture', lambda: {}, lambda _: None, (binding,)),))
    monkeypatch.setattr(api, 'CAPABILITY_SOURCES', registry)
    attrs = {'venue-measures': {'quantity': ['lot', 100], 'cash': {'currency': 'CNY'}}}
    before = deepcopy(attrs)
    assert api.native_field_units(attrs, REQUEST['routeKey'], 'unit-fixture', 'venue-wire', REQUEST) == {
        'fieldUnits': {'volume': 'hand', 'amount': 'CNY'}}
    assert attrs == before and len(calls) == 1
    with pytest.raises(ValueError):
        api.native_field_units(attrs, REQUEST['routeKey'], 'unit-fixture', 'other', REQUEST)
    assert len(calls) == 1


@pytest.mark.parametrize('provider,source', [('tencent', 'tencent'), ('akshare', 'sina'), ('hithink', 'hithink-financial-api')])
def test_foreign_private_contract_does_not_silently_disappear(provider, source):
    with pytest.raises(ValueError):
        api.native_field_units({'native_daily_contract': {}}, REQUEST['routeKey'], provider, source)


def test_two_source_contracts_are_rejected_before_projection():
    with pytest.raises(ValueError):
        api.native_field_units({'native_daily_contract': {}, 'hithink_etf_field_units': {}},
                               REQUEST['routeKey'], 'akshare', 'eastmoney')


@pytest.mark.parametrize('units', [{'volume': 'lots', 'amount': 'CNY'}, {'volume': 'hand', 'amount': 'USD'},
                                 {'volume': 'hand', 'amount': 'CNY', 'sdk': 1}])
def test_standard_units_strictly_reject_unrecognized_values(units):
    with pytest.raises(ValueError):
        DailyFieldUnits.model_validate(units)


@pytest.mark.parametrize('names,read', [((), lambda _: None), (('x', 'x'), lambda _: None),
                                    (('x',), None), (['x'], lambda _: None)])
def test_invalid_policy_is_rejected_at_definition(names, read):
    with pytest.raises(ValueError):
        DailyUnitsPolicy(names, read)


def test_invalid_binding_policy_is_rejected_at_registration():
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, bindings=(replace(SOURCE.bindings[0], public_units_policy=1),),
                                    quote_source_aliases=()),))


def test_absent_contract_keeps_existing_omission():
    assert api.native_field_units({}, REQUEST['routeKey'], 'akshare', 'eastmoney') == {}
    assert api.native_field_units({}, REQUEST['routeKey'], 'unknown-provider', 'unknown') == {}


@pytest.mark.parametrize('changes', [{'timeframe': '1w'}, {'market': 'US'}, {'capability': 'REALTIME_QUOTE'}])
def test_private_units_require_the_complete_exact_key(changes):
    with pytest.raises(ValueError):
        api.native_field_units({'native_daily_contract': {}}, {**REQUEST['routeKey'], **changes}, 'akshare', 'eastmoney')


@pytest.mark.parametrize('result', [{}, None, DailyFieldUnits.model_construct(volume='lots', amount='CNY')])
def test_present_contract_cannot_return_missing_or_unvalidated_result(monkeypatch, result):
    original = next(binding for binding in SOURCE.bindings
                    if binding.key.route_key() == REQUEST['routeKey'] and binding.upstream_source == 'eastmoney')
    policy = DailyUnitsPolicy(('fixture-units',), lambda _: result)
    registry = CapabilityRegistry((SourceDefinition('fixture', lambda: {}, lambda _: None,
                                    (replace(original, public_units_policy=policy),)),))
    monkeypatch.setattr(api, 'CAPABILITY_SOURCES', registry)
    with pytest.raises(ValueError):
        api.native_field_units({'fixture-units': {}}, REQUEST['routeKey'], 'fixture', 'eastmoney')
