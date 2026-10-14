"""BaoStock独立状态/日期查询的有界SDK进程；复用原查询与标准化。"""
from contextlib import redirect_stdout
from datetime import date
import json
import subprocess
import sys

from src.services.thesis_ledger_capability_contracts import CapabilityContractError

SDK_TIMEOUT_SECONDS = 45


def _read(payload):
    try:
        completed = subprocess.run(
            [sys.executable, '-m', 'data_provider.baostock_query_process'],
            input=json.dumps(payload), text=True, capture_output=True,
            timeout=SDK_TIMEOUT_SECONDS, check=True,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run会终止并wait子进程，不留下后台SDK连接。
        raise CapabilityContractError('timeout', 'BaoStock独立查询超时') from None
    except (subprocess.CalledProcessError, OSError):
        raise CapabilityContractError('upstream_unavailable', 'BaoStock独立查询暂不可用') from None
    try:
        wire = json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise CapabilityContractError('invalid_response', 'BaoStock查询进程响应无效') from None
    if isinstance(wire, dict) and set(wire) == {'result'} and isinstance(wire['result'], dict):
        return wire['result']
    if isinstance(wire, dict) and set(wire) == {'error'}:
        if wire['error'] == 'invalid_response':
            raise CapabilityContractError('invalid_response', 'BaoStock状态身份或字段无效')
        if wire['error'] == 'upstream_unavailable':
            raise CapabilityContractError('upstream_unavailable', 'BaoStock独立查询暂不可用')
    raise CapabilityContractError('invalid_response', 'BaoStock查询进程响应无效')


def read_daily_status(adapter, *, symbol: str, start: date, end: date):
    adapter._convert_stock_code(symbol)
    return _read({'mode': 'daily-status', 'symbol': symbol,
                  'start': start.isoformat(), 'end': end.isoformat()})


def read_basic_dates(adapter, symbol: str):
    adapter._convert_stock_code(symbol)
    return _read({'mode': 'basic-dates', 'symbol': symbol})


def main():
    from data_provider.baostock_fetcher import BaostockFetcher
    from src.services.thesis_ledger_baostock_daily_status import read_baostock_daily_status
    from src.services.thesis_ledger_baostock_lifecycle import read_baostock_lifecycle

    payload = json.load(sys.stdin)
    # SDK原控制台消息留在捕获stderr，stdout只承载本能力原标准化响应。
    with redirect_stdout(sys.stderr):
        adapter = BaostockFetcher()
        try:
            if payload['mode'] == 'daily-status':
                value = read_baostock_daily_status(adapter, symbol=payload['symbol'],
                    start=date.fromisoformat(payload['start']), end=date.fromisoformat(payload['end']))
            elif payload['mode'] == 'basic-dates':
                value = read_baostock_lifecycle(adapter, payload['symbol'])
            else:
                raise ValueError('不支持的BaoStock独立查询')
            wire = {'result': value}
        except (RuntimeError, OSError):
            wire = {'error': 'upstream_unavailable'}
        except (ValueError, TypeError, KeyError):
            wire = {'error': 'invalid_response'}
    print(json.dumps(wire, separators=(',', ':')))


if __name__ == '__main__':
    main()
