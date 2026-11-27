"""Yahoo 原生日线的末日缺价补取；同口径重叠一致才填原空字段。"""

from datetime import date, timedelta
from hashlib import sha256
from math import isclose, isfinite
import re
from time import monotonic

import pandas as pd

from data_provider.exact_daily_contract import ExactDailyError

REVISION = 'yahoo-daily-bounded-tail-window-v2'
PRICES = ('Open', 'High', 'Low', 'Close')
NATIVE_PRICES = (*PRICES, 'Adj Close')


def exclusive_end_date(end):
    return (date.fromisoformat(end) + timedelta(days=1)).isoformat()


def select_daily_dates(raw, start, end):
    days = raw.index.strftime('%Y-%m-%d')
    return raw[(days >= start) & (days <= end)].copy()


def _frame(raw, ticker, start, end):
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        raise ExactDailyError('not_covered', 'Yahoo 未返回请求日线')
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(-1):
            raise ExactDailyError('invalid_response', 'Yahoo 日线标的身份不匹配')
        raw = raw.xs(ticker, axis=1, level=-1)
    if not raw.columns.is_unique or not set(NATIVE_PRICES).issubset(raw.columns) or not isinstance(raw.index, pd.DatetimeIndex):
        raise ExactDailyError('invalid_response', 'Yahoo 日线价格列或日期无效')
    frame = select_daily_dates(raw, start, end)
    days = frame.index.strftime('%Y-%m-%d')
    if frame.empty:
        raise ExactDailyError('not_covered', 'Yahoo 未覆盖请求窗口')
    if len(set(days)) != len(days) or not frame.index.is_monotonic_increasing:
        raise ExactDailyError('invalid_response', 'Yahoo 日线日期重复或乱序')
    return frame


def _price(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ExactDailyError('invalid_response', 'Yahoo 补取价格非法') from None
    if isinstance(value, bool) or not isfinite(number) or number <= 0:
        raise ExactDailyError('invalid_response', 'Yahoo 补取价格非法')
    return number


def _same(first, second):
    return isclose(_price(first), _price(second), rel_tol=1e-10, abs_tol=1e-12)


def read_daily_window(download, *, ticker, start, end, timeout_seconds=4.5, clock=monotonic):
    per_call = min(timeout_seconds, 4.5)
    budget = min(per_call * 2, 8.0)
    began = clock()
    responses = []

    def fetch(first, last, timeout):
        if timeout <= 0:
            raise ExactDailyError('timeout', 'Yahoo 日线读取预算耗尽')
        raw = download(tickers=ticker, start=first, end=exclusive_end_date(last),
                       auto_adjust=False, progress=False, threads=False, timeout=timeout, multi_level_index=False)
        if clock() - began >= budget:
            raise ExactDailyError('timeout', 'Yahoo 日线读取超过总预算')
        frame = _frame(raw, ticker, first, last)
        responses.append({'start': first, 'end': last, 'rows': len(frame),
                          'sha256': sha256(frame.to_json(orient='split', date_format='iso').encode()).hexdigest()})
        return frame

    frame = fetch(start, end, per_call)
    missing = frame[list(NATIVE_PRICES)].isna()
    bad = missing.any(axis=1)
    repaired, overlap, fields, overlap_fields = [], None, [], []
    if bad.any():
        if bad.sum() != 1 or not bad.iloc[-1] or len(frame) < 2:
            raise ExactDailyError('not_covered', 'Yahoo 日线缺价范围无法有界补取')
        day = frame.index[-1].strftime('%Y-%m-%d')
        known = [field for field in PRICES if not missing.iloc[-1][field]]
        if len(known) < 3 or 'Volume' not in frame or pd.isna(frame.iloc[-1]['Volume']):
            raise ExactDailyError('not_covered', 'Yahoo 缺价日没有足够原始价格及成交量对照')
        _price(frame.iloc[-1]['Volume'])
        smaller = fetch(day, day, min(per_call, budget - (clock() - began)))
        replacement = smaller.iloc[0]
        for field in NATIVE_PRICES:
            _price(replacement[field])
            if not missing.iloc[-1][field] and not _same(frame.iloc[-1][field], replacement[field]):
                raise ExactDailyError('invalid_response', 'Yahoo 补取与原有价格冲突')
        if ('Volume' not in replacement or pd.isna(replacement['Volume'])
                or frame.iloc[-1]['Volume'] != replacement['Volume']):
            raise ExactDailyError('invalid_response', 'Yahoo 补取与原有成交量冲突')
        overlap, overlap_fields = day, [*known, 'Volume']
        fields = [field for field in NATIVE_PRICES if missing.iloc[-1][field]]
        for field in fields:
            frame.loc[frame.index[-1], field] = replacement[field]
        repaired = [day]
    evidence = {
        'revision': REVISION, 'range': {'start': start, 'end': end}, 'adjustment': 'qfq',
        'hashScope': 'sdkDataframe', 'responses': responses, 'repairedDates': repaired,
        'overlapDate': overlap, 'overlapFields': overlap_fields, 'filledFields': fields,
    }
    from yfinance.utils import auto_adjust
    adjusted = auto_adjust(frame)
    adjusted.attrs['yahooDailyRetrieval'] = evidence
    return adjusted


def verify_daily_transport(frame, start, end, adjustment):
    """核对来源读取证据与实际一／两次请求，不能只改 pagesFetched。"""
    evidence = getattr(frame, 'attrs', {}).get('yahooDailyRetrieval', {})
    if not isinstance(evidence, dict):
        raise ExactDailyError('invalid_response', 'Yahoo 完整窗口读取证据无效')
    responses, repaired = evidence.get('responses'), evidence.get('repairedDates')
    if (evidence.get('revision') != REVISION or evidence.get('range') != {'start': start, 'end': end}
            or adjustment != 'qfq' or evidence.get('adjustment') != adjustment
            or evidence.get('hashScope') != 'sdkDataframe' or not isinstance(responses, list)
            or len(responses) not in {1, 2}):
        raise ExactDailyError('invalid_response', 'Yahoo 完整窗口读取证据无效')
    for response in responses:
        if (not isinstance(response, dict)
                or type(response.get('rows')) is not int or response['rows'] < 1
                or not isinstance(response.get('sha256'), str)
                or re.fullmatch(r'[a-f0-9]{64}', response['sha256']) is None):
            raise ExactDailyError('invalid_response', 'Yahoo 响应摘要无效')
    if responses[0].get('start') != start or responses[0].get('end') != end:
        raise ExactDailyError('invalid_response', 'Yahoo 初始窗口不一致')
    if len(responses) == 1:
        valid = (repaired == [] and evidence.get('overlapDate') is None
                 and evidence.get('filledFields') == [] and evidence.get('overlapFields') == [])
    else:
        overlap, fields, compared = evidence.get('overlapDate'), evidence.get('filledFields'), evidence.get('overlapFields')
        valid = (isinstance(repaired, list) and len(repaired) == 1 and isinstance(repaired[0], str)
                 and isinstance(overlap, str) and start <= overlap == repaired[0] <= end
                 and responses[1].get('start') == overlap and responses[1].get('end') == overlap
                 and responses[1]['rows'] == 1
                 and isinstance(fields, list) and bool(fields)
                 and all(isinstance(field, str) and field in NATIVE_PRICES for field in fields)
                 and len(set(fields)) == len(fields)
                 and isinstance(compared, list)
                 and all(isinstance(field, str) and field in (*PRICES, 'Volume') for field in compared)
                 and len(set(compared)) == len(compared) and 'Volume' in compared
                 and len(set(compared) & set(PRICES)) >= 3 and not set(fields).intersection(compared))
        if valid:
            days = list(frame['date']) if 'date' in frame else list(frame.index.strftime('%Y-%m-%d'))
            valid = bool(days) and repaired[0] == days[-1] and overlap in days
    if not valid:
        raise ExactDailyError('invalid_response', 'Yahoo 补取证据不完整')
    return len(responses)
