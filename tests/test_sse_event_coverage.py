"""真实官方事件归档及实际注册、临时控制库、事件消费链的离线验证。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import pytest

from data_provider.sse_event_coverage import EventCoverageArchive, validate_material, label_date
from data_provider.thesis_ledger_sources import sse_event_coverage as source
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store

ARTIFACTS = os.environ.get('ARENA_RANGE_ARTIFACTS')
real = pytest.mark.skipif(not ARTIFACTS, reason='须提供已采集的真实官方目录与公告，不能用受控事实代替')
TARGET = {'providerId': source.PROVIDER, 'upstreamSource': source.UPSTREAM}
KEYS = [{ 'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': cap }
        for cap in ['CASH_DISTRIBUTION', 'SPLIT_EVENT']]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('官方归档离线验收禁止外部调用')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


@pytest.fixture
def archive():
    return Path(ARTIFACTS) / 'arena-event-coverage-20261116'


def test_source_is_default_disabled_and_only_declares_two_exact_keys():
    assert source.SOURCE.default_enabled is False
    assert [binding.key.route_key() for binding in source.SOURCE.bindings] == KEYS
    assert all(binding.admission_required for binding in source.SOURCE.bindings)
    for key in KEYS:
        assert CAPABILITY_SOURCES.resolve(key, TARGET) is not None
        assert CAPABILITY_SOURCES.resolve({**key, 'assetType': 'STOCK'}, TARGET) is None
        assert CAPABILITY_SOURCES.resolve({**key, 'market': 'HK'}, TARGET) is None


def test_conflicting_table_dates_and_prose_dates_are_distinguished():
    with pytest.raises(ValueError, match='歧义'):
        label_date('除息日2026年1月19日除息日2026年11月10日', '除息日')
    assert label_date('权益登记日2019年12月10日权益登记日在册的款项于2019年12月13日划入', '权益登记日') == '2019-12-10'


@real
def test_real_complete_indexes_and_publications_cover_only_reviewed_window(archive):
    adapter = EventCoverageArchive(archive)
    pack = adapter.scope('588000.SH', '2026-11-02', '2026-11-16', '2026-11-16T05:01:00Z')
    assert pack['scopeSymbols'] == ['510300.SH', '588000.SH'] and len(pack['publications']) == 14
    assert max(x['effectiveDate'] for x in pack['publications']) == '2026-01-19'
    for symbol, start, end, at in [('159516.SZ', '2026-11-02', '2026-11-16', '2026-11-16T05:01:00Z'),
        ('588000.SH', '2026-11-01', '2026-11-16', '2026-11-16T05:01:00Z'),
        ('588000.SH', '2026-11-02', '2026-11-17', '2026-11-17T05:01:00Z'),
        ('588000.SH', '2026-11-02', '2026-11-16', '2026-11-15T05:01:00Z')]:
        with pytest.raises(ValueError): adapter.scope(symbol, start, end, at)


@real
@pytest.mark.parametrize('mutation', ['filter', 'pagination', 'duplicate', 'empty', 'business-error',
    'wrong-symbol', 'missing-publication', 'wrong-date', 'event-in-window', 'unknown-conversion', 'future-collection'])
def test_semantically_invalid_material_cannot_be_resealed_as_complete(archive, mutation):
    pack = json.loads((archive / 'proof.json').read_bytes())
    originals = {p.name: p.read_bytes() for p in (archive / 'originals').iterdir()}
    index = pack['indexes'][0]
    raw = originals[index['sha256']].decode().strip()
    body = json.loads(raw[1:-1] if raw.startswith('(') else raw)
    if mutation == 'filter':
        parts = urlsplit(index['reference'])
        params = parse_qs(parts.query, keep_blank_values=True)
        params['TITLE'] = ['分红']
        index['reference'] = urlunsplit(parts._replace(query=urlencode(params, doseq=True)))
    elif mutation == 'pagination': body['pageHelp']['total'] += 1
    elif mutation == 'duplicate':
        body['result'][1] = body['result'][0]
        body['pageHelp']['data'] = body['result']
    elif mutation == 'empty':
        body['result'] = body['pageHelp']['data'] = []
        body['pageHelp']['total'] = 0
    elif mutation == 'business-error': body['actionErrors'] = ['denied']
    elif mutation == 'wrong-symbol': body['result'][0]['SECURITY_CODE'] = '588000'
    elif mutation == 'missing-publication': pack['publications'].pop()
    elif mutation == 'wrong-date': pack['publications'][0]['effectiveDate'] = '2026-11-10'
    elif mutation == 'event-in-window': pack['scopeDateFrom'] = '2026-01-19'
    elif mutation == 'unknown-conversion':
        body['result'][0]['TITLE'] = '基金份额折算公告'
        body['pageHelp']['data'] = body['result']
    elif mutation == 'future-collection': index['observedAt'] = '2026-11-15T23:00:00+08:00'
    changed = json.dumps(body, ensure_ascii=False).encode()
    index['sha256'] = sha256(changed).hexdigest()
    originals[index['sha256']] = changed
    with pytest.raises((ValueError, KeyError)):
        validate_material(pack, originals.__getitem__)


@real
def test_original_or_revision_changes_never_reuse_pdf_cache(archive, tmp_path):
    root = tmp_path / 'archive'
    shutil.copytree(archive, root)
    adapter = EventCoverageArchive(root)
    pack = adapter.material()
    digest = pack['publications'][0]['sha256']
    (root / 'originals' / digest).write_bytes(b'changed')
    with pytest.raises(ValueError, match='摘要'): adapter.material()
    (root / 'proof.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='修订'): adapter.material()


@real
def test_actual_registry_control_and_event_contract_with_real_archive(store, archive, tmp_path, monkeypatch):
    monkeypatch.setattr(source, 'ROOT', archive)
    monkeypatch.setattr(source, 'REVISION', sha256((archive / 'proof.json').read_bytes()).hexdigest())
    proof = MappingEvidenceStore(tmp_path / 'thesis-ledger-mapping-evidence').put((archive / 'proof.json').read_bytes())
    now = datetime.now(timezone.utc)
    for key in KEYS:
        store.record_route_admission_v3(key=key, target=TARGET, evidence_ref=proof, evidence_sha256=proof[7:],
            scope_symbols=['510300.SH', '588000.SH'], scope_date_from='2026-11-02', scope_date_to='2026-11-16',
            adapter_revision=source.ADAPTER_REVISION, source_revision=source.revisions()['sourceRevision'],
            credential_revision='not-required', valid_from=now.isoformat(),
            valid_until=(now + timedelta(hours=24)).isoformat(), recorded_by='offline-real-archive')
    store.save_provider_config(source.PROVIDER, {'requestId': 'offline-explicit-enable', 'enabled': True})
    store.apply_policy_v3({'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'offline-policy',
        'revision': 1, 'enabled': True, 'routes': [{'key': key, 'targets': [TARGET]} for key in KEYS]})
    runtime = ThesisLedgerProviderRuntime(store)
    catalog = runtime.market_route_catalog_v3()
    for symbol in ['588000.SH', '510300.SH']:
        for key in KEYS:
            request = {'contractVersion': 1, 'requestId': 'offline-real-event-window', 'symbol': symbol,
                'routeKey': key, 'routeTarget': {**TARGET, 'routeIndex': 0}, 'desiredRevision': 1,
                'effectivePolicyRevision': 1, 'catalogRevision': catalog['catalogRevision'],
                'start': '2026-11-02', 'end': '2026-11-16', 'dataAsOf': '2026-11-16T05:01:00Z'}
            result = execute_event_request(runtime, request)
            assert result['facts'] == [] and result['coverage'] == {'complete': True, 'admissionEvidenceRef': proof}
            assert result['admission']['sourceRevision'] == source.revisions()['sourceRevision']
            bad = {**request, 'start': '2026-11-01'}
            with pytest.raises(EventV3Error): execute_event_request(runtime, bad)
    store.revoke_route_admission_v3(key=KEYS[0], target=TARGET, reason='offline-revoke')
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, {**request, 'routeKey': KEYS[0]})
