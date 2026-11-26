"""仅解析已归档的上交所基金停复牌原文；不发送网络请求。"""

from datetime import date
import json
from math import ceil
from urllib.parse import parse_qs, urlsplit

SQL_ID = 'SSE_PL_JYTS_TFPXX_JJ'
PARAMETERS = {'isPagination', 'sqlId', 'secCode', 'stopReason', 'order', 'startDate', 'endDate',
              'pageHelp.pageSize', 'pageHelp.pageNo', 'pageHelp.beginPage',
              'pageHelp.cacheSize', 'pageHelp.endPage'}


def query_parameters(reference, symbol, day):
    url = urlsplit(reference)
    if (url.scheme != 'https' or url.hostname != 'query.sse.com.cn' or url.port not in (None, 443)
            or url.username or url.password or url.fragment or url.path != '/sseQuery/commonSoaQuery.do'):
        raise ValueError('历史状态查询不是允许的官方端点')
    values = parse_qs(url.query, keep_blank_values=True)
    if set(values) != PARAMETERS or any(len(v) != 1 for v in values.values()):
        raise ValueError('历史查询参数缺失、重复或含额外筛选')
    p = {k: v[0] for k, v in values.items()}
    target = day.replace('-', '')
    if (p['sqlId'] != SQL_ID or p['secCode'] != symbol.split('.')[0]
            or p['stopReason'] or p['isPagination'] != 'true'
            or not p['startDate'].isdigit() or len(p['startDate']) != 8
            or not p['startDate'] <= target or p['endDate'] != target):
        raise ValueError('历史查询代码、日期或筛选范围不符')
    date.fromisoformat(p['startDate'])
    for key in ['pageHelp.pageNo', 'pageHelp.pageSize']:
        if not p[key].isdigit() or not 1 <= int(p[key]) <= 1000:
            raise ValueError('历史查询分页参数无效')
    if (p['order'] != 'startStopDate|desc,secCode|asc'
            or any(not p[k].isdigit() or not 1 <= int(p[k]) <= 1000 for k in
                   ['pageHelp.beginPage', 'pageHelp.cacheSize', 'pageHelp.endPage'])):
        raise ValueError('历史查询排序或缓存分页参数无效')
    return p


def read_halt_rows(pages, symbol, day, read_original):
    rows, total, signature, size = [], None, None, None
    for number, evidence in enumerate(pages, 1):
        p = query_parameters(evidence.reference, symbol, day)
        current = {k: v for k, v in p.items() if k not in {
            'pageHelp.pageNo', 'pageHelp.beginPage', 'pageHelp.endPage', 'pageHelp.cacheSize'}}
        if int(p['pageHelp.pageNo']) != number or signature is not None and current != signature:
            raise ValueError('历史查询缺页、顺序或范围变化')
        signature, size = current, int(p['pageHelp.pageSize'])
        result = json.loads(read_original(evidence.sha256))
        if (not isinstance(result, dict) or result.get('sqlId') != SQL_ID
                or result.get('isPagination') != 'true' or result.get('actionErrors') != []
                or result.get('fieldErrors') != {} or not isinstance(result.get('result'), list)):
            raise ValueError('官方历史查询失败或响应结构无效')
        page = result['pageHelp']
        if (not isinstance(page, dict) or type(page.get('pageNo')) is not int or page['pageNo'] != number
                or type(page.get('pageSize')) is not int or page['pageSize'] != size):
            raise ValueError('官方响应分页元数据与请求不符')
        count = page['total']
        if type(count) is not int or not 0 <= count <= 100000 or total is not None and count != total:
            raise ValueError('历史查询总数无效或分页间变化')
        total = count
        expected = max(1, ceil(total / size))
        if (type(page['pageCount']) is not int or page['pageCount'] != ceil(total / size)
                or len(pages) != expected or result['result'] != page['data']
                or len(result['result']) != min(size, max(0, total - (number - 1) * size))):
            raise ValueError('历史查询分页未完成或内容截断')
        rows.extend(result['result'])
    if len({json.dumps(r, sort_keys=True) for r in rows}) != len(rows):
        raise ValueError('历史查询含重复记录，不能认证分页完整')
    if any(not isinstance(r, dict) or r.get('secCode') != symbol.split('.')[0] for r in rows):
        raise ValueError('官方记录证券代码不符')
    return rows, total


def halt_interval(row, day):
    """只转换明确交易停牌及固定时间码，粗时段保留不可用。"""
    source = row.get('dateSource')
    if source in ('1', '3'):  # 官方脚本区分的申赎暂停，不是二级交易停牌。
        return None
    start, end = row.get('startStopDate'), row.get('endStopDate')
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError('历史停牌日期缺失')
    start_day = date.fromisoformat(start).isoformat()
    end_day = date.fromisoformat(end).isoformat() if end else None
    if end_day and end_day < start_day:
        raise ValueError('历史停牌日期倒置')
    if start_day > day or end_day is not None and end_day < day:
        return None
    if source not in ('2', '5') or row.get('startStopType') != 'LSTP':
        raise ValueError('相关停牌事件类型或时间不明确')
    times = {'915': ('09:15:00', '10:30:00'), '9h': ('09:30:00', '10:30:00'),
             'WH': ('00:00:00', '23:59:59')}
    if row.get('stopTime') not in times or end_day != start_day:
        raise ValueError('相关停牌事件无法定位精确区间')
    begin, finish = times[row['stopTime']]
    return {'startedAt': f'{day}T{begin}+08:00', 'endedAt': f'{day}T{finish}+08:00'}
