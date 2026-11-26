"""真实包可选验证及维护入口的受控拒绝、首次准入保护。"""

import importlib.util
from pathlib import Path
import socket

import pytest
from tests.test_configured_market_routes import store

PATH = Path(__file__).parents[1] / 'scripts/local-runtime/historical-execution-admission.py'
SPEC = importlib.util.spec_from_file_location('execution_admission_maintenance', PATH)
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('维护验证禁止外部网络')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


def test_rejects_wrong_index_before_creating_destination(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'index.json').write_text('{"version":2,"contexts":{},"dailyContexts":{}}')
    destination = tmp_path / 'destination'
    with pytest.raises(ValueError, match='索引'):
        maintenance.promote(source, destination)
    assert not destination.exists()


def test_rejects_symlink_before_loading(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    linked = tmp_path / 'linked'
    linked.symlink_to(source)
    with pytest.raises(ValueError, match='软链接'):
        maintenance.promote(linked, tmp_path / 'destination')


def test_promotion_requires_explicit_confirmation():
    with pytest.raises(ValueError, match='显式确认'):
        maintenance.main({'mode': 'promote', 'source': '/unused', 'confirm': 'wrong'})


def test_expected_scope_is_canonical_and_bounded():
    record = maintenance.expected_record('a' * 64, {'adapterRevision': 'v2'})
    assert record['scopeSymbols'] == ['510300.SH', '588000.SH']
    assert record['scopeDateFrom'] == record['scopeDateTo'] == '2026-11-10'
    assert record['recordVersion'] == 1


def test_policy_heartbeat_keeps_revision_and_routes_in_guard():
    policy = {'revision': 200, 'enabled': True, 'routes': [{'key': maintenance.KEY}], 'appliedAt': 'first'}
    same_revision = {**policy, 'appliedAt': 'heartbeat'}
    assert maintenance.policy_identity(policy) == maintenance.policy_identity(same_revision)
    assert maintenance.policy_identity(policy) != maintenance.policy_identity({**same_revision, 'revision': 201})
    assert maintenance.policy_identity(policy) != maintenance.policy_identity({**same_revision, 'routes': []})
    assert policy['appliedAt'] == 'first'


def test_unrecognized_mode_cannot_open_database(tmp_path):
    with pytest.raises(ValueError, match='维护请求'):
        maintenance.admit({'mode': 'retry', 'digest': None}, tmp_path / 'db', tmp_path, tmp_path)
    assert not (tmp_path / 'db').exists()


def test_first_admission_enables_only_target_and_rejects_reapplication(store, tmp_path, monkeypatch):
    from data_provider.thesis_ledger_sources.historical_execution import REVISION
    monkeypatch.setattr(maintenance, 'INDEX', REVISION)
    monkeypatch.setattr(maintenance, 'qualify', lambda root: [{'controlled': 'control-flow-only'}])
    directory = tmp_path / 'evidence'
    collected = maintenance.admit({'mode': 'collect', 'digest': None}, store.database_path, directory, tmp_path)
    before = maintenance.protection(store)
    payload = {'mode': 'apply', 'digest': collected['digest']}
    result = maintenance.admit(payload, store.database_path, directory, tmp_path)
    assert result['record']['scopeSymbols'] == ['510300.SH', '588000.SH']
    assert result['record']['validUntil'] is None
    assert maintenance.protection(store)['protectedSha256'] == before['protectedSha256']
    assert maintenance.state(store)['sourceEnabled'] is True
    verified = maintenance.admit({**payload, 'mode': 'verify'}, store.database_path, directory, tmp_path)
    assert verified['record'] == result['record']
    with pytest.raises(ValueError, match='控制状态变化'):
        maintenance.admit(payload, store.database_path, directory, tmp_path)


def test_tampered_evidence_cannot_admit(store, tmp_path, monkeypatch):
    from data_provider.thesis_ledger_sources.historical_execution import REVISION
    monkeypatch.setattr(maintenance, 'INDEX', REVISION)
    monkeypatch.setattr(maintenance, 'qualify', lambda root: [])
    directory = tmp_path / 'evidence'
    collected = maintenance.admit({'mode': 'collect', 'digest': None}, store.database_path, directory, tmp_path)
    (directory / (collected['digest'] + '.json')).write_text('{}')
    with pytest.raises(ValueError, match='摘要变化'):
        maintenance.admit({'mode': 'apply', 'digest': collected['digest']}, store.database_path, directory, tmp_path)
    assert maintenance.protection(store)['targetAdmission'] == []


@pytest.mark.parametrize('prior_version', [1, 2])
def test_range_entry_records_revision_validity_from_known_existing_versions(store, tmp_path, monkeypatch, prior_version):
    """受控资格结果只验证维护流转，不认证新的真实材料覆盖。"""
    import runpy
    from datetime import datetime, timedelta, timezone
    from data_provider.thesis_ledger_sources.historical_execution import REVISION, ADAPTER_REVISION
    module = runpy.run_path(str(PATH.with_name('historical-execution-range.py')))
    m = module['admit_range'].__globals__
    monkeypatch.setitem(m, 'RANGE_INDEX', REVISION)
    monkeypatch.setitem(m, 'qualify_range', lambda root: [{'controlled': '维护流转'}])
    store.save_provider_config(m['TARGET']['providerId'], {'requestId': 'offline-enable', 'enabled': True})
    now = datetime.now(timezone.utc)
    for version in range(prior_version):
        digest = str(version + 1) * 64
        store.record_route_admission_v3(key=m['KEY'], target=m['TARGET'],
            evidence_ref='sha256:' + digest, evidence_sha256=digest,
            scope_symbols=m['SYMBOLS'], scope_date_from=m['RANGE_START'], scope_date_to=m['RANGE_END'],
            adapter_revision=ADAPTER_REVISION, source_revision='archive-v2:' + REVISION,
            credential_revision='not-required', valid_from=(now - timedelta(days=2)).isoformat(),
            valid_until=(now - timedelta(days=1)).isoformat(), recorded_by='offline-prior')
    directory = tmp_path / 'range-evidence'
    directory.mkdir()
    store.apply_policy_v3({'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'offline-range',
        'revision': 1, 'enabled': True, 'routes': [{'key': m['KEY'], 'enabled': True, 'targets': [m['TARGET']]}]})
    assert not store.effective_policy_v3()['routes'][0]['targets'][0]['eligible']
    collected = m['admit_range']({'mode': 'collect', 'digest': None}, store.database_path, tmp_path, directory)
    payload = {'mode': 'apply', 'digest': collected['digest']}
    result = m['admit_range'](payload, store.database_path, tmp_path, directory)
    assert result['record']['recordVersion'] == prior_version + 1
    assert result['record']['validUntil'] is None
    assert store.effective_policy_v3()['routes'][0]['targets'][0]['eligible']
    assert m['admit_range']({**payload, 'mode': 'verify'}, store.database_path, tmp_path, directory)['record'] == result['record']
    with pytest.raises(ValueError, match='控制已变化'):
        m['admit_range'](payload, store.database_path, tmp_path, directory)


def test_range_policy_projection_ignores_only_own_derived_eligibility():
    import runpy
    from copy import deepcopy
    m = runpy.run_path(str(PATH.with_name('historical-execution-range.py')))
    identity = m['range_policy_identity']
    route = {'key': m['KEY'], 'enabled': True, 'reason': 'admission_expired',
             'targets': [{**m['TARGET'], 'routeIndex': 0, 'eligible': False, 'reason': 'admission_expired'}]}
    before = {'revision': 203, 'enabled': True, 'routes': [route], 'appliedAt': 'before'}
    after = deepcopy(before)
    after['routes'][0]['reason'] = None
    after['routes'][0]['targets'][0].update(eligible=True, reason=None)
    assert identity(before) == identity(after)
    for mutation in ('revision', 'enabled', 'target', 'other-target'):
        changed = deepcopy(after)
        if mutation == 'revision': changed['revision'] = 204
        elif mutation == 'enabled': changed['routes'][0]['enabled'] = False
        elif mutation == 'target': changed['routes'][0]['targets'][0]['upstreamSource'] = 'other'
        else: changed['routes'][0]['targets'].append({'providerId': 'other', 'eligible': False, 'reason': 'unknown'})
        assert identity(before) != identity(changed)
    assert before['routes'][0]['targets'][0]['eligible'] is False
