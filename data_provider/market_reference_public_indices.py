"""中证／国证文件投影；参考 a-stock-data §12，不混合不同日期的快照。"""

from decimal import Decimal
import io
import re

import pandas as pd
from .market_reference_projection import day, number, require
from .market_reference_public_common import PublicRead, normalized_code, paged, unique

CSI = 'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/'
CNI = 'https://www.cnindex.com.cn/sample-detail/download-history'


def parse_index(content, query):
    frame = pd.read_excel(io.BytesIO(content), dtype=str).fillna('')
    frame.columns = [re.sub(r'\s+', '', str(column)) for column in frame.columns]
    csi, weights = query.dataset.startswith('csi-'), query.dataset.endswith('-weights')
    columns = ('日期Date', '成份券代码ConstituentCode', '成份券名称ConstituentName') if csi else ('日期', '样本代码', '样本简称')
    required = {*columns, *(['指数代码IndexCode', '交易所Exchange'] if csi else [])}
    weight_key = '权重(%)weight' if csi else '权重（%）'
    if weights:
        required.add(weight_key)
    require(required <= set(frame.columns), '官方指数文件列改变')
    records = []
    for raw in frame.to_dict('records'):
        code = normalized_code(raw[columns[1]])
        if csi:
            require(normalized_code(raw['指数代码IndexCode']) == query.indexCode, '中证文件返回其他指数')
            exchange = {'上海证券交易所': 'SH', '深圳证券交易所': 'SZ', '北京证券交易所': 'BJ'}.get(raw['交易所Exchange'])
        else:
            exchange = None
            if code.startswith('6'):
                exchange = 'SH'
            elif code.startswith(('0', '3')):
                exchange = 'SZ'
            elif code.startswith(('4', '8', '92')):
                exchange = 'BJ'
        require(exchange and raw[columns[2]], '指数成分的交易所或名称不明')
        record = {'date': day(raw[columns[0]]), 'symbol': code + '.' + exchange,
                  'name': raw[columns[2]], 'exchange': {'SH': '上海', 'SZ': '深圳', 'BJ': '北京'}[exchange]}
        if weights:
            record['weightPct'] = number(raw[weight_key])
            require(record['weightPct'] is not None and 0 <= Decimal(record['weightPct']) <= 100, '指数权重不是百分数')
        records.append(record)
    require(bool(records) and len({row['date'] for row in records}) == 1 and records[0]['date'] is not None, '指数文件不是单个有效日期快照')
    unique(records, ('symbol',))
    if weights:
        require(Decimal('99') <= sum(Decimal(row['weightPct']) for row in records) <= Decimal('101'), '指数权重合计或覆盖异常')
    return sorted(records, key=lambda row: row['symbol'])


def read_official_index(query, context):
    if query.dataset.startswith('csi-'):
        kind = 'closeweight' if query.dataset.endswith('-weights') else 'cons'
        url = f'{CSI}{kind}/{query.indexCode}{kind}.xls'
        content = PublicRead(context.timeout_seconds).read(url)
    else:
        url = CNI + '?indexcode=' + query.indexCode
        content = PublicRead(context.timeout_seconds).read(CNI, {'indexcode': query.indexCode})
    return paged(query, parse_index(content, query), url,
                 notes=('官方最近发布文件，使用其快照日期；成分文件与权重文件可能不同日，不按行号拼接或用于反填历史成分。',
                        '国证文件按请求指数及公开下载协议关联；逐行没有回显指数代码，不宣称已有完整历史调样库。'))
