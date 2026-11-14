"""精确窗口诊断复用生产校验，真实控制库始终只读。"""

from pathlib import Path
from types import SimpleNamespace
import json

import pandas as pd
import pytest

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from scripts.probe_backtest_daily_sources import control_fingerprint, isolated_runtime, probe_window
from src.services.thesis_ledger_control import ThesisLedgerControlStore


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('THESIS_LEDGER_DSA_SECRET_KEY', 'synthetic-master-key')
    path = tmp_path / 'source.db'
    ThesisLedgerControlStore(str(path))
    return path


def read_window(runtime, **values):
    defaults = dict(symbol='510300.SH', asset='ETF', adjustment='qfq',
                    start='2026-11-03', end='2026-11-10', provider='efinance', source='eastmoney')
    return probe_window(runtime, **{**defaults, **values})


def fake_read(monkeypatch, *, missing=False, wrong_source=False, invalid_price=False):
    calls = []
    def read(query, timeout_seconds):
        calls.append(query)
        days = ['2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-09', '2026-11-10']
        frame = pd.DataFrame([{'date': day, 'open': 1, 'high': 2, 'low': .5,
                               'close': 3 if invalid_price else 1.5, 'volume': 100, 'amount': 150}
                              for day in (days[1:] if missing else days)])
        return normalize_exact_daily(frame, start=query.start, end=query.end,
                                     source='sina' if wrong_source else 'eastmoney')
    monkeypatch.setattr('data_provider.thesis_ledger_sources.efinance.read_frame', read)
    return calls


def test_copy_is_cleaned_and_original_control_state_does_not_change(database, monkeypatch):
    original = ThesisLedgerControlStore(str(database))
    with original._connect() as connection:
        before = control_fingerprint(connection)
    with isolated_runtime(database) as runtime:
        copied = Path(runtime.store.database_path)
        assert copied != database and copied.stat().st_mode & 0o777 == 0o600
        assert copied.parent.stat().st_mode & 0o777 == 0o700
        with runtime.store._connect() as connection:
            connection.execute('DELETE FROM thesis_ledger_policy_state')
        assert copied.exists()
    assert not copied.exists()
    with original._connect() as connection:
        assert control_fingerprint(connection) == before


def test_full_window_uses_production_source_and_response_validation(database, monkeypatch):
    calls = fake_read(monkeypatch)
    with isolated_runtime(database) as runtime:
        monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: SimpleNamespace())
        result = read_window(runtime)
    assert result['status'] == 'validated', result
    assert result['rows'] == result['expectedSessions'] == 6
    assert len(calls) == 1 and calls[0].adjustment == 'qfq'
    assert result['listing']['firstTradingDate'] == '2012-05-28'
    assert len(result['inputFingerprint']) == 64


def test_native_price_probe_preserves_strict_known_listing(database, monkeypatch):
    calls = fake_read(monkeypatch)
    with isolated_runtime(database) as runtime:
        monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: SimpleNamespace())
        result = read_window(runtime, adjustment='none')
    assert result['status'] == 'validated', result
    assert result['rows'] == result['expectedSessions'] == 6
    assert result['assumedUntradableDates'] == []
    assert 'historical_tradability' not in calls[0].parameters


def test_native_price_probe_does_not_gain_unknown_listing_research_scope(database, monkeypatch):
    calls = fake_read(monkeypatch)
    with isolated_runtime(database) as runtime:
        result = read_window(runtime, symbol='510050.SH', adjustment='none')
    assert result['status'] == 'unavailable'
    assert result['errorCode'] == 'listing_fact_missing'
    assert result['attempted'] is False and calls == []


@pytest.mark.parametrize('symbol', ['00700.HK', 'AAPL.US'])
def test_explicit_raw_reference_uses_production_coverage_and_response(database, monkeypatch, symbol):
    calls = []

    def read(query):
        calls.append(query)
        frame = pd.DataFrame([{'date': day, 'open': 300, 'high': 310, 'low': 290,
                               'close': 305, 'volume': 100, 'amount': 30500}
                              for day in ['2026-11-03', '2026-11-04', '2026-11-05',
                                          '2026-11-06', '2026-11-09', '2026-11-10']])
        return normalize_exact_daily(frame, start=query.start, end=query.end, source='futu')

    with isolated_runtime(database) as runtime:
        runtime.store.save_provider_config('futu', {
            'requestId': 'configure-in-copy', 'enabled': True,
            'credentials': {'method': 'opend', 'values': {'host': 'localhost', 'port': '11111'}},
        })
        monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: SimpleNamespace(get_exact_daily=read))
        result = read_window(runtime, symbol=symbol, asset='STOCK', adjustment='none',
                             provider='futu', source='futu', price_research_reference=True)
    assert result['status'] == 'validated', json.dumps(result)
    assert result['rows'] == result['expectedSessions'] == 6
    assert result['listing'] == {'symbol': symbol, 'status': 'unknown'}
    assert result['assumedUntradableDates'] == []
    assert len(calls) == 1 and calls[0].adjustment == 'none'


@pytest.mark.parametrize('change', ['wrong_source', 'invalid_price'])
def test_invalid_source_or_prices_cannot_be_certified(database, monkeypatch, change):
    calls = fake_read(monkeypatch, **{change: True})
    with isolated_runtime(database) as runtime:
        monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: SimpleNamespace())
        result = read_window(runtime)
    assert len(calls) == 1
    assert result['status'] == 'unavailable' and result['errorCode'] == 'invalid_response', result


def test_research_missing_session_remains_explicit_assumption(database, monkeypatch):
    calls = fake_read(monkeypatch, missing=True)
    with isolated_runtime(database) as runtime:
        monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: SimpleNamespace())
        result = read_window(runtime)
    assert len(calls) == 1
    assert result['status'] == 'validated', result
    assert result['rows'] == 5 and result['expectedSessions'] == 6
    assert result['assumedUntradableDates'] == ['2026-11-03']


def test_missing_binding_stops_before_any_source_read(database, monkeypatch):
    monkeypatch.setattr('scripts.probe_backtest_daily_sources.execute_market_window_v3',
                        lambda *args: pytest.fail('不应读取上游'))
    with isolated_runtime(database) as runtime:
        unbound = probe_window(runtime, symbol='510300.SH', asset='ETF', adjustment='hfq',
                               start='2026-11-03', end='2026-11-10', provider='hithink', source='fund-market-historical')
    assert unbound['status'] == 'unavailable' and not unbound['attempted']


@pytest.mark.parametrize('symbol,market', [('AAPL.US', 'US'), ('00700.HK', 'HK')])
def test_overseas_probe_reaches_coverage_with_matching_market(database, monkeypatch, symbol, market):
    from scripts import probe_backtest_daily_sources as probe
    requests = []

    def no_coverage(request):
        requests.append(request)
        return None

    monkeypatch.setattr(probe, '_market_data_v3_coverage_context', no_coverage)
    with isolated_runtime(database) as runtime:
        runtime.store.save_provider_config('futu', {
            'requestId': 'configure-in-copy', 'enabled': True,
            'credentials': {'method': 'opend', 'values': {'host': 'localhost', 'port': '11111'}},
        })
        result = read_window(runtime, symbol=symbol, asset='STOCK', adjustment='hfq',
                             provider='futu', source='futu')
    assert result['stage'] == 'coverage', result
    assert not result['attempted']
    assert len(requests) == 1 and requests[0]['routeKey']['market'] == market
    assert requests[0]['symbol'] == symbol


def test_new_stock_research_binding_preserves_unknown_listing(database, monkeypatch):
    calls = fake_read(monkeypatch)
    with isolated_runtime(database) as runtime:
        monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: SimpleNamespace())
        result = read_window(runtime, symbol='600519.SH', asset='STOCK')
    assert len(calls) == 1 and result['status'] == 'validated', result
    assert result['listing'] == {'symbol': '600519.SH', 'status': 'unknown'}
    assert result['assumedUntradableDates'] == []


def test_failure_diagnostics_never_include_provider_exception_text(database, monkeypatch):
    def fail(*args):
        try:
            raise ValueError('synthetic-sensitive-token')
        except ValueError:
            raise ExactDailyError('invalid_response', 'synthetic-sensitive-token') from None
    monkeypatch.setattr('data_provider.thesis_ledger_sources.efinance.read_frame', fail)
    with isolated_runtime(database) as runtime:
        monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: SimpleNamespace())
        result = read_window(runtime)
    assert result['status'] == 'unavailable' and result['errorCode'] == 'invalid_response'
    assert 'synthetic-sensitive-token' not in json.dumps(result)
    assert any(item['type'] == 'ExactDailyError' for item in result['failureChain'])


def test_disabled_provider_never_reads_upstream(database, monkeypatch):
    calls = fake_read(monkeypatch)
    with isolated_runtime(database) as runtime:
        runtime.store.save_provider_config('efinance', {'requestId': 'disable-in-copy', 'enabled': False})
        result = read_window(runtime)
    assert result['errorCode'] == 'provider_disabled' and not result['attempted']
    assert calls == []


def test_yahoo_tail_repair_passes_real_production_pagination_and_coverage(database, monkeypatch):
    valid = pd.DataFrame({'Open': [10., 11.], 'High': [12., 13.], 'Low': [9., 10.],
                          'Close': [11., 12.], 'Adj Close': [11., 12.], 'Volume': [100., 200.]},
                         index=pd.DatetimeIndex(['2026-11-09', '2026-11-10'], name='Date'))
    incomplete = valid.copy()
    incomplete.loc[incomplete.index[-1], ['Close', 'Adj Close']] = float('nan')
    responses, calls = iter([incomplete, valid.iloc[-1:]]), []
    def download(**options):
        calls.append(options)
        return next(responses)
    monkeypatch.setattr('yfinance.download', download)
    with isolated_runtime(database) as runtime:
        monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: SimpleNamespace())
        result = read_window(runtime, provider='yfinance', source='yfinance', symbol='600519.SH', asset='STOCK',
                             start='2026-11-09', end='2026-11-10')
    assert result['status'] == 'validated', result
    assert len(calls) == result['pagesFetched'] == 2
    assert result['rows'] == result['expectedSessions'] == 2
    assert result['sourceRetrieval']['repairedDates'] == ['2026-11-10']
    assert result['assumedUntradableDates'] == []


@pytest.mark.parametrize('targets,start', [(['efinance/eastmoney'] * 2, '2026-09-24'),
    ([f'efinance/source-{i}' for i in range(5)], '2026-09-24'),
    (['efinance/eastmoney'], '2025-01-01')])
def test_cli_budget_rejects_invalid_inputs_before_opening_control(monkeypatch, targets, start):
    from scripts import probe_backtest_daily_sources as probe
    argv = ['probe', '--symbol', '510300.SH', '--asset', 'ETF', '--start', start, '--end', '2026-11-10']
    for target in targets:
        argv.extend(['--target', target])
    monkeypatch.setattr('sys.argv', argv)
    monkeypatch.setattr(probe, 'isolated_runtime', lambda *args: pytest.fail('无效输入不得打开控制库'))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2


@pytest.mark.parametrize('start,limit', [('2025-10-18', '400'), ('2025-07-06', '550')])
def test_long_window_is_explicit_and_keeps_original_request_budget(monkeypatch, database, start, limit):
    from scripts import probe_backtest_daily_sources as probe
    calls = []

    def observe(runtime, **request):
        calls.append(request)
        return {'status': 'unavailable', 'attempted': False}

    monkeypatch.setattr(probe, 'probe_window', observe)
    monkeypatch.setattr('sys.argv', ['probe', '--database', str(database),
        '--symbol', 'AAPL.US', '--asset', 'STOCK', '--adjustment', 'hfq',
        '--start', start, '--end', '2026-11-11',
        '--target', 'futu/futu', '--max-window-days', limit])
    assert probe.main() == 0
    assert len(calls) == 1
    assert calls[0]['start'] == start and calls[0]['end'] == '2026-11-11'


def test_raw_reference_flag_rejects_adjusted_prices_before_opening_control(monkeypatch):
    from scripts import probe_backtest_daily_sources as probe
    monkeypatch.setattr('sys.argv', ['probe', '--symbol', '00700.HK', '--asset', 'STOCK',
        '--adjustment', 'hfq', '--price-research-reference', '--start', '2026-11-03',
        '--end', '2026-11-10', '--target', 'futu/futu'])
    monkeypatch.setattr(probe, 'isolated_runtime', lambda *args: pytest.fail('无效输入不得打开控制库'))
    with pytest.raises(SystemExit) as error:
        probe.main()
    assert error.value.code == 2


def test_raw_reference_cli_forwards_explicit_mode(monkeypatch, database):
    from scripts import probe_backtest_daily_sources as probe
    calls = []
    monkeypatch.setattr(probe, 'probe_window', lambda runtime, **request:
                        calls.append(request) or {'status': 'unavailable', 'attempted': False})
    monkeypatch.setattr('sys.argv', ['probe', '--database', str(database), '--symbol', '00700.HK',
        '--asset', 'STOCK', '--adjustment', 'none', '--price-research-reference',
        '--start', '2025-07-06', '--end', '2026-11-11', '--max-window-days', '550',
        '--target', 'futu/futu'])
    assert probe.main() == 0
    assert len(calls) == 1 and calls[0]['price_research_reference'] is True
    assert calls[0]['adjustment'] == 'none'
