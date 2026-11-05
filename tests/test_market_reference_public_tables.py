"""公开利率、份额和指数的单位、日期及覆盖反例。"""

import io
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.market_reference_public_rates import parse_fixing
from data_provider.market_reference_public_funds import parse_sse, parse_szse
from data_provider.market_reference_public_indices import parse_index
from src.services.thesis_ledger_reference_contract_v1 import ReferenceRequest


def test_fixing_preserves_negative_and_zero_rates():
    records = parse_fixing(b'\xef\xbb\xbf\n2026-11-03,,,,,,-0.1,0,1.25\n')
    assert records == [{'date': '2026-11-03', 'overnight': '-0.1', 'oneWeek': '0', 'fortnight': '1.25'}]


@pytest.mark.parametrize('body', [b'2026-11-03,,,,,,1,2', b'2026-11-03,x,,,,,1,2,3',
                                      b'2026-11-03,,,,,,1,,3', b'2026-11-03,,,,,,NaN,2,3',
                                      b'2026-11-03,,,,,,1,2,3\n2026-11-03,,,,,,1,2,3'])
def test_fixing_rejects_changed_layout_missing_values_and_duplicate_dates(body):
    with pytest.raises(ValueError):
        parse_fixing(body)


def sse_payload():
    return {'pageHelp': {'total': 1}, 'result': [{'STAT_DATE': '2026-11-03', 'SEC_CODE': '510300',
            'SEC_NAME': '测试 ETF', 'TOT_VOL': '100.25', 'ETF_TYPE': '跨市'}]}


def test_sse_keeps_shares_in_ten_thousand_units():
    query = SimpleNamespace(start='2026-11-03', symbol='510300.SH')
    rows = parse_sse(sse_payload(), query)
    assert rows[0]['shares10k'] == '100.25'
    assert rows[0]['date'] == query.start


@pytest.mark.parametrize('fault', ['date', 'total', 'duplicate', 'missing_shares'])
def test_sse_rejects_stale_partial_or_corrupt_snapshots(fault):
    value = sse_payload()
    if fault == 'date':
        value['result'][0]['STAT_DATE'] = '2026-11-02'
    elif fault == 'total':
        value['pageHelp']['total'] = 2
    elif fault == 'duplicate':
        value['result'].append(dict(value['result'][0]))
        value['pageHelp']['total'] = 2
    else:
        value['result'][0]['TOT_VOL'] = None
    with pytest.raises(ValueError):
        parse_sse(value, SimpleNamespace(start='2026-11-03', symbol='510300.SH'))


def szse_payload():
    return [{'metadata': {'catalogid': '1000_lf', 'tabkey': 'tab1', 'pagecount': 1,
                         'recordcount': 1, 'subname': '2026-11-03', 'cols': {'dqgm': '当前规模（万份）'}},
             'data': [{'sys_key': '<u>159915</u>', 'jjjcurl': '<u>测试 ETF</u>', 'jjlb': 'ETF',
                       'dqgm': '<a>1,234.50</a>', 'tzlb': '股票基金'}]},
            {'metadata': {'catalogid': 'other', 'tabkey': 'tab2'}, 'data': []}]


def test_szse_selects_exact_table_with_other_tabs_and_single_security():
    records = parse_szse(szse_payload(), SimpleNamespace(symbol='159915.SZ'))
    assert records[0]['shares10k'] == '1234.50'
    assert records[0]['category'] == '股票基金'


@pytest.mark.parametrize('fault', ['unit', 'code', 'partial', 'duplicate_table'])
def test_szse_rejects_wrong_unit_identity_and_incomplete_filter(fault):
    value = szse_payload()
    if fault == 'unit':
        value[0]['metadata']['cols']['dqgm'] = '元'
    elif fault == 'code':
        value[0]['data'][0]['sys_key'] = '<u>159900</u>'
    elif fault == 'partial':
        value[0]['metadata']['pagecount'] = 39
    else:
        value.append(value[0])
    with pytest.raises(ValueError):
        parse_szse(value, SimpleNamespace(symbol='159915.SZ'))


def index_excel(rows):
    output = io.BytesIO()
    pd.DataFrame(rows).to_excel(output, index=False)
    return output.getvalue()


def index_row():
    return {'日期Date': '20261103', '指数代码 Index Code': '000300',
            '成份券代码Constituent Code': '000001', '成份券名称Constituent Name': '测试公司',
            '交易所Exchange': '深圳证券交易所', '权重(%)weight': '100'}


def test_index_normalizes_header_whitespace_without_changing_percent_scale():
    result = parse_index(index_excel([index_row()]), SimpleNamespace(dataset='csi-index-weights', indexCode='000300'))
    assert result[0]['symbol'] == '000001.SZ'
    assert result[0]['weightPct'] == '100'


@pytest.mark.parametrize('fault', ['index', 'weight', 'date', 'duplicate'])
def test_index_rejects_wrong_index_partial_weights_and_mixed_dates(fault):
    row = index_row()
    rows = [row]
    if fault == 'index':
        row['指数代码 Index Code'] = '000905'
    elif fault == 'weight':
        row['权重(%)weight'] = '0.5'
    elif fault == 'date':
        other = {**row, '日期Date': '20261102', '成份券代码Constituent Code': '000002'}
        rows.append(other)
    else:
        rows.append(dict(row))
    with pytest.raises(ValueError):
        parse_index(index_excel(rows), SimpleNamespace(dataset='csi-index-weights', indexCode='000300'))


@pytest.mark.parametrize('form', [
    {'dataset': 'etf-shares-sh', 'symbol': '159915.SZ', 'start': '2026-11-03', 'end': '2026-11-03'},
    {'dataset': 'etf-shares-sz', 'symbol': '510300.SH'},
    {'dataset': 'cni-index-members', 'indexCode': '000300'},
    {'dataset': 'szse-company-qna', 'symbol': '600519.SH'},
    {'dataset': 'tdx-day-package'},
    {'dataset': 'chinabond-curves', 'start': '2025-01-01', 'end': '2026-11-03'},
])
def test_public_scope_rejects_other_exchange_index_and_unbounded_date(form):
    with pytest.raises(ValueError):
        ReferenceRequest(requestId='sample', **form)
