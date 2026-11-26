"""复用已有真实回执，离线核对分钟格点、原文与逐分钟执行资料，不访问网络。"""

import argparse
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from data_provider.thesis_ledger_sources.historical_execution import daily_context_key
from src.services.thesis_ledger_historical_reconstruction import DailyExecutionMaterial, reconstruct_context


def audit_minutes(receipt, materials):
    receipt, materials = Path(receipt), Path(materials)
    raw = receipt.read_bytes()
    packet = json.loads(raw)
    reviewed = json.loads((materials / 'audit.json').read_bytes())
    expected = {(symbol, day) for day in reviewed['days'] for symbol in ['588000.SH', '510300.SH']}
    actual = [(x['symbol'], x['day']) for x in packet['results']]
    if packet['status'] != 'passed' or packet['errors'] or len(actual) != 22 or set(actual) != expected:
        raise ValueError('正常来源分钟回执缺少完整区间或重复证券日')
    index = json.loads((materials / 'index.json').read_bytes())
    originals = {p.name: p.read_bytes() for p in (materials / 'originals').iterdir()}
    if any(sha256(value).hexdigest() != key for key, value in originals.items()):
        raise ValueError('官方原文摘要变化')
    details, closes, difference, largest = [], {}, 0, Decimal(0)
    review = {(x['symbol'], x['day']): x for x in reviewed['entries']}
    for item in packet['results']:
        symbol, day, result = item['symbol'], item['day'], item['result']
        value = result['value']
        if (result['providerId'] != 'futu' or value['timestampConvention'] != 'interval-end'
                or value['symbol'] != symbol):
            raise ValueError('正常来源或分钟时钟约定变化')
        grid = [f'{day}T{minute // 60:02}:{minute % 60:02}:00+08:00'
                for start, end in [(571, 690), (781, 900)] for minute in range(start, end + 1)]
        rows = value['rows']
        regular = [row for row in rows if row['timestamp'] != day + 'T09:30:00+08:00']
        if len(rows) != 241 or [r['timestamp'] for r in regular] != grid:
            raise ValueError('分钟格点存在缺失、重复或未来数据')
        evidence = review[symbol, day]
        official = json.loads(originals[evidence['minuteSha256']])
        source_rows = official['kline']
        columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'prevClose']
        official_rows = [dict(zip(columns, row)) for row in source_rows]
        close = Decimal(str(regular[-1]['close']))
        if close != Decimal(str(official_rows[-1]['close'])):
            raise ValueError('正常来源与官方日收盘不一致')
        closes[symbol, day] = close
        material_key = daily_context_key(symbol, 'ETF', day)
        material_raw = (materials / 'daily' / (material_key + '.json')).read_bytes()
        if sha256(material_raw).hexdigest() != index['dailyContexts'][material_key]['sha256']:
            raise ValueError('按日执行资料摘要变化')
        material = DailyExecutionMaterial.model_validate_json(material_raw)
        for row in regular:
            if not row.get('complete'):
                raise ValueError('分钟未完成')
            for field in ['open', 'high', 'low', 'close']:
                price = Decimal(str(row[field]))
                if not Decimal(evidence['lower']) <= price <= Decimal(evidence['upper']):
                    raise ValueError('分钟价格超出真实日限价')
            reconstruct_context(material, row['timestamp'], originals.__getitem__)
        for selected, official_row in zip(rows, official_rows):
            for field in ['open', 'high', 'low', 'close']:
                delta = abs(Decimal(str(selected[field])) - Decimal(str(official_row[field])))
                if delta:
                    difference += 1
                    largest = max(largest, delta)
        details.append({'symbol': symbol, 'day': day, 'regularMinutes': len(regular),
            'closingBoundaryValidated': True, 'archiveRef': result['archiveRef'], 'providerId': result['providerId']})
    for item in reviewed['entries']:
        prior = closes.get((item['symbol'], item['previousTradingDate']))
        if prior is not None and prior != Decimal(item['previousClose']):
            raise ValueError('正常来源跨日前收盘链不一致')
    return {'status': 'passed', 'receiptSha256': sha256(raw).hexdigest(),
        'indexSha256': sha256((materials / 'index.json').read_bytes()).hexdigest(),
        'securitiesDays': len(details), 'regularMinutes': sum(x['regularMinutes'] for x in details),
        'missingMinutes': 0, 'duplicateMinutes': 0, 'executionContextsValidated': 5280,
        'crossSourceOhlcDifferences': difference, 'largestCrossSourceDifference': str(largest),
        'crossSourceDifferenceCause': '尚未确认，回放保持选定 Futu 来源，不混合分钟价格',
        'details': details, 'realModelCalls': 0, 'competitionOperations': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='已保存分钟回执的离线核验')
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--materials', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = audit_minutes(args.receipt, args.materials)
    with Path(args.output).open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    Path(args.output).chmod(0o600)
    print(json.dumps({k: v for k, v in report.items() if k != 'details'}, ensure_ascii=False))
