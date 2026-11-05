"""新浪 ETF 期权清单、报价和来源 Greeks，严格核对标的及空列偏移。"""

import re
from decimal import Decimal

from .market_reference_projection import require, number, day
from .market_reference_derivatives_common import PublicRead, count, clock, sina_values, slice_result, unique

HQ = 'https://hq.sinajs.cn/list='
MONTHS = 'https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService.getStockName'
HEADERS = {'Referer': 'https://stock.finance.sina.com.cn/'}
UNDERLYINGS = {'510050': '50ETF', '510300': '300ETF', '588000': '科创50', '510500': '500ETF'}
NOTES = ('沪市 ETF 期权成交／持仓数量为张，报价按每份标的 ETF 对应权利金展示；来源合约可能已调整，不假定每张恒为一万份。',
         '来源快照与抓取时间分开；不能从本页报价、Greeks 或理论值直接推断可交易性、合约乘数或账户风险。')


def nearest_month(payload, underlying):
    require(isinstance(payload, dict) and isinstance(payload.get('result'), dict), '期权月份封套改变')
    result = payload['result']
    require(result.get('status', {}).get('code') == 0 and isinstance(result.get('data'), dict), '期权月份接口没有明确成功')
    data = result['data']
    require(data.get('stockId') == underlying, '期权月份接口返回其他 ETF，拒绝来源默认标的')
    months = data.get('contractMonth')
    require(isinstance(months, list) and len(months) >= 2 and all(isinstance(m, str) and re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', m) for m in months), '期权月份布局改变')
    # 来源第一个元素是当前日历月提示，后续才是可选合约月；到期后不能把提示月当成近月。
    return min(months[1:]).replace('-', '')[2:]


def parse_contracts(content, underlying, month):
    keys = [f'OP_UP_{underlying}{month}', f'OP_DOWN_{underlying}{month}']
    values = sina_values(content, keys)
    records = []
    for key, label in zip(keys, ('认购', '认沽')):
        fields = values[key]
        codes = [value for value in fields if value]
        require(codes and all(re.fullmatch(r'CON_OP_\d{8}', code) for code in codes), '近月期权一侧清单为空或代码布局改变')
        records.extend({'underlying': underlying + '.SH', 'month': '20' + month[:2] + '-' + month[2:],
                        'optionType': label, 'contract': code.removeprefix('CON_OP_')} for code in codes)
    unique(records, ('contract',))
    return records


def parse_option_quote(fields, code):
    if not fields:
        return []
    require(len(fields) >= 47 and fields[36] in UNDERLYINGS and fields[37], '期权报价布局或已核对标的改变')
    require(fields[45] in ('C', 'P'), '期权报价认购／认沽标志缺失')
    expiry = day(fields[46])
    require(expiry is not None, '期权报价缺少到期日')
    indices = {'strike': 7, 'last': 2, 'bid': 1, 'ask': 3, 'open': 9, 'high': 39, 'low': 40, 'previousClose': 8, 'amount': 42}
    values = {key: number(fields[index]) for key, index in indices.items()}
    require(values['strike'] is not None and Decimal(values['strike']) > 0, '期权行权价无效')
    return [{'contract': code, 'underlying': fields[36] + '.SH', 'name': fields[37], 'sourceTime': clock(fields[32]),
             'expireDate': expiry, 'optionType': '认购' if fields[45] == 'C' else '认沽', **values,
             'bidLots': count(fields[0]), 'askLots': count(fields[4]), 'volume': count(fields[41]), 'openInterest': count(fields[5])}]


def parse_greeks(fields, code):
    if not fields:
        return []
    require(len(fields) >= 16 and fields[1:4] == ['', '', ''] and fields[0], '期权 Greeks 空列布局改变')
    values = [fields[0], *fields[4:]]
    trade_code = values[9]
    match = re.fullmatch(r'(510050|510300|588000|510500)[CP]\d{2}(?:0[1-9]|1[0-2])[MA]\d+', trade_code)
    require(match is not None, '期权 Greeks 交易代码或标的格式改变')
    row = {key: number(values[index]) for key, index in [('delta', 2), ('gamma', 3), ('theta', 4), ('vega', 5), ('iv', 6),
                                                       ('high', 7), ('low', 8), ('strike', 10), ('last', 11), ('theory', 12)]}
    require(row['iv'] is not None and Decimal(row['iv']) >= 0, '期权 IV 不是有效小数')
    return [{'contract': code, 'name': values[0], 'tradeCode': trade_code, 'volume': count(values[1]), **row}]


def read_etf_options(query, context):
    read = PublicRead(context.timeout_seconds)
    if query.dataset == 'etf-option-contracts':
        underlying = query.symbol[:6]
        month = nearest_month(read.json(MONTHS, {'exchange': 'null', 'cate': UNDERLYINGS[underlying]}, HEADERS), underlying)
        keys = [f'OP_UP_{underlying}{month}', f'OP_DOWN_{underlying}{month}']
        url = HQ + ','.join(keys)
        records = parse_contracts(read.read(url, headers=HEADERS), underlying, month)
        notes = ('清单为所选标的当前近月的认购与认沽代码，两侧完整读取后分页；月提示与可选合约月分开，不自动扫描所有月份。',
                 '合约月按本次月份接口返回选择；合约清单与报价是独立读取，翻页期间来源清单可能变化。')
    else:
        is_greeks = query.dataset == 'etf-option-greeks'
        key = ('CON_SO_' if is_greeks else 'CON_OP_') + query.symbol
        url = HQ + key
        fields = sina_values(read.read(url, headers=HEADERS), [key])[key]
        records = parse_greeks(fields, query.symbol) if is_greeks else parse_option_quote(fields, query.symbol)
        notes = NOTES
        if is_greeks:
            notes = (*notes, 'Greeks、IV 与理论值由来源提供；IV 按小数展示，0.1317 代表 13.17%。来源未给 Greek 的完整模型／时间缩放假设，不自行套算或重算。')
    return slice_result(query, records, url, notes=notes)
