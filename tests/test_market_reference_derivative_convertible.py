"""可转债未来日期、未知状态、空值与分页拒绝。"""

from types import SimpleNamespace
import pytest
from data_provider.market_reference_convertible import listing_status, parse_convertible
from tests.derivative_reference_samples import convertible_row


@pytest.mark.parametrize('patch,expected', [
    ({'DELIST_DATE': '2026-12-01'}, '已上市'),
    ({'LISTING_DATE': '2026-12-01'}, '待上市'),
    ({'DELIST_DATE': '2026-11-03'}, '已摘牌'),
    ({'TRADE_MARKET': 'STAS00', 'LISTING_DATE': None}, '已摘牌'),
    ({'TRADE_MARKET': 'UNRECOGNIZED'}, '未知'),
    ({'LISTING_DATE': None, 'PUBLIC_START_DATE': None}, '未知'),
])
def test_listing_state_uses_beijing_day_and_keeps_future_dates(patch, expected):
    assert listing_status(convertible_row() | patch, '2026-11-03') == expected


def payload(rows):
    return {'success': True, 'code': 0, 'result': {'count': len(rows), 'pages': 1, 'data': rows}}


def test_quotes_missing_vs_zero_and_no_date_guess():
    rows, total = parse_convertible(payload([convertible_row()]), SimpleNamespace(page=1, pageSize=5), '2026-11-03')
    assert total == 1 and rows[0]['bondPrice'] is None and rows[0]['premiumPct'] == '0'
    assert rows[0]['expireDate'] is None and rows[0]['stockCode'] == '000001'


def test_missing_quote_key_is_protocol_failure_not_null_quote():
    row = convertible_row();row.pop('CURRENT_BOND_PRICE')
    with pytest.raises(ValueError):
        parse_convertible(payload([row]), SimpleNamespace(page=1, pageSize=5), '2026-11-03')


def test_page_and_duplicate_identity_rejected():
    row = convertible_row()
    with pytest.raises(ValueError):
        parse_convertible(payload([row, row]), SimpleNamespace(page=1, pageSize=5), '2026-11-03')
    p = payload([row]);p['result']['pages'] = 2
    with pytest.raises(ValueError):
        parse_convertible(p, SimpleNamespace(page=1, pageSize=5), '2026-11-03')
