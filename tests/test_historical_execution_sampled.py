"""采样用途穿过真实归档与临时运行时，完整制度合同仍严格。"""

import socket
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from data_provider.thesis_ledger_sources.historical_execution import ExecutionArchive, inventory, probe
from src.services.thesis_ledger_historical_execution import ExecutionResult, sampled_context
from src.services.thesis_ledger_historical_reconstruction import sampled_daily_material
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store  # noqa: F401
from tests.test_historical_execution import sample, write_archive, admit, configure, outer
from tests.historical_reconstruction_sample import sample as daily_sample, write_archive as write_daily


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('采样隔离验证禁止网络')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


def minimal_sample():
    value = sample()
    value['request']['purpose'] = 'sampled'
    value['context'] = sampled_context(value['context']).model_dump()
    return value


def test_native_sampled_archive_through_real_runtime(store, tmp_path):
    value = minimal_sample()
    query, _ = write_archive(tmp_path / 'archive', value)
    admit(store, query, valid_until=None)
    configure(store, query)
    runtime = ThesisLedgerProviderRuntime(store, adapters={
        'historical-execution-archive': ExecutionArchive(tmp_path / 'archive'),
    })
    result = runtime.execute_request(outer(query)).value
    assert result['purpose'] == 'sampled'
    assert result['context'] == value['context']
    assert 'executionRules' not in result['context']['fact']
    assert not {'priceRule', 'ruleCoverage', 'ruleEvidenceRefs'} & result['context'].keys()
    with pytest.raises((ValueError, ValidationError)):
        ExecutionResult.model_validate({**result, 'purpose': 'full'})


def test_sampled_request_through_actual_http_router(store, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger_arena_history import build_arena_history_router
    from api.thesis_ledger_auth import require_contract_token
    query, _ = write_archive(tmp_path / 'archive', minimal_sample())
    admit(store, query, valid_until=None)
    configure(store, query)
    runtime = ThesisLedgerProviderRuntime(store, adapters={
        'historical-execution-archive': ExecutionArchive(tmp_path / 'archive'),
    })
    app = FastAPI()
    app.include_router(build_arena_history_router(lambda: runtime), prefix='/api/v1/thesis-ledger')
    app.dependency_overrides[require_contract_token] = lambda: None
    with TestClient(app) as client:
        response = client.post('/api/v1/thesis-ledger/arena/history/execution', json=query.model_dump())
        assert response.status_code == 200
        assert response.json()['purpose'] == 'sampled'
        assert 'executionRules' not in response.json()['context']['fact']
        strict = client.post('/api/v1/thesis-ledger/arena/history/execution',
                             json={**query.model_dump(), 'purpose': 'full'})
        assert strict.status_code == 422
        assert strict.json()['detail']['error']['code'] == 'insufficient_coverage'


def test_old_archive_does_not_require_unused_fee_settlement_and_limit_fields(tmp_path):
    value = sample()
    value['request']['purpose'] = 'sampled'
    value['context'].pop('ruleCoverage')
    value['context'].pop('priceRule')
    value['context']['fact'].pop('executionRules')
    value['context']['fact'].pop('tradable')
    query, _ = write_archive(tmp_path, value)
    result = ExecutionArchive(tmp_path).read(query)
    assert result.context.trading.state == 'trading'
    assert result.context.fact.lotSize == '100'
    with pytest.raises(ValueError):
        ExecutionArchive(tmp_path).read(query.model_copy(update={'purpose': 'full'}))


def test_local_function_check_uses_sampled_requirements_for_legacy_archive(tmp_path, monkeypatch):
    value = sample()
    value['context']['fact'].pop('executionRules')
    value['context'].pop('ruleCoverage')
    value['context'].pop('priceRule')
    query, _ = write_archive(tmp_path, value)
    monkeypatch.setattr('data_provider.thesis_ledger_sources.historical_execution.ROOT', tmp_path)
    monkeypatch.setattr('data_provider.thesis_ledger_sources.historical_execution.REVISION', inventory(tmp_path))
    report = probe(SimpleNamespace(key=query.routeKey.model_dump(),
        target=query.routeTarget.model_dump(exclude={'routeIndex'}), policy_revision=1))
    assert report['checkedPurpose'] == 'sampled' and report['checkedContexts'] == 1


@pytest.mark.parametrize('mutation', ['future', 'zero-unit', 'currency', 'unknown-state', 'missing-ref', 'extra-fact'])
def test_minimal_contract_keeps_required_data_checks(mutation):
    value = minimal_sample()
    context = value['context']
    if mutation == 'future': context['fact']['availableAt'] = '2026-11-10T05:02:00Z'
    elif mutation == 'zero-unit': context['fact']['lotSize'] = '0'
    elif mutation == 'currency': context['fact']['currency'] = 'USD'
    elif mutation == 'unknown-state': context['trading']['state'] = 'unknown'
    elif mutation == 'missing-ref': context['identityEvidenceRefs'] = ['missing']
    else: context['fact']['extra'] = True
    with pytest.raises(ValidationError):
        ExecutionResult.model_validate({**value['request'], 'context': context})


@pytest.mark.parametrize('symbol', ['588000.SH', '510300.SH'])
@pytest.mark.parametrize('native', [False, True])
def test_sampled_daily_status_reuses_both_legacy_and_minimal_material(tmp_path, symbol, native):
    query, material, originals = daily_sample(symbol)
    query = query.model_copy(update={'purpose': 'sampled'})
    if native:
        material = sampled_daily_material(material).model_dump()
        assert material['version'] == 2 and 'executionRules' not in material['fact']
    else:
        material['ruleCoverage']['statutoryCharges'] = False
        material['fact'].pop('executionRules')
        material.pop('priceRule')
    write_daily(tmp_path, query, material, originals)
    result = ExecutionArchive(tmp_path).read(query)
    assert result.purpose == 'sampled' and result.context.trading.state == 'trading'
    assert result.context.trading.basis.query.symbol == symbol
    assert result.context.trading.basis.query.total == 0
    assert 'priceRule' not in result.context.model_dump()


@pytest.mark.parametrize('mutation', ['http', 'not-listed', 'future-identity', 'missing-page'])
def test_sampled_status_reconstruction_still_rejects_missing_facts(tmp_path, mutation):
    query, material, originals = daily_sample()
    material = sampled_daily_material(material).model_dump()
    if mutation == 'http': material['pages'][0]['httpStatus'] = 503
    elif mutation == 'not-listed': material['listing']['listedOn'] = '2026-11-11'
    elif mutation == 'future-identity': material['fact']['availableAt'] = '2026-11-11T00:00:00Z'
    else: material['pages'] = []
    write_daily(tmp_path, query, material, originals)
    with pytest.raises(ValueError):
        ExecutionArchive(tmp_path).read(query.model_copy(update={'purpose': 'sampled'}))
