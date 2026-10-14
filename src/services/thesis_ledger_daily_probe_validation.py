"""日线连接诊断的既有整帧校验；不推导历史覆盖或改变源响应。"""

import math

import pandas as pd

from .thesis_ledger_capability_contracts import CapabilityContractError


def daily_frame(adapter_result):
    if isinstance(adapter_result, tuple):
        if len(adapter_result) != 2:
            raise CapabilityContractError('invalid_response', 'Provider 日线响应结构非法')
        return adapter_result[0]
    return adapter_result


def _number(value, field):
    try:
        result = float(value) if not isinstance(value, bool) else math.nan
    except (TypeError, ValueError):
        raise CapabilityContractError('invalid_response', f'Provider 响应字段 {field} 非法') from None
    if not math.isfinite(result) or result < 0:
        raise CapabilityContractError('invalid_response', f'Provider 响应字段 {field} 非法')
    return result


def validate_bars(frame):
    if frame is None or getattr(frame, 'empty', True):
        raise CapabilityContractError('not_covered', 'Provider 未覆盖该标的')
    required = {'date', 'open', 'high', 'low', 'close', 'volume', 'amount'}
    if not required.issubset(set(getattr(frame, 'columns', []))):
        raise CapabilityContractError('invalid_response', 'Provider Bar 响应缺少必要字段')
    timestamps = set()
    for _, row in frame.iterrows():
        timestamp = str(row.get('date'))
        if not timestamp or timestamp in timestamps:
            raise CapabilityContractError('invalid_response', 'Provider Bar 时间重复或缺失')
        timestamps.add(timestamp)
        values = {name: _number(row.get(name), name) for name in ('open', 'high', 'low', 'close')}
        for name in ('volume', 'amount'):
            value = row.get(name)
            if value is not None and not pd.isna(value):
                _number(value, name)
        if values['high'] < max(values['open'], values['close'], values['low']):
            raise CapabilityContractError('invalid_response', 'Provider Bar 最高价非法')
        if values['low'] > min(values['open'], values['close'], values['high']):
            raise CapabilityContractError('invalid_response', 'Provider Bar 最低价非法')
    return frame
