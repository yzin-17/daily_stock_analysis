"""新浪期货报价与日线；连续合约及来源时间不转换为交易保证。"""

import json
import re

from .market_reference_projection import require, day
from .market_reference_derivatives_common import PublicRead, price, count, clock, sina_values, slice_result, unique

HQ = 'https://hq.sinajs.cn/list='
HEADERS = {'Referer': 'https://finance.sina.com.cn/'}
CFFEX_PRODUCTS = ('IF', 'IH', 'IC', 'IM', 'TS', 'TF', 'T', 'TL')
NOTES = ('新浪来源即时／收盘快照，日期与时间按来源文字转北京时间；来源时间不自动等同交易所交易日或本次抓取时刻。',
         '代码末尾 0 为来源主力连续序列，不是具体可交割或可下单合约；保留来源报价单位，未按合约乘数折算。')


def parse_futures_quote(fields, code):
    if not fields:
        return []
    product = re.match(r'[A-Z]+', code)[0]
    if product in CFFEX_PRODUCTS:
        require(len(fields) >= 50 and fields[49], '新浪中金所报价布局改变')
        indices = {'open': 0, 'high': 1, 'low': 2, 'last': 3, 'bid': 16, 'ask': 26,
                   'previousSettle': 14, 'previousClose': 13, 'upperLimit': 9, 'lowerLimit': 10, 'average': 48}
        record = {key: price(fields[index]) for key, index in indices.items()}
        record.update({'name': fields[49], 'sourceTime': clock(fields[36] + ' ' + fields[37]),
                       'bidLots': count(fields[17]), 'askLots': count(fields[27]),
                       'volume': count(fields[4]), 'openInterest': count(fields[6])})
    else:
        require(len(fields) >= 28 and fields[0], '新浪商品期货报价布局改变')
        raw = fields[1].zfill(6)
        require(re.fullmatch(r'\d{6}', raw), '新浪商品期货时间格式改变')
        indices = {'open': 2, 'high': 3, 'low': 4, 'last': 8, 'bid': 6, 'ask': 7, 'previousSettle': 10, 'average': 27}
        record = {key: price(fields[index]) for key, index in indices.items()}
        record.update({'name': fields[0], 'sourceTime': clock(fields[17] + ' ' + ':'.join((raw[:2], raw[2:4], raw[4:]))),
                       'bidLots': count(fields[11]), 'askLots': count(fields[12]), 'volume': count(fields[14]),
                       'openInterest': count(fields[13]), 'previousClose': None, 'upperLimit': None, 'lowerLimit': None})
    require(record['last'] is not None, '新浪期货最新价缺失')
    return [{'contract': code, **record}]


def parse_futures_kline(content, code):
    text = content.decode('gb18030')
    match = re.search(r'var _' + re.escape(code) + r'=\((.*)\);?\s*$', text, re.S)
    require(match is not None, '新浪期货日线 JSONP 标识不符')
    records = json.loads(match[1])
    if records is None:
        return []
    require(isinstance(records, list) and all(isinstance(row, dict) for row in records), '新浪期货日线结构改变')
    result = []
    for row in records:
        stamp = day(row['d'])
        require(stamp is not None, '新浪期货日线日期缺失')
        values = {key: price(row[source]) for key, source in zip(('open', 'high', 'low', 'close', 'settle'), ('o', 'h', 'l', 'c', 's'))}
        require(values['close'] is not None, '新浪期货日线缺收盘价')
        result.append({'date': stamp, 'contract': code, **values, 'volume': count(row['v']), 'openInterest': count(row['p'])})
    unique(result, ('date',))
    return sorted(result, key=lambda item: item['date'], reverse=True)


def parse_a50(fields):
    require(len(fields) >= 14 and fields[13], '新浪 A50 来源报价缺失或布局改变')
    values = {key: price(fields[index]) for key, index in [('last', 0), ('open', 8), ('high', 4), ('low', 5), ('bid', 2), ('ask', 3), ('previousSettle', 7)]}
    require(values['last'] is not None, '新浪 A50 来源报价没有最新值')
    return [{'sourceCode': 'CHA50CFD', 'name': fields[13], 'sourceTime': clock(fields[12] + ' ' + fields[6]), **values}]


def read_futures_sina(query, context):
    read = PublicRead(context.timeout_seconds)
    if query.dataset == 'futures-kline':
        url = f'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{query.symbol}=/InnerFuturesNewService.getDailyKLine'
        records = parse_futures_kline(read.read(url, {'symbol': query.symbol}, HEADERS), query.symbol)
        notes = ('新浪每次返回该代码现有日线，再按本次日期窗口及分页筛选；未复权，主力连续换月可能跳空。',
                 '来源结算价缺失或填零时保留为空；旧合约可能已不保留，明确空返回只代表本次来源无数据，不等于从未上市。',
                 '大商所品种可使用新浪日线，但不冒充大商所官方日行情；成交量／持仓可能与官方数值有差异，不提供严格历史可见性。')
    elif query.dataset == 'a50-source-quote':
        key = 'hf_CHA50CFD'
        url = HQ + key
        records = parse_a50(sina_values(read.read(url, headers=HEADERS), [key])[key])
        notes = (*NOTES, '此处身份为新浪 CHA50CFD 来源连续报价；未提供具体合约或交易所成交身份，不将其关联为某个 SGX 可交割合约。')
    else:
        key = 'nf_' + query.symbol
        url = HQ + key
        records = parse_futures_quote(sina_values(read.read(url, headers=HEADERS), [key])[key], query.symbol)
        notes = NOTES
    return slice_result(query, records, url, notes=notes)
