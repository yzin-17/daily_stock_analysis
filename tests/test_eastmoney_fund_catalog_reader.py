"""分页读取不得发布部分、变化、重复或超时目录。"""

import json
from unittest.mock import Mock

import pytest
import requests

from data_provider.eastmoney_fund_catalog_reader import _fetch_page, read_fund_catalog


def page(index, codes, total=3):
    fields = dict(datas=[f'{code},基金{code},rest' for code in codes], allRecords=total,
                  pageIndex=index, pageNum=2, allPages=(total + 1) // 2)
    return 'var rankData = {' + ','.join(f'{k}:{json.dumps(v)}' for k, v in fields.items()) + '};'


def test_all_pages_and_hashes_preserved():
    fetch = Mock(side_effect=[page(1, ['000001', '000002']), page(2, ['000003'])])
    result = read_fund_catalog(page_size=2, fetch_page=fetch)
    assert len(result['rows']) == 3
    assert len(result['retrieval']['pageHashes']) == 2
    assert result['retrieval']['total'] == 3
    assert [call.args[:2] for call in fetch.call_args_list] == [(1, 2), (2, 2)]
    assert all(0 < call.args[2] <= 45 for call in fetch.call_args_list)


@pytest.mark.parametrize('mode', ['duplicate', 'changed', 'failure', 'budget'])
def test_incomplete_read_raises_without_retry(mode):
    second = page(2, ['000003'])
    if mode == 'duplicate':
        second = page(2, ['000001'])
    elif mode == 'changed':
        second = page(2, ['000003', '000004'], total=4)
    elif mode == 'failure':
        second = TimeoutError('upstream timeout')
    fetch = Mock(side_effect=[page(1, ['000001', '000002']), second])
    with pytest.raises((ValueError, TimeoutError)):
        read_fund_catalog(page_size=2, max_pages=1 if mode == 'budget' else 2, fetch_page=fetch)
    assert fetch.call_count == (1 if mode == 'budget' else 2)


def test_late_first_page_does_not_trigger_second_request():
    clock = iter([0, 0, 46])
    fetch = Mock(return_value=page(1, ['000001', '000002']))
    with pytest.raises(TimeoutError, match='catalog_deadline'):
        read_fund_catalog(page_size=2, fetch_page=fetch, monotonic=lambda: next(clock))
    fetch.assert_called_once()


def mock_http_response(monkeypatch, chunks, *, status=200):
    response = requests.Response()
    response.status_code = status
    response.iter_content = Mock(return_value=iter(chunks))
    response.close = Mock()
    get = Mock(return_value=response)
    monkeypatch.setattr('data_provider.eastmoney_fund_catalog_reader.requests.get', get)
    return response, get


@pytest.mark.parametrize('remaining', [2, 9])
def test_http_request_keeps_identity_tls_and_remaining_budget(monkeypatch, remaining):
    monkeypatch.setattr('data_provider.eastmoney_fund_catalog_reader._ranking_window',
                        lambda: ('2025-10-02', '2026-10-02'))
    response, get = mock_http_response(monkeypatch, [b'\xef\xbb', b'\xbf', '基金'.encode('utf-8')])
    assert _fetch_page(3, 100, remaining) == '基金'
    get.assert_called_once_with(
        'https://fund.eastmoney.com/data/rankhandler.aspx',
        params={'op': 'ph', 'dt': 'kf', 'ft': 'all', 'rs': '', 'gs': '0', 'sc': 'qjzf',
                'st': 'desc', 'sd': '2025-10-02', 'ed': '2026-10-02', 'qdii': '',
                'tabSubtype': ',,,,,', 'pi': '3', 'pn': '100', 'dx': '1'},
        headers={'Referer': 'https://fund.eastmoney.com/data/fundranking.html'},
        timeout=(min(5, remaining), remaining), allow_redirects=False, stream=True,
    )
    response.iter_content.assert_called_once_with(16384)
    response.close.assert_called_once_with()


def test_production_pages_share_one_ranking_window(monkeypatch):
    window = Mock(side_effect=[('2025-10-02', '2026-10-02'), ('2025-10-03', '2026-10-03')])
    fetch = Mock(side_effect=[page(1, ['000001', '000002']), page(2, ['000003'])])
    monkeypatch.setattr('data_provider.eastmoney_fund_catalog_reader._ranking_window', window)
    monkeypatch.setattr('data_provider.eastmoney_fund_catalog_reader._fetch_page', fetch)
    result = read_fund_catalog(page_size=2)
    window.assert_called_once()
    assert [call.kwargs for call in fetch.call_args_list] == [
        {'ranking_window': ('2025-10-02', '2026-10-02')},
        {'ranking_window': ('2025-10-02', '2026-10-02')},
    ]
    assert result['retrieval']['rankingStart'] == '2025-10-02'
    assert result['retrieval']['rankingEnd'] == '2026-10-02'


@pytest.mark.parametrize('status', [302, 503])
def test_http_redirect_or_failure_closes_without_reading_body(monkeypatch, status):
    response, get = mock_http_response(monkeypatch, [b'partial'], status=status)
    with pytest.raises(ValueError, match='^catalog_http_failure$'):
        _fetch_page(1, 1000, 10)
    get.assert_called_once()
    response.iter_content.assert_not_called()
    response.close.assert_called_once_with()


@pytest.mark.parametrize('overflow', [False, True])
def test_http_size_limit_counts_all_chunks_and_closes(monkeypatch, overflow):
    chunks = [b'a' * (1024 * 1024)] * 8
    if overflow:
        chunks.append(b'a')
    response, get = mock_http_response(monkeypatch, chunks)
    if overflow:
        with pytest.raises(ValueError, match='^catalog_response_too_large$'):
            _fetch_page(1, 1000, 10)
    else:
        assert len(_fetch_page(1, 1000, 10)) == 8 * 1024 * 1024
    get.assert_called_once()
    response.close.assert_called_once_with()


def test_http_invalid_utf8_closes_without_returning_partial_text(monkeypatch):
    response, get = mock_http_response(monkeypatch, [b'valid prefix', b'\xff'])
    with pytest.raises(UnicodeDecodeError):
        _fetch_page(1, 1000, 10)
    get.assert_called_once()
    response.close.assert_called_once_with()


def test_http_stream_timeout_closes_without_returning_partial_body(monkeypatch):
    def interrupted_body():
        yield b'partial'
        raise requests.exceptions.ReadTimeout('offline timeout')

    response, get = mock_http_response(monkeypatch, interrupted_body())
    with pytest.raises(requests.exceptions.ReadTimeout, match='offline timeout'):
        _fetch_page(1, 1000, 10)
    get.assert_called_once()
    response.close.assert_called_once_with()


def test_http_connection_timeout_propagates_without_retry(monkeypatch):
    get = Mock(side_effect=requests.exceptions.ConnectTimeout('offline timeout'))
    monkeypatch.setattr('data_provider.eastmoney_fund_catalog_reader.requests.get', get)
    with pytest.raises(requests.exceptions.ConnectTimeout, match='offline timeout'):
        _fetch_page(1, 1000, 2)
    get.assert_called_once()
