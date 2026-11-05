"""公开协议的最小受控样本，不作为实源或业务验收。"""

import json


def shfe_row(product='cu_f', month='2611'):
    return {'PRODUCTID': product, 'PRODUCTNAME': '样本品种', 'DELIVERYMONTH': month,
            'OPENPRICE': 0, 'HIGHESTPRICE': 12, 'LOWESTPRICE': 10, 'CLOSEPRICE': 11,
            'SETTLEMENTPRICE': 11, 'PRESETTLEMENTPRICE': 10, 'VOLUME': 0,
            'OPENINTEREST': 20, 'OPENINTERESTCHG': -1, 'TURNOVER': 0}


def official(rows, key='o_curinstrument', stamp='20261103'):
    return json.dumps({'report_date': stamp, key: rows}, ensure_ascii=False).encode()


def future_fields():
    values = ['0'] * 28
    for index, value in {0: '样本连续', 1: '150000', 2: '10', 3: '12', 4: '9', 6: '11', 7: '12',
                         8: '11', 10: '10', 13: '20', 14: '0', 17: '2026-11-03', 27: '10.5'}.items():
        values[index] = value
    return values


def option_fields():
    values = ['0'] * 51
    for index, value in {2: '0.02', 7: '3', 8: '0.01', 9: '0.02', 32: '2026-11-03 15:00:00',
                         36: '510050', 37: '样本购11月3000', 39: '0.03', 40: '0.01', 45: 'C', 46: '2026-11-25'}.items():
        values[index] = value
    return values


def greeks_fields():
    return ['样本购11月3000', '', '', '', '0', '0.2', '4.0', '-0.2', '0.3', '0.1317',
            '0.03', '0.01', '510050C2611M03000', '3', '0.02', '0.015', 'M']


def sina(key, values):
    return ('var hq_str_' + key + '="' + ','.join(values) + '";').encode('gb18030')


def convertible_row():
    keys = ['PUBLIC_START_DATE', 'LISTING_DATE', 'DELIST_DATE', 'EXPIRE_DATE', 'TRANSFER_START_DATE',
            'ACTUAL_ISSUE_SCALE', 'INITIAL_TRANSFER_PRICE', 'TRANSFER_PRICE', 'CURRENT_BOND_PRICE',
            'CONVERT_STOCK_PRICE', 'TRANSFER_VALUE', 'TRANSFER_PREMIUM_RATIO']
    row = dict.fromkeys(keys)
    row.update(SECURITY_CODE='123001', SECURITY_NAME_ABBR='样本转债', CONVERT_STOCK_CODE='000001',
               SECURITY_SHORT_NAME='样本正股', TRADE_MARKET='CNSESZ', LISTING_DATE='2026-11-02 00:00:00',
               TRANSFER_PREMIUM_RATIO=0, CURRENT_BOND_PRICE='-', RATING=None, CORRECODE=None)
    return row
