"""东方财富公司公开事件的字段、金额与时间投影。"""

from urllib.parse import urlencode

from .market_reference_projection import day, http_json, identity, number, require, result, text

ENDPOINT = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
# 报表、证券字段、排序／筛选日期、公开列映射。金额均保留来源人民币原值。
PROFILES = {
    'margin': ('RPTA_WEB_RZRQ_GGMX', 'SCODE', 'DATE', {
        'date': 'DATE', 'financingBalance': 'RZYE', 'securitiesBalance': 'RQYE', 'financingBuy': 'RZMRE'}),
    'holder-count': ('RPT_HOLDERNUMLATEST', 'SECURITY_CODE', 'END_DATE', {
        'period': 'END_DATE', 'publishedDate': 'HOLD_NOTICE_DATE', 'holders': 'HOLDER_NUM',
        'change': 'HOLDER_NUM_CHANGE', 'changePct': 'HOLDER_NUM_RATIO'}),
    'block-trade': ('RPT_DATA_BLOCKTRADE', 'SECURITY_CODE', 'TRADE_DATE', {
        'date': 'TRADE_DATE', 'price': 'DEAL_PRICE', 'amount': 'DEAL_AMT', 'buyer': 'BUYER_NAME', 'seller': 'SELLER_NAME'}),
    'earnings-forecast': ('RPT_PUBLIC_OP_NEWPREDICT', 'SECURITY_CODE', 'NOTICE_DATE', {
        'publishedDate': 'NOTICE_DATE', 'period': 'REPORT_DATE', 'indicator': 'PREDICT_FINANCE', 'type': 'PREDICT_TYPE',
        'amountLower': 'PREDICT_AMT_LOWER', 'amountUpper': 'PREDICT_AMT_UPPER',
        'changeLower': 'ADD_AMP_LOWER', 'changeUpper': 'ADD_AMP_UPPER'}),
    'holder-trades': ('RPT_SHARE_HOLDER_INCREASE', 'SECURITY_CODE', 'NOTICE_DATE', {
        'publishedDate': 'NOTICE_DATE', 'startDate': 'START_DATE', 'endDate': 'END_DATE', 'holder': 'HOLDER_NAME',
        'direction': 'DIRECTION', 'shares10k': 'CHANGE_NUM_SYMBOL', 'averagePrice': 'TRADE_AVERAGE_PRICE'}),
    'earnings-express': ('RPT_FCI_PERFORMANCEE', 'SECURITY_CODE', 'NOTICE_DATE', {
        'publishedDate': 'NOTICE_DATE', 'period': 'REPORT_DATE', 'revenue': 'TOTAL_OPERATE_INCOME',
        'parentProfit': 'PARENT_NETPROFIT', 'basicEps': 'BASIC_EPS'}),
    'buyback': ('RPTA_WEB_GETHGLIST_NEW', 'DIM_SCODE', 'UPD', {
        'publishedDate': 'UPDATEDATE', 'planId': 'REPURCODE', 'progress': 'REPURPROGRESS',
        'amountLower': 'REPURAMOUNTLOWER', 'amountUpper': 'REPURAMOUNTLIMIT',
        'doneAmount': 'REPURAMOUNT', 'endDate': 'REPURENDDATE'}),
    'institution-survey': ('RPT_ORG_SURVEYNEW', 'SECURITY_CODE', 'NOTICE_DATE', {
        'publishedDate': 'NOTICE_DATE', 'surveyDate': 'RECEIVE_START_DATE',
        'participants': 'RECEIVE_OBJECT', 'method': 'RECEIVE_WAY_EXPLAIN', 'place': 'RECEIVE_PLACE'}),
    'dragon-tiger': ('RPT_DAILYBILLBOARD_DETAILSNEW', 'SECURITY_CODE', 'TRADE_DATE', {
        'date': 'TRADE_DATE', 'reason': 'EXPLANATION', 'buyAmount': 'BILLBOARD_BUY_AMT',
        'sellAmount': 'BILLBOARD_SELL_AMT', 'netAmount': 'BILLBOARD_NET_AMT'}),
    'lpr-history': ('RPTA_WEB_RATE', None, 'TRADE_DATE', {'date': 'TRADE_DATE', 'oneYear': 'LPR1Y', 'fiveYear': 'LPR5Y'}),
    'macro-gdp': ('RPT_ECONOMY_GDP', None, 'REPORT_DATE', {
        'recordDate': 'REPORT_DATE', 'period': 'TIME', 'gdp': 'DOMESTICL_PRODUCT_BASE', 'changePct': 'SUM_SAME'}),
    'macro-pmi': ('RPT_ECONOMY_PMI', None, 'REPORT_DATE', {
        'recordDate': 'REPORT_DATE', 'period': 'TIME', 'manufacturing': 'MAKE_INDEX', 'nonManufacturing': 'NMAKE_INDEX'}),
}


def params_for(query):
    report, field, date_field, _ = PROFILES[query.dataset]
    filters = [f'({field}="{query.symbol[:6]}")'] if field else []
    if query.start:
        filters.append(f"({date_field}>='{query.start}')")
    if query.end:
        filters.append(f"({date_field}<='{query.end}')")
    if query.dataset == 'institution-survey':
        filters.extend(['(IS_SOURCE="1")', '(NUMBERNEW="1")'])
    # 只接入已核对为金额的收入／归母净利润，不能把 EPS／比率解释成人民币金额。
    if query.dataset == 'earnings-forecast':
        filters.append('(PREDICT_FINANCE_CODE in ("004","006"))')
    if query.dataset == 'lpr-history':
        filters.append('(LPR1Y>0)')
    return {'reportName': report, 'columns': 'ALL', 'sortColumns': date_field, 'sortTypes': '-1',
            'pageSize': str(query.pageSize), 'pageNumber': str(query.page), 'filter': ''.join(filters)}


def dc_rows(payload, query):
    require(isinstance(payload, dict), '来源封套无效')
    if payload.get('success') is False and payload.get('code') == 9201 and payload.get('result') is None:
        return [], 0, 0
    require(payload.get('success') is True and payload.get('code') == 0, '来源业务请求失败')
    data = payload.get('result')
    require(isinstance(data, dict) and isinstance(data.get('data'), list), '来源记录列表无效')
    count, pages = data.get('count'), data.get('pages')
    require(type(count) is int and type(pages) is int and count >= 0 and pages >= 0, '来源分页无效')
    rows = data['data']
    require(len(rows) <= query.pageSize and (rows or query.page > pages or count == 0), '来源当前页与分页不符')
    return rows, count, pages


def project_events(payload, query):
    from src.services.thesis_ledger_reference_catalog_v1 import CATALOG, DATE_FILTERS
    rows, count, pages = dc_rows(payload, query)
    _, identity_field, _, mapping = PROFILES[query.dataset]
    records = []
    for row in rows:
        require(isinstance(row, dict), '来源记录类型不符')
        if identity_field:
            identity(row, query, identity_field)
        if query.dataset == 'earnings-forecast':
            expected = {'004': '归属于上市公司股东的净利润', '006': '营业收入'}
            require(row.get('PREDICT_FINANCE_CODE') in expected
                    and row.get('PREDICT_FINANCE') == expected[row['PREDICT_FINANCE_CODE']], '预告金额指标不符')
        record = {}
        for col in CATALOG[query.dataset][2]:
            value = row.get(mapping[col.key])
            if col.format in {'decimal', 'integer'}:
                record[col.key] = number(value, col.format == 'integer')
            elif col.format == 'date':
                record[col.key] = day(value)
            else:
                record[col.key] = text(value)
        date_key = DATE_FILTERS.get(query.dataset, 'period')
        stamp = record.get(date_key)
        require(stamp is not None, '来源缺少记录日期')
        require((not query.start or stamp >= query.start) and (not query.end or stamp <= query.end), '来源记录越过请求日期')
        if query.dataset in {'macro-gdp', 'macro-pmi'}:
            require(record['period'] is not None, '来源统计期缺失')
        records.append(record)
    notes = ['记录日期和披露日期按来源字段展示；日期筛选不构成历史可见性保证。']
    if query.dataset == 'holder-count':
        notes.append('仅为来源最新股东户数快照，不代表完整历史。')
    if query.dataset == 'earnings-forecast':
        notes.append('本页仅接入营业收入与归母净利润预告，不包含每股收益等其他指标。')
    if query.dataset.startswith('macro-') or query.dataset == 'lpr-history':
        notes.append('东方财富转引记录；来源记录日不等于经核验的官方公布时间。')
    url = ENDPOINT + '?' + urlencode(params_for(query))
    return result(query, 'akshare', 'eastmoney', 'eastmoney-reference-events-v1', url,
                  records, total=count, has_more=query.page < pages, notes=notes)


def read_events(query, context):
    payload = http_json(ENDPOINT, params_for(query), {'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://data.eastmoney.com/'}, context.timeout_seconds)
    return project_events(payload, query)
