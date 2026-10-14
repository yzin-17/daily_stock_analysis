"""标准报价复制、Schema与单位错误延后，源对象不修改。"""
from types import SimpleNamespace
from dataclasses import replace

import pytest
from pydantic import ValidationError

from src.services.thesis_ledger_quote_validation import validate_quote
from src.services.thesis_ledger_quote_result_contract import QuoteResult
from src.services.thesis_ledger_capability_contracts import QuoteQuery, ReadContext, CapabilityContractError
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from data_provider.thesis_ledger_sources.hithink import SOURCE


def value(**changes):
    return SimpleNamespace(**{**dict(price=10, pre_close=None, change_amount=1, open_price=9, high=11, low=8,
        volume=None, amount='--', provider_timestamp=None, fetched_at='2026-10-12T00:00:00Z',
        source='hithink/a-share-prices-snapshot', units={'price_currency':'CNY','volume':'share','turnover_currency':'CNY'},
        sdk_secret='not a public field'), **changes})


def test_result_is_typed_frozen_copy_and_source_does_not_change():
    original = value()
    result = validate_quote(original)
    assert isinstance(result, QuoteResult) and result.pre_close == 9
    assert original.pre_close is None and result is not original
    assert result.volume is None and result.amount is None
    assert not hasattr(result, 'sdk_secret') and 'sdk_secret' not in result.model_dump()
    with pytest.raises(ValidationError): result.price = 100
    original.price = 999
    assert result.price == 10


def test_immutable_source_can_use_same_snapshot_previous_close_derivation():
    class Original:
        __slots__ = ()
        price=10; pre_close=None; change_amount=1; open_price=9; high=11; low=8
    assert validate_quote(Original()).pre_close == 9


@pytest.mark.parametrize('bad', [True, -1, float('inf'), 'bad'])
def test_constructed_bad_required_number_cannot_bypass_schema(bad):
    result = QuoteResult.model_construct(price=bad, pre_close=9, open_price=9, high=11, low=8)
    with pytest.raises(CapabilityContractError): validate_quote(result)


def execute(raw):
    binding = next(b for b in SOURCE.bindings if b.key.capability=='REALTIME_QUOTE' and b.key.asset_type=='STOCK')
    calls=[]
    registry=CapabilityRegistry((replace(SOURCE, bindings=tuple(replace(b, read=lambda q,c: calls.append(q.symbol) or raw)
        if b is binding else b for b in SOURCE.bindings)),))
    result=registry.quote(QuoteQuery(symbol='600519.SH',instrument_type='STOCK'), binding.key.route_key(),
        {'providerId':'hithink','upstreamSource':binding.upstream_source},ReadContext(object()))
    return result,calls


def test_bound_result_contains_only_standard_identity_and_units():
    original=value(pre_close=9)
    result,calls=execute(original)
    assert result.upstream_source=='a-share-prices-snapshot' and result.units.volume=='share'
    assert result.units.priceCurrency=='CNY' and result.unit_error is None
    assert calls==['600519.SH'] and original.units=={'price_currency':'CNY','volume':'share','turnover_currency':'CNY'}
    assert 'price_currency' not in result.model_dump_json() and not hasattr(result,'sdk_secret')
    checked=validate_quote(result)
    assert checked.units==result.units and checked.upstream_source==result.upstream_source


def test_invalid_units_are_captured_without_changing_query_lifecycle():
    result,calls=execute(value(pre_close=9,units={}))
    assert result.units is None and result.unit_error.code=='upstream_invalid_response'
    assert result.unit_error.status_code==502 and calls==['600519.SH']


def test_missing_market_clock_does_not_use_other_sdk_timestamp():
    result=validate_quote(value(timestamp='2026-10-12T01:00:00Z'))
    assert result.provider_timestamp is None and not hasattr(result,'timestamp')
    assert result.source_time_observation.text=='2026-10-12T01:00:00Z' and result.source_time_observation.basis=='unverified'


def test_typed_source_can_return_standard_units_without_raw_sdk_projection():
    from src.services.thesis_ledger_quote_units_contract import QuotePublicUnits
    result=validate_quote(value(pre_close=9)).model_copy(update={
        'upstream_source':'a-share-prices-snapshot',
        'units':QuotePublicUnits(priceCurrency='CNY',volume='share',turnoverCurrency='CNY')})
    accepted,calls=execute(result)
    assert accepted.units==result.units and accepted.unit_error is None and calls==['600519.SH']


def test_typed_source_cannot_report_another_bound_target():
    result=validate_quote(value(pre_close=9)).model_copy(update={'upstream_source':'other-source'})
    with pytest.raises(CapabilityContractError) as rejected: execute(result)
    assert rejected.value.code=='invalid_response'


def test_bad_fetch_clock_type_is_not_replaced_with_current_time():
    with pytest.raises(CapabilityContractError): validate_quote(value(fetched_at=12345))
