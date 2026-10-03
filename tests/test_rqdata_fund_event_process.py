"""通过真实 spawn 验证阻塞终止、成功读取和失败信息隔离。"""

import multiprocessing
import os
from functools import partial
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from data_provider.rqdata_fund_event_process import read_rqdata_fund_event_isolated


def split_rows(*args, **kwargs):
    import pandas as pd
    return pd.DataFrame({'split_ratio': ['1.25']}, index=pd.to_datetime(['2025-06-01']))


def working_factory():
    return SimpleNamespace(fund=SimpleNamespace(get_split=split_rows))


def blocked_factory():
    time.sleep(30)


def failed_factory():
    raise RuntimeError('synthetic-sensitive-detail')


def crashed_factory():
    os._exit(7)


def blocked_rows(marker, *args, **kwargs):
    Path(marker).write_text('entered')
    time.sleep(30)


def blocked_read_factory(marker):
    return SimpleNamespace(fund=SimpleNamespace(get_split=partial(blocked_rows, marker)))


def dividend_rows(*args, **kwargs):
    import pandas as pd
    return pd.DataFrame({'book_closure_date': ['2025-06-01'], 'payable_date': ['2025-06-03'],
                         'dividend_before_tax': ['0.012']}, index=pd.to_datetime(['2025-06-02']))


def dividend_factory():
    return SimpleNamespace(fund=SimpleNamespace(get_dividend=dividend_rows))


def read(factory, **changes):
    options = dict(timeout_seconds=10, query_fund_code='000246', instrument_type='NAV_FUND',
                   start='2025-01-01', end='2025-12-31')
    options.update(changes)
    return read_rqdata_fund_event_isolated(factory, 'split', '000246.OF', **options)


def test_spawn_preserves_normalized_facts_and_incomplete_coverage():
    result = read(working_factory)
    assert len(result['facts']) == 1
    assert result['coverage']['complete'] is False
    assert result['retrieval']['requestCount'] == 1


def test_blocked_initialization_is_terminated_without_live_child():
    before = {p.pid for p in multiprocessing.active_children()}
    started = time.monotonic()
    with pytest.raises(TimeoutError, match='rqdata_read_timeout'):
        read(blocked_factory, timeout_seconds=.3)
    assert time.monotonic() - started < 3
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_sdk_failure_does_not_expose_exception_details():
    with pytest.raises(RuntimeError, match='^rqdata_read_failed$'):
        read(failed_factory)


def test_sdk_read_itself_is_terminated(tmp_path):
    marker = tmp_path / 'entered'
    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(TimeoutError, match='rqdata_read_timeout'):
        read(partial(blocked_read_factory, str(marker)), timeout_seconds=3)
    assert marker.read_text() == 'entered'
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_child_crash_is_safe_failure():
    with pytest.raises(RuntimeError, match='^rqdata_read_failed$'):
        read(crashed_factory)


def test_unserializable_factory_is_safe_start_failure():
    with pytest.raises(RuntimeError, match='^rqdata_process_start_failed$'):
        read(lambda: None)


def test_dividend_uses_same_isolation_and_preserves_cash():
    result = read_rqdata_fund_event_isolated(
        dividend_factory, 'dividend', '050116.OF', timeout_seconds=10, query_fund_code='050116',
        instrument_type='NAV_FUND', currency='CNY', start='2025-01-01', end='2025-12-31',
    )
    assert result['facts'][0]['cashAmount'] == '0.012'
    assert result['coverage']['complete'] is False
    assert result['retrieval']['endpoint'] == 'fund.get_dividend'


@pytest.mark.parametrize('timeout', [True, 0, -1, float('inf'), float('nan'), 61])
def test_invalid_deadline_rejected_before_spawn(timeout):
    with pytest.raises(ValueError, match='invalid timeout'):
        read(blocked_factory, timeout_seconds=timeout)
