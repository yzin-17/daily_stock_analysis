"""精确净值原文证据；确定性测试不授予来源 PIT 准入。"""

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest
import requests

from data_provider import eastmoney_fund_nav as reader
from data_provider.eastmoney_nav_evidence import canonical_json, evidence_hash, native_record_fragments


def payload(days, total=3, nav='1.230000000000000000000001'):
    return {'Success': True, 'ErrCode': 0, 'ErrorCode': '0', 'TotalCount': total,
            'Datas': [{'FSRQ': day, 'DWJZ': nav, 'LJJZ': '2.3400', 'JZZZL': '0.1',
                       '原生字段': {'Datas': '嵌套字段'}} for day in days]}


class Response:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, **kwargs):
        # 跨 UTF-8 字符与 JSON token 分块，不应影响原文或摘要。
        for offset in range(0, len(self.raw), 7):
            yield self.raw[offset:offset + 7]


def install(monkeypatch, bodies):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        assert url == reader.ENDPOINT and kwargs['verify'] is True
        assert kwargs['stream'] is True
        assert kwargs['data']['FCODE'] == '110011'
        return Response(bodies[len(calls) - 1])

    monkeypatch.setattr(reader, 'PAGE_SIZE', 2)
    monkeypatch.setattr(reader.requests, 'get', get)
    return calls


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def test_exact_pages_records_precision_hashes_and_capture(monkeypatch):
    bodies = [encoded(payload(['2026-01-03', '2026-01-02'])), encoded(payload(['2026-01-01']))]
    install(monkeypatch, bodies)
    before = datetime.now().astimezone()
    evidence = reader.read_fund_nav_evidence('110011')
    after = datetime.now().astimezone()
    assert before <= datetime.fromisoformat(evidence.captured_at) <= after
    assert evidence.total_count == len(evidence.records) == 3
    assert evidence.fund_code == '110011' and evidence.reader_revision == reader.READER_REVISION
    assert len(evidence.pages) == 2
    assert [p.raw_response.encode('utf-8') for p in evidence.pages] == bodies
    assert [p.content_hash for p in evidence.pages] == [hashlib.sha256(b).hexdigest() for b in bodies]
    assert [r.valuation_date for r in evidence.records] == ['2026-01-03', '2026-01-02', '2026-01-01']
    for record in evidence.records:
        assert record.unit_nav == '1.230000000000000000000001'
        assert record.native_record_raw in evidence.pages[record.page_index - 1].raw_response
        assert json.loads(record.native_record_raw)['原生字段'] == {'Datas': '嵌套字段'}
        assert record.content_hash == hashlib.sha256(record.native_record_raw.encode('utf-8')).hexdigest()
    assert evidence.content_hash == evidence_hash('110011', reader.ENDPOINT, reader.READER_REVISION, list(evidence.pages))
    with pytest.raises(FrozenInstanceError):
        evidence.total_count = 1
    assert isinstance(evidence.pages, tuple) and isinstance(evidence.records, tuple)


def test_record_fragments_preserve_native_numeric_lexemes_and_nested_keys():
    raw = '''{ "other": {"Datas": [1]}, "Datas": [
      {"FSRQ":"2026-01-02","DWJZ":"1.2500","nativeNumber":1.234567890123456789,
       "escaped":"a\\\"Datas\\\"b"}
    ], "tail": true }'''
    record = native_record_fragments(raw)[0]
    assert '1.234567890123456789' in record and '1.2500' in record
    assert record in raw


def test_existing_display_reader_uses_same_exact_values(monkeypatch):
    install(monkeypatch, [encoded(payload(['2026-01-03', '2026-01-02'], total=2))])
    frame = reader.read_fund_nav('110011')
    assert frame.columns.tolist() == ['日期', '单位净值', '累计净值', '涨跌幅']
    assert frame.iloc[0]['单位净值'] == '1.230000000000000000000001'
    assert frame.iloc[0]['累计净值'] == '2.3400'


def test_actual_efinance_adapter_sorts_without_converting_decimal_strings(monkeypatch):
    from data_provider.efinance_fetcher import EfinanceFetcher

    body = payload(['2026-01-03', '2026-01-02'], total=2)
    body['Datas'][1]['DWJZ'] = '0.999999999999999999999999'
    install(monkeypatch, [encoded(body)])
    frame = EfinanceFetcher(sleep_min=0, sleep_max=0).get_fund_nav_history('110011')
    assert frame['日期'].tolist() == ['2026-01-02', '2026-01-03']
    assert frame['单位净值'].tolist() == ['0.999999999999999999999999', '1.230000000000000000000001']


@pytest.mark.parametrize('nav', [
    1.23, True, None, '', '0', '0.0000', '-1', 'NaN', 'Infinity', '1e-6', ' 1.2', '01.2', '1.2 ',
])
def test_invalid_decimal_on_later_page_rejects_entire_batch(monkeypatch, nav):
    calls = install(monkeypatch, [encoded(payload(['2026-01-03', '2026-01-02'])),
                                  encoded(payload(['2026-01-01'], nav=nav))])
    with pytest.raises(ValueError):
        reader.read_fund_nav_evidence('110011')
    assert len(calls) == 2


@pytest.mark.parametrize('raw', [
    b'{"Success":true,"ErrCode":0,"ErrorCode":"0","TotalCount":0,"TotalCount":1,"Datas":[]}',
    b'{"Success":true,"ErrCode":0,"ErrorCode":"0","TotalCount":1,"Datas":[{"FSRQ":"2026-01-01","DWJZ":"1","DWJZ":"2"}]}',
    b'{"Success":true,"ErrCode":0,"ErrorCode":"0","TotalCount":0,"Datas":[],"extra":NaN}',
    b'not json',
    b'\xff',
])
def test_invalid_raw_encoding_or_json_rejected(monkeypatch, raw):
    install(monkeypatch, [raw])
    with pytest.raises(ValueError):
        reader.read_fund_nav_evidence('110011')


def test_future_valuation_date_rejected(monkeypatch):
    install(monkeypatch, [encoded(payload(['9999-12-31'], total=1))])
    with pytest.raises(ValueError, match='未来估值'):
        reader.read_fund_nav_evidence('110011')


def test_total_response_budget_is_independent_from_page_budget(monkeypatch):
    bodies = [encoded(payload(['2026-01-03', '2026-01-02'])), encoded(payload(['2026-01-01']))]
    install(monkeypatch, bodies)
    monkeypatch.setattr(reader, 'MAX_TOTAL_RESPONSE_BYTES', len(bodies[0]) + len(bodies[1]) - 1)
    with pytest.raises(ValueError, match='预算'):
        reader.read_fund_nav_evidence('110011')


def test_tls_failure_does_not_retry_or_return_partial_evidence(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise requests.exceptions.SSLError('控制 TLS 错误')

    monkeypatch.setattr(reader.requests, 'get', fail)
    with pytest.raises(requests.exceptions.SSLError):
        reader.read_fund_nav_evidence('110011')
    assert len(calls) == 1 and calls[0]['verify'] is True


def test_snapshot_identity_binds_fund_endpoint_revision_and_page_bytes(monkeypatch):
    install(monkeypatch, [encoded(payload(['2026-01-03'], total=1))])
    evidence = reader.read_fund_nav_evidence('110011')
    for code, endpoint, revision in [
        ('118001', evidence.endpoint, evidence.reader_revision),
        ('110011', 'https://example.invalid', evidence.reader_revision),
        ('110011', evidence.endpoint, 'other-revision'),
    ]:
        assert evidence_hash(code, endpoint, revision, list(evidence.pages)) != evidence.content_hash
    assert canonical_json({'b': 2, 'a': 1}) == '{"a":1,"b":2}'
