"""替代目录必须有完整生命周期及一致分页，不能把当前代码表冒充历史全集。"""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from data_provider.hithink_history_catalog import read_catalog, project_catalog, CatalogSourceError


def rows():
    return [{'thscode': code, 'exchange': code.split('.')[1], 'currency': 'CNY', 'asset_type': kind,
             'list_date': '2020-01-01', 'last_trade_date': None}
            for code, kind in [('600000.SH', 'a-share'), ('000001.SZ', 'a-share'), ('920001.BJ', 'a-share'), ('510300.SH', 'fund-etf')]]


def test_lifecycle_projection_keeps_retired_on_historical_day_and_excludes_new_listing():
    data = rows()
    data[0]['last_trade_date'] = '2026-11-03'
    data.append({**data[1], 'thscode': '000002.SZ', 'list_date': '2026-11-03'})
    early = project_catalog({'items': data}, '2026-11-02')
    assert len(early) == 4 and early[0]['name'] == early[0]['symbol']
    assert '000002.SZ' not in {i['symbol'] for i in early}
    data[0]['list_date'] = None
    with pytest.raises(CatalogSourceError, match='lifecycle_incomplete'):
        project_catalog({'items': data}, '2026-11-02')


def test_reader_pins_endpoint_and_does_not_follow_redirects_or_accept_empty_success():
    response = SimpleNamespace(status_code=200, json=lambda: {'code': 0, 'data': {'item': rows(), 'timestamp': 1790000000000}})
    get = Mock(return_value=response)
    assert read_catalog('synthetic', http_get=get)['pages'] == 1
    assert get.call_args.kwargs['allow_redirects'] is False
    response.json = lambda: {'code': 0, 'data': {'item': [], 'timestamp': 1790000000000}}
    with pytest.raises(CatalogSourceError, match='empty_response'):
        read_catalog('synthetic', http_get=get)
