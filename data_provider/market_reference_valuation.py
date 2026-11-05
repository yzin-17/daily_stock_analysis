"""BaoStock 估值参考序列；不将估值比率作为价格复权。"""

from datetime import date

from .market_reference_projection import day, number, require, result


def project_valuation(payload, query):
    require(isinstance(payload, dict) and isinstance(payload.get('rows'), list), '估值响应结构无效')
    expected = query.symbol.split('.')[1].lower() + '.' + query.symbol[:6]
    records = []
    dates = set()
    for row in payload['rows']:
        require(row.get('code') == expected, '估值证券身份不符')
        stamp = day(row.get('date'))
        require(stamp and query.start <= stamp <= query.end and stamp not in dates, '估值日期越界或重复')
        dates.add(stamp)
        values = {key: number(row.get(source)) for key, source in [
            ('peTtm', 'peTTM'), ('pbMrq', 'pbMRQ'), ('psTtm', 'psTTM'),
            ('pcfTtm', 'pcfNcfTTM'), ('turnoverPct', 'turn')]}
        require(any(value is not None for value in values.values()), '估值行缺少可用比率')
        records.append({'date': stamp, **values})
    records.sort(key=lambda row: row['date'])
    offset = (query.page - 1) * query.pageSize
    return result(query, 'baostock', 'baostock', 'baostock-reference-valuation-v1',
                  'https://www.baostock.com', records[offset:offset + query.pageSize],
                  total=len(records), has_more=offset + query.pageSize < len(records),
                  notes=('按来源返回的窗口记录分页；不承诺窗口内所有交易日均有数据。',
                         '估值比率可能为负或缺失；换手率单位为百分数。'))


def read_valuation(query, context):
    from data_provider.baostock_reference_process import read_valuation as sdk_read
    value = sdk_read(context.adapter, symbol=query.symbol, start=date.fromisoformat(query.start),
                     end=date.fromisoformat(query.end), timeout_seconds=context.timeout_seconds)
    return project_valuation(value, query)
