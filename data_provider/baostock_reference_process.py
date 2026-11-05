"""估值原值的独立 SDK 会话，沿用已有 45 秒进程上限。"""

from contextlib import redirect_stdout
import json
import subprocess
import sys

from data_provider.baostock_query_process import SDK_TIMEOUT_SECONDS
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def read_valuation(adapter, *, symbol, start, end, timeout_seconds=None):
    adapter._convert_stock_code(symbol)
    timeout = SDK_TIMEOUT_SECONDS
    if timeout_seconds is not None:
        timeout = min(timeout, timeout_seconds)
    try:
        completed = subprocess.run([sys.executable, '-B', '-m', 'data_provider.baostock_reference_process'],
            input=json.dumps({'symbol': symbol, 'start': start.isoformat(), 'end': end.isoformat()}),
            text=True, capture_output=True, timeout=timeout, check=True)
        value = json.loads(completed.stdout)
    except subprocess.TimeoutExpired:
        raise CapabilityContractError('timeout', 'BaoStock 估值查询超时') from None
    except (subprocess.CalledProcessError, OSError):
        raise CapabilityContractError('upstream_unavailable', 'BaoStock 估值查询暂不可用') from None
    except (ValueError, UnicodeError):
        raise CapabilityContractError('invalid_response', 'BaoStock 估值进程响应无效') from None
    if not isinstance(value, dict) or set(value) != {'rows'}:
        raise CapabilityContractError('invalid_response', 'BaoStock 估值进程封套无效')
    return value


def main():
    from data_provider.baostock_fetcher import BaostockFetcher
    payload = json.load(sys.stdin)
    code = BaostockFetcher()._convert_stock_code(payload['symbol'])
    fields = 'date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM,turn,tradestatus,isST'
    with redirect_stdout(sys.stderr):
        with BaostockFetcher()._baostock_session() as sdk:
            response = sdk.query_history_k_data_plus(code, fields, start_date=payload['start'],
                end_date=payload['end'], frequency='d', adjustflag='3')
            if response.error_code != '0':
                raise RuntimeError('来源查询没有成功')
            rows = []
            while response.next():
                rows.append(dict(zip(response.fields, response.get_row_data())))
    print(json.dumps({'rows': rows}, separators=(',', ':')))


if __name__ == '__main__':
    main()
