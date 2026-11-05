"""龙虎榜的披露原因、成交额／席位额和双榜层级反例。"""

from copy import deepcopy
import json

import pytest

from data_provider.reference_backup_dragon import parse_sh_dragon, parse_sz_dragon


def sh_content():
    lines = ['交易日期:2026年11月03日', '一、日价格涨幅偏离值达到7%:',
             '证券代码: 600327    证券简称: 样本']
    for side in ['买入', '卖出']:
        lines.append(side + '营业部名称: 累计' + side + '金额(元):')
        lines.extend(f'({i}) 会员{i}  {i}.00' for i in range(1, 6))
    lines.append('证券代码: 600519    证券简称: 下一片段')
    return ('cb(' + json.dumps({'dateTx': '2026-11-03', 'fileContents': lines}, ensure_ascii=False) + ')').encode()


def sz_content():
    parent = [{'metadata': {'catalogid': '1842_xxpl', 'recordcount': 46, 'cols': {'cjje': '成交额(亿元)'}},
               'data': [{'zqdm': '000417', 'dqrq': '2026-11-03', 'plyy': '日振幅', 'cjje': '10.04'}]}]
    summary = {'metadata': {'catalogid': '1842_detal', 'tabkey': 'tab1'},
               'data': [{'dqrq': '2026-11-03', 'zqjc': '样本 (000417)', 'plyy': '日振幅', 'cjje': '1,004,005,838 元', 'ycqj': '无'}]}
    seats = {'metadata': {'catalogid': '1842_detal', 'tabkey': 'tab2',
                         'cols': {'mrje': '买入金额(元)', 'mcje': '卖出金额(元)'}},
             'data': [{'mmlb': side + str(rank), 'zsmc': '同一会员', 'mrje': str(rank), 'mcje': '0'}
                      for side in ['买', '卖'] for rank in range(1, 6)]}
    return parent, [summary, seats]


def test_sh_representative_slice_preserves_side_and_reason_without_claiming_whole_market():
    result = parse_sh_dragon(sh_content(), '2026-11-03')
    assert result['code'] == '600327' and len(result['seats']) == 10
    assert result['buyRankTop5Sum'] == '15.00'
    assert not result['fullMarketProjection'] and not result['directReplacement']


@pytest.mark.parametrize('change', ['callback', 'date', 'unit', 'rank'])
def test_sh_changed_callback_date_unit_or_missing_rank_is_rejected(change):
    raw = sh_content()
    replacements = {'callback': (b'cb(', b'evil('), 'date': (b'2026-11-03', b'2026-11-02'),
                    'unit': ('(元)'.encode(), '(万元)'.encode()), 'rank': (b'(5)', b'(4)')}
    before, after = replacements[change]
    with pytest.raises(ValueError):
        parse_sh_dragon(raw.replace(before, after), '2026-11-03')


def test_sz_turnover_is_separate_from_both_sides_member_amounts():
    parent, detail = sz_content()
    result = parse_sz_dragon(parent, detail, '2026-11-03', '000417')
    assert result['turnover'] == '1004005838'
    assert len(result['seats']) == 10
    assert result['seats'][0]['sellAmount'] == '0'
    assert 'buyAmount' not in result
    assert result['listTotal'] == 46 and not result['fullMarketProjection']


@pytest.mark.parametrize('change', ['identity', 'reason', 'date', 'unit', 'missing-rank'])
def test_sz_cross_page_identity_reason_date_and_units_are_checked(change):
    parent, detail = sz_content()
    if change == 'identity':
        detail[0]['data'][0]['zqjc'] = '其他 (000001)'
    elif change == 'reason':
        detail[0]['data'][0]['plyy'] = '三日涨幅'
    elif change == 'date':
        detail[0]['data'][0]['dqrq'] = '2026-11-02'
    elif change == 'unit':
        parent[0]['metadata']['cols']['cjje'] = '成交额(万元)'
    else:
        detail[1]['data'].pop()
    with pytest.raises(ValueError):
        parse_sz_dragon(parent, detail, '2026-11-03', '000417')
