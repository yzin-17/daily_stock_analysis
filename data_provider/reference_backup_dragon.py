"""沪深官方龙虎榜代表片段，区分披露原因、榜单层级与各自单位。"""

from datetime import date
from decimal import Decimal
from html import unescape
import json
import re

from .market_reference_projection import number, require


def parse_sh_dragon(content, stamp):
    date.fromisoformat(stamp)
    match = re.fullmatch(r'cb\((.*)\);?\s*', content.decode('utf-8'), re.S)
    require(match is not None, '沪市龙虎榜不是固定 JSONP 回调')
    payload = json.loads(match[1])
    lines = payload.get('fileContents')
    require(payload.get('dateTx') == stamp and isinstance(lines, list)
            and bool(lines) and all(isinstance(line, str) for line in lines), '沪市龙虎榜日期或正文不符')
    year, month, day = stamp.split('-')
    require(any(f'交易日期:{year}年{month}月{day}日' in line.replace(' ', '') for line in lines), '沪市龙虎榜正文没有所查交易日期')
    reason, code, name, side = None, None, None, None
    seats = []
    for line in lines:
        heading = re.match(r'^\s*[一二三四五六七八九十]+、(.+)', line)
        if heading and code is None:
            reason = heading[1].strip().rstrip(':：')
        identity = re.search(r'证券代码[:：]\s*(\d{6})\s+证券简称[:：]\s*(.+)', line)
        if identity:
            if code is not None:
                break
            require(re.fullmatch(r'(?:60\d{4}|688\d{3})', identity[1]) is not None and reason is not None,
                    '沪市龙虎榜代表证券或披露原因无效')
            code, name = identity[1], identity[2].strip()
            continue
        if code is None:
            continue
        if '买入营业部名称' in line or '卖出营业部名称' in line:
            require('(元)' in line, '沪市龙虎榜营业部金额单位变化')
            side = 'buy' if '买入营业部名称' in line else 'sell'
            continue
        row = re.fullmatch(r'\s*\((\d+)\)\s+(.+?)\s+([\d,]+(?:\.\d+)?)\s*', line)
        if row:
            require(side is not None, '沪市龙虎榜席位没有买卖侧')
            seats.append({'side': side, 'rank': int(row[1]), 'member': row[2].strip(),
                          'amount': number(row[3].replace(',', '')), 'unit': '元'})
    require(code is not None, '沪市龙虎榜没有可核对的证券详情')
    for side in ['buy', 'sell']:
        ranks = [row['rank'] for row in seats if row['side'] == side]
        require(sorted(ranks) == [1, 2, 3, 4, 5], '沪市龙虎榜代表片段前五席位不完整')
    sums = {side: format(sum(Decimal(row['amount']) for row in seats if row['side'] == side), 'f')
            for side in ['buy', 'sell']}
    return {'date': stamp, 'code': code, 'name': name, 'reason': reason, 'seats': seats,
            'buyRankTop5Sum': sums['buy'], 'sellRankTop5Sum': sums['sell'],
            'scope': '原全文首个沪市证券／原因的双侧前五席位代表片段', 'fullMarketProjection': False,
            'directReplacement': False,
            'remainingQualification': ['未证明当前东财买入／卖出／净额字段与前五席位合计口径等价']}


def parse_sz_dragon(parent, detail, stamp, code):
    date.fromisoformat(stamp)
    require(isinstance(parent, list) and len(parent) == 1 and isinstance(detail, list) and len(detail) == 2,
            '深市龙虎榜封套或详情分表变化')
    parent_meta = parent[0].get('metadata', {})
    require(parent_meta.get('catalogid') == '1842_xxpl', '深市龙虎榜主表目录不符')
    candidates = [row for row in parent[0].get('data', []) if row.get('zqdm') == code and row.get('dqrq') == stamp]
    require(len(candidates) == 1, '深市龙虎榜代表证券或日期不符')
    summary, member_table = detail
    require(summary.get('metadata', {}).get('tabkey') == 'tab1'
            and member_table.get('metadata', {}).get('tabkey') == 'tab2'
            and all(table.get('metadata', {}).get('catalogid') == '1842_detal' for table in detail), '深市龙虎榜详情目录／分表不符')
    require(len(summary.get('data', [])) == 1, '深市龙虎榜证券摘要不唯一')
    source = summary['data'][0]
    expected = candidates[0]
    require(source.get('dqrq') == stamp and f'({code})' in unescape(source.get('zqjc', ''))
            and source.get('plyy') == expected.get('plyy'), '深市龙虎榜证券／日期／原因跨页变化')
    amount = re.fullmatch(r'([\d,]+) 元', source.get('cjje', ''))
    require(amount is not None, '深市龙虎榜详情成交金额缺少元单位')
    turnover = number(amount[1].replace(',', ''))
    require('亿元' in parent_meta.get('cols', {}).get('cjje', ''), '深市龙虎榜主表成交额量纲变化')
    parent_amount = number(expected.get('cjje'))
    require(parent_amount is not None and abs(Decimal(turnover) - Decimal(parent_amount) * 100000000) <= Decimal(500000),
            '深市龙虎榜成交金额跨页不一致')
    cols = member_table.get('metadata', {}).get('cols', {})
    require('元' in cols.get('mrje', '') and '元' in cols.get('mcje', ''), '深市龙虎榜席位金额单位不明')
    seats = []
    for row in member_table.get('data', []):
        rank = re.fullmatch(r'(买|卖)([1-5])', row.get('mmlb', ''))
        require(rank is not None and bool(row.get('zsmc')), '深市龙虎榜席位身份或层级变化')
        buy, sell = number(row.get('mrje', '').replace(',', '')), number(row.get('mcje', '').replace(',', ''))
        require(buy is not None and sell is not None, '深市龙虎榜席位金额缺失')
        seats.append({'side': 'buy' if rank[1] == '买' else 'sell', 'rank': int(rank[2]),
                      'member': unescape(row['zsmc']), 'buyAmount': buy, 'sellAmount': sell, 'unit': '元'})
    for side in ['buy', 'sell']:
        require(sorted(row['rank'] for row in seats if row['side'] == side) == [1, 2, 3, 4, 5],
                '深市龙虎榜前五席位重复或不完整')
    return {'date': stamp, 'code': code, 'reason': source['plyy'], 'abnormalPeriod': source.get('ycqj'),
            'turnover': turnover, 'turnoverUnit': '元', 'seats': seats,
            'listPageRows': len(parent[0]['data']), 'listTotal': parent_meta.get('recordcount'),
            'fullMarketProjection': False, 'directReplacement': False,
            'remainingQualification': ['主表成交额不等于榜单买入额；双榜可能重复席位，未与当前东财汇总口径对拍']}
