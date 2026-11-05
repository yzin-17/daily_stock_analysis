"""五所期货日行情；借鉴 a-stock-data Layer 13，日期与见证严格关联。"""

import csv
import io
import json
import re
from xml.etree import ElementTree

from .market_reference_projection import number, require
from .market_reference_derivatives_common import PublicRead, price, count, text, slice_result, unique

HOSTS = {'shfe': 'https://www.shfe.com.cn', 'ine': 'https://www.ine.cn'}
GFEX = 'http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList'
CFFEX = 'http://www.cffex.com.cn/fzjy/mrhq/{ym}/{day}/'
FIELDS = ('open', 'high', 'low', 'close', 'settle', 'previousSettle')
NOTES = ('价格保留各合约来源报价单位，不按合约乘数折算为账户金额；成交量／持仓为手，成交额为万元。',
         '仅为所选来源交易日的官方文件，不声明完整历史可见性、实时可交易性或停牌判断；来源填零的缺报价保留为空。')


def official_json(content, stamp, key):
    payload = json.loads(content)
    require(isinstance(payload, dict) and str(payload.get('report_date')) == stamp.replace('-', ''), '交易所报告日期不符或缺失')
    rows = payload.get(key)
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), '交易所行列表结构改变')
    return rows


def shfe_url(exchange, stamp, kind='kx'):
    return f"{HOSTS[exchange]}/data/tradedata/future/dailydata/{kind}{stamp.replace('-', '')}.dat"


def parse_shfe(content, stamp, exchange, witness=None):
    rows = official_json(content, stamp, 'o_curinstrument')
    skip = set()
    if exchange == 'shfe' and stamp >= '2018-03-26':
        require(witness is not None, '上期所缺少同日能源排除见证')
        parse_shfe(witness, stamp, 'ine')
        skip = {row.get('PRODUCTID') for row in official_json(witness, stamp, 'o_curinstrument')}
    records = []
    for row in rows:
        product, month = str(row.get('PRODUCTID', '')).strip(), str(row.get('DELIVERYMONTH', '')).strip()
        if month in ('小计', '合计', '总计') or (product == '总计' and month == '') or product == 'sc_tas':
            continue
        require(re.fullmatch(r'[A-Za-z]{1,2}_f', product) and re.fullmatch(r'\d{2}(?:0[1-9]|1[0-2])', month)
                and row.get('PRODUCTNAME'), '期货品种代码、到期月或名称改变')
        if product in skip:
            continue
        values = {key: price(row[source]) for key, source in zip(FIELDS,
                  ('OPENPRICE', 'HIGHESTPRICE', 'LOWESTPRICE', 'CLOSEPRICE', 'SETTLEMENTPRICE', 'PRESETTLEMENTPRICE'))}
        records.append({'date': stamp, 'exchange': '上期所' if exchange == 'shfe' else '上期能源',
                        'contract': product[:-2] + month, 'product': row['PRODUCTNAME'].strip(), **values,
                        'volume': count(row['VOLUME']), 'openInterest': count(row['OPENINTEREST']),
                        'openInterestChange': count(row['OPENINTERESTCHG']), 'turnover10k': number(row.get('TURNOVER'))})
    require(bool(records), '交易所文件没有可核对的期货合约，可能未发布或格式改变')
    unique(records, ('contract',))
    return records


def czce_text(content, stamp):
    content = text(content)
    require(stamp in content.splitlines()[0], '郑商所文件标题日期不符')
    return content


def parse_czce(content, stamp):
    lines = czce_text(content, stamp).splitlines()
    aliases = {'品种月份': '合约代码', '品种代码': '合约代码', '空盘量': '持仓量'}
    header = None
    records = []
    for line in lines[1:]:
        cells = [cell.strip().replace(',', '') for cell in line.split('|')]
        if cells[0] in ('合约代码', '品种月份', '品种代码'):
            header = [aliases.get(cell, cell) for cell in cells]
            continue
        if not re.fullmatch(r'[A-Za-z]{1,2}\d{3,4}', cells[0]):
            continue
        require(header is not None and len(cells) == len(header), '郑商所合约在表头前出现或列数改变')
        row = dict(zip(header, cells))
        values = {key: price(row[source]) for key, source in zip(FIELDS, ('今开盘', '最高价', '最低价', '今收盘', '今结算', '昨结算'))}
        records.append({'date': stamp, 'exchange': '郑商所', 'contract': cells[0],
                        'product': re.match(r'[A-Za-z]+', cells[0])[0], **values,
                        'volume': count(row['成交量(手)']), 'openInterest': count(row['持仓量']),
                        'openInterestChange': count(row['增减量']), 'turnover10k': number(row['成交额(万元)'])})
    require(bool(records), '郑商所文件没有合格期货行')
    unique(records, ('contract',))
    return records


def cffex_table(content, witness, stamp):
    try:
        table = list(csv.reader(io.StringIO(text(content)), strict=True))
    except csv.Error:
        raise ValueError('中金所 CSV 无法按已核对格式解析') from None
    require(bool(table) and table[0][:3] == ['合约代码', '今开盘', '最高价'], '中金所行情 CSV 表头改变')
    require(b'<!DOCTYPE' not in witness.upper() and b'<!ENTITY' not in witness.upper(), '中金所 XML 含外部实体声明')
    try:
        root = ElementTree.fromstring(witness)
    except ElementTree.ParseError:
        raise ValueError('中金所 XML 无法解析') from None
    observed = {}
    for item in root.findall('dailydata'):
        code = item.findtext('instrumentid', '').strip()
        require(code and code not in observed and item.findtext('tradingday') == stamp.replace('-', ''), '中金所 XML 合约重复或交易日不符')
        observed[code] = item
    rows = {}
    for cells in table[1:]:
        if not cells or cells[0].strip() in ('', '小计', '合计', '总计'):
            continue
        require(len(cells) == len(table[0]) and cells[0].strip() not in rows, '中金所 CSV 行列或合约身份异常')
        rows[cells[0].strip()] = dict(zip(table[0], cells))
    require(rows and set(rows) == set(observed), '中金所 CSV 与日期见证不是同一份合约集合')
    for code, row in rows.items():
        for field, tag in [('成交量', 'volume'), ('今收盘', 'closeprice'), ('持仓量', 'openinterest')]:
            require(number(row[field]) == number(observed[code].findtext(tag)), '中金所 CSV 与日期见证数值不符')
    return rows


def parse_cffex(content, witness, stamp):
    records = []
    for code, row in cffex_table(content, witness, stamp).items():
        if '-C-' in code or '-P-' in code:
            continue
        require(re.fullmatch(r'[A-Za-z]{1,2}\d{4}', code), '中金所期货代码格式改变')
        values = {key: price(row[source]) for key, source in zip(FIELDS, ('今开盘', '最高价', '最低价', '今收盘', '今结算', '前结算'))}
        records.append({'date': stamp, 'exchange': '中金所', 'contract': code, 'product': re.match(r'[A-Za-z]+', code)[0],
                        **values, 'volume': count(row['成交量']), 'openInterest': count(row['持仓量']),
                        'openInterestChange': count(row['持仓变化']), 'turnover10k': number(row['成交金额'])})
    require(bool(records), '中金所文件没有合格期货行')
    return records


def parse_gfex(content, stamp):
    payload = json.loads(content)
    require(isinstance(payload, dict) and str(payload.get('code')) == '0', '广期所没有明确成功结果')
    require(payload.get('param') == {'trade_date': [stamp.replace('-', '')], 'trade_type': ['0']}, '广期所回显的日期或交易类型不符')
    rows = payload.get('data')
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), '广期所行结构改变')
    records = []
    for row in rows:
        month = str(row.get('delivMonth') or '')
        if not month:
            require(re.search(r'小计|总计|合计', str(row.get('variety', ''))), '广期所合约行缺到期月')
            continue
        require(re.fullmatch(r'\d{4}', month) and re.fullmatch(r'[A-Za-z]{1,2}', row.get('varietyOrder', '')), '广期所合约身份改变')
        values = {key: price(row[source]) for key, source in zip(FIELDS, ('open', 'high', 'low', 'close', 'clearPrice', 'lastClear'))}
        records.append({'date': stamp, 'exchange': '广期所', 'contract': row['varietyOrder'] + month,
                        'product': row['variety'], **values, 'volume': count(row['volumn']),
                        'openInterest': count(row['openInterest']), 'openInterestChange': count(row['diffI']),
                        'turnover10k': number(row['turnover'])})
    require(bool(records), '广期所文件没有合格期货合约')
    unique(records, ('contract',))
    return records


def read_futures_daily(query, context):
    exchange, stamp = query.dataset.rsplit('-', 1)[1], query.start
    read = PublicRead(context.timeout_seconds)
    if exchange in HOSTS:
        url = shfe_url(exchange, stamp)
        witness = read.read(shfe_url('ine', stamp)) if exchange == 'shfe' and stamp >= '2018-03-26' else None
        records = parse_shfe(read.read(url), stamp, exchange, witness)
    elif exchange == 'czce':
        ymd = stamp.replace('-', '')
        url = f'https://www.czce.com.cn/cn/DFSStaticFiles/Future/{ymd[:4]}/{ymd}/FutureDataDaily.txt'
        records = parse_czce(read.read(url), stamp)
    elif exchange == 'cffex':
        ymd = stamp.replace('-', '')
        base = CFFEX.format(ym=ymd[:6], day=ymd[6:])
        url = base + ymd + '_1.csv'
        records = parse_cffex(read.read(url), read.read(base + 'index.xml'), stamp)
    else:
        url = GFEX
        records = parse_gfex(read.read(url, method='POST', data={'trade_date': stamp.replace('-', ''), 'trade_type': '0'},
                            headers={'Referer': 'http://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml'}), stamp)
    return slice_result(query, records, url.replace('http:', 'https:', 1), notes=NOTES)
