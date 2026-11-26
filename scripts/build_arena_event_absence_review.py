"""离线审阅完整官方目录与相关事件公告；候选报告不签发覆盖或准入。"""

import argparse
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re

from scripts.collect_arena_range_sources import event_document_requests


def read_original(root, capture):
    raw = (root / capture['file']).read_bytes()
    if capture['status'] != 200 or sha256(raw).hexdigest() != capture['sha256']:
        raise ValueError('原文采集或摘要不符')
    return raw


def label_date(text, label):
    # 只接受表格标签后的日期；正文再次提及该标签时不能跨句抓取汇款日期。
    matches = re.findall(label + r'[:：]*(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if not matches or len({tuple(map(int, value)) for value in matches}) != 1:
        raise ValueError('原文缺少明确日期：' + label)
    from datetime import date
    return date(*map(int, matches[0])).isoformat()


def review(index_root, documents_root):
    from pypdf import PdfReader
    index_root, documents_root = Path(index_root), Path(documents_root)
    wanted = event_document_requests(index_root)
    documents_receipt = json.loads((documents_root / 'receipt.json').read_bytes())
    documents = {item['id']: item for item in documents_receipt['results']}
    if set(documents) != {item['id'] for item in wanted}:
        raise ValueError('相关事件公告采集不完整')
    publications = []
    for item in wanted:
        capture = documents[item['id']]
        if any(capture.get(k) != item[k] for k in ['url', 'symbol', 'publishedOn', 'title']):
            raise ValueError('公告采集身份与目录不符')
        reader = PdfReader(BytesIO(read_original(documents_root, capture)))
        if not 1 <= len(reader.pages) <= 10:
            raise ValueError('事件公告超出有界审阅页数')
        text = re.sub(r'\s+', '', ''.join(page.extract_text() or '' for page in reader.pages))
        if item['symbol'].split('.')[0] not in text or not re.search('分红|收益分配', item['title']):
            raise ValueError('尚未支持或不能确认的事件原文')
        dates = {'recordDate': label_date(text, '权益登记日'), 'effectiveDate': label_date(text, '除息日'),
                 'paymentDate': label_date(text, '(?:现金)?红利发放日')}
        if not item['publishedOn'] <= dates['recordDate'] <= dates['effectiveDate'] <= dates['paymentDate']:
            raise ValueError('事件日期顺序需要人工核对')
        publications.append({**item, **dates, 'sha256': capture['sha256'], 'pages': len(reader.pages),
            'observedAt': capture['observedAt']})
    index_receipt = json.loads((index_root / 'receipt.json').read_bytes())
    indexes = []
    for capture in index_receipt['results']:
        if capture['kind'] != 'announcements':
            continue
        text = read_original(index_root, capture).decode().strip()
        body = json.loads(text[1:-1] if text.startswith('(') and text.endswith(')') else text)
        rows = body['result']
        in_window = [row for row in rows if '2026-11-02' <= row['SSEDATE'] <= '2026-11-16']
        indexes.append({**capture, 'totalRows': len(rows), 'pages': body['pageHelp']['pageCount'],
            'latestPublication': max(row['SSEDATE'] for row in rows), 'publicationsInWindow': in_window,
            'categories': dict(Counter(row['ORG_BULLETIN_TYPE_DESC'] for row in rows)),
            'actionCandidates': [row for row in rows if re.search('分红|收益分配|折算|拆分|份额转换', row['TITLE'])]})
    relevant = [item for item in publications if any('2026-11-02' <= item[k] <= '2026-11-16'
                for k in ['recordDate', 'effectiveDate', 'paymentDate'])]
    return {'version': 'sse-event-absence-review-v1', 'status': 'candidate', 'admissionWritten': False,
        'scopeSymbols': ['510300.SH', '588000.SH'], 'scopeDateFrom': '2026-11-02', 'scopeDateTo': '2026-11-16',
        'indexes': indexes, 'publications': publications, 'eventDatesInWindow': relevant,
        'realModelCalls': 0, 'competitionOperations': 0,
        'limitations': ['尚未作为独立来源接入，未变更事件完整性合同；正式覆盖仍需核验重建口径和失败边界']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='两证券事件公告日期的离线候选审阅')
    parser.add_argument('--index', required=True)
    parser.add_argument('--documents', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = review(args.index, args.documents)
    with Path(args.output).open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    Path(args.output).chmod(0o600)
    print(json.dumps({'status': result['status'], 'indexes': len(result['indexes']),
        'publications': len(result['publications']), 'eventsInWindow': len(result['eventDatesInWindow']),
        'realModelCalls': 0, 'competitionOperations': 0}, ensure_ascii=False))
