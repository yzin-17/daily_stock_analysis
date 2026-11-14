"""核对参考仓库的公开读取路径，不安装 Skill 或修改产品配置。"""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from public_reference_source_links import FOLLOW_PROFILES, follow_request
from derivative_reference_source_cases import PROFILES as DERIVATIVE_PROFILES, FOLLOW_PROFILES as DERIVATIVE_FOLLOW_PROFILES, request_spec as derivative_request, follow_request as derivative_follow
from backup_reference_source_cases import PROFILES as BACKUP_PROFILES, request_spec as backup_request
from backup_reference_source_links import FOLLOW_PROFILES as BACKUP_FOLLOW_PROFILES, follow_request as backup_follow

PROFILES = (
    *BACKUP_PROFILES,
    *DERIVATIVE_PROFILES,
    'fr', 'fdr', 'sse-etf', 'szse-etf', 'csi-members', 'csi-weights',
    'cni-index', 'sw-history', 'cctv', 'sse-qna', 'szse-org',
    'ths-consensus', 'ths-theme', 'tdx-package', 'pboc-index',
    'nbs-index', 'chinabond',
    'sse-etf-filter', 'szse-etf-filter',
    'chinabond-all', 'tdx-etf-quote',
)
MAX_BODY_BYTES = 24 * 1024 * 1024
UA = 'Mozilla/5.0'


def request_spec(profile, day, code):
    if profile in BACKUP_PROFILES:
        return backup_request(profile, day, code)
    if profile in DERIVATIVE_PROFILES:
        return derivative_request(profile, day, code)
    if profile not in PROFILES or not re.fullmatch(r'\d{6}', code):
        raise ValueError('采证类型或代码无效')
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('日期必须为规范 YYYY-MM-DD')
    headers = {'User-Agent': UA, 'Accept': '*/*'}
    params, data, method = {}, None, 'GET'
    if profile in ('fr', 'fdr'):
        name = 'frr' if profile == 'fr' else 'fdr'
        url = f'https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/{name}-chrt.csv'
        headers['Referer'] = 'https://www.chinamoney.com.cn/chinese/bkfrr/'
    elif profile in ('sse-etf', 'sse-etf-filter'):
        url = 'https://query.sse.com.cn/commonQuery.do'
        headers['Referer'] = 'https://www.sse.com.cn/'
        params = {'sqlId': 'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L', 'STAT_DATE': day,
                  'isPagination': 'true', 'pageHelp.pageSize': '10000', 'pageHelp.pageNo': '1',
                  'pageHelp.beginPage': '1', 'pageHelp.cacheSize': '1', 'pageHelp.endPage': '1'}
        if profile == 'sse-etf-filter':
            params['SEC_CODE'] = code
    elif profile in ('szse-etf', 'szse-etf-filter'):
        url = 'https://fund.szse.cn/api/report/ShowReport/data'
        headers['Referer'] = 'https://fund.szse.cn/'
        params = {'SHOWTYPE': 'JSON', 'CATALOGID': '1000_lf', 'TABKEY': 'tab1',
                  'selectJjlb': 'ETF', 'PAGENO': '1'}
        if profile == 'szse-etf-filter':
            params['txtkey1'] = code
    elif profile in ('csi-members', 'csi-weights'):
        kind = 'cons' if profile == 'csi-members' else 'closeweight'
        url = f'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/{kind}/000300{kind}.xls'
    elif profile == 'cni-index':
        url = 'https://www.cnindex.com.cn/sample-detail/download-history'
        params = {'indexcode': code if code.startswith('399') else '399001'}
    elif profile == 'sw-history':
        url = 'https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls'
    elif profile == 'cctv':
        url = f"https://tv.cctv.com/lm/xwlb/day/{day.replace('-', '')}.shtml"
    elif profile == 'sse-qna':
        url = 'https://sns.sseinfo.com/ajax/feeds.do'
        headers['Referer'] = 'https://sns.sseinfo.com/'
        params = {'type': '11', 'pageSize': '5', 'lastid': '-1', 'show': '1', 'page': '1'}
    elif profile == 'szse-org':
        url = 'https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo'
        method, data = 'POST', {'keyWord': code}
    elif profile == 'ths-consensus':
        url = f'https://basic.10jqka.com.cn/new/{code}/worth.html'
        headers['Referer'] = 'https://basic.10jqka.com.cn/'
    elif profile == 'ths-theme':
        url = f'https://zx.10jqka.com.cn/event/api/getharden/date/{day}/orderby/date/orderway/desc/charset/GBK/'
    elif profile == 'tdx-package':
        url = f"https://www.tdx.com.cn/products/data/data/g4day/{day.replace('-', '')}.zip"
    elif profile == 'pboc-index':
        url = 'https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html'
    elif profile == 'nbs-index':
        url = 'https://www.stats.gov.cn/sj/zxfb/'
    elif profile == 'tdx-etf-quote':
        if not re.fullmatch(r'(?:5[168]\d{4}|15\d{4})', code):
            raise ValueError('ETF 量纲核对仅接受规范沪深 ETF 代码')
        prefix = 'sh' if code.startswith('5') else 'sz'
        url = f'https://qt.gtimg.cn/q={prefix}{code}'
    else:
        url = 'https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery'
        params = {'startDate': day, 'endDate': day, 'gjqx': '0',
                  'qxId': 'ycqx' if profile == 'chinabond-all' else 'hzsylqx', 'locale': 'cn_ZH'}
    return {'method': method, 'url': url, 'params': params, 'data': data, 'headers': headers}


def response_outcome(status, content_type, body):
    if status in (401, 403):
        return 'permission-denied'
    if status == 429:
        return 'rate-limited'
    if 300 <= status < 400:
        return 'redirect-needs-review'
    if status != 200:
        return 'http-error'
    if not body:
        return 'empty-body'
    if 'html' in content_type and body[:400].lower().find(b'<html') >= 0:
        return 'html-needs-parsing'
    return 'response-received'


def execute(profile, day, code, output, attempt=1, parent=None):
    import requests

    spec = resolve_request(profile, parent, day, code)
    root = Path(output).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem = root / f'{profile}-{attempt}'
    permit = {'profile': profile, 'date': day, 'code': code, 'request': spec,
              'at': datetime.now(timezone.utc).isoformat(), 'providerRequests': 1,
              'configurationWrites': 0, 'modelCalls': 0, 'newCostUSD': 0}
    if parent:
        permit['parentEvidence'] = str(Path(parent).resolve())
    for suffix in ('.permit', '.json', '.body'):
        if Path(str(stem) + suffix).exists():
            raise FileExistsError('证据或许可已存在，不能覆盖历史或隐式重试')
    with Path(str(stem) + '.permit').open('x') as handle:
        json.dump(permit, handle, ensure_ascii=False, indent=2)
    receipt = {**permit, 'attempt': attempt, 'response': None}
    started = time.monotonic()
    try:
        with requests.Session() as session:
            # 显式空认证器阻止 requests 从本机 netrc 自动附加凭据；环境代理保持。
            session.auth = lambda request: request
            timeout = (10, 90) if profile == 'tdx-package' else (10, 30)
            with session.request(**spec, allow_redirects=False, stream=True, timeout=timeout) as response:
                body = bytearray()
                truncated = False
                for chunk in response.iter_content(65536):
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        truncated = True
                        break
                    body.extend(chunk)
                content = bytes(body)
                with Path(str(stem) + '.body').open('xb') as handle:
                    handle.write(content)
                content_type = response.headers.get('Content-Type', '')
                receipt['outcome'] = 'resource-boundary' if truncated else response_outcome(response.status_code, content_type, content)
                receipt['response'] = {'httpStatus': response.status_code, 'contentType': content_type,
                                       'bytes': len(content), 'sha256': sha256(content).hexdigest(),
                                       'location': response.headers.get('Location'), 'truncated': truncated,
                                       'url': response.url}
    except requests.RequestException as error:
        receipt['outcome'] = 'transport-failure'
        receipt['errorType'] = type(error).__name__
        receipt['errorDetail'] = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1<redacted>@', str(error))
    receipt['elapsedSeconds'] = round(time.monotonic() - started, 3)
    with Path(str(stem) + '.json').open('x') as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return {key: receipt.get(key) for key in ('profile', 'attempt', 'outcome', 'response', 'errorType', 'elapsedSeconds')}


def resolve_request(profile, parent, day, code):
    if profile in BACKUP_FOLLOW_PROFILES:
        return backup_follow(profile, parent, day, code)
    if profile in DERIVATIVE_FOLLOW_PROFILES:
        return derivative_follow(profile, parent, day, code)
    if profile in FOLLOW_PROFILES:
        return follow_request(profile, parent, day, code)
    return request_spec(profile, day, code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=(*PROFILES, *FOLLOW_PROFILES, *DERIVATIVE_FOLLOW_PROFILES, *BACKUP_FOLLOW_PROFILES))
    parser.add_argument('--date', required=True)
    parser.add_argument('--code', default='600519')
    parser.add_argument('--output', required=True)
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--parent')
    args = parser.parse_args()
    if not Path(args.output).is_absolute() or args.attempt < 1:
        parser.error('需要绝对证据目录和正整数尝试序号')
    if args.case in (*FOLLOW_PROFILES, *DERIVATIVE_FOLLOW_PROFILES, *BACKUP_FOLLOW_PROFILES) and (not args.parent or not Path(args.parent).is_absolute()):
        parser.error('后续固定页面需要绝对父回执路径')
    spec = resolve_request(args.case, args.parent, args.date, args.code)
    if args.execute:
        result = execute(args.case, args.date, args.code, args.output, args.attempt, args.parent)
    else:
        result = {'request': spec, 'providerRequests': 1, 'configurationWrites': 0,
                  'modelCalls': 0, 'newCostUSD': 0, 'executed': False}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
