"""基金类型只取来源字段，原文和传输预算均保留。"""

import pytest

from data_provider import eastmoney_fund_identity as reader
from data_provider.eastmoney_nav_evidence import canonical_json, hash_raw


def raw(rows):
    return 'var reData=' + canonical_json({'datas': rows}) + ';'


@pytest.mark.parametrize('kind,expected', [
    ('股票型', 'domestic'), ('指数型-股票', 'domestic'), ('QDII', 'qdii'), ('QDII-指数', 'qdii')])
def test_source_type_not_name(kind, expected):
    value = raw([['161725', 'QDII ordinary 混淆名字', kind]])
    evidence = reader.identity_from_raw(value, '161725', '2026-09-30T00:00:00Z')
    assert evidence.fund_type == expected
    assert evidence.record['responseRaw'] == value
    assert evidence.record['responseHash'] == hash_raw(value)


@pytest.mark.parametrize('rows', [
    [], [['161726', 'x', '股票型']], [['161725', 'QDII', '未知']],
    [['161725', 'x', 'QDII'], ['161725', 'x', '股票型']]])
def test_unknown_missing_duplicate_reject(rows):
    with pytest.raises(ValueError):
        reader.identity_from_raw(raw(rows), '161725', '2026-09-30T00:00:00Z')


def test_reader_tls_status_and_raw(monkeypatch):
    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, **kwargs):
            yield raw([['161725', 'x', '指数型-股票']]).encode()
    response = Response()

    def get(url, **kwargs):
        assert url == reader.IDENTITY_ENDPOINT
        assert kwargs['verify'] is True and kwargs['allow_redirects'] is False
        assert kwargs['timeout'] == (5, 10) and kwargs['stream'] is True
        return response
    monkeypatch.setattr(reader.requests, 'get', get)
    assert reader.read_fund_identity('161725').fund_type == 'domestic'
    response.status_code = 302
    with pytest.raises(ValueError):
        reader.read_fund_identity('161725')
