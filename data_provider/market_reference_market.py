"""东财板块、资金流、监控名单及涨跌停事实。"""

from decimal import Decimal
from urllib.parse import urlencode

from .market_reference_projection import day, http_json, number, require, result, text

POOLS = {'limit-up': ('getTopicZTPool', 'fbt:asc'), 'limit-break': ('getTopicZBPool', 'fbt:asc'),
         'limit-down': ('getTopicDTPool', 'fund:asc'), 'yesterday-limit': ('getYesterdayZTPool', 'zs:desc')}


def request_spec(query):
    common = {'fltt': '2', 'invt': '2', 'po': '1'}
    if query.dataset == 'concepts':
        return ('https://push2.eastmoney.com/api/qt/slist/get', common | {
            'secid': ('1' if query.symbol.endswith('.SH') else '0') + '.' + query.symbol[:6],
            'spt': '3', 'pi': str(query.page - 1), 'pz': str(query.pageSize), 'fields': 'f12,f14,f3,f128'})
    if query.dataset == 'fund-flow':
        return ('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get', {
            'secid': ('1' if query.symbol.endswith('.SH') else '0') + '.' + query.symbol[:6],
            'fields1': 'f1,f2,f3,f7', 'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
            'lmt': str(query.page * query.pageSize + 1), 'klt': '101', 'ut': 'b2884a393a59ad64002292a3e90d46a5'})
    if query.dataset == 'stock-monitor':
        return 'https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json', {}
    endpoint, sort = POOLS[query.dataset]
    return ('https://push2ex.eastmoney.com/' + endpoint, {
        'ut': '7eea3edcaed734bea9cbfc24409ed989', 'dpt': 'wz.ztzt', 'Pageindex': str(query.page - 1),
        'pagesize': str(query.pageSize), 'sort': sort, 'date': query.start.replace('-', '')})


def project_market(payload, query):
    url, params = request_spec(query)
    url += '?' + urlencode(params) if params else ''
    records, total, more = [], None, False
    if query.dataset == 'stock-monitor':
        require(isinstance(payload, list), '监控名单封套无效')
        total = len(payload)
        start = (query.page - 1) * query.pageSize
        for row in payload[start:start + query.pageSize]:
            market = {'1': 'SH', '0': 'SZ', 'B': 'BJ'}.get(str(row.get('MARKET')))
            require(market and isinstance(row.get('STKCODE'), str) and len(row['STKCODE']) == 6 and row['STKCODE'].isdigit(), '监控证券市场无效')
            records.append({'symbol': row['STKCODE'] + '.' + market, 'name': text(row.get('STKNAME')),
                            'startDate': day(row.get('VALIDATESTARTDATE')), 'endDate': day(row.get('VALIDATEENDDATE')),
                            'originalUrl': text(row.get('LINK_URL'))})
        more = start + len(records) < total
        notes = ['按来源名单展示有效期，包含尚未开始或已结束条目；不将名单解读为投资建议。']
    else:
        require(isinstance(payload, dict) and payload.get('rc') == 0 and isinstance(payload.get('data'), dict), '市场资料业务封套无效')
        data = payload['data']
        if query.dataset == 'fund-flow':
            require(data.get('code') == query.symbol[:6] and data.get('market') == (1 if query.symbol.endswith('.SH') else 0), '资金流证券身份不符')
            lines = data.get('klines')
            require(isinstance(lines, list), '资金流列表缺失')
            parsed = []
            for line in lines:
                require(isinstance(line, str) and len(line.split(',')) == 15, '资金流字段缺失')
                row = line.split(',')
                parsed.append({'date': day(row[0]), 'mainNet': number(row[1]), 'mainPct': number(row[6]),
                               'largeNet': number(row[4]), 'superNet': number(row[5])})
            require(len({x['date'] for x in parsed}) == len(parsed), '资金流日期重复')
            parsed.sort(key=lambda row: row['date'], reverse=True)
            start = (query.page - 1) * query.pageSize
            records = parsed[start:start + query.pageSize]
            more = start + len(records) < len(parsed)
            notes = ['资金流按来源的大单口径估算，不代表交易所逐笔身份；仅查询来源最近可用窗口。']
        elif query.dataset in POOLS:
            require(day(str(data.get('qdate'))) == query.start, '来源未返回请求交易日，不能视为历史空态')
            pool, total = data.get('pool'), data.get('tc')
            require(isinstance(pool, list) and type(total) is int and total >= 0, '涨跌停池分页无效')
            for row in pool:
                market = {0: 'SZ', 1: 'SH'}.get(row.get('m'))
                require(market and isinstance(row.get('c'), str) and row['c'].isdigit() and len(row['c']) == 6, '池证券身份无效')
                price = number(row.get('p'))
                records.append({'date': query.start, 'symbol': row['c'] + '.' + market, 'name': text(row.get('n')),
                                'price': number(Decimal(price) / 1000) if price is not None else None,
                                'changePct': number(row.get('zdp')), 'amount': number(row.get('amount')),
                                'industry': text(row.get('hybk'))})
            more = query.page * query.pageSize < total
            notes = ['仅接受来源回显与请求一致的交易日；来源历史保留窗口有限。昨涨停池的涨跌幅为所选日表现。']
        else:
            rows, total = data.get('diff'), data.get('total')
            if rows is None and total == 0:
                rows = []
            require(isinstance(rows, (list, dict)) and type(total) is int and total >= 0, '来源板块分页无效')
            for row in rows.values() if isinstance(rows, dict) else rows:
                code, name = text(row.get('f12')), text(row.get('f14'))
                require(code and name, '板块身份缺失')
                record = {'code': code, 'name': name}
                records.append(record)
            more = query.page * query.pageSize < total
            notes = ['来源当前分类与排名，不代表历史行业、指数分类或完整主题生命周期。']
    return result(query, 'akshare', 'eastmoney', 'eastmoney-market-reference-v1', url,
                  records, total=total, has_more=more, notes=notes)


def read_market(query, context):
    url, params = request_spec(query)
    return project_market(http_json(url, params, {'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://quote.eastmoney.com/'}, context.timeout_seconds), query)
