"""新浪三表的原报表口径；保留币种与实际披露日期。"""

from urllib.parse import urlencode

from .market_reference_projection import day, http_json, number, require, result, text

ENDPOINT = 'https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022'
PROFILES = {
    'financial-balance': ('fzb', {'assets': 'TOTASSET', 'liabilities': 'TOTLIAB', 'parentEquity': 'PARESHARRIGH'}),
    'financial-income': ('lrb', {'revenue': 'BIZTOTINCO', 'netProfit': 'NETPROFIT', 'parentProfit': 'PARENETP', 'basicEps': 'BASICEPS'}),
    'financial-cashflow': ('llb', {'operating': 'MANANETR', 'investing': 'INVNETCASHFLOW', 'financing': 'FINNETCFLOW'}),
}


def project_financial(payload, query):
    report_type, mapping = PROFILES[query.dataset]
    require(isinstance(payload, dict) and isinstance(payload.get('result'), dict), '财报封套无效')
    root = payload['result']
    require(root.get('status', {}).get('code') == 0, '来源没有明确财报成功回执')
    data = root.get('data')
    require(isinstance(data, dict) and isinstance(data.get('report_list'), dict), '财报列表结构不符')
    count = int(data['report_count'])
    records = []
    for period, report in sorted(data['report_list'].items(), reverse=True):
        require(isinstance(report, dict) and isinstance(report.get('data'), list), '财报科目结构不符')
        require(report.get('rCurrency') == 'CNY', '当前财报合同仅接受明确的人民币原表')
        report_date, published = day(period), day(report.get('publish_date'))
        require(report_date is not None and published is not None and published >= report_date, '财报期间或披露日不符')
        fields = {}
        for item in report['data']:
            require(isinstance(item, dict), '财报科目结构不符')
            key = item.get('item_field')
            if key not in mapping.values():
                continue
            require(item.get('item_source') == report_type and key not in fields, '来源科目类型或身份不符')
            fields[key] = item.get('item_value')
        values = {key: number(fields.get(field)) for key, field in mapping.items()}
        require(any(value is not None for value in values.values()), '财报缺少本切片的核心科目')
        records.append({'period': report_date, 'publishedDate': published,
                        'basis': text(report.get('rType')), **values})
    require(count >= 0 and (records or count <= (query.page - 1) * query.pageSize), '财报总数与当前页不符')
    source_url = ENDPOINT + '?' + urlencode(request_params(query))
    return result(query, 'akshare', 'sina', 'sina-original-finance-report-v1', source_url, records,
                  total=count, has_more=query.page * query.pageSize < count,
                  notes=('金额保留 CNY 原值；EPS 为每股值。按来源原报表展示，不进行单季度拆分。',
                         '历史报告可能包含来源后续修订值，不能直接用于严格历史回测。'))


def request_params(query):
    code, exchange = query.symbol.split('.')
    return {'paperCode': exchange.lower() + code, 'source': PROFILES[query.dataset][0],
              'type': '0', 'page': str(query.page), 'num': str(query.pageSize)}


def read_financial(query, context):
    params = request_params(query)
    payload = http_json(ENDPOINT, params, {'User-Agent': 'Mozilla/5.0'}, context.timeout_seconds)
    return project_financial(payload, query)
