"""只读单批盘中采样：原精确绑定、原凭证快照、无缓存和控制写入。"""

import argparse
import contextlib
from datetime import datetime, timezone
import io
import json
import logging
import sys
from time import monotonic
from zoneinfo import ZoneInfo

from .thesis_ledger_bar_observations import STOP_CODES, compare_observations, tail_points
from .thesis_ledger_capability_contracts import DailyQuery, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_minute_contract_v1 import CONTRACT, MinuteRequest
from .thesis_ledger_probe_store import ReadOnlyProbeStore
from .thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, classify_provider_exception


SYMBOLS = {'CN': '600519.SH', 'HK': '00700.HK', 'US': 'AAPL.US'}
ZONES = {'CN': 'Asia/Shanghai', 'HK': 'Asia/Hong_Kong', 'US': 'America/New_York'}
MAX_CALLS = 12
MAX_BATCHES = 4


def candidates(catalog, markets, now):
    seen = set()
    for entry in catalog['entries']:
        key = entry['key']
        if (entry['state'] != 'ready' or key['market'] not in markets or key['assetType'] != 'STOCK'
                or key.get('adjustment') != 'none' or key.get('timeframe') not in {'1m', '60m', '1d'}):
            continue
        access = entry.get('accountAccess')
        if access:
            expiry = access.get('validUntil')
            if (access.get('state') != 'allowed' or not access.get('attempted') or not access.get('checkedAt')
                    or (not expiry and access.get('persisted') is not True)
                    or (expiry and datetime.fromisoformat(expiry.replace('Z', '+00:00')) <= now)):
                continue
        identity = (entry['target']['providerId'], key['market'], key['timeframe'])
        if identity in seen:
            continue
        seen.add(identity)
        yield entry


def read_entry(runtime, entry, symbol, now):
    key, target = entry['key'], entry['target']
    def check_current():
        catalog = runtime.market_route_catalog_v3()
        if catalog.get('integrity') != 'complete' or not any(
                current['key'] == key and current['target'] == target
                for current in candidates(catalog, [key['market']], datetime.now(timezone.utc))):
            raise ValueError('来源配置或读取资格变化，观察结果未发布')
    check_current()
    snapshot = runtime.store.provider_credential_snapshot(target['providerId'])
    adapter = runtime._adapter(target['providerId'], snapshot=snapshot, cache=False, probe=True, market_v3_route_key=key)
    day = now.astimezone(ZoneInfo(ZONES[key['market']])).date().isoformat()
    context = ReadContext(adapter, snapshot, timeout_seconds=4.5, probe=True,
                          database_path=runtime.store.database_path)
    try:
        if key['timeframe'] == '1d':
            query = DailyQuery(symbol=symbol, instrument_type='STOCK', adjustment='none', start=day, end=day,
                               request_id='intraday-observation', parameters={'interactive_chart': True})
            frame = CAPABILITY_SOURCES.daily(query, key, target, context).frame
            points = tail_points(frame.to_dict('records'))
        else:
            query = MinuteRequest(symbol=symbol, instrument_type='STOCK', timeframe=key['timeframe'], start=day, end=day)
            value = CAPABILITY_SOURCES.query(CONTRACT, query, key, target, context)
            points = tail_points([row.model_dump() for row in value.rows])
        current = runtime.store.provider_credential_snapshot(target['providerId'])
        if current != snapshot:
            raise ValueError('凭证代次变化，观察结果未发布')
        check_current()
        if not points:
            raise ValueError('来源没有当日 K 线')
        return points
    finally:
        close = getattr(adapter, 'close', None)
        if callable(close):
            close()


def sample_batch(runtime, markets, previous, *, now=None, reader=read_entry, clock=None):
    now = now or datetime.now(timezone.utc)
    if len(previous.get('batches', [])) >= MAX_BATCHES:
        raise ValueError('已完成四批，禁止继续请求')
    observations = list(previous.get('observations', []))
    counts, stopped = {}, set()
    for item in observations:
        scope = (item['providerId'], item['key']['market'])
        counts[scope] = counts.get(scope, 0) + 1
        if item.get('errorCode') in STOP_CODES:
            stopped.add(scope)
    catalog = runtime.market_route_catalog_v3()
    if catalog.get('integrity') != 'complete':
        raise ValueError('来源目录不完整，未发起采样')
    batch = []
    for entry in candidates(catalog, markets, now):
        key, target = entry['key'], entry['target']
        scope = (target['providerId'], key['market'])
        if scope in stopped or counts.get(scope, 0) >= MAX_CALLS:
            continue
        # 同一来源有多个上游时共用预算，不扩大为每个上游十二次。
        counts[scope] = counts.get(scope, 0) + 1
        began = monotonic()
        started = clock() if clock else datetime.now(timezone.utc)
        item = {**target, 'key': key, 'symbol': SYMBOLS[key['market']], 'observedAt': started.isoformat(),
                'bindingRevision': getattr(CAPABILITY_SOURCES.resolve(key, target), 'adapter_revision', None)}
        try:
            item.update(status='success', points=reader(runtime, entry, item['symbol'], started))
            item['sourceConfirmationAvailability'] = '仅记录绑定暴露的原始标识；未暴露不代表上游没有确认标识'
            item['comparison'] = compare_observations(observations, item)
        except Exception as error:
            code = getattr(error, 'code', None) or classify_provider_exception(error)
            allowed = STOP_CODES | {'invalid_response', 'not_covered', 'not_adapted', 'unsupported_capability',
                                   'upstream_failure', 'network_failure', 'timeout', 'insufficient_coverage'}
            code = code if code in allowed else 'probe_failure'
            item.update(status='failed', errorCode=code)
            if code in STOP_CODES:
                stopped.add(scope)
        item['latencyMs'] = round((monotonic() - began) * 1000)
        item['completedAt'] = (clock() if clock else datetime.now(timezone.utc)).isoformat()
        batch.append(item)
    return {'contractVersion': 1, 'readOnly': True, 'markets': list(markets),
            'batches': [*previous.get('batches', []), now.isoformat()],
            'observations': [*observations, *batch]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--markets', nargs='+', choices=tuple(SYMBOLS), required=True)
    parser.add_argument('--previous-stdin', action='store_true')
    parser.add_argument('--plan', action='store_true', help='只读来源目录，不请求上游')
    args = parser.parse_args()
    previous = json.load(sys.stdin) if args.previous_stdin else {}
    if (previous and (previous.get('contractVersion') != 1 or previous.get('markets') != args.markets
                      or len(previous.get('observations', [])) > 500)):
        parser.error('历史采样报告与本次市场不匹配或超出预算')
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            runtime = ThesisLedgerProviderRuntime(store=ReadOnlyProbeStore())
            if args.plan:
                catalog = runtime.market_route_catalog_v3()
                report = {'contractVersion': 1, 'readOnly': True, 'upstreamReads': 0,
                          'integrity': catalog.get('integrity'),
                          'candidates': list(candidates(catalog, args.markets, datetime.now(timezone.utc)))}
            else:
                report = sample_batch(runtime, args.markets, previous)
    except Exception:
        print(json.dumps({'contractVersion': 1, 'readOnly': True, 'errorCode': 'observation_precondition_failed'}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
