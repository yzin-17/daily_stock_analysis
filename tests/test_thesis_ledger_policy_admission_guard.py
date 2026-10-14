"""政策准入守卫的有限上下文、原读取顺序及来源扩展合同。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from data_provider.thesis_ledger_sources.hithink import SOURCE, policy_admission_guard
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_price_route_access import policy_route_admission_reason, configured_data_route
import src.services.thesis_ledger_price_route_access as access

KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'CASH_DISTRIBUTION'}
TARGET = {'providerId': 'hithink', 'upstreamSource': 'fund-dividend'}


def setup(monkeypatch, *, state='admitted', match=True, snapshot_error=None, provider='hithink', guard=None):
    calls = []
    payload = {'adapterRevision': 'adapter', 'sourceRevision': 'source', 'credentialRevision': 'credential'}
    target = {**TARGET, 'providerId': provider}
    store = SimpleNamespace(
        _route_admission_identity_v3=lambda *args: ('consumer', 'key', provider, target['upstreamSource']),
        _route_admission_payload_v3=lambda row: payload,
        _route_admission_state_v3=lambda value: state,
    )

    def snapshot(provider_id):
        calls.append(('credential', provider_id))
        if snapshot_error:
            raise snapshot_error
        return object()

    store.provider_credential_snapshot = snapshot

    def execute(sql, identity):
        calls.append(('sql', identity))
        return SimpleNamespace(fetchone=lambda: {'row': True})

    connection = SimpleNamespace(execute=execute)
    monkeypatch.setattr(access, 'basic_market_price_route', lambda *args: False)
    monkeypatch.setattr(access, 'configured_data_route', lambda *args: False)
    if guard is not None:
        monkeypatch.setattr(access, 'CAPABILITY_SOURCES', SimpleNamespace(
            source=lambda provider_id: SimpleNamespace(policy_admission_guard=guard)))

    def current(store, key, target, manifest):
        calls.append(('event', target['providerId']))
        return payload

    monkeypatch.setattr('src.services.thesis_ledger_event_v3_adapters.event_admission_reader', lambda *args: current)
    monkeypatch.setattr('src.services.thesis_ledger_control._provider_credential_revision_from_snapshot', lambda snapshot: 'credential')

    def matches(value, key, target, manifest, *, credential_revision):
        calls.append(('match', credential_revision))
        assert value == {**payload, 'admissionState': state}
        return match

    monkeypatch.setattr('src.services.thesis_ledger_market_v3_revisions._market_v3_admission_matches_current', matches)
    return store, connection, target, calls


def test_hithink_preserves_post_event_credential_read(monkeypatch):
    store, connection, target, calls = setup(monkeypatch)
    assert policy_route_admission_reason(store, connection, KEY, target, 'consumer', {}) is None
    assert [call[0] for call in calls] == ['sql', 'event', 'credential', 'match']
    assert calls[2] == ('credential', 'hithink')


@pytest.mark.parametrize('state', ['pending', 'expired', 'revoked'])
def test_hithink_not_admitted_has_no_post_credential_read(monkeypatch, state):
    store, connection, target, calls = setup(monkeypatch, state=state)
    assert policy_route_admission_reason(store, connection, KEY, target, 'consumer', {}) == 'not_admitted'
    assert [call[0] for call in calls] == ['sql', 'event']


@pytest.mark.parametrize('error', [ValueError('credential invalid'), None])
def test_hithink_changed_or_unreadable_credential_rejected(monkeypatch, error):
    store, connection, target, calls = setup(monkeypatch, match=False, snapshot_error=error)
    assert policy_route_admission_reason(store, connection, KEY, target, 'consumer', {}) == 'not_admitted'
    assert sum(call[0] == 'credential' for call in calls) == 1


def test_event_rejection_precedes_source_guard(monkeypatch):
    store, connection, target, calls = setup(monkeypatch)
    monkeypatch.setattr('src.services.thesis_ledger_event_v3_adapters.event_admission_reader',
                        lambda *args: lambda *args: None)
    assert policy_route_admission_reason(store, connection, KEY, target, 'consumer', {}) == 'not_admitted'
    assert [call[0] for call in calls] == ['sql']


@pytest.mark.parametrize('answer', [True, False, None, 1])
def test_new_source_guard_uses_original_policy_entry_and_fails_closed(monkeypatch, answer):
    def guard(context):
        assert dict(context.key) == KEY
        assert context.target['providerId'] == 'new-source'
        assert context.state == 'admitted'
        assert not hasattr(context, 'store') and not hasattr(context, 'connection')
        with pytest.raises(TypeError):
            context.payload['credentialRevision'] = 'changed'
        context.read_credentials()
        return answer

    store, connection, target, calls = setup(monkeypatch, provider='new-source', guard=guard)
    result = policy_route_admission_reason(store, connection, KEY, target, 'consumer', {})
    assert result == (None if answer is True else 'not_admitted')
    assert [call[0] for call in calls] == ['sql', 'event', 'credential']
    assert calls[-1] == ('credential', 'new-source')


def test_registration_rejects_non_callable_guard():
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, policy_admission_guard=1),))
    assert SOURCE.policy_admission_guard is policy_admission_guard


@pytest.mark.parametrize(('state', 'expected'), [('admitted', None), ('pending', 'not_admitted'), ('expired', 'admission_expired')])
def test_other_source_without_guard_keeps_original_state(monkeypatch, state, expected):
    store, connection, target, calls = setup(monkeypatch, state=state, provider='unregistered-source')
    assert policy_route_admission_reason(store, connection, KEY, target, 'consumer', {}) == expected
    assert [call[0] for call in calls] == ['sql', 'event']


def test_configured_route_short_circuits_all_admission_reads(monkeypatch):
    store, connection, target, calls = setup(monkeypatch)
    monkeypatch.setattr(access, 'configured_data_route', lambda *args: True)
    monkeypatch.setattr('src.services.thesis_ledger_provider_dependencies.provider_dependency_ready', lambda *args: True)
    assert policy_route_admission_reason(store, connection, KEY, target, 'consumer', {}) is None
    assert calls == []


def test_all_hithink_quotes_use_exact_configured_path(monkeypatch):
    for key, target, binding in CapabilityRegistry((SOURCE,)).routes():
        if key['capability'] == 'REALTIME_QUOTE':
            assert configured_data_route(key, target)
    # 所有已迁移报价来自唯一绑定，不靠末尾供应商fallback。
    monkeypatch.setattr(access, 'CAPABILITY_SOURCES', SimpleNamespace(resolve=lambda *args: None))
    monkeypatch.setattr(access, 'local_rule_route_matches', lambda *args: False)
    monkeypatch.setattr(access, 'current_data_adapter_revisions', lambda *args: None)
    assert not configured_data_route(
        {**KEY, 'capability': 'REALTIME_QUOTE'},
        {'providerId': 'hithink', 'upstreamSource': 'fund-market-snapshot'},
    )
