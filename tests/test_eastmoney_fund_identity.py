"""基金类型只取来源字段，原文和传输预算均保留。"""

import pytest
import importlib.util
import json
import sys
from types import ModuleType
from unittest.mock import Mock

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


def isolated_reader(monkeypatch):
    spec = importlib.util.spec_from_file_location('_identity_reader_compat_test', reader.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_import_does_not_require_fund_module_transitive_exports(monkeypatch):
    import akshare.fund.fund_em as legacy
    monkeypatch.delattr(legacy, 'demjson', raising=False)
    monkeypatch.delattr(legacy, 'headers', raising=False)
    loaded = isolated_reader(monkeypatch)
    source = 'var reData={datas:[["161725","样本","QDII"]],allRecords:1};'
    assert loaded.identity_from_raw(source, '161725', '2026-11-04T00:00:00Z').fund_type == 'qdii'


def test_new_sdk_jsonish_entry_is_used_without_global_reload(monkeypatch):
    module = ModuleType('akshare.utils.jsonish')
    module.loads = Mock(side_effect=json.loads)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    loaded = isolated_reader(monkeypatch)
    source = raw([['161725', '样本', '指数型-股票']])
    result = loaded.identity_from_raw(source, '161725', '2026-11-04T00:00:00Z')
    assert result.fund_type == 'domestic' and result.response_raw == source
    module.loads.assert_called_once()
