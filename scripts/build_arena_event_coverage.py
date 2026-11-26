"""复用真实候选与原文构建事件归档，禁止直接覆盖运行材料。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from data_provider.sse_event_coverage import ROOT, METHOD, validate_material, EventCoverageArchive
from scripts.build_arena_event_absence_review import review, read_original
from scripts.build_arena_range_materials import query
from data_provider.thesis_ledger_sources.historical_execution import ExecutionArchive


def build(index_root, documents_root, execution_root):
    index_root, documents_root = Path(index_root), Path(documents_root)
    reviewed = review(index_root, documents_root)
    if reviewed['eventDatesInWindow']:
        raise ValueError('窗口内有事件，不能构建无事件覆盖')
    index_receipt = json.loads((index_root / 'receipt.json').read_bytes())
    document_receipt = json.loads((documents_root / 'receipt.json').read_bytes())
    if sorted(x['symbol'] for x in index_receipt['results'] if x['kind'] == 'announcements') != ['510300.SH', '588000.SH']:
        raise ValueError('只构建已授权两证券')
    archive = ExecutionArchive(Path(execution_root))
    originals, indexes, publications = {}, [], []
    for item in index_receipt['results']:
        if item['kind'] != 'announcements':
            continue
        context = archive.read(query(item['symbol'], '2026-11-10T05:01:00Z')).context
        listed = context.trading.basis.listing.listedOn
        raw = read_original(index_root, item)
        originals[item['sha256']] = raw
        indexes.append({'symbol': item['symbol'], 'listedOn': listed, 'reference': item['url'],
            'sha256': item['sha256'], 'observedAt': item['observedAt']})
    by_id = {x['id']: x for x in document_receipt['results']}
    for item in reviewed['publications']:
        raw = read_original(documents_root, by_id[item['id']])
        originals[item['sha256']] = raw
        publications.append({**{k: item[k] for k in ['symbol', 'publishedOn', 'sha256', 'observedAt',
            'recordDate', 'effectiveDate', 'paymentDate']}, 'reference': item['url']})
    pack = {'version': 1, 'method': METHOD, 'scopeSymbols': ['510300.SH', '588000.SH'],
        'scopeDateFrom': '2026-11-02', 'scopeDateTo': '2026-11-16',
        'indexes': sorted(indexes, key=lambda x: x['symbol']),
        'publications': sorted(publications, key=lambda x: x['reference'])}
    validate_material(pack, originals.__getitem__)
    return pack, originals


def write_candidate(pack, originals, output):
    output = Path(output).resolve()
    if output == ROOT.resolve() or ROOT.resolve() in output.parents or output in ROOT.resolve().parents:
        raise ValueError('离线候选禁止覆盖生产目录')
    output.mkdir(mode=0o700)
    (output / 'originals').mkdir()
    raw = (json.dumps(pack, ensure_ascii=False, indent=2) + '\n').encode()
    (output / 'proof.json').write_bytes(raw)
    for digest, content in originals.items():
        if sha256(content).hexdigest() != digest:
            raise ValueError('原文摘要不符')
        (output / 'originals' / digest).write_bytes(content)
    EventCoverageArchive(output).material()
    return {'status': 'ready', 'proofSha256': sha256(raw).hexdigest(), 'files': 1 + len(originals),
        'scopeSymbols': pack['scopeSymbols'], 'scopeDateFrom': pack['scopeDateFrom'], 'scopeDateTo': pack['scopeDateTo'],
        'admissionWritten': False, 'realModelCalls': 0, 'competitionOperations': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='真实官方事件无事件窗口候选，不加载或准入')
    for key in ['index', 'documents', 'execution', 'output']:
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    pack, originals = build(args.index, args.documents, args.execution)
    print(json.dumps(write_candidate(pack, originals, args.output), ensure_ascii=False))
