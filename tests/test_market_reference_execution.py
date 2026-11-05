"""真实 Control／runtime 与 HTTP 接缝的离线测试。"""

from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_auth import require_contract_token
from api.thesis_ledger_reference_query import build_reference_query_router
from data_provider import market_reference_financial as financial_source
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ProviderCallError
from src.services import thesis_ledger_reference_query_v1 as bridge
from tests.test_configured_market_routes import apply, no_admissions, store
from tests.test_market_reference_projection import financial, query

KEY = query().route_key()
TARGET = {'providerId': 'akshare', 'upstreamSource': 'sina'}


def request():
    return ThesisLedgerDataRequest(KEY['capability'], '600519.SH', instrument_type='STOCK', request_id='reference-test')


def test_real_runtime_uses_exact_configured_binding_without_changing_admission(store, monkeypatch):
    applied = apply(store, KEY, TARGET)
    assert applied['effective']['routes'][0]['targets'][0]['eligible'] is True
    read = Mock(return_value=financial())
    monkeypatch.setattr(financial_source, 'http_json', read)
    runtime = ThesisLedgerProviderRuntime(store, adapters={'akshare': object()})
    value = runtime.execute_request(request())
    assert value.value['items'][0]['values']['assets'] == '0'
    assert value.provider == 'akshare' and value.upstream_source == 'sina'
    assert read.call_count == 1
    no_admissions(store)


@pytest.mark.parametrize('change', ['policy', 'config', 'disabled'])
def test_late_response_after_control_change_is_discarded(change):
    policy = {'contractVersion': 3, 'enabled': True, 'revision': 1, 'sourceDesiredRevision': 1,
              'routes': [{'key': KEY, 'targets': [TARGET | {'eligible': True, 'routeIndex': 0, 'reason': None}]}]}
    provider = {'providerId': 'akshare', 'enabled': True, 'configVersion': 1}
    runtime = SimpleNamespace(store=SimpleNamespace(database_path=':memory:',
        effective_policy_v3=lambda: policy, provider_registry=lambda: [provider]))
    def consume(*args):
        if change == 'policy':policy['revision'] += 1
        elif change == 'config':provider['configVersion'] += 1
        else:provider['enabled'] = False
        return financial_source.project_financial(financial(), query())
    operation = bridge.reference_operation(request(), ProviderCallError, runtime)
    with patch.object(bridge.CAPABILITY_SOURCES, 'query', side_effect=consume):
        with pytest.raises(ProviderCallError) as error:
            operation('akshare', object(), 'sina')
    assert error.value.code == 'not_admitted'


def test_http_rejects_arbitrary_parameters_before_runtime():
    service = SimpleNamespace(execute_request=Mock())
    app = FastAPI();app.dependency_overrides[require_contract_token] = lambda: None
    app.include_router(build_reference_query_router(lambda: service), prefix='/api/v3/thesis-ledger')
    body = query().model_dump() | {'contractVersion': 3, 'consumer': 'thesis-ledger', 'url': 'https://example.com'}
    response = TestClient(app).post('/api/v3/thesis-ledger/market/reference', json=body)
    assert response.status_code == 422
    service.execute_request.assert_not_called()
