"""新浪期货字段布局、连续／具体身份和日线重复拒绝。"""

import pytest
from data_provider.market_reference_futures_sina import parse_futures_quote, parse_futures_kline, parse_a50
from data_provider.market_reference_derivatives_common import sina_values
from tests.derivative_reference_samples import future_fields, sina


def test_commodity_quote_zero_count_and_clock():
    row = parse_futures_quote(future_fields(), 'RB0')[0]
    assert row['contract'] == 'RB0' and row['volume'] == '0' and row['last'] == '11'
    assert row['sourceTime'] == '2026-11-03T15:00:00+08:00'
    assert row['previousClose'] is None


def test_cffex_uses_different_slots_for_concrete_contract():
    fields = ['0'] * 50
    fields[0:7] = ['10', '12', '9', '11', '0', '0', '20']
    fields[36:38] = ['2026-11-03', '15:00:00'];fields[49] = '样本股指期货'
    row = parse_futures_quote(fields, 'IF2611')[0]
    assert row['contract'] == 'IF2611' and row['last'] == '11' and row['openInterest'] == '20'
    assert row['upperLimit'] is None


def test_kline_identity_duplicate_and_null_response():
    raw = b'var _M0=([{"d":"2026-11-03","o":"10","h":"12","l":"9","c":"11","s":"0","v":"0","p":"20"}]);'
    row = parse_futures_kline(raw, 'M0')[0]
    assert row['settle'] is None and row['volume'] == '0'
    assert parse_futures_kline(b'var _M0=(null);', 'M0') == []
    with pytest.raises(ValueError):
        parse_futures_kline(raw, 'RB0')
    duplicate = raw.replace(b'}]', b'},{"d":"2026-11-03","o":"10","h":"12","l":"9","c":"11","s":"0","v":"0","p":"20"}]')
    with pytest.raises(ValueError):
        parse_futures_kline(duplicate, 'M0')


def test_quote_variable_identity_is_not_inferred_from_response_position():
    raw = sina('nf_RB0', future_fields())
    with pytest.raises(ValueError):
        sina_values(raw, ['nf_M0'])
    with pytest.raises(ValueError):
        sina_values(raw + raw, ['nf_RB0'])


def test_a50_retains_source_cfd_code():
    fields = ['100', '', '99', '101', '102', '98', '15:00:00', '99', '98', '0', '0', '0', '2026-11-03', '来源 A50']
    row = parse_a50(fields)[0]
    assert row['sourceCode'] == 'CHA50CFD' and row['sourceTime'].endswith('+08:00')
