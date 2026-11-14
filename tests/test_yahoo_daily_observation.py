"""同次 Yahoo 观察不改数据、请求与异常，只输出公开字段统计。"""

import json

import pandas as pd
import pytest
from yfinance import utils

from data_provider.exact_daily_contract import ExactDailyError
from data_provider.thesis_ledger_sources import yfinance as source
from scripts.probe_backtest_daily_sources import observe_yahoo_daily, yahoo_frame_summary


def frame():
    return pd.DataFrame({'Open': [10., 10.], 'High': [12., 12.], 'Low': [9., 9.], 'Close': [11., 11.],
                         'Adj Close': [11., float('nan')], 'Volume': [100., 100.]},
                        index=pd.DatetimeIndex(['2026-11-09', '2026-11-10'], name='Date'))


def test_missing_adjusted_close_is_observed_without_changing_sdk_behavior():
    raw = frame()
    before = raw.copy(deep=True)
    expected = utils.auto_adjust(raw)
    with observe_yahoo_daily(True) as observations:
        adjusted = utils.auto_adjust(raw)
        pd.testing.assert_frame_equal(adjusted, expected)
        with pytest.raises(ExactDailyError) as error:
            source.normalize_exact_daily(adjusted.reset_index().rename(columns={
                'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}),
                start='2026-11-09', end='2026-11-10', source='yfinance')
    pd.testing.assert_frame_equal(raw, before)
    assert error.value.code == 'invalid_response'
    assert observations[0]['missingCounts']['Open'] == 0
    assert observations[0]['missingCounts']['Adj Close'] == 1
    assert observations[0]['nonFiniteAdjustmentRatios'] == 1
    assert observations[1]['missingCounts']['Open'] == 1
    assert observations[2]['missingDateSamples']['Close'] == ['2026-11-10']


def test_observer_restores_functions_after_failure():
    adjust, normalize, parse = utils.auto_adjust, source.normalize_exact_daily, utils.parse_quotes
    with pytest.raises(RuntimeError):
        with observe_yahoo_daily(True):
            raise RuntimeError('synthetic')
    assert utils.auto_adjust is adjust and source.normalize_exact_daily is normalize
    assert utils.parse_quotes is parse


def test_raw_chart_observation_preserves_sdk_parse_result():
    from copy import deepcopy
    raw = {'meta': {'exchangeTimezoneName': 'Asia/Shanghai'},
           'timestamp': [int(pd.Timestamp('2026-11-10', tz='Asia/Shanghai').timestamp())],
           'indicators': {'quote': [{'open': [10.], 'high': [12.], 'low': [9.], 'close': [None], 'volume': [100]}],
                          'adjclose': [{'adjclose': [None]}]}}
    before = deepcopy(raw)
    expected = utils.parse_quotes(raw)
    with observe_yahoo_daily(True) as observations:
        actual = utils.parse_quotes(raw)
    pd.testing.assert_frame_equal(actual, expected)
    assert raw == before
    assert observations[0]['stage'] == 'sdk-raw-chart'
    assert observations[0]['missingCounts']['Close'] == 1
    assert observations[0]['missingCounts']['Open'] == 0
    assert observations[0]['missingDateSamples']['Adj Close'] == ['2026-11-10']


def test_observations_are_bounded_and_exclude_unrelated_columns_or_attributes():
    raw = pd.concat([frame()] * 30)
    raw['synthetic-sensitive-token'] = 'secret'
    raw.attrs['cookie'] = 'secret'
    summary = yahoo_frame_summary(raw, 'test')
    assert len(summary['missingDateSamples']['Adj Close']) == 8
    assert summary['missingCounts']['Adj Close'] == 30
    assert 'secret' not in json.dumps(summary)
    assert 'synthetic-sensitive-token' not in json.dumps(summary)


def test_disabled_observer_keeps_original_callables():
    adjust, normalize = utils.auto_adjust, source.normalize_exact_daily
    with observe_yahoo_daily(False) as observations:
        assert observations == []
        assert utils.auto_adjust is adjust and source.normalize_exact_daily is normalize
