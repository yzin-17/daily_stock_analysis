"""从真实区间原文构建候选 v2 归档；可见性不足时保留缺口，禁止加载。"""

import argparse
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path

from scripts.arena_execution_sources import OfficialSources, day_end
from scripts.arena_execution_materials import quote_rows
from data_provider.thesis_ledger_sources.historical_execution import ExecutionArchive, daily_context_key
from src.services.thesis_ledger_historical_execution import ExecutionRequest
from src.services.thesis_ledger_historical_reconstruction import DailyExecutionMaterial, reconstruct_context


SYMBOLS = ['588000.SH', '510300.SH']
KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'HISTORICAL_EXECUTION_CONTEXT'}


def query(symbol, at):
    return ExecutionRequest.model_validate({'contractVersion': 1, 'consumer': 'thesis-ledger',
        'requestId': 'range-preparation', 'routeKey': KEY, 'routeTarget': {
            'providerId': 'historical-execution-archive', 'upstreamSource': 'qualified-cn-execution', 'routeIndex': 0},
        'policyRevision': 201, 'symbol': symbol, 'asOf': at})


def build_range(captures, original, rules=None):
    import exchange_calendars as xcals
    calendar = xcals.get_calendar('XSHG')
    captures, original = Path(captures), Path(original)
    receipt = json.loads((captures / 'receipt.json').read_bytes())
    days = [s.date().isoformat() for s in calendar.sessions_in_range(receipt['start'], receipt['end'])]
    if receipt['start'] != '2026-11-02' or receipt['end'] != '2026-11-16' or receipt['days'] != days:
        raise ValueError('只构建已核验的两证券授权区间')
    sources = OfficialSources(captures.parent)
    earlier_rules = {}
    if rules:
        rules = Path(rules)
        rule_sources = OfficialSources(rules.parent)
        review = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/sse-etf-execution-reviewed.json').read_text())
        for ident, capture_id in [('trading-rules', 'published-rules-text'), ('deferred-rules', 'published-rules-deferred')]:
            spec = {**review['sources'][ident], 'group': rules.name, 'captureId': capture_id}
            proof, text = rule_sources.document(ident, spec)
            if datetime.fromisoformat(proof['availableAt']) >= datetime.fromisoformat('2026-11-02T00:00:00+08:00'):
                raise ValueError('补充规则仍晚于目标开始日期')
            earlier_rules[ident] = proof
        sources.raw.update(rule_sources.raw)
    index = json.loads((original / 'index.json').read_bytes())
    original_archive = ExecutionArchive(original)
    templates = {symbol: json.loads((original / 'daily' /
        (daily_context_key(symbol, 'ETF', '2026-11-10') + '.json')).read_bytes()) for symbol in SYMBOLS}
    for symbol in SYMBOLS:
        original_archive.read(query(symbol, '2026-11-10T05:01:00.000Z'))
    items = {(x.get('kind'), x.get('symbol'), x.get('day')): x for x in receipt['results']}
    minutes, audit, materials = {}, [], {}
    raw = {p.name: p.read_bytes() for p in (original / 'originals').iterdir()}
    if any(sha256(b).hexdigest() != name for name, b in raw.items()):
        raise ValueError('原有归档原文变化')
    # 对每个交易日前收盘单独核验，首日依赖上一交易日而不是假设上个自然日。
    for day in [receipt['previous'], *days]:
        for symbol in SYMBOLS:
            item = items['minutes', symbol, day]
            rows, proof = quote_rows(sources, captures.name, item['id'], item['sha256'], symbol, day)
            minutes[symbol, day] = rows, proof
    for day in days:
        previous = calendar.previous_session(day).date().isoformat()
        for symbol in SYMBOLS:
            current, minute_proof = minutes[symbol, day]
            prior, prior_proof = minutes[symbol, previous]
            close = Decimal(str(prior[-1]['close']))
            if any(Decimal(str(row['prevClose'])) != close for row in current):
                raise ValueError('相邻交易日前收盘不一致')
            material = deepcopy(templates[symbol])
            material['day'] = day
            rule = material['fact']['executionRules']
            ratio = Decimal(rule['price']['maxUpRatio'])
            lower = (close * (1 - ratio)).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
            upper = (close * (1 + ratio)).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
            material['priceRule'].update(lower=str(lower), upper=str(upper))
            rule['range'] = {'start': day, 'end': day}
            material['sessions'] = [
                {'startAt': calendar.session_open(day).isoformat(), 'endAt': calendar.session_break_start(day).isoformat(),
                 'evidenceRefs': ['trading-rules', 'calendar-notice']},
                {'startAt': calendar.session_break_end(day).isoformat(), 'endAt': calendar.session_close(day).isoformat(),
                 'evidenceRefs': ['trading-rules', 'calendar-notice']}]
            evidence = [deepcopy(earlier_rules.get(x['id'], x)) for x in material['evidence'] if x['id'] != 'previous-close']
            evidence.append({'id': 'previous-close', 'kind': 'snapshot', 'sha256': prior_proof['sha256'],
                'reference': prior_proof['url'], 'availableAt': day_end(previous), 'observedAt': prior_proof['observedAt']})
            material['evidence'] = evidence
            material['fact']['occurredAt'] = material['sessions'][0]['startAt']
            material['fact']['availableAt'] = max(evidence, key=lambda x: datetime.fromisoformat(x['availableAt']))['availableAt']
            halt = items['halts', symbol, day]
            _, halt_proof = sources.read(captures.name, halt['id'], halt['sha256'])
            material['pages'] = [{'id': 'official-halts', 'kind': 'reconstruction', 'sha256': halt_proof['sha256'],
                'reference': halt_proof['url'], 'httpStatus': halt_proof['status'], 'observedAt': halt_proof['observedAt']}]
            # 保留已验收的 11 月 10 日字节，不以重新采集覆盖旧资料。
            if day == '2026-11-10':
                material = templates[symbol]
            else:
                revision = 'official-etf-range-v1:' + sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
                material['fact']['providerRevision'] = revision
                rule['version'] = revision
            validated = DailyExecutionMaterial.model_validate(material)
            all_raw = {**raw, **sources.raw}
            entry = {'symbol': symbol, 'day': day, 'previousTradingDate': previous,
                'previousClose': str(close), 'lower': str(lower), 'upper': str(upper), 'officialRows': len(current),
                'minuteSha256': minute_proof['sha256'], 'ruleAvailableAt': material['fact']['availableAt']}
            try:
                for time in ['01:31:00', '03:29:00', '05:01:00', '06:59:00']:
                    at = f'{day}T{time}Z'
                    context = reconstruct_context(validated, at, all_raw.__getitem__)
                    ExecutionRequest.model_validate(query(symbol, at).model_dump())
                    from src.services.thesis_ledger_historical_execution import ExecutionResult
                    ExecutionResult.model_validate({**query(symbol, at).model_dump(), 'context': context.model_dump()})
                entry['status'] = 'ready'
                materials[daily_context_key(symbol, 'ETF', day)] = material
            except ValueError as exc:
                entry.update(status='blocked', reason=str(exc)[:450])
            audit.append(entry)
    raw.update(sources.raw)
    return {'materials': materials, 'originals': raw, 'audit': audit, 'days': days,
            'priorIndexSha256': sha256((original / 'index.json').read_bytes()).hexdigest(), 'priorIndex': index}


def write_range(package, output, original):
    output, original = Path(output).resolve(), Path(original).resolve()
    if original == output or original in output.parents or output in original.parents:
        raise ValueError('候选归档不得覆盖原归档')
    output.mkdir(mode=0o700, exist_ok=False)
    (output / 'daily').mkdir()
    (output / 'originals').mkdir()
    index = {'version': 2, 'contexts': {}, 'dailyContexts': {}}
    for key, material in sorted(package['materials'].items()):
        old = original / 'daily' / (key + '.json')
        raw = old.read_bytes() if key in package['priorIndex']['dailyContexts'] else json.dumps(material, ensure_ascii=False).encode()
        (output / 'daily' / (key + '.json')).write_bytes(raw)
        index['dailyContexts'][key] = {'assetType': 'ETF', 'sha256': sha256(raw).hexdigest()}
    for digest, raw in package['originals'].items():
        (output / 'originals' / digest).write_bytes(raw)
    (output / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
    report = {'status': 'blocked' if any(x['status'] != 'ready' for x in package['audit']) else 'ready',
        'start': package['days'][0], 'end': package['days'][-1], 'days': package['days'],
        'priorIndexSha256': package['priorIndexSha256'], 'indexSha256': sha256((output / 'index.json').read_bytes()).hexdigest(),
        'entries': package['audit'], 'realModelCalls': 0, 'competitionOperations': 0}
    (output / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description='区间候选归档与逐日缺口检查，不部署或写来源控制')
    parser.add_argument('--captures', required=True)
    parser.add_argument('--original', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--rules', help='已核验的发布时原始附件采集目录')
    args = parser.parse_args()
    result = write_range(build_range(args.captures, args.original, args.rules), args.output, args.original)
    print(json.dumps({'status': result['status'], 'entries': len(result['entries']),
        'ready': sum(x['status'] == 'ready' for x in result['entries']),
        'blocked': [{k: x[k] for k in ['day', 'symbol', 'reason']} for x in result['entries'] if x['status'] != 'ready'],
        'indexSha256': result['indexSha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
