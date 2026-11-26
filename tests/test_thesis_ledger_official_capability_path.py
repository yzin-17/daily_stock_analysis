"""实际证据包经过新绑定与原 Runtime；不联网获取或修改公告资产。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_reference_data import build_reference_data_router
from api.thesis_ledger_local_rules import build_local_rule_router
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_status_capabilities_v1 import RESULTS, REQUESTS
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_trading_status import OfficialTradingStatusProvider
from src.services.thesis_ledger_multimarket_rules import MultimarketRuleProvider
from tests.test_thesis_ledger_multimarket_rules import request as multimarket_request


def payload(capability, source, symbol, day, **parameters):
    return {"contractVersion": 1, "capabilityVersion": 1, "consumer": "thesis-ledger", "requestId": "official-capability",
            "symbol": symbol, "routeKey": {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": capability},
            "routeTarget": {"providerId": "official-states", "upstreamSource": source, "routeIndex": 0},
            "policyRevision": 1, "range": {"start": day, "end": day}, **parameters}


def read(monkeypatch, tmp_path, value):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'isolated-data-token')
    store = ThesisLedgerControlStore(tmp_path / 'official-capability.db')
    store.apply_policy_v3({'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'official-policy', 'revision': 1,
                          'enabled': True, 'routes': [{'key': value['routeKey'], 'enabled': True,
                                                      'targets': [{'providerId': 'official-states', 'upstreamSource': value['routeTarget']['upstreamSource']}]}]})
    runtime = ThesisLedgerProviderRuntime(store, adapters={'official-states': OfficialTradingStatusProvider()})
    app = FastAPI()
    app.include_router(build_reference_data_router(lambda: runtime), prefix='/api/v1/thesis-ledger')
    response = TestClient(app).post('/api/v1/thesis-ledger/market/status-capabilities', json=value,
                                    headers={'authorization': 'Bearer isolated-data-token'})
    assert response.status_code == 200, response.text
    return RESULTS.validate_python(response.json())


def test_old_suspension_document_without_hash_preserves_unknown_integrity(monkeypatch, tmp_path):
    result = read(monkeypatch, tmp_path, payload('DAILY_TRADING_STATUS', 'cn-stock-daily-suspension', '000995.SZ', '2023-08-17'))
    assert result.days[0].state == 'suspended'
    document = next(item for item in result.evidence if item.kind == 'document')
    assert document.sha256 is None and document.hashScope == 'unknown'
    assert document.observedAt is None and document.availableAt is None
    assert document.reference.startswith('https://')


@pytest.mark.parametrize("market", ["HK", "US"])
@pytest.mark.parametrize("local_materials", [False, True])
def test_multimarket_status_preserves_halt_and_document_through_current_route(
    monkeypatch, tmp_path, market, local_materials,
):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "controlled-status-token")
    original_request = multimarket_request(market, "HISTORICAL_TRADING_STATUS")
    original = MultimarketRuleProvider().read(original_request)
    query = {**original_request, "capabilityVersion": 1, "policyRevision": 1,
             "routeKey": {**original_request["routeKey"], "capability": "DAILY_TRADING_STATUS"}}
    if local_materials:
        query["routeTarget"] = {"providerId": "local-materials",
                                "upstreamSource": "local-materials", "routeIndex": 0}
    store = ThesisLedgerControlStore(tmp_path / "multimarket-status.db")
    target = {key: query["routeTarget"][key] for key in ("providerId", "upstreamSource")}
    store.apply_policy_v3({"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "project-status",
                          "revision": 1, "enabled": True,
                          "routes": [{"key": query["routeKey"], "enabled": True, "targets": [target]}]})
    runtime = ThesisLedgerProviderRuntime(store)
    app = FastAPI()
    app.include_router(build_reference_data_router(lambda: runtime), prefix="/api/v1/thesis-ledger")
    app.include_router(build_local_rule_router(lambda: runtime), prefix="/api/v1/thesis-ledger")
    client = TestClient(app)
    headers = {"authorization": "Bearer controlled-status-token"}
    assert client.post("/api/v1/thesis-ledger/market/trading-status", json=original_request, headers=headers).status_code == 404
    result = client.post("/api/v1/thesis-ledger/market/status-capabilities", json=query, headers=headers)
    assert result.status_code == 200, result.text
    projected = RESULTS.validate_python(result.json())
    assert projected.routeTarget.model_dump() == query["routeTarget"]
    assert [day.model_dump() for day in projected.days] == [
        {"date": day["date"], "state": day["state"], "intradayHalts": day.get("intradayHalts", []),
         "evidenceRefs": day.get("sourceDocumentIds", [])} for day in original["days"]
    ]
    assert projected.clock.timezone == original["provenance"]["timezone"]
    assert [item.reference for item in projected.evidence if item.kind == "document"] == [
        doc["url"] for doc in original["sourceDocuments"]
    ]


def test_precise_risk_coverage_uses_requested_categories_only(monkeypatch, tmp_path):
    value = payload('RISK_WARNING', 'cn-stock-risk-price-limits', '000995.SZ', '2023-08-16',
                    detail={'kind': 'riskCategories', 'categories': ['delisting']})
    result = read(monkeypatch, tmp_path, value)
    assert result.days[0].delisting == 'active' and result.days[0].other == 'unknown'
    assert result.days[0].coarseST is None
    if result.coverage.calendarEvidenceRefs:
        assert result.coverage.complete
    # 相同已知字段在新增必需类别后必须保留缺口；不重复写同一个隔离政策修订。
    value = result.model_dump(exclude_unset=True)
    value['detail']['categories'].append('other')
    value['coverage'].update(complete=False, unknownDates=value['coverage']['expectedDates'])
    changed = RESULTS.validate_python(value)
    assert not changed.coverage.complete


def test_ratio_rule_keeps_its_representation(monkeypatch, tmp_path):
    result = read(monkeypatch, tmp_path, payload('DAILY_PRICE_RULE', 'cn-stock-risk-price-limits', '000995.SZ', '2023-08-17',
                                               representation='rule'))
    assert result.days[0].rule.kind == 'ratio'
    assert result.days[0].rule.maxUpRatio == '0.05'


@pytest.mark.parametrize('capability,parameters', [
    ('DAILY_PRICE_RULE', {'representation': 'exactPrices'}),
    ('RISK_WARNING', {'detail': {'kind': 'coarseST'}}),
])
def test_unsupported_precision_rejected_before_source_read(capability, parameters):
    value = REQUESTS.validate_python(payload(capability, 'cn-stock-risk-price-limits', '000995.SZ', '2023-08-17', **parameters))
    binding = CAPABILITY_SOURCES.resolve(value.routeKey.model_dump(), value.routeTarget.model_dump())
    with pytest.raises(CapabilityContractError) as caught:
        CAPABILITY_SOURCES.query(binding.contract, value, value.routeKey.model_dump(), value.routeTarget.model_dump(), ReadContext(None, None))
    assert caught.value.code == 'unsupported_capability'


@pytest.mark.parametrize('source,symbol,day,phase', [
    ('cn-stock-initial-listing', '603350.SH', '2024-07-02', 'notListed'),
    ('cn-stock-delisting-period', '600462.SH', '2025-07-10', 'delistingPeriod'),
])
def test_listing_phase_has_independent_request_and_document_evidence(monkeypatch, tmp_path, source, symbol, day, phase):
    result = read(monkeypatch, tmp_path, payload('LISTING_PHASE', source, symbol, day))
    assert result.days[0].phase == phase
    assert result.days[0].evidenceRefs
    assert all(item.kind != 'record' for item in result.evidence)
    assert not hasattr(result, 'dates') and not hasattr(result, 'coverageProof')
