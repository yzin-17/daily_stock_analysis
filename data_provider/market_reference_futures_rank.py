"""交易所会员排名，三套会员维度分开；参考 a-stock-data Layer 13。"""

import csv
import io
import re

from .market_reference_projection import require
from .market_reference_derivatives_common import PublicRead, count, text, slice_result, unique
from .market_reference_futures_daily import official_json, shfe_url, czce_text

CFFEX_FIRST = {'IF': '2010-04-16', 'IH': '2015-04-16', 'IC': '2015-04-16', 'IM': '2022-07-22',
               'TS': '2018-08-17', 'TF': '2013-09-06', 'T': '2015-03-20', 'TL': '2023-04-21'}
NAMES = {'shfe': '上期所', 'ine': '上期能源', 'czce': '郑商所', 'cffex': '中金所'}


def member(value):
    if value is None:
        return None
    value = str(value).strip()
    return None if value in ('', '-', '--') else value


def rank_record(stamp, exchange, level, code, cells):
    require(len(cells) == 10, '会员排名列结构改变')
    rank = count(cells[0])
    require(rank is not None and 1 <= int(rank) <= 20, '会员排名名次超出来源前二十名口径')
    return {'date': stamp, 'exchange': NAMES[exchange], 'level': level, 'contract': code, 'rank': rank,
            'volumeMember': member(cells[1]), 'volume': count(cells[2]), 'volumeChange': count(cells[3]),
            'longMember': member(cells[4]), 'longInterest': count(cells[5]), 'longChange': count(cells[6]),
            'shortMember': member(cells[7]), 'shortInterest': count(cells[8]), 'shortChange': count(cells[9])}


def parse_shfe_rank(content, stamp, exchange, witness=None):
    rows = official_json(content, stamp, 'o_cursor')
    skip = set()
    if exchange == 'shfe' and stamp >= '2020-07-03':
        require(witness is not None, '上期所会员排名缺少同日能源对照')
        parse_shfe_rank(witness, stamp, 'ine')
        skip = {str(row.get('INSTRUMENTID', '')).strip() for row in official_json(witness, stamp, 'o_cursor')}
    records = []
    fields = ('RANK', 'PARTICIPANTABBR1', 'CJ1', 'CJ1_CHG', 'PARTICIPANTABBR2', 'CJ2', 'CJ2_CHG', 'PARTICIPANTABBR3', 'CJ3', 'CJ3_CHG')
    for row in rows:
        rank = count(row.get('RANK'))
        require(rank is not None, '会员排名缺少名次')
        code = str(row.get('INSTRUMENTID', '')).strip()
        if not 1 <= int(rank) <= 20 or code in skip:
            continue
        require(re.fullmatch(r'[A-Za-z]{1,2}\d{3,4}', code), '会员排名不是具体合约')
        records.append(rank_record(stamp, exchange, '合约', code, [row[field] for field in fields]))
    require(bool(records), '交易所未返回合格会员排名')
    unique(records, ('level', 'contract', 'rank'))
    return records


def parse_czce_rank(content, stamp):
    body = czce_text(content, stamp)
    level = code = None
    records = []
    for line in body.splitlines():
        heading = re.match(r'^(品种|合约)：\s*(\S+)\s+日期：\s*(\d{4}-\d{2}-\d{2})', line.strip())
        if heading:
            require(heading[3] == stamp, '郑商所会员排名段日期不符')
            match = re.search(r'[A-Za-z]+\d*$', heading[2])
            require(match is not None, '郑商所会员排名缺少品种或合约')
            level, code = ('品种' if heading[1] == '品种' else '合约'), match[0]
            continue
        cells = [cell.strip().replace(',', '') for cell in line.split('|')]
        if not cells[0].isdigit():
            continue
        if cells[-1] == '':
            cells.pop()
        require(level is not None and code is not None, '郑商所排名在品种／合约标题之前出现')
        records.append(rank_record(stamp, 'czce', level, code, cells))
    require(bool(records), '郑商所文件没有合格会员排名')
    unique(records, ('level', 'contract', 'rank'))
    return records


def parse_cffex_rank(content, stamp, product):
    records, ranking = [], False
    try:
        table = list(csv.reader(io.StringIO(text(content)), strict=True))
    except csv.Error:
        raise ValueError('中金所会员排名 CSV 无法解析') from None
    for cells in table:
        cells = [value.strip() for value in cells]
        if cells[:2] == ['交易日', '合约']:
            ranking = cells[2:3] == ['排名']
            continue
        if not cells or not cells[0].isdigit() or not ranking:
            continue
        require(len(cells) == 12 and cells[0] == stamp.replace('-', ''), '中金所会员排名行列或交易日不符')
        require(re.fullmatch(re.escape(product) + r'\d{4}', cells[1]), '中金所会员排名返回了其他品种')
        records.append(rank_record(stamp, 'cffex', '合约', cells[1], cells[2:]))
    require(bool(records), '中金所品种文件没有合格会员排名')
    unique(records, ('level', 'contract', 'rank'))
    return records


def read_futures_rank(query, context):
    exchange, stamp = query.dataset.rsplit('-', 1)[1], query.start
    read = PublicRead(context.timeout_seconds)
    if exchange in ('shfe', 'ine'):
        url = shfe_url(exchange, stamp, 'pm')
        witness = read.read(shfe_url('ine', stamp, 'pm')) if exchange == 'shfe' and stamp >= '2020-07-03' else None
        records = parse_shfe_rank(read.read(url), stamp, exchange, witness)
    elif exchange == 'czce':
        ymd = stamp.replace('-', '')
        url = f'https://www.czce.com.cn/cn/DFSStaticFiles/Future/{ymd[:4]}/{ymd}/FutureDataHolding.txt'
        records = parse_czce_rank(read.read(url), stamp)
    else:
        ymd = stamp.replace('-', '')
        records = []
        for product, first in CFFEX_FIRST.items():
            if stamp < first:
                continue
            file_url = f'http://www.cffex.com.cn/sj/ccpm/{ymd[:6]}/{ymd[6:]}/{product}_1.csv'
            records.extend(parse_cffex_rank(read.read(file_url), stamp, product))
        require(bool(records), '该日未有已登记的中金所会员排名品种')
        url = 'https://www.cffex.com.cn/ccpm/'
    unique(records, ('level', 'contract', 'rank'))
    return slice_result(query, records, url.replace('http:', 'https:', 1), notes=(
        '成交量、持买单量、持卖单量分别对应三套会员排名，同一行不表示同一会员；数量与增减量为手。',
        '上期与能源按同日文件排除重复；中金所逐一读取当日已上市的登记品种文件，缺任一文件即拒绝部分结果。',
        '郑商所的品种合计与具体合约层级分开；交易所代码按原样保留，不自动转换为新浪代码，不据此生成交易或仓位信号。'))
