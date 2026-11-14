"""既有免费来源的单页采证；不写入来源配置或资格。"""

from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from uuid import uuid4

PROFILES = {
    'report-eps': ('akshare', 'eastmoney'),
    'sina-fzb': ('akshare', 'sina'), 'sina-lrb': ('akshare', 'sina'), 'sina-llb': ('akshare', 'sina'),
    'valuation': ('baostock', 'baostock'), 'lpr': ('akshare', 'eastmoney'),
    'global-news': ('akshare', 'eastmoney'), 'fund-flow': ('akshare', 'eastmoney'),
    'tencent-ticks': ('tencent', 'tencent'),
}
DATA_CENTER = {
    'margin': ('RPTA_WEB_RZRQ_GGMX', 'SCODE', 'DATE'),
    'holder-count': ('RPT_HOLDERNUMLATEST', 'SECURITY_CODE', 'END_DATE'),
    'block-trade': ('RPT_DATA_BLOCKTRADE', 'SECURITY_CODE', 'TRADE_DATE'),
    'earnings-forecast': ('RPT_PUBLIC_OP_NEWPREDICT', 'SECURITY_CODE', 'NOTICE_DATE'),
    'earnings-express': ('RPT_FCI_PERFORMANCEE', 'SECURITY_CODE', 'NOTICE_DATE'),
    'institution-survey': ('RPT_ORG_SURVEYNEW', 'SECURITY_CODE', 'NOTICE_DATE'),
    'holder-trades': ('RPT_SHARE_HOLDER_INCREASE', 'SECURITY_CODE', 'NOTICE_DATE'),
    'buyback': ('RPTA_WEB_GETHGLIST_NEW', 'DIM_SCODE', 'UPD'),
    'dragon-tiger': ('RPT_DAILYBILLBOARD_DETAILSNEW', 'SECURITY_CODE', 'TRADE_DATE'),
    'consensus-eps': ('RPT_WEB_RESPREDICT', 'SECURITY_CODE', 'RATING_ORG_NUM'),
    'macro-gdp': ('RPT_ECONOMY_GDP', None, 'REPORT_DATE'),
    'macro-pmi': ('RPT_ECONOMY_PMI', None, 'REPORT_DATE'),
}
PROFILES.update({name: ('akshare', 'eastmoney') for name in [*DATA_CENTER, 'concepts', 'industry-ranking', 'news-original']})
PROFILES.update({'index-members': ('akshare', 'sina'), 'tencent-date': ('tencent', 'tencent')})
POOLS = {'limit-up': ('getTopicZTPool', 'fbt:asc'), 'limit-break': ('getTopicZBPool', 'fbt:asc'),
         'limit-down': ('getTopicDTPool', 'fund:asc'), 'yesterday-limit': ('getYesterdayZTPool', 'zs:desc')}
PROFILES.update({name: ('akshare', 'eastmoney') for name in [*POOLS, 'stock-monitor']})
PROFILES['macro-calendar'] = ('wallstreetcn', 'wallstreetcn-a-stock')
PROFILES['bond-yields'] = ('akshare', 'eastmoney')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def validate_input(value):
    if not isinstance(value, dict) or set(value) != {'profile', 'symbol', 'start', 'end'}:
        raise ValueError('采证输入字段不符')
    if value['profile'] not in PROFILES or not re.fullmatch(r'\d{6}\.(SH|SZ)', value['symbol']):
        raise ValueError('来源或证券身份无效')
    for key in ('start', 'end'):
        datetime.strptime(value[key], '%Y-%m-%d')
    if value['start'] > value['end']:
        raise ValueError('日期窗口倒序')
    return value


def request_spec(value):
    profile, symbol = value['profile'], value['symbol']
    code, exchange = symbol.split('.')
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json,text/plain,*/*'}
    if profile == 'bond-yields':
        return ('https://datacenter.eastmoney.com/api/data/get', {
            'type': 'RPTA_WEB_TREASURYYIELD', 'sty': 'ALL', 'st': 'SOLAR_DATE', 'sr': '-1',
            'token': '894050c76af8597a853f5b408b759f5d', 'p': '1', 'ps': '5', 'pageNo': '1', 'pageNum': '1',
            'filter': f"(SOLAR_DATE>='{value['start']}')(SOLAR_DATE<='{value['end']}')"}, headers)
    if profile == 'macro-calendar':
        zone = timezone(timedelta(hours=8))
        begin = datetime.fromisoformat(value['start']).replace(tzinfo=zone)
        finish = datetime.fromisoformat(value['end']).replace(hour=23, minute=59, second=59, tzinfo=zone)
        if (finish - begin).days > 6:
            raise ValueError('宏观日历采证窗口最多七天')
        return ('https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas',
                {'start': int(begin.timestamp()), 'end': int(finish.timestamp())}, headers)
    if profile in POOLS:
        endpoint, sort = POOLS[profile]
        return ('https://push2ex.eastmoney.com/' + endpoint,
                {'ut': '7eea3edcaed734bea9cbfc24409ed989', 'dpt': 'wz.ztzt', 'Pageindex': '0',
                 'pagesize': '5', 'sort': sort, 'date': value['end'].replace('-', '')}, headers)
    if profile == 'stock-monitor':
        return ('https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json', {}, headers)
    if profile in DATA_CENTER:
        report, identity, date = DATA_CENTER[profile]
        params = {'reportName': report, 'columns': 'ALL', 'sortColumns': date, 'sortTypes': '-1',
                  'pageSize': '5', 'pageNumber': '1'}
        if identity is not None:
            params['filter'] = f'({identity}="{code}")'
        if profile == 'consensus-eps':
            params['columns'] = 'WEB_RESPREDICT'
        return ('https://datacenter-web.eastmoney.com/api/data/v1/get',
                params,
                headers | {'Referer': 'https://data.eastmoney.com/'})
    if profile == 'index-members':
        return ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData',
                {'node': 'hs300' if code == '000300' else 'zhishu_' + code,
                 'page': '1', 'num': '5', 'sort': 'symbol', 'asc': '1', '_s_r_a': 'init'}, headers)
    if profile == 'tencent-date':
        return ('https://qt.gtimg.cn/q=' + exchange.lower() + code, {}, headers)
    if profile == 'report-eps':
        return ('https://reportapi.eastmoney.com/report/list',
                {'code': code, 'qType': '0', 'pageNo': '1', 'pageSize': '2', 'industryCode': '*',
                 'industry': '*', 'rating': '*', 'ratingChange': '*', 'beginTime': '', 'endTime': ''},
                headers | {'Referer': 'https://data.eastmoney.com/'})
    if profile.startswith('sina-'):
        return ('https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022',
                {'paperCode': exchange.lower() + code, 'source': profile.split('-')[1],
                 'type': '0', 'page': '1', 'num': '2'}, headers)
    if profile == 'lpr':
        return ('https://datacenter-web.eastmoney.com/api/data/v1/get',
                {'reportName': 'RPTA_WEB_RATE', 'columns': 'TRADE_DATE,LPR1Y,LPR5Y',
                 'sortColumns': 'TRADE_DATE', 'sortTypes': '-1', 'pageSize': '5', 'pageNumber': '1'},
                headers | {'Referer': 'https://data.eastmoney.com/'})
    if profile in {'global-news', 'news-original'}:
        return ('https://np-weblist.eastmoney.com/comm/web/getFastNewsList',
                {'client': 'web', 'biz': 'web_724', 'fastColumn': '102', 'sortEnd': '', 'pageSize': '5',
                 'req_trace': str(uuid4())},
                headers | {'Referer': 'https://kuaixun.eastmoney.com/'})
    if profile == 'fund-flow':
        secid = ('1' if exchange == 'SH' else '0') + '.' + code
        return ('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get',
                {'secid': secid, 'fields1': 'f1,f2,f3,f7',
                 'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65', 'lmt': '5',
                 'klt': '101', 'ut': 'b2884a393a59ad64002292a3e90d46a5'},
                headers | {'Referer': 'https://quote.eastmoney.com/'})
    if profile == 'concepts':
        return ('https://push2.eastmoney.com/api/qt/slist/get',
                {'fltt': '2', 'invt': '2', 'secid': ('1' if exchange == 'SH' else '0') + '.' + code,
                 'spt': '3', 'pi': '0', 'pz': '5', 'po': '1', 'fields': 'f12,f14,f3,f128'},
                headers | {'Referer': 'https://quote.eastmoney.com/'})
    if profile == 'industry-ranking':
        return ('https://17.push2.eastmoney.com/api/qt/clist/get',
                {'fltt': '2', 'invt': '2', 'fid': 'f3', 'fs': 'm:90+t:2', 'pn': '1', 'pz': '5',
                 'po': '1', 'fields': 'f12,f14,f3,f62'},
                headers | {'Referer': 'https://quote.eastmoney.com/'})
    if profile == 'tencent-ticks':
        return ('https://stock.gtimg.cn/data/index.php',
                {'appn': 'detail', 'action': 'data', 'c': exchange.lower() + code, 'p': '0'},
                headers | {'Referer': 'https://gu.qq.com/'})
    raise ValueError('SDK 来源没有 HTTP 地址')


def readonly_store():
    from src.services.thesis_ledger_control import ThesisLedgerControlStore, _database_path

    class ReadonlyStore(ThesisLedgerControlStore):
        def __init__(self):
            self.database_path = str(Path(_database_path()).resolve())

        def _connect(self):
            connection = sqlite3.connect(Path(self.database_path).as_uri() + '?mode=ro', uri=True)
            connection.row_factory = sqlite3.Row
            return connection

    return ReadonlyStore()


def control_snapshot(store):
    with store._connect() as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                 if row[0].startswith('thesis_ledger_') and any(part in row[0] for part in ('provider_config', 'policy', 'admission'))]
        values = {}
        for name in names:
            if not re.fullmatch(r'[A-Za-z0-9_]+', name):
                raise ValueError('表名无效')
            rows = [dict(row) for row in connection.execute('SELECT * FROM "' + name + '"')]
            values[name] = sorted(sha256(canonical(row)).hexdigest() for row in rows)
    return sha256(canonical(values)).hexdigest()


def assert_source(store, value):
    provider_id, source_id = PROFILES[value['profile']]
    row = next(item for item in store.provider_registry() if item['providerId'] == provider_id)
    if row.get('enabled') is not True or row.get('configured') is not True:
        raise ValueError('来源未配置启用，本入口不变更状态')
    if source_id not in {item['sourceId'] for item in row['upstreamSources']}:
        raise ValueError('实际上游不在既有来源内')
    with store._connect() as connection:
        config = connection.execute('SELECT settings_json FROM thesis_ledger_provider_config WHERE provider_id=?',
                                    (provider_id,)).fetchone()
    settings = json.loads(config[0]) if config else {}
    if any(key.lower() in {'headers', 'baseurl', 'base_url', 'proxy', 'timeout', 'timeoutms'} for key in settings):
        raise ValueError('存在自定义传输配置，必须先按其契约执行，不能忽略')
    return {'providerId': provider_id, 'upstreamSource': source_id, 'configVersion': row['configVersion'],
            'settingsKeys': sorted(settings)}


def sdk_valuation(value):
    code = value['symbol'].lower().split('.')
    payload = {'code': code[1] + '.' + code[0], 'start': value['start'], 'end': value['end']}
    worker = '''import json,sys
from data_provider.baostock_fetcher import BaostockFetcher
p=json.load(sys.stdin)
with BaostockFetcher()._baostock_session() as sdk:
 r=sdk.query_history_k_data_plus(p['code'],'date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM,turn,tradestatus,isST',start_date=p['start'],end_date=p['end'],frequency='d',adjustflag='3')
 if r.error_code!='0':raise RuntimeError(r.error_code+':'+r.error_msg)
 fields=r.fields;rows=[]
 while r.next():rows.append(dict(zip(fields,r.get_row_data())))
 print('SDK_RESULT '+json.dumps({'fields':fields,'rows':rows}))
'''
    result = subprocess.run([sys.executable, '-B', '-c', worker], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=45)
    line = next((line for line in result.stdout.splitlines() if line.startswith('SDK_RESULT ')), None)
    if result.returncode or not line:
        raise RuntimeError('BaoStock 未取得明确查询回执：' + result.stderr[-600:])
    return json.loads(line[len('SDK_RESULT '):])


def main(value):
    value = validate_input(value)
    store = readonly_store()
    before = control_snapshot(store)
    source = assert_source(store, value)
    result = {'request': value, 'source': source, 'requests': [], 'configurationWritten': False}
    try:
        if value['profile'] == 'valuation':
            result['requests'].append({'kind': 'sdk-session-query', 'startedAt': datetime.now(timezone.utc).isoformat()})
            result['response'] = sdk_valuation(value)
        else:
            import requests
            url, params, headers = request_spec(value)
            event = {'url': url, 'params': params, 'startedAt': datetime.now(timezone.utc).isoformat()}
            result['requests'].append(event)
            response = requests.get(url, params=params, headers=headers, timeout=30, allow_redirects=False)
            raw = response.content
            result['response'] = {'httpStatus': response.status_code, 'bytes': len(raw),
                                  'sha256': sha256(raw).hexdigest(), 'contentType': response.headers.get('Content-Type')}
            try:
                result['response']['payload'] = response.json()
            except ValueError:
                result['response']['text'] = response.text
            if value['profile'] == 'news-original':
                rows = result['response'].get('payload', {}).get('data', {}).get('fastNewsList', [])
                code = rows[0]['code'] if rows else ''
                if not re.fullmatch(r'\d{16,24}', code):
                    raise ValueError('没有可核对的新闻来源标识')
                article_url = f'https://finance.eastmoney.com/a/{code}.html'
                result['requests'].append({'url': article_url, 'startedAt': datetime.now(timezone.utc).isoformat()})
                article = requests.get(article_url, headers=headers, timeout=30, allow_redirects=False)
                raw_article = article.content
                title = re.search(r'<title[^>]*>(.*?)</title>', article.text, re.I | re.S)
                result['article'] = {'url': article_url, 'httpStatus': article.status_code,
                                     'sha256': sha256(raw_article).hexdigest(), 'title': title.group(1).strip() if title else None,
                                     'expectedTitle': rows[0].get('title')}
        result['outcome'] = 'response-collected'
    except Exception as error:
        result['outcome'] = 'source-error'
        result['error'] = {'type': type(error).__name__, 'message': str(error)[-1200:]}
    result['protectionPassed'] = control_snapshot(store) == before
    result['controlSha256'] = before
    return result


if __name__ == '__main__':
    print('ASTOCK_SOURCE_RESULT ' + json.dumps(main(json.load(sys.stdin)), ensure_ascii=False))
