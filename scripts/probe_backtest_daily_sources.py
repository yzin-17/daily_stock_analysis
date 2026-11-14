"""在临时控制库副本中核验精确回测行情窗口，不修改真实策略或准入。"""

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import monotonic
import traceback
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] if '__file__' in globals() else Path.cwd()))

from api.thesis_ledger import (
    _backtest_market_for_symbol, _market_data_v3_request,
    _market_data_v3_coverage_context, _market_data_v3_response,
)
from api.thesis_ledger_multi_window_v3 import execute_market_window_v3, try_daily_multi_window_v3
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_market_v3_facts import market_listing_fact_v3
from src.services.thesis_ledger_probe_execution import run_probe
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


def control_fingerprint(connection):
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'thesis_ledger_%' ORDER BY name"
    ) if any(part in row[0] for part in ('provider_config', 'provider_health', 'policy_state', 'route_admission'))]
    digest = sha256()
    for table in tables:
        digest.update(table.encode())
        for row in sorted(connection.execute('SELECT * FROM "' + table + '"').fetchall(), key=repr):
            digest.update(repr(row).encode())
    return digest.hexdigest()


@contextmanager
def isolated_runtime(database_path):
    source = Path(database_path).resolve()
    if not source.is_file():
        raise ValueError('当前控制库不存在')
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original:
        before = control_fingerprint(original)
        with TemporaryDirectory(prefix='backtest-source-probe-') as directory:
            copied = Path(directory) / 'control.db'
            with sqlite3.connect(copied) as destination:
                original.backup(destination)
            copied.chmod(0o600)
            runtime = ThesisLedgerProviderRuntime(ThesisLedgerControlStore(str(copied)))
            try:
                yield runtime
            finally:
                if control_fingerprint(original) != before:
                    raise RuntimeError('真实控制状态在诊断期间发生变化，结果不可认证')


def error_code(error):
    detail = getattr(error, 'detail', None)
    if isinstance(detail, dict):
        return detail.get('error', detail).get('code', 'invalid_response')
    return getattr(error, 'code', 'diagnostic_failure')


def failure_chain(error):
    """只记录代码位置和异常类型，不输出供应商异常原文。"""
    chain = []
    seen = set()
    while error is not None and id(error) not in seen and len(chain) < 6:
        seen.add(id(error))
        chain.append({'type': type(error).__name__, 'code': error_code(error),
                      'path': [f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
                               for frame in traceback.extract_tb(error.__traceback__)[-5:]]})
        error = error.__cause__ or error.__context__
    return chain


def yahoo_frame_summary(frame, stage):
    """只保留公开字段的缺失统计和少量日期，不输出价格或其他列。"""
    import pandas as pd
    fields = ('Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume')
    missing = {}
    for field in fields:
        if field in frame.columns:
            missing[field] = [str(day)[:10] for day in frame.index[frame[field].isna()]]
    return {'stage': stage, 'rows': len(frame),
            'fields': [field for field in fields if field in frame.columns],
            'missingCounts': {field: len(days) for field, days in missing.items()},
            'missingDateSamples': {field: days[:8] for field, days in missing.items() if days},
            'nonFiniteAdjustmentRatios': int((~pd.Series(
                frame['Adj Close'] / frame['Close']).map(isfinite)).sum())
            if 'Adj Close' in frame and 'Close' in frame else None}


@contextmanager
def observe_yahoo_daily(enabled):
    observations = []
    if not enabled:
        yield observations
        return
    from unittest.mock import patch
    from yfinance import utils
    from data_provider.thesis_ledger_sources import yfinance as source
    original_adjust, original_parse = utils.auto_adjust, utils.parse_quotes
    original_normalize = source.normalize_exact_daily

    def record(frame, stage):
        try:
            observations.append(yahoo_frame_summary(frame, stage))
        except (TypeError, ValueError, KeyError, AttributeError):
            observations.append({'stage': stage, 'observationError': True})

    def adjust(frame):
        record(frame, 'sdk-before-auto-adjust')
        result = original_adjust(frame)
        record(result, 'sdk-after-auto-adjust')
        return result

    def parse(data):
        try:
            import pandas as pd
            quote = data['indicators']['quote'][0]
            dates = pd.to_datetime(data['timestamp'], unit='s', utc=True).tz_convert(
                data['meta']['exchangeTimezoneName'])
            columns = {field.title(): values for field, values in quote.items()
                       if field in {'open', 'high', 'low', 'close', 'volume'}}
            adjusted = data['indicators'].get('adjclose')
            if adjusted:
                columns['Adj Close'] = adjusted[0]['adjclose']
            record(pd.DataFrame(columns, index=dates), 'sdk-raw-chart')
        except (TypeError, ValueError, KeyError, AttributeError):
            observations.append({'stage': 'sdk-raw-chart', 'observationError': True})
        return original_parse(data)

    def normalize(frame, **options):
        # 适配器调用前仍使用 Yahoo 的公开列名；只观察，不改参数和数据。
        public = frame.rename(columns={'date': 'Date', 'open': 'Open', 'high': 'High',
                                       'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
        if 'Date' in public:
            public = public.set_index('Date')
        record(public, 'adapter-before-normalize')
        return original_normalize(frame, **options)

    with patch.object(utils, 'auto_adjust', adjust), patch.object(utils, 'parse_quotes', parse), \
            patch.object(source, 'normalize_exact_daily', normalize):
        yield observations


def probe_window(runtime, *, symbol, asset, adjustment, start, end, provider, source, inspect_yahoo=False,
                 price_research_reference=False):
    if price_research_reference and adjustment != 'none':
        raise ValueError('不复权参考模式必须选择 none')
    began = monotonic()
    result = {'symbol': symbol, 'assetType': asset, 'adjustment': adjustment,
              'providerId': provider, 'upstreamSource': source, 'start': start, 'end': end,
              'attempted': False, 'status': 'unavailable', 'stage': 'control'}
    try:
        key = {'kind': 'bar', 'market': _backtest_market_for_symbol(symbol),
               'assetType': asset, 'capability': 'DAILY_BAR',
               'timeframe': '1d', 'adjustment': adjustment}
        target = {'providerId': provider, 'upstreamSource': source}
        registered = next((p for p in runtime.store.provider_registry() if p['providerId'] == provider), None)
        if not registered or not registered['enabled']:
            result['errorCode'] = 'provider_disabled'
            return result
        catalog = runtime.market_route_catalog_v3()
        entry = next((e for e in catalog['entries'] if e['key'] == key and e['target'] == target), None)
        if not entry or entry['state'] != 'ready':
            result['errorCode'] = entry['state'] if entry else 'not_adapted'
            return result
        request = _market_data_v3_request({
            'contractVersion': 3, 'requestId': str(uuid4()), 'symbol': symbol, 'routeKey': key,
            'routeTarget': {**target, 'routeIndex': 0}, 'start': start, 'end': end,
            **({'tradabilityMode': 'assume-untradable-no-bar'}
               if adjustment != 'none' or price_research_reference else {}),
        }, 'source-window-probe')
        result['stage'] = 'coverage'
        context = _market_data_v3_coverage_context(request)
        if context is None:
            result['errorCode'] = 'listing_fact_missing' if market_listing_fact_v3(symbol) is None else 'calendar_coverage_missing'
            return result
        previous = runtime.store.effective_policy_v3()
        revision = previous['revision'] + 1 if previous else 1
        runtime.store.apply_policy_v3({
            'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': str(uuid4()),
            'revision': revision, 'enabled': True, 'routes': [{'key': key, 'targets': [target]}],
        })
        result['stage'] = 'read'
        result['attempted'] = True

        def render(data, execution, covered):
            result['stage'] = 'response'
            result['sourceRows'] = len(execution.value)
            if provider == 'yfinance':
                result['sourceRetrieval'] = execution.value.attrs.get('yahooDailyRetrieval')
            return _market_data_v3_response(data, execution, covered)

        def read():
            multi = try_daily_multi_window_v3(request, context, runtime, render,
                _market_data_v3_coverage_context, max_requests=8, timeout_seconds=4.5)
            if multi is not None:
                return multi
            execution = execute_market_window_v3(request, runtime)
            return render(request, execution, context)

        with observe_yahoo_daily(inspect_yahoo) as observations:
            try:
                response = run_probe(read)
            finally:
                if inspect_yahoo:
                    result['sourceObservations'] = list(observations)
        assumed = sorted({day['date'] for window in response.get('historicalTradabilityWindows', [])
                          for day in window['days'] if day['state'] == 'assumed-untradable-no-bar'})
        if response.get('windowObservations'):
            result['observations'] = len(response['windowObservations'])
        result.update(status='validated', stage='complete', rows=len(response['bars']),
                      firstDate=response['coverage']['actualStart'], lastDate=response['coverage']['actualEnd'],
                      inputFingerprint=response['inputFingerprint'], fieldUnits=response['sourcePriceBasis'].get('fieldUnits'),
                      listing=context['listing'], expectedSessions=len(context['expectedPostListingSessionDates']),
                      assumedUntradableDates=assumed, pagesFetched=response['coverageProof']['pagination']['pagesFetched'])
    except Exception as error:
        result['errorCode'] = error_code(error)
        result['failureChain'] = failure_chain(error)
    finally:
        result['latencyMs'] = round((monotonic() - began) * 1000)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=os.getenv('DATABASE_PATH', './data/stock_analysis.db'))
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--asset', choices=['STOCK', 'ETF'], required=True)
    parser.add_argument('--adjustment', choices=['none', 'qfq', 'hfq'], default='qfq',
                        help='未复权默认按严格上市／日历覆盖核验')
    parser.add_argument('--price-research-reference', action='store_true',
                        help='显式按不复权研究参考核验；仍须来源声明身份与研究日历，不授予真实份额资格')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--max-window-days', type=int, choices=[93, 400, 550], default=93,
                        help='默认最多93天；长窗须显式选择400或550，不增加请求或超时预算')
    parser.add_argument('--target', action='append', required=True, help='精确 providerId/upstreamSource')
    parser.add_argument('--inspect-yahoo', action='store_true', help='观察同次 Yahoo 读取的复权与适配缺值阶段')
    args = parser.parse_args()
    if args.price_research_reference and args.adjustment != 'none':
        parser.error('不复权参考模式必须选择 --adjustment none')
    first, last = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if not 0 <= (last - first).days < args.max_window_days or len(args.target) > 12 or len(set(args.target)) != len(args.target):
        parser.error(f'窗口最多{args.max_window_days}天，精确目标须去重且最多12个')
    targets = [target.split('/', 1) for target in args.target]
    if any(len(target) != 2 or not all(target) for target in targets):
        parser.error('精确目标必须为 providerId/upstreamSource')
    if args.inspect_yahoo and targets != [['yfinance', 'yfinance']]:
        parser.error('Yahoo 阶段观察只能选择唯一 yfinance/yfinance 目标')
    counts = {}
    for provider, _ in targets:
        counts[provider] = counts.get(provider, 0) + 1
    if max(counts.values()) > 4:
        parser.error('单个提供方最多四个目标')
    results = []
    with isolated_runtime(args.database) as runtime:
        for provider, source in targets:
            result = probe_window(runtime, symbol=args.symbol, asset=args.asset, adjustment=args.adjustment,
                                  start=args.start, end=args.end, provider=provider, source=source,
                                  inspect_yahoo=args.inspect_yahoo,
                                  price_research_reference=args.price_research_reference)
            results.append(result)
            print('BACKTEST_SOURCE_RESULT=' + json.dumps(result, ensure_ascii=False), flush=True)
    print('BACKTEST_SOURCE_PROBE=' + json.dumps({
        'observedAt': datetime.now(timezone.utc).isoformat(), 'isolatedControl': True,
        'productionControlUnchanged': True, 'results': results,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
