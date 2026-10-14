"""当前资料合同：精确整数、未知与身份关联；查询参数不污染其他能力。"""

from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, QueryBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_security_static_contract_v1 import CONTRACT, StaticRequest, StaticResult
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest


def result(**changes):
    return {'symbol': '00700.HK', 'market': 'HK', 'assetType': 'STOCK', 'name': None,
        'securityId': '9007199254740993', 'sharesPerLot': None, 'listingDate': None, 'delisted': None,
        'exchange': None, 'provider': 'fixture-static', 'observedAt': '2026-07-01T00:00:00+00:00',
        'historicalChanges': 'unverified', **changes}


def test_schema_preserves_string_id_optional_unknowns_and_query_only():
    value = StaticResult.model_validate(result())
    assert value.model_dump() == result()
    assert StaticResult.model_json_schema()['properties']['securityId']['anyOf'][0]['type'] == 'string'
    assert CONTRACT.operations == ('query',)


@pytest.mark.parametrize('changes', [{'securityId': 9007199254740993}, {'securityId': '1.0'},
    {'sharesPerLot': '0'}, {'sharesPerLot': True}, {'delisted': 'false'}, {'historicalChanges': 'verified'},
    {'observedAt': '2026-07-01'}, {'listingDate': '20260701'}, {'listingDate': '2026-02-30'},
    {'market': 'US'}, {'coverageProof': 'not-a-price-proof'}])
def test_invalid_result_does_not_gain_precision_history_or_price_proof(changes):
    with pytest.raises(ValidationError):
        StaticResult.model_validate(result(**changes))


@pytest.mark.parametrize('changes', [{'start': '2026-07-01'}, {'end': '2026-07-01'}, {'timeframe': '1d'},
    {'adjustment': 'none'}, {'parameters': {'asOf': '2026-07-01'}}])
def test_current_request_rejects_historical_and_extra_parameters(changes):
    with pytest.raises(ValueError):
        StaticRequest.from_request(ThesisLedgerDataRequest('SECURITY_STATIC_INFO', '00700.HK', instrument_type='STOCK', **changes))


@pytest.mark.parametrize('symbol', ['AAPL', '700.HK', '00000.HK', '600519.SH', 'A.HK.US', 'aapl.US'])
def test_noncanonical_identity_fails_before_source(symbol):
    with pytest.raises(ValidationError):
        StaticRequest(symbol=symbol, instrument_type='STOCK')


@pytest.mark.parametrize('changes', [{'symbol': '00147.HK'}, {'assetType': 'ETF'}, {'provider': 'other'}])
def test_standard_handler_correlates_identity_asset_and_provider(changes):
    calls = []
    def read(request, context):
        calls.append(request)
        return result(**changes)
    binding = QueryBinding(CONTRACT, ExactKey('HK', 'STOCK', 'SECURITY_STATIC_INFO'), 'fixture-static', 'adapter-v1', 'source-v1', read)
    registry = CapabilityRegistry((SourceDefinition('fixture-static', lambda: {}, lambda _: SimpleNamespace(), (binding,)),))
    with pytest.raises(CapabilityContractError) as error:
        registry.query(CONTRACT, StaticRequest(symbol='00700.HK', instrument_type='STOCK'), binding.key.route_key(),
            {'providerId': 'fixture-static', 'upstreamSource': 'fixture-static'}, ReadContext(None))
    assert error.value.code == 'invalid_response' and len(calls) == 1


def test_second_source_reuses_same_static_contract_without_public_execution_changes():
    binding = QueryBinding(CONTRACT, ExactKey('HK', 'STOCK', 'SECURITY_STATIC_INFO'), 'fixture-static',
        'adapter-v1', 'source-v1', lambda request, context: result())
    registry = CapabilityRegistry((SourceDefinition('fixture-static', lambda: {}, lambda _: object(), (binding,)),))
    value = registry.query(CONTRACT, StaticRequest(symbol='00700.HK', instrument_type='STOCK'), binding.key.route_key(),
        {'providerId': 'fixture-static', 'upstreamSource': 'fixture-static'}, ReadContext(None))
    assert value.securityId == '9007199254740993' and value.delisted is None


def test_admission_fact_rejects_truthy_untyped_value():
    binding = QueryBinding(CONTRACT, ExactKey('HK', 'STOCK', 'SECURITY_STATIC_INFO'), 'fixture-static',
        'adapter-v1', 'source-v1', lambda request, context: result(), admission_required='false')
    with pytest.raises(ValueError, match='查询准入事实'):
        CapabilityRegistry((SourceDefinition('fixture-static', lambda: {}, lambda _: object(), (binding,)),))
