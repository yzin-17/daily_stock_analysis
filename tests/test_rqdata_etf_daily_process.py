"""真实 spawn 验证 RQData ETF 日线的总期限与安全失败。"""

import multiprocessing
import os
from functools import partial
from pathlib import Path
import signal
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.rqdata_etf_daily_process import read_rqdata_etf_daily_isolated
from data_provider.rqdata_isolated_process import (
    IsolatedReadError, MAX_RESULT_BYTES, read_isolated_json,
)


SYMBOL = '159516.SZ'
SOURCE_ID = '159516.XSHE'
START = '2026-07-09'
END = '2026-07-10'


def price_rows(**request):
    assert request['order_book_ids'] == SOURCE_ID
    assert request['frequency'] == '1d'
    assert request['adjust_type'] == 'none'
    assert request['start_date'] == START and request['end_date'] == END
    index = pd.MultiIndex.from_tuples(
        [(SOURCE_ID, pd.Timestamp(START)), (SOURCE_ID, pd.Timestamp(END))],
        names=['order_book_id', 'date'],
    )
    return pd.DataFrame(
        [(1, 1.1, .9, 1.05, 100, 105), (.51, .55, .48, .52, 200, 104)],
        index=index, columns=request['fields'],
    )


def working_factory():
    return SimpleNamespace(get_price=price_rows)


def blocked_factory():
    time.sleep(30)


def failed_factory():
    raise RuntimeError('synthetic-sensitive-detail')


def crashed_factory():
    os._exit(7)


def blocked_rows(marker, **request):
    Path(marker).write_text('entered')
    time.sleep(30)


def blocked_read_factory(marker):
    return SimpleNamespace(get_price=partial(blocked_rows, marker))


def uncooperative_rows(marker, **request):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(marker).write_text('entered')
    time.sleep(30)


def uncooperative_factory(marker):
    return SimpleNamespace(get_price=partial(uncooperative_rows, marker))


def malformed_rows(**request):
    return pd.DataFrame({'open': [1]})


def malformed_factory():
    return SimpleNamespace(get_price=malformed_rows)


def oversized_result(factory):
    return 'x' * (MAX_RESULT_BYTES + 1)


def read(factory, **changes):
    options = {'timeout_seconds': 10, 'maximum_rows': 366}
    options.update(changes)
    return read_rqdata_etf_daily_isolated(
        factory, SYMBOL, SOURCE_ID, START, END, **options,
    )


def test_spawn_returns_validated_rows_without_claiming_source_coverage():
    result = read(working_factory)
    assert [row['date'] for row in result['rows']] == [START, END]
    assert result['validation']['nativeVolumeUnit'] == 'unknown'
    assert result['validation']['historicalCoverageComplete'] is False
    assert result['request']['adjust_type'] == 'none'
    assert 'requestCount' not in result


def test_blocked_initialization_is_terminated():
    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(TimeoutError, match='^rqdata_price_read_timeout$'):
        read(blocked_factory, timeout_seconds=.3)
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_blocked_sdk_call_is_terminated(tmp_path):
    marker = tmp_path / 'entered'
    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(TimeoutError, match='^rqdata_price_read_timeout$'):
        read(partial(blocked_read_factory, str(marker)), timeout_seconds=3)
    assert marker.read_text() == 'entered'
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_sdk_ignoring_terminate_is_killed(tmp_path):
    marker = tmp_path / 'entered'
    before = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(TimeoutError, match='^rqdata_price_read_timeout$'):
        read(partial(uncooperative_factory, str(marker)), timeout_seconds=3)
    assert marker.read_text() == 'entered'
    assert {p.pid for p in multiprocessing.active_children()} == before


@pytest.mark.parametrize('factory', [failed_factory, crashed_factory, malformed_factory])
def test_sdk_failures_and_bad_frame_are_sanitized(factory):
    with pytest.raises(RuntimeError, match='^rqdata_price_read_failed$'):
        read(factory)


def test_unpicklable_factory_is_safe_start_failure():
    with pytest.raises(RuntimeError, match='^rqdata_price_process_start_failed$'):
        read(lambda: None)


@pytest.mark.parametrize('timeout', [True, 0, -1, float('inf'), float('nan'), 61])
def test_invalid_timeout_rejected_before_spawn(timeout):
    with pytest.raises(ValueError, match='invalid timeout'):
        read(working_factory, timeout_seconds=timeout)


def test_invalid_scope_rejected_before_spawn():
    with pytest.raises(ValueError, match='identity_invalid'):
        read_rqdata_etf_daily_isolated(
            blocked_factory, SYMBOL, '159516.XSHG', START, END, timeout_seconds=10,
        )


def test_child_result_budget_fails_closed():
    with pytest.raises(IsolatedReadError):
        read_isolated_json(
            working_factory, oversized_result, (), timeout_seconds=10,
            temp_prefix='rqdata-budget-test-',
        )
