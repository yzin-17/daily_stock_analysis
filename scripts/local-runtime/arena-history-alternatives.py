"""已启用替代来源的有界目录诊断；不改变来源、路由和比赛。"""

import argparse
from contextlib import redirect_stdout
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import urllib.request


def child(day):
    from data_provider.baostock_fetcher import BaostockFetcher
    from data_provider.baostock_history_catalog import read_history_catalog
    with redirect_stdout(sys.stderr):
        raw = read_history_catalog(BaostockFetcher(), day)
    print(json.dumps(raw, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description='已启用来源历史目录检查')
    parser.add_argument('--date', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if date.fromisoformat(args.date).isoformat() != args.date:
        raise ValueError('目录日期无效')
    if args.child:
        child(args.date)
        return
    plan = {'provider': 'baostock', 'date': args.date, 'maxNativeQueries': 2, 'timeoutSeconds': 45, 'execute': args.execute}
    if not args.execute:
        print(json.dumps(plan))
        return
    with urllib.request.urlopen('http://127.0.0.1:3000/api/market-data/providers', timeout=5) as response:
        providers = json.load(response)['providers']
    if not any(p['providerId'] == 'baostock' and p['enabled'] is True for p in providers):
        raise ValueError('BaoStock 未启用')
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(output) + '.permit').open('x') as handle:
        json.dump(plan, handle)
    try:
        result = subprocess.run([sys.executable, '-B', __file__, '--child', '--date', args.date, '--output', str(output)],
                                text=True, capture_output=True, timeout=45, check=True)
        raw = json.loads(result.stdout)
        with Path(str(output) + '.raw.json').open('x') as handle:
            json.dump(raw, handle, ensure_ascii=False)
        from data_provider.baostock_history_catalog import catalog_summary
        receipt = {**plan, 'status': 'observed', 'summary': catalog_summary(raw)}
    except subprocess.TimeoutExpired:
        receipt = {**plan, 'status': 'unavailable', 'reason': 'timeout'}
    except (subprocess.CalledProcessError, ValueError, OSError):
        receipt = {**plan, 'status': 'unavailable', 'reason': 'source_query_failed'}
    with output.open('x') as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
