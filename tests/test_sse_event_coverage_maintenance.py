"""真实归档加载与双能力首次准入的事务保护，全部使用临时目录和控制库。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import socket

import pytest

from data_provider.thesis_ledger_sources import sse_event_coverage as source
from tests.test_configured_market_routes import store

m = runpy.run_path(str(Path(__file__).parents[1] / 'scripts/local-runtime/sse-event-coverage-admission.py'))
ARTIFACTS = os.environ.get('ARENA_RANGE_ARTIFACTS')
pytestmark = pytest.mark.skipif(not ARTIFACTS, reason='须提供既有真实归档；生产库不可用于维护测试')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('维护离线验收禁止外部调用')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


@pytest.fixture
def archive(monkeypatch):
    root = Path(ARTIFACTS) / 'arena-event-coverage-20261116'
    monkeypatch.setattr(source, 'ROOT', root)
    monkeypatch.setattr(source, 'REVISION', m['PROOF'])
    return root


@pytest.fixture
def prepared(store, archive):
    """原路由身份复用真实快照，执行准入时间和控制库均明确为受控输入。"""
    policy = json.loads((Path(ARTIFACTS) / 'arena-range-admission-20261116/collection.json').read_bytes())['control']['policy']
    routes = [{ 'key': r['key'], 'targets': [{k: t[k] for k in ['providerId', 'upstreamSource']} for t in r['targets']],
        **({'enabled': r['enabled']} if 'enabled' in r else {}) } for r in policy['routes']]
    store.apply_policy_v3({'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'offline-base',
        'revision': 201, 'enabled': True, 'routes': routes})
    now = datetime.now(timezone.utc)
    key = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'HISTORICAL_EXECUTION_CONTEXT'}
    options = dict(key=key, target={'providerId': 'historical-execution-archive', 'upstreamSource': 'qualified-cn-execution'},
        evidence_ref='sha256:' + 'a' * 64, evidence_sha256='a' * 64, scope_symbols=m['SYMBOLS'],
        scope_date_from=m['START'], scope_date_to=m['END'], adapter_revision='historical-execution-archive-v3',
        source_revision='archive-v2:dff009c696ce8e161268297fcf15c25ae69de72eb9ecb3aa0844cb7501cfc675',
        credential_revision='not-required', valid_from=now.isoformat(),
        valid_until=(now + timedelta(hours=2)).isoformat(), recorded_by='offline-execution')
    store.record_route_admission_v3(**{**options, 'scope_date_from': '2026-11-10', 'scope_date_to': '2026-11-10'})
    execution = store.record_route_admission_v3(**options)
    return store, routes, execution


def test_load_preserves_existing_and_requires_reviewed_revision(archive, tmp_path):
    destination = tmp_path / 'production'
    result = m['promote'](archive, destination)
    assert result['files'] == 17
    assert len(m['qualify'](destination)) == 44
    before = (destination / 'proof.json').read_bytes()
    with pytest.raises(ValueError, match='覆盖'):
        m['promote'](archive, destination)
    assert (destination / 'proof.json').read_bytes() == before
    linked = tmp_path / 'linked'
    linked.symlink_to(archive)
    with pytest.raises(ValueError, match='软链接'):
        m['promote'](linked, tmp_path / 'other')


def test_two_exact_records_and_policy_append_verify_without_reapplication(prepared, archive, tmp_path):
    store, routes, execution = prepared
    evidence = tmp_path / 'evidence'
    collected = m['admit']({'mode': 'collect', 'digest': None}, store.database_path, evidence, archive)
    result = m['admit']({'mode': 'apply', 'digest': collected['digest']}, store.database_path, evidence, archive)
    assert len(result['records']) == 2 and all(r['recordVersion'] == 1 for r in result['records'])
    assert all(r['validUntil'] == execution['validUntil'] for r in result['records'])
    assert result['protectedSha256'] == collected['before']['protectedSha256']
    with pytest.raises(ValueError, match='状态变化|已应用'):
        m['admit']({'mode': 'apply', 'digest': collected['digest']}, store.database_path, evidence, archive)
    store.apply_policy_v3({'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'offline-append',
        'revision': 202, 'enabled': True, 'routes': routes + [
            {'key': key, 'enabled': True, 'targets': [m['TARGET']]} for key in m['KEYS']]})
    verified = m['admit']({'mode': 'verify', 'digest': collected['digest']}, store.database_path, evidence, archive)
    assert verified['records'] == result['records']


def test_partial_write_never_enables_source_or_overwrites_first_record(prepared, archive, tmp_path, monkeypatch):
    store, _, _ = prepared
    evidence = tmp_path / 'evidence'
    collected = m['admit']({'mode': 'collect', 'digest': None}, store.database_path, evidence, archive)
    original = type(store).record_route_admission_v3
    calls = []
    def fail_second(self, **options):
        calls.append(options['key'])
        if len(calls) == 2:
            raise RuntimeError('受控第二条写入失败')
        return original(self, **options)
    monkeypatch.setattr(type(store), 'record_route_admission_v3', fail_second)
    with pytest.raises(RuntimeError, match='第二条'):
        m['admit']({'mode': 'apply', 'digest': collected['digest']}, store.database_path, evidence, archive)
    state = m['control'](store)
    assert state['sourceEnabled'] is False and len(state['target']['thesis_ledger_route_admission_v3']) == 1
    assert state['protectedSha256'] == collected['before']['protectedSha256']
    with pytest.raises(ValueError, match='状态变化'):
        m['admit']({'mode': 'apply', 'digest': collected['digest']}, store.database_path, evidence, archive)


def test_expired_qualification_cannot_change_control(prepared, archive, tmp_path):
    store, _, _ = prepared
    evidence = tmp_path / 'evidence'
    collected = m['admit']({'mode': 'collect', 'digest': None}, store.database_path, evidence, archive)
    old = json.loads((evidence / (collected['digest'] + '.json')).read_bytes())
    old['at'] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    raw = m['canonical'](old)
    digest = sha256(raw).hexdigest()
    (evidence / (digest + '.json')).write_bytes(raw)
    with pytest.raises(ValueError, match='过期'):
        m['admit']({'mode': 'apply', 'digest': digest}, store.database_path, evidence, archive)
    assert m['control'](store) == collected['before']
