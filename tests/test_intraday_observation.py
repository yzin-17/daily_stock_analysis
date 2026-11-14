"""受控 K 线比较与跨批预算；不访问真实来源。"""

from datetime import datetime, timezone
from types import SimpleNamespace
import sqlite3

import pytest

from src.services.thesis_ledger_bar_observations import compare_observations, tail_points
from src.services.thesis_ledger_intraday_observation import candidates, read_entry, sample_batch
from src.services.thesis_ledger_probe_store import ReadOnlyProbeStore


NOW = datetime(2026, 11, 11, 2, 5, tzinfo=timezone.utc)


def entry(period='1d', source='eastmoney'):
    return {'state': 'ready', 'target': {'providerId': 'akshare', 'upstreamSource': source},
            'key': {'kind': 'bar', 'market': 'CN', 'assetType': 'STOCK', 'timeframe': period,
                    'capability': 'DAILY_BAR' if period == '1d' else 'MINUTE_BAR', 'adjustment': 'none'}}


def points(close=10):
    return tail_points([{'timestamp': '2026-11-11T10:00:00+08:00', 'open': 10, 'high': 12,
                         'low': 9, 'close': close, 'volume': 100, 'amount': None, 'complete': False}])


def observation(close=10):
    return {**entry()['target'], 'key': entry()['key'], 'symbol': '600519.SH', 'status': 'success',
            'observedAt': NOW.isoformat(), 'points': points(close)}


def test_timestamp_or_adapter_confirmation_is_not_a_price_change():
    old, new = observation(), observation()
    new['observedAt'] = '2026-11-11T02:15:00+00:00'
    new['points'][0]['adapterComplete'] = True
    assert compare_observations([old], new)['result'] == 'unchanged_inconclusive'
    assert new['points'][0]['sourceConfirmation'] is None


def test_only_same_route_and_bar_are_comparable():
    old, new = observation(), observation(11)
    assert compare_observations([old], new)['result'] == 'same_bar_changed'
    new['upstreamSource'] = 'sina'
    assert compare_observations([old], new)['result'] == 'insufficient_observations'


def test_four_batches_are_balanced_and_bounded():
    catalog = {'integrity': 'complete', 'entries': [entry(p, s) for p in ('1d', '60m', '1m')
                                                  for s in ('eastmoney', 'sina')]}
    runtime = SimpleNamespace(market_route_catalog_v3=lambda: catalog)
    calls, report = [], {}

    def reader(*args):
        calls.append(args[1])
        return points()

    for _ in range(4):
        report = sample_batch(runtime, ['CN'], report, now=NOW, reader=reader)
    assert len(calls) == 12
    assert {v['key']['timeframe'] for v in calls} == {'1d', '60m', '1m'}
    with pytest.raises(ValueError, match='四批'):
        sample_batch(runtime, ['CN'], report, now=NOW, reader=reader)
    assert len(calls) == 12


def test_upstream_restriction_stops_remaining_reads_and_later_batches():
    catalog = {'integrity': 'complete', 'entries': [entry(p) for p in ('1d', '60m', '1m')]}
    runtime = SimpleNamespace(market_route_catalog_v3=lambda: catalog)
    calls = []

    def reader(*args):
        calls.append(1)
        raise SimpleProbeError('rate_limited', 'secret upstream text')

    report = sample_batch(runtime, ['CN'], {}, now=NOW, reader=reader)
    report = sample_batch(runtime, ['CN'], report, now=NOW, reader=reader)
    assert len(calls) == 1
    assert 'secret' not in str(report)


class SimpleProbeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def test_probe_store_rejects_any_write(tmp_path):
    path = tmp_path / 'control.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE sample(value TEXT)')
    with ReadOnlyProbeStore(path)._connect() as connection:
        assert connection.execute('SELECT count(*) FROM sample').fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO sample VALUES ('changed')")


def test_persisted_access_and_actual_request_times_are_retained():
    value = {**entry(), 'accountAccess': {'state': 'allowed', 'attempted': True,
             'persisted': True, 'checkedAt': NOW.isoformat(), 'configVersion': 1}}
    catalog = {'integrity': 'complete', 'entries': [value]}
    assert list(candidates(catalog, ['CN'], NOW)) == [value]
    runtime = SimpleNamespace(market_route_catalog_v3=lambda: catalog)
    report = sample_batch(runtime, ['CN'], {}, now=NOW, clock=lambda: NOW, reader=lambda *args: points())
    assert report['observations'][0]['observedAt'] == NOW.isoformat()
    assert report['observations'][0]['completedAt'] == NOW.isoformat()


def test_daily_reader_uses_real_query_contract_and_closes_adapter(monkeypatch):
    import pandas as pd
    from src.services import thesis_ledger_intraday_observation as probe
    closed, requests = [], []
    catalog = {'integrity': 'complete', 'entries': [entry()]}
    adapter = SimpleNamespace(close=lambda: closed.append(True))
    runtime = SimpleNamespace(store=SimpleNamespace(provider_credential_snapshot=lambda _: 'same', database_path='unused'),
                              market_route_catalog_v3=lambda: catalog, _adapter=lambda *args, **kwargs: adapter)
    def daily(query, key, target, context):
        requests.append(query)
        return SimpleNamespace(frame=pd.DataFrame([{'date': '2026-11-11', 'open': 10, 'high': 11,
                                                    'low': 9, 'close': 10, 'volume': 100}]))
    monkeypatch.setattr(probe.CAPABILITY_SOURCES, 'daily', daily)
    assert read_entry(runtime, entry(), '600519.SH', NOW)[0]['close'] == '10'
    assert requests[0].request_id == 'intraday-observation'
    assert requests[0].start == '2026-11-11'
    assert closed == [True]
