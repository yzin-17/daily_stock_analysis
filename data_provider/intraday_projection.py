"""原生分钟价格的统一投影；单位和时间语义必须由来源显式提供。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo
import pandas as pd
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_minute_contract_v1 import MinuteResult, MARKET_TIMEZONES
from src.services.thesis_ledger_minute_contract_v1 import minute_market


def project_intraday(raw, query, source, *, columns=None, convention, volume_unit='unknown',
                     amount_unit='unknown', time_unit=None, observed_at=None):
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        raise CapabilityContractError('not_covered', '来源未返回请求的分钟行情')
    observed = observed_at or datetime.now(timezone.utc)
    zone = ZoneInfo(MARKET_TIMEZONES[minute_market(query.symbol)])
    frame = raw.rename(columns=columns or {})
    rows = []
    try:
        for item in frame.to_dict(orient='records'):
            value = item['timestamp']
            stamp = pd.Timestamp(value, unit=time_unit, tz='UTC') if time_unit else pd.Timestamp(value)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize(zone, ambiguous='raise', nonexistent='raise')
            stamp = stamp.tz_convert(zone)
            if not query.start <= stamp.date().isoformat() <= query.end:
                continue
            finish = stamp
            if convention == 'interval-start':
                finish += timedelta(minutes=int(query.timeframe[:-1]))
            numbers = {}
            for field in ('open', 'high', 'low', 'close', 'volume', 'amount'):
                number = item.get(field)
                if field in {'volume', 'amount'} and (number is None or pd.isna(number) or number == ''):
                    numbers[field] = None
                elif isinstance(number, bool):
                    raise ValueError('boolean numeric value')
                else:
                    numbers[field] = float(number)
            complete = None if convention == 'source-native' else finish <= observed
            rows.append({'timestamp': stamp.isoformat(), **numbers, 'complete': complete})
        result = MinuteResult.model_validate({
            'symbol': query.symbol, 'assetType': query.instrument_type, 'market': minute_market(query.symbol),
            'source': source, 'rows': sorted(rows, key=lambda row: row['timestamp']),
            'observedAt': observed.isoformat(), 'timezone': str(zone), 'timeframe': query.timeframe,
            'timestampConvention': convention, 'fieldUnits': {'volume': volume_unit, 'amount': amount_unit},
            'session': 'regular', 'responseSha256': sha256(raw.to_json(date_format='iso', orient='split').encode()).hexdigest(),
        })
        return result
    except (ValueError, TypeError, KeyError, OverflowError):
        raise CapabilityContractError('invalid_response', '来源分钟时间、价格或量额字段无效') from None


def intraday_bindings(reader, source, *, markets=('CN',), assets=('STOCK', 'ETF'), periods=('1m', '5m', '15m', '30m', '60m')):
    from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding
    from src.services.thesis_ledger_minute_contract_v1 import CONTRACT
    return tuple(QueryBinding(CONTRACT, ExactKey(market, asset, 'MINUTE_BAR', 'none', period), source,
        f'{source}-native-intraday-v1', f'{source}-native-intraday-v1', reader, admission_required=False)
        for market in markets for asset in assets for period in periods)


def cn_identity(query):
    code, exchange = query.symbol.split('.')
    if exchange not in {'SH', 'SZ'} or len(code) != 6 or not code.isdigit():
        raise CapabilityContractError('not_covered', '该分钟来源仅覆盖明确的沪深证券')
    return code, exchange
