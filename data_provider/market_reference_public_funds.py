"""沪深 ETF 份额；参考 a-stock-data §4.8，保留来源日期及万份单位。"""

import re

from bs4 import BeautifulSoup
from .market_reference_projection import day, number, require
from .market_reference_public_common import PublicRead, paged, unique

SSE = 'https://query.sse.com.cn/commonQuery.do'
SZSE = 'https://fund.szse.cn/api/report/ShowReport/data'


def sse_params(stamp):
    return {'sqlId': 'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L', 'STAT_DATE': stamp,
            'isPagination': 'true', 'pageHelp.pageSize': '10000', 'pageHelp.pageNo': '1',
            'pageHelp.beginPage': '1', 'pageHelp.cacheSize': '1', 'pageHelp.endPage': '1'}


def parse_sse(payload, query):
    require(isinstance(payload, dict) and isinstance(payload.get('result'), list), '上交所份额封套改变')
    rows, metadata = payload['result'], payload.get('pageHelp')
    require(all(isinstance(row, dict) for row in rows), '上交所份额行结构改变')
    require(isinstance(metadata, dict) and type(metadata.get('total')) is int, '上交所份额缺少总数')
    require(len(rows) == metadata['total'] and bool(rows), '上交所份额快照不完整或尚未发布')
    records = []
    for row in rows:
        require(row.get('STAT_DATE') == query.start and bool(re.fullmatch(r'5\d{5}', row.get('SEC_CODE', ''))), '上交所份额日期或代码改变')
        shares = number(row.get('TOT_VOL'))
        require(shares is not None and row.get('SEC_NAME'), '上交所份额或名称缺失')
        records.append({'date': day(row['STAT_DATE']), 'symbol': row['SEC_CODE'] + '.SH',
                        'name': row['SEC_NAME'], 'shares10k': shares, 'category': row.get('ETF_TYPE')})
    unique(records, ('symbol',))
    return [row for row in records if row['symbol'] == query.symbol]


def parse_szse(payload, query):
    require(isinstance(payload, list), '深交所份额封套改变')
    tables = [table for table in payload if isinstance(table, dict)
              and isinstance(table.get('metadata'), dict)
              and table.get('metadata', {}).get('catalogid') == '1000_lf'
              and table.get('metadata', {}).get('tabkey') == 'tab1']
    require(len(tables) == 1, '深交所 ETF 表身份不唯一')
    table = tables[0]
    metadata, rows = table.get('metadata'), table.get('data')
    require(isinstance(metadata, dict) and isinstance(rows, list), '深交所份额结构改变')
    require(all(isinstance(row, dict) for row in rows), '深交所份额行结构改变')
    require(metadata.get('pagecount') == 1 and metadata.get('recordcount') == len(rows), '深交所单标过滤没有返回完整页')
    stamp = day(metadata.get('subname'))
    require(stamp and '万份' in metadata.get('cols', {}).get('dqgm', ''), '深交所份额日期或单位改变')
    records = []
    for row in rows:
        code = BeautifulSoup(row.get('sys_key', ''), 'html.parser').get_text(strip=True)
        require(code == query.symbol[:6] and row.get('jjlb') == 'ETF', '深交所份额返回其他证券或基金类别')
        shares = number(BeautifulSoup(row.get('dqgm', ''), 'html.parser').get_text(strip=True).replace(',', ''))
        require(shares is not None, '深交所份额缺失')
        records.append({'date': stamp, 'symbol': query.symbol,
                        'name': BeautifulSoup(row.get('jjjcurl', ''), 'html.parser').get_text(strip=True),
                        'shares10k': shares, 'category': row.get('tzlb')})
    unique(records, ('symbol',))
    return records


def read_etf_shares(query, context):
    read = PublicRead(context.timeout_seconds)
    if query.dataset == 'etf-shares-sh':
        payload = read.json(SSE, sse_params(query.start), {'Referer': 'https://www.sse.com.cn/'})
        records, url = parse_sse(payload, query), SSE
        notes = ('上交所接口返回该日完整快照，一次读取后选取本证券；SEC_CODE 参数实测不能过滤，不进行逐股扫描。',
                 '来源单位为万份，不转换成净申赎金额；日期未发布不冒充真实无持仓。')
    else:
        params = {'SHOWTYPE': 'JSON', 'CATALOGID': '1000_lf', 'TABKEY': 'tab1',
                  'selectJjlb': 'ETF', 'PAGENO': '1', 'txtkey1': query.symbol[:6]}
        records, url = parse_szse(read.json(SZSE, params, {'Referer': 'https://fund.szse.cn/'}), query), SZSE
        notes = ('深交所仅提供当前快照，使用证券过滤，不扫描全部 39 页。T 日晚间值可能为预估，以 T+1 早间确认值为准；接口未逐条标记确认状态。',
                 '来源单位为万份；深市投资类别与沪市 ETF 类型含义不同。')
    return paged(query, records, url, notes=notes)
