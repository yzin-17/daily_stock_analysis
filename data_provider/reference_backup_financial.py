"""同花顺三表资格投影：只取公司 report，保留来源舍入及未知元数据。"""

from datetime import date
from decimal import Decimal
import json
import re

from .market_reference_projection import require

FIELDS = {
    'debt': {'assets': ('资产合计', '*资产合计'), 'liabilities': ('负债合计', '*负债合计'),
             'parentEquity': ('归属于母公司所有者权益合计', '*归属于母公司所有者权益合计')},
    'benefit': {'revenue': ('一、营业总收入', '*营业总收入'),
                'netProfit': ('五、净利润', '*净利润'),
                'parentProfit': ('归属于母公司所有者的净利润', '*归属于母公司所有者的净利润'),
                'basicEps': ('（一）基本每股收益', '基本每股收益')},
    'cash': {'operating': ('经营活动产生的现金流量净额', '*经营活动产生的现金流量净额'),
             'investing': ('投资活动产生的现金流量净额', '*投资活动产生的现金流量净额'),
             'financing': ('筹资活动产生的现金流量净额', '*筹资活动产生的现金流量净额')},
}


def displayed_number(value):
    if value is None or value is False or value == '' or value == '--':
        return None, None
    require(isinstance(value, str), '三表显示值不是已核对的数值文本')
    match = re.fullmatch(r'(-?\d+(?:\.\d+)?)(万|亿)?', value.replace(',', ''))
    require(match is not None, '三表显示值或量纲变化')
    number = Decimal(match[1])
    scale = {'万': Decimal(10000), '亿': Decimal(100000000), None: Decimal(1)}[match[2]]
    places = len(match[1].split('.')[1]) if '.' in match[1] else 0
    return format(number * scale, 'f'), format(scale * Decimal(10) ** -places / 2, 'f')


def parse_financial(payload, kind, *, limit=5):
    require(kind in FIELDS and type(limit) is int and limit > 0, '三表类型或切片无效')
    require(isinstance(payload, dict) and isinstance(payload.get('flashData'), str), '同花顺三表封套变化')
    # fieldflashData 是不同统计口径的数据，不能与公司报表混用。
    table = json.loads(payload['flashData'])
    titles, matrix = table.get('title'), table.get('report')
    require(isinstance(titles, list) and isinstance(matrix, list) and len(titles) == len(matrix), '三表标题／矩阵不对应')
    periods = matrix[0]
    require(isinstance(periods, list) and bool(periods), '三表没有报告期')
    normalized = [date.fromisoformat(stamp).isoformat() for stamp in periods]
    require(normalized == periods and len(set(periods)) == len(periods) and periods == sorted(periods, reverse=True), '三表报告期无效或重复')
    selected = {}
    for key, candidates in FIELDS[kind].items():
        indexes = []
        for label in candidates:
            indexes = [i for i, title in enumerate(titles) if isinstance(title, list) and title[0] == label]
            if indexes:
                break
        require(len(indexes) == 1, '三表核心科目缺失或重复：' + key)
        index = indexes[0]
        require(titles[index][1] == '元' and isinstance(matrix[index], list)
                and len(matrix[index]) == len(periods), '三表单位或科目长度变化')
        selected[key] = matrix[index]
    records = []
    for index, period in enumerate(periods[:limit]):
        row = {'period': period, 'publishedDate': None, 'currency': None,
               'sourceBasis': 'report', 'rawValues': {}, 'roundingHalfUnits': {}}
        for key, values in selected.items():
            raw = values[index]
            row[key], row['roundingHalfUnits'][key] = displayed_number(raw)
            row['rawValues'][key] = raw
        records.append(row)
    return {'records': records, 'periodCount': len(periods), 'monetaryUnit': '来源元',
            'identityEvidence': '请求 URL 定位；正文未独立回显证券',
            'missingContractFields': ['publishedDate', 'explicitIsoCurrency', 'explicitConsolidationBasis'],
            'numericPrecision': '来源显示值与舍入区间，不替代原值精度', 'directReplacement': False}
