"""既有Control只读视图绑定精确准入，默认响应保持，零数据/凭据写入。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient as ApiClient
import pytest

from api.thesis_ledger_auth import require_control_token
from src.services import thesis_ledger_route_catalog as catalog_module
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_eastmoney_split_diagnostics import forbid_network as offline_network_fixture

forbid_network = offline_network_fixture
KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'SPLIT_EVENT'}
TARGET = {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}
DIGEST = 'b1a4f03f94faf27acf95d2bef9d508ec06eabec1280e04c921bb677154f5fe8e'


@pytest.fixture
def fixture(monkeypatch):
    record = {'consumer': 'thesis-ledger', 'routeKey': KEY, 'target': TARGET,
              'status': 'admitted', 'admissionState': 'admitted', 'recordVersion': 1,
              'evidenceRef': f'sha256:{DIGEST}', 'evidenceSha256': DIGEST,
              'scopeSymbols': ['159516.SZ'], 'scopeDateFrom': '2026-03-30', 'scopeDateTo': '2026-07-10',
              'adapterRevision': 'dsa-eastmoney-fund-split-mapped-v1',
              'sourceRevision': 'eastmoney-fund-cf-pageinfo-json-v1', 'credentialRevision': 'not-required',
              'validFrom': '2020-01-01T00:00:00Z', 'validUntil': '2099-01-01T00:00:00Z',
              'recordedAt': '2020-01-01T00:00:00Z', 'invalidatedAt': None, 'invalidationReason': None,
              'recordedBy': 'private-token-fixture', 'credentialValue': 'private-token-fixture'}
    getter = Mock(side_effect=lambda *args: deepcopy(record))
    registry = Mock(return_value=[{'providerId': 'akshare', 'configured': True, 'enabled': True, 'requiresCredential': False}])
    forbidden = Mock(side_effect=AssertionError('此视图禁止数据/凭据写入'))
    runtime = SimpleNamespace(store=SimpleNamespace(provider_registry=registry, record_route_admission_v3=forbidden,
                                                    save_credentials=forbidden),
                              _catalog_market_v3_admission_is_current=lambda *args: True,
                              _current_market_v3_admission=getter)
    runtime.market_route_catalog_v3 = lambda **kwargs: ThesisLedgerProviderRuntime.market_route_catalog_v3(runtime, **kwargs)
    monkeypatch.setattr(catalog_module, 'exact_route_inventory', lambda: [(KEY, TARGET, True)])
    monkeypatch.setattr(catalog_module, 'catalog_manifest_matches_v3', lambda *args: True)
    monkeypatch.setattr(catalog_module, 'provider_dependency_ready', lambda *args: True)
    return runtime, record, getter, registry, forbidden


def test_default_shape_and_explicit_view_share_revision_without_sensitive_fields(fixture):
    runtime, _, getter, _, forbidden = fixture
    default = runtime.market_route_catalog_v3()
    view = runtime.market_route_catalog_v3(event_admissions=True)
    assert set(default) == {'contractVersion', 'consumer', 'catalogRevision', 'generatedAt', 'integrity', 'entries'}
    assert view['protocolVersion'] == 'event-admission-catalog-v1'
    assert view['catalog']['catalogRevision'] == default['catalogRevision']
    assert view['admissions'][0]['recordVersion'] == 1
    assert 'private-token-fixture' not in str(view)
    assert getter.call_count == 2
    forbidden.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('recordVersion', 2), ('scopeSymbols', ['510300.SH']),
    ('evidenceSha256', 'c' * 64), ('validUntil', '2098-01-01T00:00:00Z'),
])
def test_record_change_while_ready_changes_opaque_revision(fixture, field, value):
    runtime, record, _, _, forbidden = fixture
    before = runtime.market_route_catalog_v3()
    record[field] = value
    after = runtime.market_route_catalog_v3()
    assert before['entries'] == after['entries']
    assert before['catalogRevision'] != after['catalogRevision']
    forbidden.assert_not_called()


def test_unverifiable_record_is_missing_without_guess_or_write(fixture):
    runtime, _, getter, _, forbidden = fixture
    getter.side_effect = ValueError('private-token-fixture')
    view = runtime.market_route_catalog_v3(event_admissions=True)
    assert view['admissions'] == [] and 'private-token-fixture' not in str(view)
    forbidden.assert_not_called()


def test_existing_http_entry_is_explicit_and_auth_precedes_read(fixture, monkeypatch):
    from api.thesis_ledger import router_v3
    from src.services import thesis_ledger_provider_runtime as runtime_module
    runtime, _, getter, registry, forbidden = fixture
    monkeypatch.setattr(runtime_module, 'get_thesis_ledger_runtime', lambda: runtime)
    app = FastAPI()
    app.include_router(router_v3, prefix='/api/v3')
    app.dependency_overrides[require_control_token] = lambda: None
    client = ApiClient(app)
    endpoint = '/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=3'
    default = client.get(endpoint)
    view = client.get(endpoint + '&eventAdmissions=v1')
    assert default.status_code == 200 and 'protocolVersion' not in default.json()
    assert view.status_code == 200 and view.json()['protocolVersion'] == 'event-admission-catalog-v1'
    assert client.get(endpoint + '&eventAdmissions=v2').status_code == 422
    count = getter.call_count

    def denied():
        raise HTTPException(status_code=401, detail='测试未授权')

    app.dependency_overrides[require_control_token] = denied
    assert client.get(endpoint + '&eventAdmissions=v1').status_code == 401
    assert getter.call_count == count and registry.call_count == count
    forbidden.assert_not_called()
