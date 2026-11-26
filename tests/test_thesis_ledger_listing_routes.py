"""上市/退市生产包通过实际控制路由和鉴权网关。"""

import pytest

from tests import test_thesis_ledger_public_fee_routes as routes
from src.services.thesis_ledger_trading_status import PROVIDER_ID, ROUTE_KEY as SOURCE_KEY

configured_client = routes.configured_client
ROUTE_KEY = {**SOURCE_KEY, "capability": "LISTING_PHASE"}
DATA_URL = "/api/v1/thesis-ledger/market/status-capabilities"


@pytest.mark.parametrize("source,symbol,start,end,phase", [
    ("cn-stock-initial-listing", "603350.SH", "2024-07-02", "2024-07-11", "notListed"),
    ("cn-stock-delisting-period", "600462.SH", "2025-07-10", "2025-07-22", "delistingPeriod"),
])
def test_real_source_catalog_policy_and_authenticated_read(configured_client, source, symbol, start, end, phase):
    client, _runtime, _store = configured_client
    target = {"providerId": PROVIDER_ID, "upstreamSource": source}
    catalog = client.get("/api/v1/thesis-ledger/control/routes/capabilities?contractVersion=1",
                         headers=routes.CONTROL_HEADERS).json()
    entry = next(row for row in catalog["entries"] if row["key"] == ROUTE_KEY and row["target"] == target)
    assert entry["state"] == "ready" and entry["verification"] == {"state": "unverified"}
    policy = {"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "listing-policy", "revision": 1,
              "enabled": True, "routes": [{"key": ROUTE_KEY, "enabled": True, "targets": [target]}]}
    assert client.post("/api/v1/thesis-ledger/control/policies/apply", json=policy, headers=routes.CONTROL_HEADERS).status_code == 200
    request = {"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "listing-read",
               "capabilityVersion": 1, "policyRevision": 1,
               "routeKey": ROUTE_KEY, "routeTarget": {**target, "routeIndex": 0}, "symbol": symbol,
               "range": {"start": start, "end": end}}
    assert client.post(DATA_URL, json=request, headers={}).status_code == 401
    response = client.post(DATA_URL, json=request, headers=routes.DATA_HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["days"][0]["phase"] == phase
    assert result["routeTarget"]["upstreamSource"] == source
    documents = [item for item in result["evidence"] if item["kind"] == "document"]
    assert documents and all(doc["sha256"] and doc["observedAt"] for doc in documents)
