"""官方两融完整性、证券／日期、空值与目录舍入见证。"""

from copy import deepcopy
import io

import pandas as pd
import pytest

from data_provider.reference_backup_margin import parse_sh_margin, parse_sz_margin


def sh_pages():
    def row(code):
        return {'stockCode': code, 'opDate': '20261103', 'securityAbbr': '样本',
                'rzye': 100, 'rzmre': 0, 'rqylje': None, 'rqyl': 0, 'rqmcl': 0}
    return [{'detailsDate': '20261103', 'pageHelp': {'pageNo': page, 'pageSize': 2,
              'pageCount': 2, 'total': 3, 'data': rows}} for page, rows in
            [(1, [row('600519'), row('510050')]), (2, [row('688001')])]]


def sz_sample():
    frame = pd.DataFrame([{'证券代码': '000001', '证券简称': '样本', '融资买入额(元)': '100000000',
        '融资余额(元)': '200000000', '融券余额(元)': '30000', '融券余量(股/份)': '40000',
        '融券卖出量(股/份)': '50000'}])
    meta = [{'metadata': {'catalogid': '1837_xxpl', 'tabkey': 'tab2', 'recordcount': 1,
        'conditions': [{'name': 'txtDate', 'defaultValue': '2026-11-03'}],
        'cols': {'jrrzmr': '融资买入额(亿元)', 'jrrjye': '融券余额(万元)', 'jrrjyl': '融券余量(万股/万份)'}},
        'data': [{'zqdm': '000001', 'jrrzmr': '1.00', 'jrrzye': '2.00', 'jrrjye': '3.00', 'jrrjyl': '4.00', 'jrrjmc': '5.00'}]}]
    return frame, meta


def workbook(frame):
    output = io.BytesIO()
    frame.to_excel(output, index=False)
    return output.getvalue()


def test_sh_requires_all_pages_and_keeps_unknown_short_amount():
    result = parse_sh_margin(sh_pages(), '2026-11-03')
    assert result['total'] == 3 and result['complete']
    assert result['missingShortBalances'] == 3
    assert result['records'][0]['financingBuy'] == '0'
    assert result['records'][0]['securitiesBalance'] is None


def test_sh_source_page_size_cap_cannot_be_treated_as_complete():
    with pytest.raises(ValueError, match='未读页'):
        parse_sh_margin(sh_pages()[:1], '2026-11-03')


def test_source_689_range_is_recorded_without_expanding_current_stock_contract():
    pages = sh_pages()
    pages[1]['pageHelp']['data'][0]['stockCode'] = '689009'
    result = parse_sh_margin(pages, '2026-11-03')
    assert result['outsideCurrentStockCodeScope'] == ['689009']


@pytest.mark.parametrize('mutation', ['date', 'identity', 'duplicate', 'missing-field', 'changed-total'])
def test_sh_wrong_record_or_page_snapshot_fails(mutation):
    pages = sh_pages()
    row = pages[1]['pageHelp']['data'][0]
    if mutation == 'date':
        row['opDate'] = '20261102'
    elif mutation == 'identity':
        row['stockCode'] = '300750'
    elif mutation == 'duplicate':
        row['stockCode'] = '600519'
    elif mutation == 'missing-field':
        del row['rqylje']
    else:
        pages[1]['pageHelp']['total'] = 4
    with pytest.raises(ValueError):
        parse_sh_margin(pages, '2026-11-03')


def test_sz_excel_exact_units_are_not_replaced_by_rounded_json():
    frame, metadata = sz_sample()
    result = parse_sz_margin(workbook(frame), metadata, '2026-11-03')
    assert result['total'] == 1 and result['directoryWitnessRows'] == 1
    assert result['records'][0]['financingBalance'] == '200000000'
    assert result['units']['amount'] == '元'


def test_sz_directory_truncates_instead_of_rounding_to_nearest():
    frame, metadata = sz_sample()
    frame.loc[0, '融资买入额(元)'] = '123999999'
    metadata[0]['data'][0]['jrrzmr'] = '1.23'
    assert parse_sz_margin(workbook(frame), metadata, '2026-11-03')['records'][0]['financingBuy'] == '123999999'
    metadata[0]['data'][0]['jrrzmr'] = '1.24'
    with pytest.raises(ValueError):
        parse_sz_margin(workbook(frame), metadata, '2026-11-03')


@pytest.mark.parametrize('mutation', ['date', 'count', 'unit', 'witness', 'missing'])
def test_sz_date_completeness_units_and_witness_are_required(mutation):
    frame, metadata = sz_sample()
    if mutation == 'date':
        metadata[0]['metadata']['conditions'][0]['defaultValue'] = '2026-11-02'
    elif mutation == 'count':
        metadata[0]['metadata']['recordcount'] = 2
    elif mutation == 'unit':
        frame = frame.rename(columns={'融资余额(元)': '融资余额(万元)'})
    elif mutation == 'witness':
        metadata[0]['data'][0]['jrrzmr'] = '9.99'
    else:
        frame.loc[0, '融资余额(元)'] = ''
    with pytest.raises(ValueError):
        parse_sz_margin(workbook(frame), metadata, '2026-11-03')
