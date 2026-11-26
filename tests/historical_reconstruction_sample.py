"""官方空查询配合受控上市、会话和制度，构造临时按日归档。"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from data_provider.thesis_ledger_sources.historical_execution import daily_context_key
from src.services.thesis_ledger_historical_execution import ExecutionRequest

FIXTURES = Path(__file__).parent / 'fixtures'


def sample(symbol='588000.SH'):
    base = json.loads((FIXTURES / 'historical-execution.shared.json').read_text())
    captured = json.loads((FIXTURES / 'sse-halt-history.shared.json').read_text())
    page = next(c['page'] for c in captured['cases'] if c['symbol'] == symbol)
    original = captured['raw'].encode()
    assert sha256(original).hexdigest() == page['sha256']
    context = base['context']
    fact = deepcopy(context['fact'])
    fact['symbol'] = symbol
    # 上市和会话依据使用明确标为受控的制度原文，不借用状态快照。
    material = {k: deepcopy(context[k]) for k in ['priceRule', 'ruleCoverage', 'ruleEvidenceRefs']}
    material.update(version=1, day='2026-11-10', fact=fact,
        evidence=[e for e in context['evidence'] if e['kind'] == 'rule'],
        listing={'listedOn': '2020-01-01', 'delistedOn': None, 'evidenceRefs': ['rules']},
        sessions=[{'startAt': '2026-11-10T05:00:00Z', 'endAt': '2026-11-10T07:00:00Z',
                   'evidenceRefs': ['rules']}], pages=[page])
    originals = {sha256(text.encode()).hexdigest(): text.encode() for text in base['originals'].values()}
    originals[page['sha256']] = original
    query = ExecutionRequest.model_validate({**base['request'], 'symbol': symbol})
    return query, material, originals


def write_archive(root, query, material, originals):
    (root / 'daily').mkdir(parents=True, exist_ok=True)
    (root / 'originals').mkdir(exist_ok=True)
    for digest, raw in originals.items():
        (root / 'originals' / digest).write_bytes(raw)
    key = daily_context_key(query.symbol, query.routeKey.assetType, material['day'])
    path = root / 'daily' / (key + '.json')
    path.write_text(json.dumps(material, ensure_ascii=False))
    index = {'version': 2, 'contexts': {}, 'dailyContexts': {key: {
        'assetType': query.routeKey.assetType, 'sha256': sha256(path.read_bytes()).hexdigest()}}}
    (root / 'index.json').write_text(json.dumps(index))
    return path
