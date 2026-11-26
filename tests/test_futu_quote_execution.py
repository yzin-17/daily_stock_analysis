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


def test_hk_snapshot_reuses_registered_currency_and_returns_current_facts(monkeypatch):
    sdk, ctx = sdk_for('HK.00700', update_time='2026-11-23 09:31:35',
                      suspension=False, sec_status='NORMAL', lot_size=100)
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{
        'code': 'HK.00700', 'stock_type': 'STOCK', 'stock_id': 123,
        'exchange_type': 'HK_MAINBOARD', 'delisting': False}])
    raw = read_futu_quote(sdk, 'localhost', 11111, '00700.HK', 'STOCK')
    assert raw['units']['priceCurrency'] == 'HKD'
    assert raw['execution_facts']['market'] == 'HK'
    assert raw['execution_facts']['symbol'] == '00700.HK'
    assert raw['execution_facts']['tradingState'] == 'normal'
    assert raw['execution_facts']['sharesPerLot'] == '100'

    class Adapter:
        def get_realtime_quote(self, *args, **kwargs):
            return raw

    def no_us_rules(*args, **kwargs):
        pytest.fail('港股当前观察不能启动美国规则核验')
    monkeypatch.setattr('src.services.us_live_execution_rules.current_us_cash_rules', no_us_rules)
    result = quote(QuoteQuery(symbol='00700.HK', instrument_type='STOCK'), ReadContext(Adapter()))
    assert result.units.priceCurrency == 'HKD'
    assert result.execution_facts.market == 'HK'
    assert result.execution_rules is None
    ctx.get_stock_basicinfo.assert_called_once()
    ctx.get_market_snapshot.assert_called_once_with(['HK.00700'])


@pytest.mark.parametrize('symbol,asset,exchange', [
    ('00147.HK', 'STOCK', 'HK_MAINBOARD'), ('02801.HK', 'ETF', 'HK_MAINBOARD'),
    ('02800.HK', 'STOCK', 'HK_MAINBOARD'),
    ('00700.HK', 'STOCK', 'US_NASDAQ'),
])
def test_hk_currency_requires_its_registered_symbol_asset_and_exchange(symbol, asset, exchange):
    code = 'HK.' + symbol.split('.')[0]
    sdk, ctx = sdk_for(code, asset, suspension=False, sec_status='NORMAL', lot_size=100)
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{
        'code': code, 'stock_type': asset, 'exchange_type': exchange, 'delisting': False}])
    raw = read_futu_quote(sdk, 'localhost', 11111, symbol, asset)
    assert raw.get('units') is None
    assert raw['execution_facts']['market'] == 'HK'


def test_hk_unknown_observation_remains_unknown_and_keeps_price():
    sdk, _ = sdk_for('HK.00700', suspension=None, sec_status='NONE', lot_size=None)
    raw = read_futu_quote(sdk, 'localhost', 11111, '00700.HK', 'STOCK')
    assert raw['price'] == 12
    assert raw['execution_facts']['tradingState'] == 'unknown'
    assert raw['execution_facts']['sharesPerLot'] is None


@pytest.mark.parametrize('symbol,market,valid', [
    ('00700.HK', 'HK', True), ('AAPL.US', 'US', True),
    ('00700.HK', 'US', False), ('AAPL.US', 'HK', False),
])
def test_current_fact_market_and_symbol_are_correlated(symbol, market, valid):
    from pydantic import ValidationError
    from src.services.thesis_ledger_quote_execution_facts import QuoteExecutionFacts
    sdk, _ = sdk_for('US.AAPL', lot_size=1)
    facts = read_futu_quote(sdk, 'localhost', 11111, 'AAPL.US', 'STOCK')['execution_facts']
    facts.update(symbol=symbol, market=market)
    if valid:
        assert QuoteExecutionFacts.model_validate(facts).symbol == symbol
    else:
        with pytest.raises(ValidationError):
            QuoteExecutionFacts.model_validate(facts)


def test_hk_currency_observation_must_be_visible_at_the_quote_fetch_time(monkeypatch):
    from data_provider import futu_market
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat('2026-10-16T09:45:00+00:00').astimezone(tz)
    monkeypatch.setattr(futu_market, 'datetime', Clock)
    sdk, ctx = sdk_for('HK.00700')
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{
        'code': 'HK.00700', 'stock_type': 'STOCK', 'exchange_type': 'HK_MAINBOARD'}])
    raw = read_futu_quote(sdk, 'localhost', 11111, '00700.HK', 'STOCK')
    assert raw.get('units') is None
    assert raw['execution_facts']['tradingState'] == 'unknown'
