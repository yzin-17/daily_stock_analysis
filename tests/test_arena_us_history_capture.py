"""验收导出必须保留分钟表来源属性，不把 DataFrame 当接口对象。"""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest


def load_capture():
    path = Path(__file__).parents[1] / 'scripts/local-runtime/arena-us-history-capture.py'
    spec = importlib.util.spec_from_file_location('arena_us_history_capture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_minute_frame_retains_provenance_and_nulls():
    frame = pd.DataFrame([{'timestamp': '2026-11-13T09:30:00-05:00', 'open': 200.0, 'high': 201.0,
                           'low': 199.0, 'close': 200.0, 'volume': None, 'amount': None, 'complete': True}])
    frame.attrs = {'upstream_source': 'futu', 'observedAt': '2026-11-14T08:00:00Z',
                   'timezone': 'America/New_York', 'timeframe': '1m', 'session': 'regular',
                   'timestampConvention': 'interval-start', 'intradayFieldUnits': {'volume': 'share', 'amount': 'unknown'},
                   'responseSha256': 'a' * 64}
    value = load_capture().minute_value(frame, 'AAPL.US')
    assert value['rows'][0]['volume'] is None
    assert value['responseSha256'] == 'a' * 64
    assert value['assetType'] == 'STOCK'
    del frame.attrs['responseSha256']
    with pytest.raises(KeyError):
        load_capture().minute_value(frame, 'AAPL.US')
