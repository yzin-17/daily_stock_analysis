"""期权标的、月份哨兵、报价与 Greeks 的独立布局和计量。"""

import pytest
from data_provider.market_reference_etf_options import nearest_month, parse_contracts, parse_option_quote, parse_greeks
from data_provider.market_reference_derivatives_common import sina_values
from src.services.thesis_ledger_reference_catalog_v1 import columns
from tests.derivative_reference_samples import option_fields, greeks_fields, sina


def test_nearest_contract_month_is_not_calendar_hint():
    payload = {'result': {'status': {'code': 0}, 'data': {'stockId': '510050', 'contractMonth': ['2026-11', '2026-12', '2027-03']}}}
    assert nearest_month(payload, '510050') == '2612'
    with pytest.raises(ValueError):
        nearest_month(payload, '588000')


def test_two_sided_chain_preserves_codes_and_rejects_duplicate_or_wrong_variable():
    raw = sina('OP_UP_5100502611', ['CON_OP_10012440', '']) + sina('OP_DOWN_5100502611', ['CON_OP_10012441', ''])
    rows = parse_contracts(raw, '510050', '2611')
    assert [r['optionType'] for r in rows] == ['认购', '认沽']
    with pytest.raises(ValueError):
        parse_contracts(raw.replace(b'10012441', b'10012440'), '510050', '2611')
    with pytest.raises(ValueError):
        parse_contracts(raw, '510300', '2611')


def test_quote_expiry_underlying_and_zero():
    row = parse_option_quote(option_fields(), '10012440')[0]
    assert row['underlying'] == '510050.SH' and row['expireDate'] == '2026-11-25'
    assert row['volume'] == '0' and row['sourceTime'].endswith('+08:00')
    bad = option_fields();bad[36] = '600519'
    with pytest.raises(ValueError):
        parse_option_quote(bad, '10012440')
    bad = option_fields();bad[45] = ''
    with pytest.raises(ValueError):
        parse_option_quote(bad, '10012440')


def test_greeks_empty_slots_and_iv_decimal_are_not_shifted():
    row = parse_greeks(greeks_fields(), '10012440')[0]
    assert row['delta'] == '0.2' and row['theta'] == '-0.2' and row['iv'] == '0.1317'
    assert row['tradeCode'] == '510050C2611M03000'
    bad = greeks_fields();bad[1] = 'unexpected'
    with pytest.raises(ValueError):
        parse_greeks(bad, '10012440')


def test_option_units_are_shares_premium_and_contract_counts():
    cols = {c['key']: c['unit'] for c in columns('etf-option-quote')}
    assert cols['volume'] == '张' and cols['last'] == '元／份'
    assert {c['key']: c['unit'] for c in columns('etf-option-greeks')}['iv'] == '比例'


def test_explicit_empty_contract_vs_missing_protocol():
    assert parse_option_quote([], '10012440') == []
    assert sina_values(sina('CON_OP_10012440', []), ['CON_OP_10012440']) == {'CON_OP_10012440': []}
    with pytest.raises(ValueError):
        sina_values(b'<html>blocked</html>', ['CON_OP_10012440'])
