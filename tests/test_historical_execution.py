"""受控 ETF 归档的离线合同、原文与真实路由边界。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from data_provider.thesis_ledger_sources.historical_execution import (
    ExecutionArchive, SOURCE, context_key, inventory, probe,
)
from src.services.thesis_ledger_historical_execution import ExecutionRequest, ExecutionResult
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ThesisLedgerGatewayError,
)
from tests.test_configured_market_routes import store

FIXTURE = Path(__file__).parent / 'fixtures/historical-execution.shared.json'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('离线验证禁止网络调用')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


def sample():
    return json.loads(FIXTURE.read_text())


def write_archive(root, value):
    (root / 'contexts').mkdir(parents=True, exist_ok=True)
    (root / 'originals').mkdir(exist_ok=True)
    for text in value['originals'].values():
        raw = text.encode()
        (root / 'originals' / sha256(raw).hexdigest()).write_bytes(raw)
    query = ExecutionRequest.model_validate(value['request'])
    path = root / 'contexts' / (context_key(query.symbol, query.routeKey.assetType, query.asOf) + '.json')
    path.write_text(json.dumps(value['context'], ensure_ascii=False))
    (root / 'index.json').write_text(json.dumps({'version': 1, 'contexts': {path.stem: {
        'assetType': query.routeKey.assetType, 'sha256': sha256(path.read_bytes()).hexdigest()}}}))
    return query, path


def outer(query):
    return ThesisLedgerDataRequest(query.routeKey.capability, query.symbol,
        instrument_type=query.routeKey.assetType, start=query.range.start, end=query.range.end,
        request_id=query.requestId, parameters=query.model_dump())


def test_actual_production_binding_reads_verified_originals(tmp_path):
    value = sample()
    query, _ = write_archive(tmp_path, value)
    result = CAPABILITY_SOURCES.query(SOURCE.bindings[0].contract, query,
        query.routeKey.model_dump(), query.routeTarget.model_dump(), ReadContext(ExecutionArchive(tmp_path)))
    assert result.context.model_dump() == value['context']
    assert result.context.evidence[0].observedAt > result.asOf  # 采集时间与历史可见时间分开。
    assert SOURCE.default_enabled is False
    assert all(binding.admission_required is True for binding in SOURCE.bindings)


@pytest.mark.parametrize('mutation', ['future', 'expired', 'wrong-symbol', 'wrong-type', 'missing-ref',
                                     'incomplete-fees', 'future-halt', 'inverted-prices', 'naive-clock',
                                     'extra-fact', 'boolean-lag', 'duplicate-fees', 'invalid-price', 'invalid-clock'])
def test_unqualified_context_rejected(mutation):
    value = sample()
    context = value['context']
    if mutation == 'future':
        context['evidence'][0]['availableAt'] = '2026-11-10T05:02:00.000Z'
    elif mutation == 'expired':
        context['fact']['executionRules']['range']['end'] = '2026-11-09'
    elif mutation == 'wrong-symbol':
        context['fact']['symbol'] = '510300.SH'
    elif mutation == 'wrong-type':
        context['fact']['instrumentType'] = 'STOCK'
    elif mutation == 'missing-ref':
        context['trading']['evidenceRefs'] = ['missing']
    elif mutation == 'incomplete-fees':
        context['ruleCoverage']['statutoryCharges'] = False
    elif mutation == 'future-halt':
        context['trading']['intradayHalts'] = [{'startedAt': '2026-11-10T05:02:00Z', 'endedAt': None}]
    elif mutation == 'naive-clock':
        context['asOf'] = '2026-11-10T05:01:00'
    elif mutation == 'extra-fact':
        context['fact']['extra'] = True
    elif mutation == 'boolean-lag':
        context['fact']['executionRules']['cashSettlement']['buyDebitAfterTradingDays'] = False
    elif mutation == 'duplicate-fees':
        context['fact']['executionRules']['statutoryCharges'].append(context['fact']['executionRules']['statutoryCharges'][0])
    elif mutation == 'invalid-price':
        context['priceRule']['lower'] = 'bad'
    elif mutation == 'invalid-clock':
        context['asOf'] = 'bad'
    else:
        context['priceRule'].update(lower='1.000000000000000002', upper='1.000000000000000001')
    with pytest.raises((ValidationError, ValueError)):
        ExecutionResult.model_validate({**value['request'], 'context': context})


def test_missing_archive_and_tampered_original_remain_blocked(tmp_path):
    value = sample()
    query, _ = write_archive(tmp_path, value)
    other = query.model_copy(update={'asOf': '2026-11-10T05:02:00.000Z'})
    with pytest.raises(CapabilityContractError, match='缺少'):
        ExecutionArchive(tmp_path).read(other)
    path = tmp_path / 'originals' / value['context']['evidence'][0]['sha256']
    path.write_text('篡改')
    with pytest.raises(CapabilityContractError, match='原文校验'):
        ExecutionArchive(tmp_path).read(query)


def test_archive_mutation_requires_new_revision(tmp_path):
    query, _ = write_archive(tmp_path, sample())
    adapter = ExecutionArchive(tmp_path)
    path = tmp_path / 'index.json'
    path.write_text(path.read_text() + '\n')
    with pytest.raises(CapabilityContractError) as error:
        adapter.read(query)
    assert error.value.code == 'admission_invalid'


def test_real_factory_and_probe_use_pinned_index(monkeypatch, tmp_path):
    query, _ = write_archive(tmp_path, sample())
    monkeypatch.setattr('data_provider.thesis_ledger_sources.historical_execution.ROOT', tmp_path)
    monkeypatch.setattr('data_provider.thesis_ledger_sources.historical_execution.REVISION', inventory(tmp_path))
    adapter = SOURCE.factory(ReadContext(None))
    assert adapter.read(query).context.fact.symbol == query.symbol
    result = probe(SimpleNamespace(key=query.routeKey.model_dump(),
        target={k: v for k, v in query.routeTarget.model_dump().items() if k != 'routeIndex'}, policy_revision=1))
    assert result['checkedContexts'] == 1
    (tmp_path / 'index.json').write_text('{}')
    with pytest.raises(CapabilityContractError) as error:
        adapter.read(query)
    assert error.value.code == 'admission_invalid'


def admit(store, query, **changes):
    now = datetime.now(timezone.utc)
    binding = CAPABILITY_SOURCES.resolve(query.routeKey.model_dump(), query.routeTarget.model_dump())
    values = dict(key=query.routeKey.model_dump(), target={k: v for k, v in query.routeTarget.model_dump().items() if k != 'routeIndex'},
        evidence_ref='fixture://controlled-archive', evidence_sha256='a' * 64,
        scope_symbols=[query.symbol], scope_date_from=query.range.start, scope_date_to=query.range.end,
        adapter_revision=binding.adapter_revision, source_revision=binding.source_revision,
        credential_revision='not-required', valid_from=(now - timedelta(days=1)).isoformat(),
        valid_until=(now + timedelta(days=1)).isoformat(), recorded_by='offline-pytest')
    store.record_route_admission_v3(**{**values, **changes})


def configure(store, query):
    store.save_provider_config(SOURCE.provider_id, {'requestId': 'controlled-enable', 'enabled': True})
    return store.apply_policy_v3({'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'controlled-route',
        'revision': 1, 'enabled': True, 'routes': [{'key': query.routeKey.model_dump(), 'enabled': True,
        'targets': [{k: v for k, v in query.routeTarget.model_dump().items() if k != 'routeIndex'}]}]})


def test_actual_runtime_requires_admission_then_reads_exact_context(store, tmp_path):
    query, _ = write_archive(tmp_path / 'archive', sample())
    projection = configure(store, query)
    assert not projection['effective']['routes'][0]['targets'][0]['eligible']
    runtime = ThesisLedgerProviderRuntime(store, adapters={SOURCE.provider_id: ExecutionArchive(tmp_path / 'archive')})
    with pytest.raises(ThesisLedgerGatewayError):
        runtime.execute_request(outer(query))
    admit(store, query)
    result = runtime.execute_request(outer(query))
    assert result.value['context'] == sample()['context']
    assert result.provider == SOURCE.provider_id and not result.fallback_used


def test_authenticated_endpoint_dispatch_and_safe_errors(store, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger_arena_history import build_arena_history_router
    from api.thesis_ledger_auth import require_contract_token
    query, _ = write_archive(tmp_path / 'archive', sample())
    admit(store, query)
    configure(store, query)
    runtime = ThesisLedgerProviderRuntime(store, adapters={SOURCE.provider_id: ExecutionArchive(tmp_path / 'archive')})
    app = FastAPI()
    app.include_router(build_arena_history_router(lambda: runtime))
    app.dependency_overrides[require_contract_token] = lambda: None
    with TestClient(app) as client:
        assert client.post('/arena/history/execution', json=query.model_dump()).json()['context'] == sample()['context']
        response = client.post('/arena/history/execution', json={**query.model_dump(), 'asOf': '2026-11-10T05:02:00.000Z'})
        assert response.status_code == 422
        assert response.json()['detail']['error']['code'] == 'insufficient_coverage'
        assert response.json()['detail']['requestId'] == query.requestId
        response = client.post('/arena/history/execution', json={**query.model_dump(), 'unknown': True})
        assert response.status_code == 422 and response.json()['detail']['error']['code'] == 'invalid_request'


@pytest.mark.parametrize('scope', ['symbol', 'day', 'revision', 'expired'])
def test_runtime_admission_does_not_extend_scope(store, tmp_path, scope):
    query, _ = write_archive(tmp_path / 'archive', sample())
    changes = {'symbol': {'scope_symbols': ['510300.SH']},
               'day': {'scope_date_from': '2026-11-09', 'scope_date_to': '2026-11-09'},
               'revision': {'source_revision': 'obsolete'},
               'expired': {'valid_until': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}}[scope]
    admit(store, query, **changes)
    configure(store, query)
    runtime = ThesisLedgerProviderRuntime(store, adapters={SOURCE.provider_id: ExecutionArchive(tmp_path / 'archive')})
    with pytest.raises(ThesisLedgerGatewayError):
        runtime.execute_request(outer(query))


def test_stale_policy_rejected_before_archive_read(store, tmp_path, monkeypatch):
    query, _ = write_archive(tmp_path / 'archive', sample())
    admit(store, query)
    configure(store, query)
    adapter = ExecutionArchive(tmp_path / 'archive')
    calls = []
    monkeypatch.setattr(adapter, 'read', lambda request: calls.append(request))
    runtime = ThesisLedgerProviderRuntime(store, adapters={SOURCE.provider_id: adapter})
    with pytest.raises(ThesisLedgerGatewayError):
        runtime.execute_request(outer(query.model_copy(update={'policyRevision': 2})))
    assert calls == []


def test_provider_change_during_read_discards_result(store, tmp_path, monkeypatch):
    query, _ = write_archive(tmp_path / 'archive', sample())
    admit(store, query)
    configure(store, query)
    adapter = ExecutionArchive(tmp_path / 'archive')
    read = adapter.read

    def change(request):
        result = read(request)
        store.save_provider_config(SOURCE.provider_id, {'requestId': 'controlled-disable', 'enabled': False})
        return result

    monkeypatch.setattr(adapter, 'read', change)
    runtime = ThesisLedgerProviderRuntime(store, adapters={SOURCE.provider_id: adapter})
    with pytest.raises(ThesisLedgerGatewayError):
        runtime.execute_request(outer(query))


def test_shared_fixture_matches_server_when_sibling_exists():
    sibling = FIXTURE.parents[3] / 'thesis-ledger/apps/server/test/market/fixtures/historical-execution.shared.json'
    if sibling.exists():
        assert sibling.read_bytes() == FIXTURE.read_bytes()
