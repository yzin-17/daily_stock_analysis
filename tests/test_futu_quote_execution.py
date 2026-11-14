"""当前执行资料穿过隔离 SDK、标准结果及 HTTP 投影；未知事实不补造。"""

from datetime import datetime, timezone
import pandas as pd
import pytest

from data_provider.futu_market import read_futu_quote
from data_provider.thesis_ledger_sources.futu import quote
from src.services.thesis_ledger_capability_contracts import QuoteQuery, ReadContext
from src.services.thesis_ledger_quote_result_contract import QuoteResult
from api.thesis_ledger_quote_fields import quote_response_payload
from tests.test_futu_market import sdk_for


@pytest.mark.parametrize('asset,symbol,code', [('STOCK', 'AAPL.US', 'US.AAPL'), ('ETF', 'SPY.US', 'US.SPY')])
def test_current_snapshot_reaches_public_result(asset, symbol, code):
    sdk, ctx = sdk_for(code, asset, suspension=False, sec_status='NORMAL', lot_size=1, price_spread=0.25)
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{
        'code': code, 'stock_type': asset, 'stock_id': 123, 'exchange_type': 'US_NASDAQ', 'delisting': False}])
    raw = read_futu_quote(sdk, 'localhost', 11111, symbol, asset)

    class Adapter:
        def get_realtime_quote(self, *args, **kwargs):
            return raw

    result = quote(QuoteQuery(symbol=symbol, instrument_type=asset), ReadContext(Adapter()))
    assert isinstance(result, QuoteResult)
    assert result.units.priceCurrency == 'USD'
    facts = result.execution_facts
    assert facts.tradingState == 'normal' and facts.sharesPerLot == '1' and facts.securityId == '123'
    assert facts.marketTime == result.provider_timestamp and facts.assetType == asset
    assert 'tick' not in facts.model_dump() and 'price_spread' not in facts.model_dump()
    payload = quote_response_payload(
        result, symbol=symbol, provider='futu', fallback_used=False,
        source='futu', fetched_at=datetime.now(timezone.utc).isoformat(), number=lambda v, _: float(v), timestamp=str)
    assert payload['executionFacts'] == facts.model_dump()


@pytest.mark.parametrize('suspension,status,delisted,expected', [
    (False, 'NORMAL', False, 'normal'), (True, 'NORMAL', False, 'suspended'),
    (False, 'SUSPENDED', False, 'suspended'), (False, 'RECOVERABLE_CIRCUIT_BREAKER', False, 'suspended'),
    (False, 'NONE', False, 'unknown'), (None, 'NORMAL', False, 'unknown'),
    (False, 'NORMAL', None, 'unknown'), (False, 'NORMAL', True, 'unknown'),
])
def test_unknown_and_blocked_are_never_promoted(suspension, status, delisted, expected):
    sdk, ctx = sdk_for('US.AAPL', suspension=suspension, sec_status=status, lot_size=1)
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{
        'code': 'US.AAPL', 'stock_type': 'STOCK', 'delisting': delisted}])
    raw = read_futu_quote(sdk, 'localhost', 11111, 'AAPL.US', 'STOCK')
    assert raw['execution_facts']['tradingState'] == expected


def test_missing_or_fractional_lot_and_unknown_exchange_remain_unknown():
    sdk, _ = sdk_for('US.AAPL', lot_size=1.5, suspension=False, sec_status='NORMAL')
    facts = read_futu_quote(sdk, 'localhost', 11111, 'AAPL.US', 'STOCK')['execution_facts']
    assert facts['sharesPerLot'] is None and facts['exchange'] is None
