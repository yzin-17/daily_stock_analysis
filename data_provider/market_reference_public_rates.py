"""中国货币网 FR／FDR；接口与 CSV 布局参考 a-stock-data §11.4。"""

import csv
import io

from .market_reference_projection import day, number, require
from .market_reference_public_common import PublicRead, paged, unique

BASE = 'https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/'


def parse_fixing(content):
    records = []
    for fields in csv.reader(io.StringIO(content.decode('utf-8-sig'))):
        if not fields or not any(field.strip() for field in fields):
            continue
        require(len(fields) == 9 and not any(field.strip() for field in fields[1:6]), '定盘利率 CSV 列结构改变')
        values = [number(value.strip()) for value in fields[6:]]
        require(all(value is not None for value in values), '定盘利率缺少核心期限')
        stamp = day(fields[0].strip())
        require(stamp is not None, '定盘利率缺少日期')
        records.append({'date': stamp, 'overnight': values[0], 'oneWeek': values[1], 'fortnight': values[2]})
    require(bool(records), '定盘利率来源未给有效记录')
    unique(records, ('date',))
    return sorted(records, key=lambda row: row['date'], reverse=True)


def read_fixing(query, context):
    filename = 'frr-chrt.csv' if query.dataset == 'repo-fr' else 'fdr-chrt.csv'
    url = BASE + filename
    records = parse_fixing(PublicRead(context.timeout_seconds).read(url,
                           headers={'Referer': 'https://www.chinamoney.com.cn/chinese/bkfrr/'}))
    return paged(query, records, url, notes=('中国货币网公开定盘利率，单位为百分数；FR 与 FDR 的样本范围不同，不混合计算。',))
