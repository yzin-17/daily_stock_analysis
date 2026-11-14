"""指定日期证券集合与基础类型原文读取；覆盖范围由消费者单独核验。"""

from datetime import date, datetime, timezone


def _rows(result, required):
    if result.error_code != '0' or not set(required) <= set(result.fields):
        raise ValueError('BaoStock 目录查询失败或字段不完整')
    rows = []
    while result.next():
        rows.append(dict(zip(result.fields, result.get_row_data())))
        if len(rows) >= 30000:
            raise ValueError('BaoStock 目录超过有界容量')
    if result.error_code != '0':
        raise ValueError('BaoStock 目录未完整返回')
    return rows


def read_history_catalog(adapter, day):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('目录日期无效')
    with adapter._baostock_session() as sdk:
        members = _rows(sdk.query_all_stock(day=day), ('code',))
        basics = _rows(sdk.query_stock_basic(), ('code', 'type', 'ipoDate', 'outDate'))
    return {'date': day, 'observedAt': datetime.now(timezone.utc).isoformat(), 'members': members, 'basics': basics}


def catalog_summary(raw):
    basics = {row['code']: row for row in raw['basics']}
    exchanges, types, unknown = {}, {}, []
    for member in raw['members']:
        code = member['code']
        exchange = code.split('.')[0]
        exchanges[exchange] = exchanges.get(exchange, 0) + 1
        basic = basics.get(code)
        if basic is None:
            unknown.append(code)
            continue
        kind = basic['type']
        types[kind] = types.get(kind, 0) + 1
    return {'date': raw['date'], 'members': len(raw['members']), 'basics': len(raw['basics']),
            'exchanges': exchanges, 'types': types, 'missingTypes': len(unknown), 'unknownSamples': unknown[:5]}
