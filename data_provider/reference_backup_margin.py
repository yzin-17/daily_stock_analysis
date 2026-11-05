"""沪深官方两融资格投影，分别证明分页、日期见证和金额／数量单位。"""

from datetime import date
from decimal import Decimal, ROUND_DOWN
import io
import re

import pandas as pd

from .market_reference_projection import number, require

SH_FIELDS = {'financingBalance': 'rzye', 'financingBuy': 'rzmre',
             'securitiesBalance': 'rqylje', 'shortVolume': 'rqyl', 'shortSellVolume': 'rqmcl'}
SZ_FIELDS = {'financingBalance': '融资余额(元)', 'financingBuy': '融资买入额(元)',
             'securitiesBalance': '融券余额(元)', 'shortVolume': '融券余量(股/份)',
             'shortSellVolume': '融券卖出量(股/份)'}


def normalized_day(stamp):
    require(isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', stamp) is not None, '两融日期无效')
    require(date.fromisoformat(stamp).isoformat() == stamp, '两融日期无效')
    return stamp


def sh_code(code):
    return isinstance(code, str) and re.fullmatch(r'(?:60\d{4}|68[89]\d{3}|5\d{5})', code) is not None


def parse_sh_margin(pages, stamp):
    normalized_day(stamp)
    require(bool(pages), '上交所两融没有实际页面')
    records, seen = [], set()
    total, size, count = None, None, None
    for index, payload in enumerate(pages, 1):
        require(payload.get('detailsDate') == stamp.replace('-', ''), '上交所两融请求日期回显不符')
        page = payload.get('pageHelp')
        require(isinstance(page, dict), '上交所两融分页缺失')
        if total is None:
            total, size, count = page.get('total'), page.get('pageSize'), page.get('pageCount')
            require(all(type(value) is int and value > 0 for value in [total, size, count]), '上交所两融未发布或分页无效')
            require(count == (total + size - 1) // size, '上交所两融页数不符')
        require(page.get('pageNo') == index and page.get('total') == total
                and page.get('pageSize') == size and page.get('pageCount') == count, '上交所两融分页期间变化')
        rows = page.get('data')
        expected = min(size, total - (index - 1) * size)
        require(isinstance(rows, list) and len(rows) == expected and expected > 0, '上交所两融页面不完整')
        for row in rows:
            require(row.get('opDate') == stamp.replace('-', '') and sh_code(row.get('stockCode')), '上交所两融记录日期或证券不符')
            code = row['stockCode']
            require(code not in seen and set(SH_FIELDS.values()).issubset(row), '上交所两融证券重复或核心字段缺失')
            seen.add(code)
            record = {'date': stamp, 'code': code, 'name': row.get('securityAbbr')}
            for key, source in SH_FIELDS.items():
                record[key] = number(row[source])
                require(record[key] is not None or key == 'securitiesBalance', '上交所两融核心数值缺失')
            records.append(record)
    require(len(pages) == count and len(records) == total, '上交所两融尚有未读页，不能计为完整快照')
    return {'records': records, 'total': total, 'complete': True,
            'missingShortBalances': sum(row['securitiesBalance'] is None for row in records),
            'outsideCurrentStockCodeScope': [row['code'] for row in records if row['code'].startswith('689')],
            'directReplacement': False, 'units': {'amount': '元', 'quantity': '股／份，依证券类别'},
            'unitEvidence': 'https://www.sse.com.cn/market/othersdata/margin/detail/'}


def parse_sz_margin(content, metadata, stamp):
    normalized_day(stamp)
    require(isinstance(metadata, list) and len(metadata) == 1, '深交所两融日期见证封套无效')
    section = metadata[0]
    meta = section.get('metadata', {})
    require(meta.get('catalogid') == '1837_xxpl' and meta.get('tabkey') == 'tab2', '深交所两融见证不是明细目录')
    conditions = [row for row in meta.get('conditions', []) if row.get('name') == 'txtDate']
    require(len(conditions) == 1 and conditions[0].get('defaultValue') == stamp, '深交所两融来源未回显所查日期')
    frame = pd.read_excel(io.BytesIO(content), dtype=str).fillna('')
    require({'证券代码', '证券简称', *SZ_FIELDS.values()}.issubset(frame.columns), '深交所两融 Excel 表头变化')
    total = meta.get('recordcount')
    require(type(total) is int and total > 0 and len(frame) == total, '深交所两融 Excel 未发布或与目录总数不符')
    records, by_code = [], {}
    for source in frame.to_dict('records'):
        code = source['证券代码']
        require(re.fullmatch(r'(?:00\d{4}|30\d{4}|1\d{5})', code) is not None and code not in by_code, '深交所两融证券无效或重复')
        row = {'date': stamp, 'code': code, 'name': source['证券简称']}
        for key, label in SZ_FIELDS.items():
            row[key] = number(source[label].replace(',', ''))
            require(row[key] is not None, '深交所两融核心金额或数量缺失')
        records.append(row)
        by_code[code] = row
    # 实际目录按显示单位截去两位之后的小数；100 项原件对照已确认，不能当作四舍五入。
    mapping = {'jrrzmr': ('financingBuy', Decimal(100000000)),
               'jrrzye': ('financingBalance', Decimal(100000000)),
               'jrrjye': ('securitiesBalance', Decimal(10000)),
               'jrrjyl': ('shortVolume', Decimal(10000)),
               'jrrjmc': ('shortSellVolume', Decimal(10000))}
    labels = meta.get('cols', {})
    require('亿元' in labels.get('jrrzmr', '') and '万元' in labels.get('jrrjye', '')
            and '万股/万份' in labels.get('jrrjyl', ''), '深交所两融目录量纲变化')
    witness = section.get('data')
    require(isinstance(witness, list) and bool(witness), '深交所两融日期见证没有实际记录')
    for source in witness:
        require(source.get('zqdm') in by_code, '深交所两融见证证券不在 Excel')
        row = by_code[source['zqdm']]
        for field, (key, scale) in mapping.items():
            raw = source.get(field, '').replace(',', '')
            require(re.fullmatch(r'\d+\.\d{2}', raw) is not None, '深交所两融目录显示精度变化')
            value = number(raw)
            expected = (Decimal(row[key]) / scale).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            require(value is not None and Decimal(value) == expected,
                    '深交所两融目录与 Excel 数值不一致')
    return {'records': records, 'total': total, 'complete': True, 'directoryWitnessRows': len(witness),
            'dateEvidence': '同日请求、同目录日期回显、总数及首屏数值交叉见证',
            'units': {'amount': '元', 'quantity': '股／份'}, 'directReplacement': False,
            'remainingQualification': ['本轮未证明全市场／全部日期适用性，产品绑定尚未启用']}
