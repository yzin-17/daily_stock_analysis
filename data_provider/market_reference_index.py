"""新浪当前指数成分；不生成权重或历史生效区间。"""

import re
from urllib.parse import urlencode

from .market_reference_projection import http_json, require, result, text

ENDPOINT = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'


def params_for(query):
    return {'node': 'hs300' if query.indexCode == '000300' else 'zhishu_' + query.indexCode,
            'page': str(query.page), 'num': str(query.pageSize), 'sort': 'symbol', 'asc': '1', '_s_r_a': 'init'}


def project_index(payload, query):
    require(isinstance(payload, list), '指数来源没有返回成分列表')
    records = []
    for row in payload:
        require(isinstance(row, dict) and isinstance(row.get('symbol'), str) and re.fullmatch(r'(?:sh|sz)\d{6}', row['symbol']), '成分证券身份无效')
        require(row.get('code') == row['symbol'][2:], '成分代码与来源市场不符')
        records.append({'symbol': row['symbol'][2:] + '.' + row['symbol'][:2].upper(), 'name': text(row.get('name'))})
    return result(query, 'akshare', 'sina', 'sina-current-index-members-v1', ENDPOINT + '?' + urlencode(params_for(query)),
                  records, has_more=len(records) == query.pageSize,
                  notes=('新浪当前成分参考；来源不回显指数身份、权重或历史生效日期，不用于正式历史指数还原。',))


def read_index(query, context):
    return project_index(http_json(ENDPOINT, params_for(query), {'User-Agent': 'Mozilla/5.0'}, context.timeout_seconds, encoding='gb18030'), query)
