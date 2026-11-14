"""有界读取现有 OpenD 的沪深日线；不访问控制库、不改主备或凭证。"""

import argparse
from datetime import date, datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_provider.futu_market import FutuMarketAdapter
from src.services.thesis_ledger_capability_contracts import DailyQuery, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start > end or (end - start).days > 7 or not 1 <= args.port <= 65535:
        parser.error('窗口须有序且最多八个自然日，端口须有效')
    adapter = FutuMarketAdapter(host=args.host, port=args.port)
    results = []
    for symbol in ('600519.SH', '000001.SZ'):
        for adjustment in ('none', 'qfq', 'hfq'):
            began = monotonic()
            item = {'symbol': symbol, 'adjustment': adjustment}
            query = DailyQuery(symbol=symbol, instrument_type='STOCK', adjustment=adjustment,
                               start=args.start, end=args.end, request_id='futu-cn-daily-probe')
            key = {'kind': 'bar', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'DAILY_BAR',
                   'timeframe': '1d', 'adjustment': adjustment}
            try:
                result = CAPABILITY_SOURCES.daily(query, key,
                    {'providerId': 'futu', 'upstreamSource': 'futu'}, ReadContext(adapter, probe=True))
                frame = ThesisLedgerProviderRuntime._validate_bars(result.frame)
                item.update(status='success', rows=len(frame), firstDate=str(frame.iloc[0]['date']),
                            lastDate=str(frame.iloc[-1]['date']), responseSha256=frame.attrs['responseSha256'],
                            fieldAvailability={name: bool(frame[name].notna().all()) for name in ('volume', 'amount')})
            except Exception as error:
                item.update(status='failed', errorCode=getattr(error, 'code', 'probe_failure'))
            item['latencyMs'] = round((monotonic() - began) * 1000)
            results.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
    summary = {'observedAt': datetime.now(timezone.utc).isoformat(), 'sdkVersion': version('futu-api'),
               'readOnly': True, 'windowsAttempted': len(results), 'start': args.start, 'end': args.end,
               'successes': sum(row['status'] == 'success' for row in results), 'results': results}
    print('FUTU_CN_DAILY_PROBE=' + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary['successes'] == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
