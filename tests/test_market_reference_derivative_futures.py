"""官方期货数据的日期见证、排除、布局、计量及重复拒绝。"""

import copy
import json
import pytest

from data_provider.market_reference_futures_daily import parse_shfe, parse_czce, parse_cffex, parse_gfex
from data_provider.market_reference_futures_rank import parse_shfe_rank, parse_czce_rank, parse_cffex_rank
from tests.derivative_reference_samples import shfe_row, official

DAY = '2026-11-03'


def test_shfe_excludes_ine_and_aggregate_preserves_zero_count():
    rows = [shfe_row(), shfe_row('sc_f'), shfe_row('cu_f', '小计'), shfe_row('sc_tas')]
    result = parse_shfe(official(rows), DAY, 'shfe', official([shfe_row('sc_f')]))
    assert [row['contract'] for row in result] == ['cu2611']
    assert result[0]['volume'] == '0' and result[0]['open'] is None and result[0]['openInterestChange'] == '-1'


@pytest.mark.parametrize('witness', [None, official([], stamp='20261103'), official([shfe_row('sc_f')], stamp='20261102')])
def test_shfe_requires_valid_same_day_ine_witness(witness):
    with pytest.raises(ValueError):
        parse_shfe(official([shfe_row()]), DAY, 'shfe', witness)


def test_shfe_rejects_duplicate_and_unknown_non_summary():
    for rows in [[shfe_row(), shfe_row()], [shfe_row(), shfe_row('unknown')]]:
        with pytest.raises(ValueError):
            parse_shfe(official(rows), DAY, 'ine')


def test_czce_declared_unit_header_and_date():
    content = ('郑州商品交易所期货每日行情表(2026-11-03)\n'
               '合约代码|昨结算|今开盘|最高价|最低价|今收盘|今结算|成交量(手)|持仓量|增减量|成交额(万元)\n'
               'AP611|10|0|12|9|11|11|0|20|-1|1,200.5\n小计||||||||||').encode()
    result = parse_czce(content, DAY)
    assert result[0]['turnover10k'] == '1200.5' and result[0]['contract'] == 'AP611'
    with pytest.raises(ValueError):
        parse_czce(content, '2026-11-02')


def cffex_pair():
    csv = ('合约代码,今开盘,最高价,最低价,成交量,成交金额,持仓量,持仓变化,今收盘,今结算,前结算,涨跌1,涨跌2,Delta\n'
           'IF2611,10,12,9,0,0,20,-1,11,11,10,1,1,\n').encode('gb18030')
    xml = b'<dailydatas><dailydata><instrumentid>IF2611</instrumentid><tradingday>20261103</tradingday><volume>0</volume><closeprice>11</closeprice><openinterest>20</openinterest></dailydata></dailydatas>'
    return csv, xml


def test_cffex_cross_checks_identity_date_and_values():
    csv, xml = cffex_pair()
    assert parse_cffex(csv, xml, DAY)[0]['volume'] == '0'
    for altered in [xml.replace(b'20261103', b'20261102'), xml.replace(b'<volume>0', b'<volume>1'),
                    xml.replace(b'IF2611', b'IF2612'), b'<!DOCTYPE bad>' + xml]:
        with pytest.raises(ValueError):
            parse_cffex(csv, altered, DAY)


def test_gfex_echo_and_summary_not_missing_contract():
    row = {'variety': '样本', 'varietyOrder': 'si', 'delivMonth': '2611', 'open': 0, 'high': 12, 'low': 9,
           'close': 10, 'clearPrice': 10, 'lastClear': 11, 'volumn': 0, 'openInterest': 20, 'diffI': -1, 'turnover': 0}
    payload = {'code': 0, 'param': {'trade_date': ['20261103'], 'trade_type': ['0']}, 'data': [row, {'variety': '小计'}]}
    assert parse_gfex(json.dumps(payload).encode(), DAY)[0]['open'] is None
    bad = copy.deepcopy(payload);bad['param']['trade_type'] = ['1']
    with pytest.raises(ValueError):
        parse_gfex(json.dumps(bad).encode(), DAY)
    bad = copy.deepcopy(payload);bad['data'][0].pop('delivMonth')
    with pytest.raises(ValueError):
        parse_gfex(json.dumps(bad).encode(), DAY)


def rank_row(code='cu2611', rank=1):
    return {'INSTRUMENTID': code, 'RANK': rank, 'PARTICIPANTABBR1': '成交会员', 'CJ1': 0, 'CJ1_CHG': -1,
            'PARTICIPANTABBR2': '持买会员', 'CJ2': 20, 'CJ2_CHG': 1,
            'PARTICIPANTABBR3': '--', 'CJ3': None, 'CJ3_CHG': None}


def test_rank_dimensions_and_energy_exclusion():
    witness = official([rank_row('sc2611')], 'o_cursor')
    rows = [rank_row(), rank_row('sc2611'), rank_row('cuall', -1), rank_row('cu2611', 999)]
    result = parse_shfe_rank(official(rows, 'o_cursor'), DAY, 'shfe', witness)
    assert len(result) == 1
    assert result[0]['volumeMember'] == '成交会员' and result[0]['longMember'] == '持买会员'
    assert result[0]['shortMember'] is None and result[0]['volume'] == '0'


def test_czce_rank_level_and_segment_date():
    content = ('郑州商品交易所期货持仓排名表(2026-11-03)\n品种：苹果AP 日期：2026-11-03\n'
               '1|成交会员|0|-1|持买会员|20|1|持卖会员|21|2|\n'
               '合约：苹果AP611 日期：2026-11-03\n1|成交会员|0|-1|持买会员|20|1|持卖会员|21|2|').encode()
    rows = parse_czce_rank(content, DAY)
    assert [r['level'] for r in rows] == ['品种', '合约']
    with pytest.raises(ValueError):
        parse_czce_rank(content.replace(b'AP611 ', b'AP611 ').replace(b'2026-11-03\n1', b'2026-11-02\n1'), DAY)


def test_cffex_rank_product_and_date():
    raw = ('交易日,合约,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,\n'
           '20261103,IF2611,1,成交会员,0,-1,持买会员,20,1,持卖会员,21,2').encode('gb18030')
    assert parse_cffex_rank(raw, DAY, 'IF')[0]['contract'] == 'IF2611'
    with pytest.raises(ValueError):
        parse_cffex_rank(raw, DAY, 'IC')
    with pytest.raises(ValueError):
        parse_cffex_rank(raw, '2026-11-02', 'IF')
