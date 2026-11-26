"""新增风险来源通过真实临时控制库与已认证状态网关。"""

from tests import test_thesis_ledger_public_fee_routes as routes
from tests.test_thesis_ledger_risk_price_limits import request
from src.services.thesis_ledger_trading_status import ROUTE_KEY, PROVIDER_ID, RISK_UPSTREAM_SOURCE

configured_client = routes.configured_client
TARGET = {"providerId": PROVIDER_ID, "upstreamSource": RISK_UPSTREAM_SOURCE}
URL = "/api/v1/thesis-ledger/market/status-capabilities"
RISK_KEY = {**ROUTE_KEY, "capability": "RISK_WARNING"}
PRICE_KEY = {**ROUTE_KEY, "capability": "DAILY_PRICE_RULE"}


def status_request(key, **fields):
    return {**request(), "routeKey": key, "capabilityVersion": 1, "policyRevision": 1, **fields}


def test_risk_catalog_policy_and_authenticated_production_read(configured_client):
    client, _runtime, store = configured_client
    catalog = client.get("/api/v1/thesis-ledger/control/routes/capabilities?contractVersion=1",
                         headers=routes.CONTROL_HEADERS).json()
    expected_keys = (RISK_KEY, PRICE_KEY)
    for key in expected_keys:
        entry = next(entry for entry in catalog["entries"] if entry["key"] == key and entry["target"] == TARGET)
        assert entry["state"] == "ready"
        assert entry["verification"] == {"state": "unverified"}
    policy = {"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "risk-policy",
              "revision": 1, "enabled": True,
              "routes": [{"key": key, "enabled": True, "targets": [TARGET]} for key in expected_keys]}
    saved = client.post("/api/v1/thesis-ledger/control/policies/apply", json=policy,
                        headers=routes.CONTROL_HEADERS)
    assert saved.status_code == 200
    assert all(route["targets"][0]["eligible"] for route in saved.json()["effective"]["routes"])
    for key in expected_keys:
        assert store.get_route_admission_v3(key=key, target=TARGET) is None
    price = client.post(URL, json=status_request(PRICE_KEY, representation="rule"), headers=routes.DATA_HEADERS)
    risk = client.post(URL, json=status_request(RISK_KEY, detail={"kind": "riskCategories",
                                                                "categories": ["delisting", "other"]}),
                       headers=routes.DATA_HEADERS)
    assert price.status_code == 200, price.text
    assert risk.status_code == 200, risk.text
    for result in (price.json(), risk.json()):
        assert result["routeTarget"] == {**TARGET, "routeIndex": 0}
        assert result["evidence"]
    assert price.json()["days"][0]["rule"]["maxUpRatio"] == "0.05"
    assert price.json()["days"][2]["rule"]["maxUpRatio"] == "0.1"
    assert risk.json()["days"][2]["delisting"] == "none"
    assert client.post("/api/v1/thesis-ledger/market/trading-status", json=request(),
                       headers=routes.DATA_HEADERS).status_code == 404


def test_risk_source_still_requires_data_authentication(configured_client):
    client, _runtime, _store = configured_client
    for headers in ({}, routes.CONTROL_HEADERS):
        assert client.post(URL, json=status_request(PRICE_KEY, representation="rule"), headers=headers).status_code == 401
