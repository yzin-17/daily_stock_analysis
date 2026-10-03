"""研究规则原文、适用区间与真实配置/采集时刻的确定性门禁。"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest
import requests

from data_provider import efunds_nav_disclosure as provider
from data_provider.eastmoney_nav_evidence import hash_raw
from src.services import thesis_ledger_nav_rules as rules


NOW = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)
AS_OF = '2026-09-30T16:00:00Z'
PDF = b'%PDF-1.7\ncontrolled test document\n%%EOF'


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(rules, 'datetime', Clock)
    monkeypatch.setattr(provider, 'datetime', Clock)


def decision(**changes):
    return json.dumps({
        'schemaVersion': 'nav-research-default-v1', 'symbol': '161725.OF',
        'fundType': 'domestic', 'delayWorkdays': 1,
        'applicableRange': {'startDate': '2026-09-07', 'endDate': '2026-09-21'},
        'configuredAt': '2026-09-30T14:00:00Z', 'decision': '用户显式选择普通基金 T+1 研究默认',
        **changes,
    }, ensure_ascii=False, indent=2)


class Response:
    status_code = 200

    def __init__(self, raw=PDF):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('控制 HTTP 错误')

    def iter_content(self, **kwargs):
        yield self.raw


def install_document(monkeypatch, symbol='110011.OF', body=PDF):
    # 仅在受控测试中替换审查摘要；在线验收使用原始生产 registry。
    spec = replace(provider.EFUNDS_NAV_RULES[symbol], document_hash=sha256(PDF).hexdigest())
    monkeypatch.setitem(provider.EFUNDS_NAV_RULES, symbol, spec)
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        assert url == spec.url
        assert kwargs['verify'] is True and kwargs['stream'] is True
        assert kwargs['allow_redirects'] is False
        return Response(body)

    monkeypatch.setattr(provider.requests, 'get', get)
    return calls


def test_domestic_default_binds_user_decision_without_source_publication():
    raw = decision()
    evidence = rules.build_domestic_nav_rule(raw, evidence_ref='research-config://test/domestic', data_as_of=AS_OF)
    assert evidence.rule['delayWorkdays'] == 1 and evidence.rule['basis'] == 'domestic-default'
    assert evidence.rule['documentHash'] == hash_raw(raw)
    assert evidence.rule['contentHash'] == hash_raw(evidence.rule_raw)
    assert evidence.document_raw.decode('utf-8') == raw
    assert evidence.document_kind == 'research-config'
    assert evidence.rule['configuredAt'] == '2026-09-30T14:00:00Z'
    assert evidence.captured_at == NOW.isoformat()
    assert 'sourcePublishedAt' not in evidence.rule_raw
    mutated = evidence.rule
    mutated['delayWorkdays'] = 9
    assert evidence.rule['delayWorkdays'] == 1
    with pytest.raises(FrozenInstanceError):
        evidence.rule_raw = '{}'


@pytest.mark.parametrize('symbol, delay', [('110011.OF', 1), ('118001.OF', 2)])
def test_qdii_downloads_verified_document_and_emits_current_rule(monkeypatch, symbol, delay):
    calls = install_document(monkeypatch, symbol)
    evidence = rules.read_qdii_nav_rule(symbol, start='2026-09-07', end='2026-09-21', data_as_of=AS_OF)
    assert len(calls) == 1
    assert evidence.document_raw == PDF and evidence.document_kind == 'fund-prospectus'
    assert evidence.rule['symbol'] == symbol and evidence.rule['fundType'] == 'qdii'
    assert evidence.rule['delayWorkdays'] == delay and evidence.rule['basis'] == 'verified-fund-rule'
    assert evidence.rule['documentHash'] == sha256(PDF).hexdigest()
    assert evidence.rule['contentHash'] == hash_raw(evidence.rule_raw)
    assert evidence.rule['configuredAt'] == evidence.captured_at == NOW.isoformat()


@pytest.mark.parametrize('changes', [
    {'symbol': '110011.OF'}, {'fundType': 'qdii'}, {'fundType': 'unknown'}, {'delayWorkdays': 2},
    {'delayWorkdays': True}, {'decision': ''}, {'schemaVersion': 'old'}, {'symbol': '510300.SH'},
    {'configuredAt': '2026-09-30T16:00:00.000001Z'}, {'configuredAt': '2026-09-30T14:00:00.0000001Z'},
    {'configuredAt': '2026-09-30T14:00:00+00:99'}, {'configuredAt': '2026-09-30T14:00:00'},
    {'applicableRange': {'startDate': '2026-09-21', 'endDate': '2026-09-07'}},
    {'applicableRange': {'startDate': '2026-09-07', 'endDate': '2026-09-21', 'extra': True}},
    {'unexpected': True},
])
def test_invalid_domestic_default_rejected(changes):
    with pytest.raises(ValueError):
        rules.build_domestic_nav_rule(decision(**changes), evidence_ref='research-config://test/domestic', data_as_of=AS_OF)


@pytest.mark.parametrize('raw', [
    '{}', 'not json', 'null', 'x' * 65537, '{"symbol":"161725.OF","symbol":"118001.OF"}',
])
def test_missing_oversized_or_duplicate_default_raw_rejected(raw):
    with pytest.raises(ValueError):
        rules.build_domestic_nav_rule(raw, evidence_ref='research-config://test/domestic', data_as_of=AS_OF)


@pytest.mark.parametrize('ref', ['', 'research-config://', 'https://example.invalid', 'research-config://x '])
def test_domestic_reference_cannot_masquerade_as_fund_document(ref):
    with pytest.raises(ValueError):
        rules.build_domestic_nav_rule(decision(), evidence_ref=ref, data_as_of=AS_OF)


def test_domestic_capture_after_cutoff_rejected():
    with pytest.raises(ValueError, match='采集晚于'):
        rules.build_domestic_nav_rule(decision(), evidence_ref='research-config://test/domestic',
                                      data_as_of='2026-09-30T14:59:59.999999Z')


@pytest.mark.parametrize('symbol, start, end', [
    ('999999.OF', '2026-09-07', '2026-09-21'), ('118001.OF', '2026-07-01', '2026-09-21'),
    ('110011.OF', '2008-06-19', '2026-09-21'), ('110011.OF', '2026-09-07', '2026-10-01'),
    ('110011.OF', '2026-09-21', '2026-09-07'), ('110011.OF', '2026-02-30', '2026-09-07'),
])
def test_unknown_or_outside_audited_rule_fails_before_network(monkeypatch, symbol, start, end):
    calls = []
    monkeypatch.setattr(provider.requests, 'get', lambda *args, **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError):
        rules.read_qdii_nav_rule(symbol, start=start, end=end, data_as_of=AS_OF)
    assert calls == []


@pytest.mark.parametrize('body', [b'%PDF-1.7\nchanged', b'not a pdf', b''])
def test_changed_or_missing_document_rejected(monkeypatch, body):
    install_document(monkeypatch, body=body)
    with pytest.raises(ValueError, match='原文'):
        rules.read_qdii_nav_rule('110011.OF', start='2026-09-07', end='2026-09-21', data_as_of=AS_OF)


def test_qdii_capture_one_microsecond_after_cutoff_rejected(monkeypatch):
    install_document(monkeypatch)
    with pytest.raises(ValueError, match='采集晚于'):
        rules.read_qdii_nav_rule(
            '110011.OF', start='2026-09-07', end='2026-09-21',
            data_as_of='2026-09-30T14:59:59.999999Z',
        )


def test_document_byte_budget(monkeypatch):
    install_document(monkeypatch)
    monkeypatch.setattr(provider, 'MAX_DOCUMENT_BYTES', len(PDF) - 1)
    with pytest.raises(ValueError, match='预算'):
        rules.read_qdii_nav_rule('110011.OF', start='2026-09-07', end='2026-09-21', data_as_of=AS_OF)


def test_document_deadline(monkeypatch):
    install_document(monkeypatch)
    times = iter([0, 31])
    monkeypatch.setattr(provider.time, 'monotonic', lambda: next(times))
    with pytest.raises(ValueError, match='预算'):
        rules.read_qdii_nav_rule('110011.OF', start='2026-09-07', end='2026-09-21', data_as_of=AS_OF)


def test_tls_failure_has_no_retry(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise requests.exceptions.SSLError('控制证书错误')

    monkeypatch.setattr(provider.requests, 'get', fail)
    with pytest.raises(requests.exceptions.SSLError):
        rules.read_qdii_nav_rule('110011.OF', start='2026-09-07', end='2026-09-21', data_as_of=AS_OF)
    assert len(calls) == 1 and calls[0]['verify'] is True


def test_redirect_rejected(monkeypatch):
    install_document(monkeypatch)
    monkeypatch.setattr(Response, 'status_code', 302)
    with pytest.raises(ValueError, match='状态'):
        rules.read_qdii_nav_rule('110011.OF', start='2026-09-07', end='2026-09-21', data_as_of=AS_OF)


@pytest.mark.parametrize('url', [
    'http://cdn.efunds.com.cn/a.pdf', 'https://example.invalid/a.pdf',
    'https://cdn.efunds.com.cn/a.pdf?redirect=1',
])
def test_document_source_cannot_be_changed(monkeypatch, url):
    spec = replace(provider.EFUNDS_NAV_RULES['110011.OF'], url=url)
    calls = []
    monkeypatch.setattr(provider.requests, 'get', lambda *args, **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match='地址'):
        provider.read_verified_nav_document(spec)
    assert calls == []


def test_equivalent_offset_at_cutoff_is_accepted(monkeypatch):
    install_document(monkeypatch)
    evidence = rules.read_qdii_nav_rule(
        '110011.OF', start='2026-09-07', end='2026-09-21', data_as_of='2026-09-30T23:00:00+08:00',
    )
    assert evidence.captured_at == NOW.isoformat()
