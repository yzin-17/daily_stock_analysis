"""精确 NAV 接口的路由、来源绑定、读取竞态与鉴权验收。"""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_provider.eastmoney_fund_identity import identity_from_raw
from data_provider.eastmoney_nav_evidence import hash_raw
from src.services import thesis_ledger_nav_production as production
from src.services.thesis_ledger_nav_request import NAV_KEY, NAV_TARGET, NAV_ADAPTER_REVISION, NAV_SOURCE_REVISION, NavProductionError
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
from tests.test_thesis_ledger_market_v3_admission_runtime import _CatalogStore, _identity
from tests.test_thesis_ledger_nav_calendar import source, rule, decision, Clock, workdays, AS_OF


@pytest.fixture
def state(monkeypatch):
    from src.services import thesis_ledger_nav_calendar as calendar, thesis_ledger_nav_rules as rules
    for module in [production, calendar, rules]:
        monkeypatch.setattr(module, 'datetime', Clock)
    monkeypatch.setattr(calendar, 'read_nav_research_workdays', workdays)
    admission = {'consumer': 'thesis-ledger', 'routeKey': deepcopy(NAV_KEY), 'target': deepcopy(NAV_TARGET),
                 'status': 'admitted', 'admissionState': 'admitted', 'scopeSymbols': ['161725.OF'],
                 'scopeDateFrom': '2026-09-01', 'scopeDateTo': '2026-09-30', 'evidenceRef': 'fixture://nav',
                 'evidenceSha256': 'a' * 64, 'adapterRevision': NAV_ADAPTER_REVISION,
                 'sourceRevision': NAV_SOURCE_REVISION, 'credentialRevision': 'not-required',
                 'validFrom': '2020-01-01T00:00:00Z', 'validUntil': '2099-01-01T00:00:00Z',
                 'recordVersion': 1, 'recordedAt': '2020-01-01T00:00:00Z',
                 'invalidatedAt': None, 'invalidationReason': None, 'recordedBy': 'private-reviewer'}
    store = _CatalogStore({_identity(NAV_KEY, NAV_TARGET): admission})
    store.providers.append({**PROVIDER_MANIFESTS['efinance'], 'configured': True, 'enabled': True,
                            'credentialConfigured': False, 'tombstone': None})
    policy = {'contractVersion': 3, 'enabled': True, 'revision': 5, 'sourceDesiredRevision': 5,
              'routes': [{'key': deepcopy(NAV_KEY), 'targets': [
                  {**NAV_TARGET, 'routeIndex': 0, 'eligible': True, 'reason': None}]}]}
    store.effective_policy_v3 = lambda: deepcopy(policy)
    runtime = ThesisLedgerProviderRuntime(store=store)
    request = {'contractVersion': 3, 'requestId': 'nav-test', 'symbol': '161725.OF', 'fundType': 'domestic',
               'routeKey': deepcopy(NAV_KEY), 'routeTarget': {**NAV_TARGET, 'routeIndex': 0},
               'desiredRevision': 5, 'effectivePolicyRevision': 5,
               'catalogRevision': runtime.market_route_catalog_v3()['catalogRevision'],
               'start': '2026-09-09', 'end': '2026-09-14', 'dataAsOf': AS_OF,
               'warmupPeriods': 2, 'tailTradingDays': 2, 'visibilityMode': 'research-assumption',
               'calendarDecisionRaw': decision(), 'domesticRuleDecisionRaw': rule().document_raw.decode()}
    identity = identity_from_raw('var reData={datas:[["161725","含 QDII 的名字","指数型-股票"]]};',
                                 '161725', '2026-09-30T15:00:00Z')
    readers = {'identity_reader': Mock(return_value=identity), 'nav_reader': Mock(return_value=source())}
    return runtime, request, readers, admission, policy


def execute(state, **kwargs):
    runtime, request, readers, *_ = state
    return production.execute_nav_request(runtime, request, **{**readers, **kwargs})


def test_exact_response_preserves_native_pages_identity_and_research_config(state):
    result = execute(state)
    assert result['coverage'] == {'complete': True, 'startDate': '2026-09-07', 'endDate': '2026-09-14'}
    assert result['source']['responseHash'] == hash_raw(result['responseRaw'])
    assert 'recordedBy' not in result['admission']
    envelope = json.loads(result['responseRaw'])
    assert envelope['identity']['sourceType'] == '指数型-股票'
    assert envelope['navSource']['pages'][0]['rawResponse'] == source().pages[0].raw_response
    assert '2026-09-11' not in result['calendar']['tradingDates']
    assert '2026-09-12' in result['calendar']['valuationDates']
    for fact, raw in zip(result['facts'], result['publicationRecords']):
        record = json.loads(raw['rawRecord'])
        native = json.loads(record['nativeRecordRaw'])
        assert native['DWJZ'] == fact['nav']
        assert fact['publicationEvidence']['rawRecordHash'] == hash_raw(raw['rawRecord'])
        assert fact['publicationEvidence']['evidenceRef'] == result['navVisibility']['rule']['evidenceRef']
        assert record in envelope['records']
        assert 'sourcePublishedAt' not in record


@pytest.mark.parametrize('field,value', [
    ('visibilityMode', 'strict-publication'), ('symbol', '110011.OF'), ('desiredRevision', 6),
    ('catalogRevision', 1), ('warmupPeriods', True), ('routeTarget', {**NAV_TARGET, 'routeIndex': 1}),
    ('calendarDecisionRaw', '{}'), ('domesticRuleDecisionRaw', '{}'), ('fundType', []),
])
def test_invalid_request_or_initial_gate_has_zero_upstream_calls(state, field, value):
    state[1][field] = value
    with pytest.raises(NavProductionError):
        execute(state)
    state[2]['identity_reader'].assert_not_called()
    state[2]['nav_reader'].assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('adapterRevision', 'efinance-data-fund_nav_history-em-v1'), ('validUntil', '2020-01-01T00:00:00Z'),
    ('scopeSymbols', []), ('recordedAt', '2099-01-01T00:00:00Z'), ('invalidatedAt', '2026-01-01T00:00:00Z'),
])
def test_old_or_invalid_admission_never_reads_source(state, field, value):
    state[3][field] = value
    with pytest.raises(NavProductionError):
        execute(state)
    state[2]['identity_reader'].assert_not_called()


@pytest.mark.parametrize('field,value', [('scopeDateFrom', '2026-09-09'), ('scopeDateTo', '2026-09-14')])
def test_final_gate_includes_actual_warmup_and_tail(state, field, value):
    state[3][field] = value
    with pytest.raises(NavProductionError, match='not_admitted'):
        execute(state)
    state[2]['nav_reader'].assert_called_once()


@pytest.mark.parametrize('change', ['revoke', 'policy', 'source', 'disable'])
def test_read_race_rejects_late_result(state, change):
    def changed(*args):
        if change == 'revoke':
            state[3]['admissionState'] = 'revoked'
        elif change == 'policy':
            state[4]['revision'] += 1
        elif change == 'source':
            state[3]['sourceRevision'] = 'old'
        else:
            state[4]['enabled'] = False
        return source()
    state[2]['nav_reader'].side_effect = changed
    with pytest.raises(NavProductionError):
        execute(state)


@pytest.mark.parametrize('mutation', [
    lambda identity: replace(identity, fund_type='qdii'),
    lambda identity: replace(identity, captured_at='2026-10-01T03:00:00Z'),
])
def test_identity_tampering_or_future_capture_prevents_nav_read(state, mutation):
    reader = state[2]['identity_reader']
    reader.return_value = mutation(reader.return_value)
    with pytest.raises(NavProductionError):
        execute(state)
    state[2]['nav_reader'].assert_not_called()


def test_insufficient_tail_and_corrupt_source_rejected(state):
    state[1]['tailTradingDays'] = 100
    with pytest.raises(NavProductionError, match='insufficient_evidence'):
        execute(state)
    state[1]['tailTradingDays'] = 2
    state[2]['nav_reader'].return_value = replace(source(), content_hash='0' * 64)
    with pytest.raises(NavProductionError, match='insufficient_evidence'):
        execute(state)


def test_security_is_rechecked_after_upstream(state):
    guard = Mock(side_effect=[None, PermissionError('token rotated')])
    with pytest.raises(PermissionError):
        execute(state, check_security=guard)
    assert guard.call_count == 2
    state[2]['nav_reader'].assert_called_once()


def test_http_authentication_and_actual_router_production(state, monkeypatch):
    import api.thesis_ledger_nav_v3 as api
    import src.services.thesis_ledger_provider_runtime as runtime_module
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'test-token')
    monkeypatch.setattr(runtime_module, 'get_thesis_ledger_runtime', lambda: state[0])
    for name, reader in state[2].items():
        monkeypatch.setattr(production, {'identity_reader': 'read_fund_identity', 'nav_reader': 'read_fund_nav_evidence'}[name], reader)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v3')
    with TestClient(app) as client:
        path = '/api/v3/thesis-ledger/backtest/nav-inputs'
        assert client.post(path, json=state[1]).status_code == 401
        state[2]['identity_reader'].assert_not_called()
        headers = {'Authorization': 'Bearer test-token'}
        result = client.post(path, json=state[1], headers=headers)
        assert result.status_code == 200, result.text[:300]
        assert result.json()['source']['adapterRevision'] == NAV_ADAPTER_REVISION
        invalid = client.post(path, json={**state[1], 'visibilityMode': 'strict-publication'}, headers=headers)
        assert invalid.status_code == 422
        assert invalid.json()['error']['code'] == 'publication_unavailable'
