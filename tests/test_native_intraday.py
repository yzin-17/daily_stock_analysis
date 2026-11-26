"""跨来源原生分钟的周期、时间边界、单位、精确身份及配置竞态。"""

from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import pytest
from data_provider.intraday_projection import project_intraday
from src.services.thesis_ledger_minute_contract_v1 import MinuteRequest, correlate, CONTRACT
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def query(period='60m', **changes):
    return MinuteRequest(symbol='510300.SH', instrument_type='ETF', start='2026-11-06',
                         end='2026-11-06', timeframe=period, **changes)


def raw(stamp='2026-11-06 10:30:00', **changes):
    return pd.DataFrame([{'timestamp': stamp, 'open': 4.124, 'high': 4.169, 'low': 4.095,
                         'close': 4.13, 'volume': 0, 'amount': None, **changes}])


@pytest.mark.parametrize('period', ['1m', '5m', '15m', '30m', '60m'])
def test_native_period_and_end_timestamp_are_preserved(period):
    request = query(period)
    value = project_intraday(raw(), request, 'eastmoney', convention='interval-end',
        observed_at=datetime(2026, 11, 6, 2, 30, tzinfo=timezone.utc), volume_unit='hand', amount_unit='CNY')
    result = correlate(request, value, {'upstreamSource': 'eastmoney'})
    assert result.rows[0].timestamp == '2026-11-06T10:30:00+08:00'
    assert result.rows[0].complete and result.rows[0].close == 4.13
    assert result.rows[0].volume == 0 and result.rows[0].amount is None
    assert result.timeframe == period and result.fieldUnits == {'volume': 'hand', 'amount': 'CNY'}


def test_hour_completion_uses_full_duration_for_start_timestamps():
    value = project_intraday(raw(), query(), 'source', convention='interval-start',
        observed_at=datetime(2026, 11, 6, 3, 29, tzinfo=timezone.utc))
    assert not value.rows[0].complete
    bad = value.model_copy(update={'rows': [value.rows[0].model_copy(update={'complete': True})]})
    with pytest.raises(ValueError):
        correlate(query(), bad, {'upstreamSource': 'source'})


@pytest.mark.parametrize('change', [{'high': 4.0}, {'open': True}, {'volume': -1}, {'close': float('nan')},
                                   {'timestamp': '2026-11-06 10:30:01'}])
def test_bad_source_fields_reject_whole_result(change):
    with pytest.raises(CapabilityContractError):
        project_intraday(raw(**change), query(), 'source', convention='interval-end')


def test_correlation_rejects_duplicate_period_identity_source_and_wrong_timezone():
    value = project_intraday(raw(), query(), 'source', convention='interval-end')
    for bad in [value.model_copy(update={'timeframe': '1m'}), value.model_copy(update={'source': 'fallback'}),
                value.model_copy(update={'rows': value.rows * 2}),
                value.model_copy(update={'rows': [value.rows[0].model_copy(update={'timestamp': '2026-11-06T10:30:00+00:00'})]})]:
        with pytest.raises(ValueError):
            correlate(query(), bad, {'upstreamSource': 'source'})


def test_public_minute_read_does_not_require_a_secret_but_rechecks_policy(monkeypatch):
    from src.services.thesis_ledger_minute_execution import execute_minute
    import src.services.thesis_ledger_minute_execution as module
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ProviderCallError
    request = query()
    key, target = request.route_key(), {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}
    policy = {'enabled': True, 'revision': 7, 'sourceDesiredRevision': 7,
              'routes': [{'key': key, 'targets': [{**target, 'eligible': True}]}]}
    store = SimpleNamespace(provider_credential_snapshot=lambda _: None, effective_policy_v3=lambda: policy)
    runtime = SimpleNamespace(store=store, _adapter=lambda *a, **kw: object())
    value = project_intraday(raw(), request, 'eastmoney', convention='interval-end', volume_unit='hand', amount_unit='CNY')
    monkeypatch.setattr(module.CAPABILITY_SOURCES, 'query', lambda *a: value)
    data = ThesisLedgerDataRequest('MINUTE_BAR', request.symbol, instrument_type='ETF', timeframe='60m',
        adjustment='none', start=request.start, end=request.end, parameters={'interactive_chart': True})
    result = execute_minute(runtime, data, key, policy, **{'provider_id': 'akshare', 'upstream_source': 'eastmoney'})
    assert result.attrs['timeframe'] == '60m' and result.attrs['timestampConvention'] == 'interval-end'
    store.effective_policy_v3 = lambda: {**policy, 'revision': 8}
    with pytest.raises(ProviderCallError) as error:
        execute_minute(runtime, data, key, policy, 'akshare', 'eastmoney')
    assert error.value.code == 'policy_not_applied'


@pytest.mark.parametrize('market,symbol,code,offset', [('CN', '510300.SH', 'SH.510300', '+08:00'),
                                                     ('US', 'SPY.US', 'US.SPY', '-04:00')])
def test_futu_native_hour_reaches_exact_sdk_enum(market, symbol, code, offset):
    from tests.test_futu_minute import sdk, row, request
    from data_provider.futu_minute import read_futu_minute
    value, ctx = sdk([(0, pd.DataFrame([row(code=code)]), None)])
    value.KLType.K_60M, value.Market.SH = 'K_60M', 'SH'
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{'code': code, 'stock_type': 'ETF'}])
    frame = read_futu_minute(value, 'localhost', 11111, request(symbol=symbol, instrumentType='ETF', timeframe='60m'))
    assert frame.iloc[0]['timestamp'].endswith(offset) and frame.attrs['timeframe'] == '60m'
    assert ctx.request_history_kline.call_args.kwargs['ktype'] == 'K_60M'
    ctx.close.assert_called_once()


def test_efinance_native_hour_pins_market_period_and_raw_adjustment(monkeypatch):
    import efinance
    import data_provider.efinance_fetcher as fetcher
    from data_provider.eastmoney_intraday import efinance_minute
    calls = []
    def read(method, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{'股票代码': '510300', '日期': '2026-11-06 10:30:00', '开盘': 4.1,
            '最高': 4.2, '最低': 4., '收盘': 4.124, '成交量': 1, '成交额': None}])
    monkeypatch.setattr(fetcher, '_ef_call_with_timeout', read)
    value = efinance_minute(query(), SimpleNamespace(timeout_seconds=4.5))
    assert value.rows[0].close == 4.124 and calls[0]['stock_codes'] == '1.510300'
    assert calls[0]['klt'] == 60 and calls[0]['fqt'] == 0
    monkeypatch.setattr(fetcher, '_ef_call_with_timeout', lambda *a, **kw: pd.DataFrame([{'股票代码': '600519'}]))
    with pytest.raises(CapabilityContractError):
        efinance_minute(query(), SimpleNamespace(timeout_seconds=4.5))


def test_unknown_source_time_convention_keeps_completion_unknown():
    from api.thesis_ledger_exact_bar_fields import minute_chart_point
    value = project_intraday(raw(), query(), 'tickflow', convention='source-native')
    assert value.rows[0].complete is None
    point = minute_chart_point(value.rows[0].model_dump(), 'CN', value.observedAt,
        lambda number, field: float(number), timeframe='60m', convention='source-native')
    assert point['completionStatus'] == 'unknown'


def test_tushare_minute_entitlements_are_explicit_and_skip_undeclared_reads():
    from data_provider.tushare_account_level import excluded_access, parse_account_level
    key = query().route_key()
    assert excluded_access(key, {'points': 120, 'independentApis': []}) == {
        'state': 'not_included', 'attempted': False, 'errorCode': 'independent_permission_not_enabled'}
    assert excluded_access(key, parse_account_level({'points': 120, 'independentApis': ['etf_mins']})) is None


def test_finnhub_early_close_filters_postmarket_without_network_calendar(monkeypatch):
    from data_provider.finnhub_intraday import read_minute
    import data_provider.multimarket_exact_daily as module
    zone = __import__('zoneinfo').ZoneInfo('America/New_York')
    stamps = [int(datetime(2026, 11, 27, hour, minute, tzinfo=zone).timestamp()) for hour, minute in [(12, 30), (13, 0), (15, 0)]]
    monkeypatch.setattr(module, '_http_json', lambda *a: {'s': 'ok', 't': stamps, 'o': [4]*3, 'h': [4]*3,
        'l': [4]*3, 'c': [4]*3, 'v': [0]*3})
    request = MinuteRequest(symbol='SPY.US', instrument_type='ETF', start='2026-11-27', end='2026-11-27', timeframe='30m')
    value = read_minute(request, SimpleNamespace(adapter=SimpleNamespace(_api_key='synthetic')))
    assert len(value.rows) == 1 and '12:30:00' in value.rows[0].timestamp


def test_tickflow_keeps_missing_amount_and_source_native_completion():
    from data_provider.tickflow_intraday import read_minute
    from unittest.mock import Mock
    client = Mock()
    client.klines.get.return_value = {'timestamp': [1793928600000], 'open': [4], 'high': [4], 'low': [4], 'close': [4], 'volume': [0]}
    adapter = SimpleNamespace(_get_client=lambda: client, _date_to_ms=lambda *a, **kw: 0)
    value = read_minute(query(), SimpleNamespace(adapter=adapter))
    assert value.rows[0].amount is None and value.rows[0].complete is None
    assert client.klines.get.call_args.kwargs['period'] == '60m'
    assert client.klines.get.call_args.kwargs['adjust'] == 'none'


def test_rqdata_exact_identity_and_unadjusted_hour():
    from data_provider.rqdata_intraday import _read
    from unittest.mock import Mock
    client = Mock()
    client.get_price.return_value = pd.DataFrame([{'order_book_id': '510300.XSHG', 'datetime': '2026-11-06 10:30',
        'open': 4, 'high': 4, 'low': 4, 'close': 4, 'volume': 0, 'total_turnover': None}]).set_index(['order_book_id', 'datetime'])
    value = _read(lambda: client, query().model_dump())
    assert value['timeframe'] == '60m' and value['rows'][0]['complete'] is None
    assert client.get_price.call_args.kwargs['frequency'] == '60m'
    assert client.get_price.call_args.kwargs['adjust_type'] == 'none'
    client.get_price.return_value = client.get_price.return_value.rename(index={'510300.XSHG': '600519.XSHG'})
    with pytest.raises(ValueError):
        _read(lambda: client, query().model_dump())


def test_pytdx_hour_uses_native_category_and_rejects_repeated_pages():
    from data_provider.pytdx_intraday import read_minute
    from unittest.mock import Mock
    from contextlib import nullcontext
    api = Mock()
    api.get_security_bars.return_value = [{'datetime': '2026-11-06 10:30', 'open': 4, 'high': 4, 'low': 4, 'close': 4, 'vol': 0, 'amount': None}]
    request = MinuteRequest(symbol='600519.SH', instrument_type='STOCK', start='2026-11-06', end='2026-11-06', timeframe='60m')
    context = SimpleNamespace(timeout_seconds=4.5, adapter=SimpleNamespace(_pytdx_session=lambda **kw: nullcontext(api)))
    value = read_minute(request, context)
    assert value.rows[0].volume == 0
    api.get_security_bars.assert_called_once_with(3, 1, '600519', 0, 800)
    api.get_security_bars.return_value *= 800
    with pytest.raises(CapabilityContractError, match='未向前推进'):
        read_minute(request, context)


def test_longbridge_hour_pins_session_and_rejects_truncated_window(monkeypatch):
    from data_provider.longbridge_intraday import read_minute
    from longbridge import openapi
    from longbridge.openapi import Period, AdjustType
    from unittest.mock import Mock, sentinel
    # CI 的旧版发行 wheel 不含此枚举。镜像构建单独验证实际 SDK 的正常时段接口。
    if not hasattr(openapi, "TradeSessions"):
        monkeypatch.setattr(openapi, "TradeSessions", SimpleNamespace(Intraday=sentinel.intraday), raising=False)
    client = Mock()
    client.history_candlesticks_by_date.return_value = [SimpleNamespace(timestamp=datetime(2026, 11, 6, 2, 30, tzinfo=timezone.utc),
        open=4, high=4, low=4, close=4, volume=0, turnover=None)]
    context = SimpleNamespace(adapter=SimpleNamespace(_get_ctx=lambda: client))
    value = read_minute(query(), context)
    assert value.rows[0].complete is None
    args = client.history_candlesticks_by_date.call_args.args
    assert args[:3] == ('510300.SH', Period.Min_60, AdjustType.NoAdjust)
    assert args[-1] == openapi.TradeSessions.Intraday
    client.history_candlesticks_by_date.return_value *= 1000
    with pytest.raises(CapabilityContractError, match='上限'):
        read_minute(query(), context)


def test_account_access_keeps_six_request_ceiling_with_minute_declarations(monkeypatch, tmp_path):
    from tests.test_tushare_account_access import envelope, response
    import src.services.thesis_ledger_control as module
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
    from src.services.thesis_ledger_provider_exact_checks import check_provider
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'account.db'))
    monkeypatch.setenv('THESIS_LEDGER_DSA_SECRET_KEY', 'synthetic')
    monkeypatch.setattr(module, '_environment_credential_values', lambda _: {})
    runtime = ThesisLedgerProviderRuntime(module.ThesisLedgerControlStore())
    runtime.store.save_provider_config('tushare', envelope(credentials={'method': 'token', 'values': {'token': 'synthetic'}}))
    calls = []
    monkeypatch.setattr('data_provider.tushare_fetcher.requests.post', lambda *a, **kw: calls.append(kw['json']) or response())
    result = check_provider(runtime, 'tushare', envelope(purpose='account_access', accountLevel={
        'points': 6000, 'independentApis': ['hk_daily', 'rt_k', 'stk_mins', 'etf_mins']}))
    assert len(calls) == 6
    assert any(r['access'].get('errorCode') == 'request_budget_exceeded' for r in result['accountAccess']['routes'])
