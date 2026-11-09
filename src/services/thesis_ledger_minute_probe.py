"""原生分钟诊断复用生产合同，单证券、上一工作日且不写业务路由。"""

from datetime import date, timedelta
from .thesis_ledger_capability_contracts import ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_minute_contract_v1 import CONTRACT, MinuteRequest


def read_minute_probe(runtime, key, target, snapshot, symbol, end):
    day = date.fromisoformat(end)
    while day.weekday() > 4:
        day -= timedelta(days=1)
    query = MinuteRequest(symbol=symbol, instrument_type=key['assetType'], timeframe=key['timeframe'],
                          start=day.isoformat(), end=day.isoformat())
    adapter = runtime._adapter(target['providerId'], snapshot=snapshot, cache=False, probe=True, market_v3_route_key=key)
    CAPABILITY_SOURCES.query(CONTRACT, query, key, target, ReadContext(adapter, snapshot, 4.5, probe=True))
    return {'symbol': symbol, 'start': query.start, 'end': query.end}
