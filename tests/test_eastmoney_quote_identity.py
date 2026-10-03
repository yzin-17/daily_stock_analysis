"""通过股票与 ETF 实际 Fetcher 接缝验证唯一原行约束。"""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider import akshare_fetcher as module
from data_provider.eastmoney_quote_identity import unique_quote_row
from data_provider.realtime_types import RealtimeSource


@pytest.fixture(params=[
    ('_get_stock_realtime_quote_em', 'stock_zh_a_spot_em', '_realtime_cache', '600519'),
    ('_get_etf_realtime_quote', 'fund_etf_spot_em', '_etf_realtime_cache', '159516'),
])
def adapter(request, monkeypatch):
    method, endpoint, cache_name, code = request.param
    for name in ('_realtime_cache', '_etf_realtime_cache'):
        monkeypatch.setattr(module, name, {'data': None, 'timestamp': 0, 'ttl': 1200})
    sdk = Mock()
    monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace(**{endpoint: sdk}))
    monkeypatch.setattr(module, 'get_realtime_circuit_breaker', Mock(return_value=Mock()))
    fetcher = module.AkshareFetcher()
    monkeypatch.setattr(fetcher, '_set_random_user_agent', lambda: None)
    monkeypatch.setattr(fetcher, '_enforce_rate_limit', lambda: None)
    return getattr(fetcher, method), sdk, getattr(module, cache_name), code


def quote_row(code, price=12.34):
    return {'代码': code, '名称': '目标', '最新价': price, '成交量': 345,
            '成交额': 678.9, '今开': 11.2, '开盘价': 11.2, '涨跌幅': 1.25}


@pytest.mark.parametrize('multiple', [False, True])
def test_unique_original_row_preserves_quote(adapter, multiple):
    getter, sdk, cache, code = adapter
    rows = [quote_row(code)]
    if multiple:
        rows = [quote_row('000001', 999), *rows, quote_row('000002', 888)]
    frame = pd.DataFrame(rows)
    original = frame.copy(deep=True)
    sdk.return_value = frame
    quote = getter(code)
    assert quote.code == code
    assert quote.name == '目标'
    assert quote.source == RealtimeSource.AKSHARE_EM
    assert (quote.price, quote.volume, quote.amount, quote.open_price) == (12.34, 345, 678.9, 11.2)
    assert quote.change_pct == 1.25
    assert quote.provider_timestamp is None
    assert getter(code) == quote
    sdk.assert_called_once_with()
    assert cache['data'] is frame
    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize('case', [
    'duplicate_same', 'duplicate_conflict', 'wrong_code', 'missing_column', 'empty',
    'numeric_row', 'unicode_row', 'space_row', 'duplicate_column',
])
@pytest.mark.parametrize('cached', [False, True])
def test_invalid_snapshot_rejected_in_actual_adapter(adapter, case, cached):
    getter, sdk, cache, code = adapter
    frames = {
        'duplicate_same': pd.DataFrame([quote_row(code), quote_row(code)]),
        'duplicate_conflict': pd.DataFrame([quote_row(code), quote_row(code, 99)]),
        'wrong_code': pd.DataFrame([quote_row('000001')]),
        'missing_column': pd.DataFrame([{'最新价': 12.34}]),
        'empty': pd.DataFrame(),
        'numeric_row': pd.DataFrame([quote_row(int(code))]),
        'unicode_row': pd.DataFrame([quote_row(code.translate(str.maketrans('0123456789', '０１２３４５６７８９')))]),
        'space_row': pd.DataFrame([quote_row(' ' + code)]),
        'duplicate_column': pd.DataFrame([[code, code]], columns=['代码', '代码']),
    }
    frame = frames[case]
    sdk.return_value = frame
    if cached:
        cache.update(data=frame, timestamp=module.time.time())
    assert getter(code) is None
    assert getter(code) is None
    assert sdk.call_count == (0 if cached else 1)
    assert cache['data'] is frame


@pytest.mark.parametrize('invalid', [None, 600519, '', '60051', '6005190', ' 600519',
                                     '600519 ', '６００５１９', 'sh600519'])
def test_invalid_request_does_not_match_or_coerce(adapter, invalid):
    getter, sdk, _, code = adapter
    sdk.return_value = pd.DataFrame([quote_row(code), quote_row(invalid)])
    assert getter(invalid) is None
    sdk.assert_called_once_with()


def test_helper_preserves_original_index_and_fields():
    frame = pd.DataFrame([quote_row('600519')], index=[42])
    pd.testing.assert_series_equal(unique_quote_row(frame, '600519'), frame.iloc[0])
