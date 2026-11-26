"""采集已选两证券的区间官方原文；有界、无重试，不访问模型或业务服务。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from scripts.arena_execution_sources import HOSTS


def validate_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password
            or p.port not in ((None, 443, 32042) if p.hostname == 'yunhq.sse.com.cn' else (None, 443))):
        raise ValueError('只允许已审阅官方 HTTPS 来源')


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, url):
        validate_url(url)
        return super().redirect_request(req, fp, code, msg, headers, url)


def collect(item, output):
    result = {**item, 'observedAt': datetime.now(timezone.utc).isoformat()}
    try:
        validate_url(item['url'])
        request = Request(item['url'], headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.sse.com.cn/'})
        with build_opener(Redirect()).open(request, timeout=20) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('原文超过预算')
            name = item['id'] + '.raw'
            with (output / name).open('xb') as stream:
                stream.write(raw)
            (output / name).chmod(0o600)
            result.update(status=response.status, finalUrl=response.url, bytes=len(raw),
                sha256=sha256(raw).hexdigest(), file=name, lastModified=response.headers.get('Last-Modified'),
                contentType=response.headers.get('Content-Type'), serverDate=response.headers.get('Date'))
    except Exception as exc:
        result.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
    return result


def requests(start, end):
    import exchange_calendars as xcals
    calendar = xcals.get_calendar('XSHG')
    days = [x.date().isoformat() for x in calendar.sessions_in_range(start, end)]
    previous = calendar.previous_session(days[0]).date().isoformat()
    items = []
    for day in [previous, *days]:
        for code in ['588000', '510300']:
            params = {'select': 'date,open,high,low,close,volume,amount,prevClose', 'begin': 0,
                      'end': -1, 'period': day.replace('-', '')}
            items.append({'id': f'minutes-{code}-{day}', 'kind': 'minutes', 'symbol': code + '.SH', 'day': day,
                'url': f'https://yunhq.sse.com.cn:32042/v1/sh1/mink/{code}?' + urlencode(params)})
            if day == previous:
                continue
            params = {'isPagination': 'true', 'sqlId': 'SSE_PL_JYTS_TFPXX_JJ', 'secCode': code,
                'stopReason': '', 'order': 'startStopDate|desc,secCode|asc', 'startDate': '19901219',
                'endDate': day.replace('-', ''), 'pageHelp.pageSize': 25, 'pageHelp.pageNo': 1,
                'pageHelp.beginPage': 1, 'pageHelp.cacheSize': 1, 'pageHelp.endPage': 1}
            items.append({'id': f'halts-{code}-{day}', 'kind': 'halts', 'symbol': code + '.SH', 'day': day,
                'url': 'https://query.sse.com.cn/sseQuery/commonSoaQuery.do?' + urlencode(params)})
    for ident, path in [
        ('rules-stock', 'stocks/exchange/c/c_20260424_10816482.shtml'),
        ('rules-universal', 'trade/universal/c/c_20260424_10816492.shtml'),
        ('rules-stock-deferred', 'stocks/exchange/c/10816482/files/c4510656662e48dc9d8d0deb8cc864b3.docx'),
        ('rules-stock-text', 'stocks/exchange/c/10816482/files/e3a3b98e36894edca77841837936e006.docx'),
    ]:
        items.append({'id': ident, 'kind': 'rules', 'url': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/' + path})
    return days, previous, items


def event_notice_requests(end):
    """核查已选证券公告目录；完整性尚须审阅，不把查询成功当作事件覆盖。"""
    items = [{'id': 'official-fund-announcement-script', 'kind': 'interface',
        'url': 'https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_fundInformation_2021.js'}]
    for code, listed in [('588000', '2020-11-16'), ('510300', '2012-05-28')]:
        params = {'isPagination': 'true', 'pageHelp.pageSize': 1000, 'pageHelp.pageNo': 1,
            'pageHelp.beginPage': 1, 'pageHelp.cacheSize': 1, 'pageHelp.endPage': 1,
            'type': 'inParams', 'sqlId': 'COMMON_PL_JJXX_JJGG_NEW_L', 'TITLE': '',
            'SECURITY_CODE': code, 'BULLETIN_TYPE': '', 'OTHER_TYPE': '', 'START_DATE': listed,
            'END_DATE': end, 'DATE_DESC': 1, 'DATE_ASC': '', 'CODE_DESC': '', 'CODE_ASC': ''}
        items.append({'id': 'event-notices-' + code, 'kind': 'announcements', 'symbol': code + '.SH',
            'url': 'https://query.sse.com.cn/commonQuery.do?' + urlencode(params)})
    return items


def event_document_requests(directory):
    import re
    directory = Path(directory)
    receipt = json.loads((directory / 'receipt.json').read_bytes())
    items = []
    for capture in receipt['results']:
        if capture['kind'] != 'announcements':
            continue
        raw = (directory / capture['file']).read_bytes()
        if capture['status'] != 200 or sha256(raw).hexdigest() != capture['sha256']:
            raise ValueError('官方公告目录原文变化')
        text = raw.decode().strip()
        body = json.loads(text[1:-1] if text.startswith('(') and text.endswith(')') else text)
        rows, page = body['result'], body['pageHelp']
        if (body.get('actionErrors') or body.get('fieldErrors') or page['pageCount'] != 1
                or page['pageNo'] != 1 or page['total'] != len(rows) or page['data'] != rows):
            raise ValueError('官方公告目录并非完整单页')
        for row in rows:
            if row['SECURITY_CODE'] + '.SH' != capture['symbol']:
                raise ValueError('官方公告目录包含其他证券')
            if not re.search('分红|收益分配|折算|拆分|份额转换', row['TITLE']):
                continue
            if not re.fullmatch(r'/disclosure/fund/announcement/c/(?:new/)?\d{4}-\d{2}-\d{2}/' + row['SECURITY_CODE'] + r'_[^/]+\.pdf', row['URL']):
                raise ValueError('公告原文路径不属于目标证券官方 PDF')
            items.append({'id': 'event-document-' + row['SECURITY_CODE'] + '-' + row['SSEDATE'],
                'kind': 'event-document', 'symbol': capture['symbol'], 'publishedOn': row['SSEDATE'],
                'title': row['TITLE'], 'url': 'https://www.sse.com.cn' + row['URL']})
    if not 1 <= len(items) <= 24 or len({item['id'] for item in items}) != len(items):
        raise ValueError('事件公告数量或唯一性超出本轮审阅预算')
    return items


def main():
    parser = argparse.ArgumentParser(description='两证券区间官方原文采集')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--supplement', action='store_true', help='仅采集补充规则原文，不重复区间行情')
    parser.add_argument('--product-documents', action='store_true', help='仅采集上市公告和有发布日期的交易问答')
    parser.add_argument('--legacy-rules', action='store_true', help='核对同一官方附件在规则迁移前路径的可用性')
    parser.add_argument('--published-attachments', action='store_true', help='仅采集发布时链接的原始规则附件')
    parser.add_argument('--event-notices', action='store_true', help='只核查两证券上市以来公告目录及官方查询脚本，不签发事件覆盖')
    parser.add_argument('--event-documents', help='已有完整官方目录的采集目录；仅补采相关事件原文，不签发覆盖')
    args = parser.parse_args()
    if sum([args.supplement, args.product_documents, args.legacy_rules, args.published_attachments,
            args.event_notices, bool(args.event_documents)]) > 1:
        raise ValueError('补充采集模式不能重叠')
    if not '2026-11-02' <= args.start <= args.end <= datetime.now().date().isoformat():
        raise ValueError('仅允许已授权的历史区间')
    days, previous, items = requests(args.start, args.end)
    if args.event_notices:
        items = event_notice_requests(args.end)
    if args.event_documents:
        items = event_document_requests(args.event_documents)
    if args.supplement:
        items = [
            {'id': 'rules-universal-deferred', 'kind': 'rules', 'url': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/10816492/files/7476908baade49b8b14881439d2f89b6.docx'},
            {'id': 'rules-universal-text', 'kind': 'rules', 'url': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/10816492/files/58bd5ce4dfc94b57a5391f60eb2719ab.docx'},
            {'id': 'sse-star-funds', 'kind': 'rules', 'url': 'https://www.sse.com.cn/assortment/fund/star/'},
        ]
    if args.product_documents:
        if args.supplement:
            raise ValueError('补充采集模式不能重叠')
        items = [
            {'id': '588000-listing', 'kind': 'rules', 'url': 'https://www.sse.com.cn/disclosure/fund/announcement/c/2020-11-11/588000_20201111_2.pdf'},
            {'id': 'etf-lot-tick', 'kind': 'rules', 'url': 'https://www.sse.com.cn/assortment/fund/etf/question/c/c_20240118_5734754.shtml'},
        ]
    if args.legacy_rules:
        if args.supplement or args.product_documents:
            raise ValueError('补充采集模式不能重叠')
        items = [{'id': ident, 'kind': 'rules', 'url': 'https://www.sse.com.cn/lawandrules/sselawsrules/' + path}
                 for ident, path in [
            ('legacy-universal-text', 'trading/universal/c/10816492/files/58bd5ce4dfc94b57a5391f60eb2719ab.docx'),
            ('legacy-universal-deferred', 'trading/universal/c/10816492/files/7476908baade49b8b14881439d2f89b6.docx'),
            ('legacy-fund-text', 'fund/trading/c/10817739/files/7ffa42ba08bf4605a9e680bd38d452b4.docx'),
            ('legacy-fund-deferred', 'fund/trading/c/10817739/files/6b7d569ee87145298a6b135146bcf793.docx'),
        ]]
    if args.published_attachments:
        if args.supplement or args.product_documents or args.legacy_rules:
            raise ValueError('补充采集模式不能重叠')
        items = [{'id': ident, 'kind': 'rules', 'url':
                  'https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/10816492/files/' + filename}
                 for ident, filename in [
            ('published-rules-text', '704204728fe74fff89de4f16efda4791.docx'),
            ('published-rules-explanation', '92435c67211f452fa2e01792bf72012b.docx'),
            ('published-rules-deferred', '2ebdf02d60684b07a6ca0fd1f8cd1456.docx'),
        ]]
    if len(days) > 15:
        raise ValueError('超过本次有界采集预算')
    output = Path(args.output).resolve()
    output.mkdir(mode=0o700, exist_ok=False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: collect(item, output), items))
    receipt = {'version': 1, 'start': args.start, 'end': args.end, 'days': days, 'previous': previous,
        'modelCalls': 0, 'competitionOperations': 0, 'automaticRetries': 0, 'results': results}
    with (output / 'receipt.json').open('x') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    (output / 'receipt.json').chmod(0o600)
    print(json.dumps({'days': days, 'requests': len(results), 'failed': [x['id'] for x in results if x['status'] != 200],
                      'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
