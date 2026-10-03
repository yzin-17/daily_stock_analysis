"""当前 ETF 目录不得由错节点计数、缺行或合成身份取得完整性。"""

import json
from unittest.mock import Mock

import pytest
import requests

from data_provider import sina_etf_catalog_reader as reader


def envelope(rows):
    return reader._CALLBACK + '(' + json.dumps(rows) + ');'


def rows():
    return [{'symbol': 'sz159516', 'code': '159516', 'name': 'ETF甲'},
            {'symbol': 'sh510300', 'code': '510300', 'name': 'ETF乙'}]


def fetch(values):
    return Mock(side_effect=[(value, str(i) * 64) for i, value in enumerate(values)])


def test_same_node_complete_identity_and_raw_hashes():
    request = fetch(['"2"', envelope(rows()), '"2"'])
    result = reader.read_sina_etf_catalog(fetch=request)
    assert [(x['canonicalCode'], x['market'], x['instrumentType']) for x in result['rows']] == [
        ('159516', 'SZ', 'ETF'), ('510300', 'SH', 'ETF')]
    assert result['retrieval']['responseHashes'] == ['0' * 64, '1' * 64, '2' * 64]
    assert 'getHQNodeStockCountSimple' not in request.call_args_list[0].args[0]
    assert request.call_args_list[1].args[1]['node'] == 'etf_hq_fund'
    assert all(0 < call.args[2] <= 20 for call in request.call_args_list)


@pytest.mark.parametrize('count', ['"0"', 'true', '"5000"', '"02"', 'null'])
def test_invalid_or_limit_count_rejected_before_list(count):
    request = fetch([count])
    with pytest.raises(ValueError):
        reader.read_sina_etf_catalog(fetch=request)
    assert request.call_count == 1


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'code', 'symbol', 'name', 'script'])
def test_bad_list_never_returns_partial_catalog(mutation):
    items = rows()
    if mutation == 'duplicate':
        items[1] = items[0]
    elif mutation == 'missing':
        items.pop()
    elif mutation == 'code':
        items[0]['code'] = '510300'
    elif mutation == 'symbol':
        items[0]['symbol'] = '159516'
    elif mutation == 'name':
        items[0]['name'] = ' '
    text = envelope(items) + ('alert(1)' if mutation == 'script' else '')
    request = fetch(['"2"', text])
    with pytest.raises(ValueError):
        reader.read_sina_etf_catalog(fetch=request)
    assert request.call_count == 2


def test_changed_count_rejects_complete_list():
    request = fetch(['"2"', envelope(rows()), '"3"'])
    with pytest.raises(ValueError, match='catalog_changed_during_read'):
        reader.read_sina_etf_catalog(fetch=request)
    assert request.call_count == 3


def test_exact_source_comment_is_data_and_other_script_is_rejected():
    text = reader._PREFIX + '\n' + envelope(rows())
    assert len(reader._rows(text, 2)) == 2
    with pytest.raises(ValueError, match='invalid_etf_catalog_envelope'):
        reader._rows("/* other */" + envelope(rows()), 2)


def test_duplicate_json_identity_field_is_rejected():
    text = reader._CALLBACK + '([{"symbol":"sh510300","symbol":"sz159516"}]);'
    with pytest.raises(ValueError, match='duplicate_etf_catalog_field'):
        reader._rows(text, 1)


def test_late_first_response_stops_before_list():
    clock = iter([0, 0, 21])
    request = fetch(['"2"'])
    with pytest.raises(TimeoutError):
        reader.read_sina_etf_catalog(fetch=request, monotonic=lambda: next(clock))
    assert request.call_count == 1


@pytest.mark.parametrize('status', [302, 503])
def test_http_failure_closes_without_reading(monkeypatch, status):
    response = requests.Response()
    response.status_code = status
    response.close = Mock()
    response.iter_content = Mock()
    monkeypatch.setattr(reader.requests, 'get', Mock(return_value=response))
    with pytest.raises(ValueError, match='catalog_http_failure'):
        reader._fetch(reader._COUNT, {'node': 'etf_hq_fund'}, 4)
    response.iter_content.assert_not_called()
    response.close.assert_called_once()


def test_http_size_and_raw_encoding(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response.headers['Content-Type'] = 'application/javascript; charset=gbk'
    body = 'ETF目录'.encode('gbk')
    response.iter_content = Mock(return_value=[body])
    response.close = Mock()
    request = Mock(return_value=response)
    monkeypatch.setattr(reader.requests, 'get', request)
    assert reader._fetch(reader._LIST, {}, 4)[0] == 'ETF目录'
    assert request.call_args.kwargs['allow_redirects'] is False
    assert request.call_args.kwargs['timeout'] == (4, 4)
    response.iter_content.return_value = [b'a' * (2 * 1024 * 1024 + 1)]
    with pytest.raises(ValueError, match='catalog_response_too_large'):
        reader._fetch(reader._LIST, {}, 4)
