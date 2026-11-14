"""Yahoo 末日有界补取保留原值、口径及共享预算，失败不再循环读取。"""

from copy import deepcopy

import pandas as pd
import pytest

from data_provider.exact_daily_contract import ExactDailyError
from data_provider.yfinance_daily_window import read_daily_window, verify_daily_transport
from yfinance.utils import auto_adjust


def frame():
    return pd.DataFrame({'Open': [10., 11., 12.], 'High': [12., 13., 14.], 'Low': [9., 10., 11.],
                         'Close': [11., 12., 13.], 'Adj Close': [11., 12., 13.], 'Volume': [100, 200, 300]},
                        index=pd.DatetimeIndex(['2026-11-06', '2026-11-09', '2026-11-10'], name='Date'))


def fetcher(*frames):
    values, calls = iter(frames), []
    def download(**options):
        calls.append(options)
        return next(values).copy(deep=True)
    return download, calls


def read(download, **options):
    return read_daily_window(download, ticker='600519.SS', start='2026-11-06', end='2026-11-10', **options)


def test_complete_prices_do_not_trigger_second_request_or_change_values():
    original = frame()
    download, calls = fetcher(original)
    result = read(download)
    pd.testing.assert_frame_equal(result, auto_adjust(original))
    assert len(calls) == verify_daily_transport(result, '2026-11-06', '2026-11-10', 'qfq') == 1
    assert result.attrs['yahooDailyRetrieval']['repairedDates'] == []


def test_tail_repair_uses_same_ticker_basis_and_overlap_without_replacing_metrics():
    original = frame()
    original.loc[original.index[-1], ['Close', 'Adj Close']] = float('nan')
    download, calls = fetcher(original, frame().iloc[-1:])
    result = read(download)
    pd.testing.assert_frame_equal(result, auto_adjust(frame()))
    assert len(calls) == 2 and calls[1]['start'] == '2026-11-10'
    assert all(call['tickers'] == '600519.SS' and call['end'] == '2026-11-11'
               and call['auto_adjust'] is False and 'repair' not in call for call in calls)
    assert calls[0]['timeout'] == 4.5 and 0 < calls[1]['timeout'] <= 4.5
    evidence = result.attrs['yahooDailyRetrieval']
    assert evidence['repairedDates'] == ['2026-11-10'] and evidence['overlapDate'] == '2026-11-10'
    assert evidence['overlapFields'] == ['Open', 'High', 'Low', 'Volume']
    assert evidence['hashScope'] == 'sdkDataframe'
    assert verify_daily_transport(result, '2026-11-06', '2026-11-10', 'qfq') == 2
    assert pd.isna(original.iloc[-1]['Close'])


def test_repair_uses_native_adjusted_close_then_original_sdk_adjustment():
    complete = frame()
    complete['Adj Close'] /= 2
    incomplete = complete.copy()
    incomplete.loc[incomplete.index[-1], ['Close', 'Adj Close']] = float('nan')
    download, _ = fetcher(incomplete, complete.iloc[-1:])
    result = read(download)
    pd.testing.assert_frame_equal(result, auto_adjust(complete))
    assert result.iloc[-1]['Close'] == 6.5 and result.iloc[-1]['Open'] == 6


@pytest.mark.parametrize('rows', [[0], [1], [1, 2]])
def test_middle_or_multiple_missing_days_stop_without_additional_request(rows):
    original = frame()
    original.iloc[rows, :4] = float('nan')
    download, calls = fetcher(original)
    with pytest.raises(ExactDailyError) as error:
        read(download)
    assert error.value.code == 'not_covered' and len(calls) == 1


@pytest.mark.parametrize('change', ['overlap', 'existing_price', 'volume', 'still_missing', 'missing_overlap'])
def test_incomplete_or_conflicting_repair_is_rejected(change):
    original, smaller = frame(), frame().iloc[-1:].copy()
    original.loc[original.index[-1], 'Open'] = float('nan')
    if change == 'overlap':
        smaller.loc[smaller.index[-1], 'High'] *= 2
    elif change == 'existing_price':
        smaller.iloc[-1, 3] = 13.5
    elif change == 'volume':
        smaller.loc[smaller.index[-1], 'Volume'] = 301
    elif change == 'still_missing':
        smaller.iloc[-1, 0] = float('nan')
    else:
        smaller = smaller.iloc[0:0]
    download, calls = fetcher(original, smaller)
    with pytest.raises(ExactDailyError) as error:
        read(download)
    assert error.value.code in {'invalid_response', 'not_covered'} and len(calls) == 2
    assert pd.isna(original.iloc[-1]['Open'])


def test_both_requests_share_window_deadline_and_keep_per_call_limit():
    original = frame()
    original.loc[original.index[-1], ['Close', 'Adj Close']] = float('nan')
    download, calls = fetcher(original, frame().iloc[-1:])
    clock = iter([0., 4., 5., 9.])
    with pytest.raises(ExactDailyError) as error:
        read(download, clock=lambda: next(clock))
    assert error.value.code == 'timeout' and len(calls) == 2
    assert calls[0]['timeout'] == 4.5 and calls[1]['timeout'] == 3


def test_exhausted_budget_never_calls_sdk():
    download, calls = fetcher()
    with pytest.raises(ExactDailyError) as error:
        read(download, timeout_seconds=0)
    assert error.value.code == 'timeout' and calls == []


def test_single_incomplete_day_request_is_not_repeated():
    raw = frame().iloc[-1:].copy()
    raw.loc[raw.index[-1], ['Close', 'Adj Close']] = float('nan')
    download, calls = fetcher(raw)
    with pytest.raises(ExactDailyError) as error:
        read(download)
    assert error.value.code == 'not_covered' and len(calls) == 1


def test_repair_cannot_change_ticker_identity():
    raw = frame()
    raw.loc[raw.index[-1], ['Close', 'Adj Close']] = float('nan')
    other = frame().iloc[-1:].copy()
    other.columns = pd.MultiIndex.from_product([other.columns, ['OTHER']])
    download, calls = fetcher(raw, other)
    with pytest.raises(ExactDailyError) as error:
        read(download)
    assert error.value.code == 'invalid_response' and len(calls) == 2


@pytest.mark.parametrize('change', ['revision', 'window', 'hash', 'count', 'repair', 'adjustment'])
def test_transport_rejects_forged_or_incomplete_proof(change):
    download, _ = fetcher(frame())
    result = read(download)
    evidence = deepcopy(result.attrs['yahooDailyRetrieval'])
    if change == 'revision':
        evidence['revision'] = 'unknown'
    elif change == 'window':
        evidence['range']['end'] = '2026-11-09'
    elif change == 'hash':
        evidence['responses'][0]['sha256'] = 'unknown'
    elif change == 'count':
        evidence['responses'] *= 3
    elif change == 'repair':
        evidence['repairedDates'] = ['2026-11-10']
    else:
        evidence['adjustment'] = 'none'
    result.attrs['yahooDailyRetrieval'] = evidence
    with pytest.raises(ExactDailyError) as error:
        verify_daily_transport(result, '2026-11-06', '2026-11-10', 'qfq')
    assert error.value.code == 'invalid_response'
