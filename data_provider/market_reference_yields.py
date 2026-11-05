"""东财转引的中美国债收益率；缺期限不补齐，不声明完整曲线。"""

from .market_reference_projection import day, http_json, number, require, result
from .market_reference_events import dc_rows

ENDPOINT = 'https://datacenter.eastmoney.com/api/data/get'
FIELDS = {'china2y': 'EMM00588704', 'china5y': 'EMM00166462', 'china10y': 'EMM00166466',
          'china30y': 'EMM00166469', 'us2y': 'EMG00001306', 'us5y': 'EMG00001308',
          'us10y': 'EMG00001310', 'us30y': 'EMG00001312'}


def params_for(query):
    filters = []
    if query.start:
        filters.append(f"(SOLAR_DATE>='{query.start}')")
    if query.end:
        filters.append(f"(SOLAR_DATE<='{query.end}')")
    return {'type': 'RPTA_WEB_TREASURYYIELD', 'sty': 'ALL', 'st': 'SOLAR_DATE', 'sr': '-1',
            'token': '894050c76af8597a853f5b408b759f5d', 'p': str(query.page), 'ps': str(query.pageSize),
            'pageNo': str(query.page), 'pageNum': str(query.page), 'filter': ''.join(filters)}


def project_yields(payload, query):
    rows, count, pages = dc_rows(payload, query)
    records = []
    for row in rows:
        stamp = day(row.get('SOLAR_DATE'))
        require(stamp and (not query.start or stamp >= query.start) and (not query.end or stamp <= query.end), '收益率来源日期与请求不符')
        values = {key: number(row.get(field)) for key, field in FIELDS.items()}
        require(any(value is not None for value in values.values()), '收益率来源行没有可用期限')
        records.append({'date': stamp, **values})
    return result(query, 'akshare', 'eastmoney', 'eastmoney-bond-yields-v1',
                  'https://data.eastmoney.com/cjsj/zmgzsyl.html', records, total=count, has_more=query.page < pages,
                  notes=('东方财富转引记录，各期限可能缺值；没有中国期限值时不视为已取得中国曲线。',
                         '来源记录日不代表经核验的官方公布时间，不声明完整曲线或严格历史可见性。'))


def read_yields(query, context):
    return project_yields(http_json(ENDPOINT, params_for(query), {'User-Agent': 'Mozilla/5.0'}, context.timeout_seconds), query)
