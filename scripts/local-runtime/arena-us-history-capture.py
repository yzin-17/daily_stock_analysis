"""固定现有富途主路由读取单证券单日真实行情，不修改来源配置。"""

import argparse
from datetime import date
import json
import sys


def minute_value(frame, symbol):
    """把运行时保留的分钟表及来源元数据还原为已有强合同。"""
    from src.services.thesis_ledger_minute_contract_v1 import MinuteResult
    attrs = frame.attrs
    return MinuteResult(
        symbol=symbol, assetType='STOCK', market='US', source=attrs['upstream_source'],
        rows=json.loads(frame.to_json(orient='records')), observedAt=attrs['observedAt'],
        timezone=attrs['timezone'], timeframe=attrs['timeframe'], session=attrs['session'],
        timestampConvention=attrs['timestampConvention'], fieldUnits=attrs['intradayFieldUnits'],
        responseSha256=attrs['responseSha256'],
    ).model_dump(mode='json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--symbol', required=True, choices=['AAPL.US'])
    parser.add_argument('--date', required=True)
    args = parser.parse_args()
    assert date.fromisoformat(args.date).isoformat() == args.date
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
    from src.services.thesis_ledger_bar_route_execution import execute_bar_route
    runtime = ThesisLedgerProviderRuntime()
    policy = runtime.store.effective_policy_v3()
    key = {'kind': 'bar', 'market': 'US', 'assetType': 'STOCK', 'capability': 'MINUTE_BAR',
           'timeframe': '1m', 'adjustment': 'none'}
    route = next((row for row in policy.get('routes', []) if row.get('key') == key), None)
    targets = [target for target in (route or {}).get('targets', []) if target.get('eligible')]
    assert policy.get('enabled') and targets, '需要已选合格来源'
    assert (targets[0].get('providerId'), targets[0].get('upstreamSource'),
            targets[0].get('routeIndex')) == ('futu', 'futu', 0), '既有主来源不是富途'
    pin = {key: targets[0][key] for key in ('providerId', 'upstreamSource', 'routeIndex')}
    receipt = {'symbol': args.symbol, 'date': args.date, 'routeKey': key, 'providerId': 'futu',
               'policyRevision': policy['revision'], 'providerReads': 0, 'routeWrites': 0, 'modelCalls': 0}
    if not args.apply:
        return {**receipt, 'status': 'preview'}
    receipt['providerReads'] = 1
    request = ThesisLedgerDataRequest(
        'MINUTE_BAR', args.symbol,
        instrument_type='STOCK', start=args.date, end=args.date, timeframe='1m', adjustment='none',
        parameters={'historical_reconstruction': True})
    result = execute_bar_route(runtime, request, key, route_target=pin)
    assert (result.provider, result.upstream_source) == ('futu', 'futu')
    value = minute_value(result.value, args.symbol)
    assert value['symbol'] == args.symbol and value['assetType'] == 'STOCK' and value['market'] == 'US'
    assert runtime.store.effective_policy_v3()['revision'] == policy['revision'], '读取期间来源配置变化'
    return {**receipt, 'status': 'captured', 'value': value}


if __name__ == '__main__':
    try:
        print('THESIS_ARENA_US_HISTORY=' + json.dumps(main(), ensure_ascii=False))
    except Exception as error:
        print('THESIS_ARENA_US_HISTORY=' + json.dumps({'status': 'stopped',
              'code': getattr(error, 'code', type(error).__name__), 'automaticRetries': 0,
              'message': '单次历史行情读取未通过，未修改来源配置'}))
        sys.exit(2)
