"""独立备用生产包及精确主备 target 的实际 HTTP 读取。"""

import pytest

from src.services import thesis_ledger_public_fees as fees
from tests.test_thesis_ledger_public_fees import request
from tests import test_thesis_ledger_public_fee_routes as routes
from tests.test_thesis_ledger_public_fee_routes import DATA_HEADERS, DATA_URL, KEY, TARGET, policy, wire_request

configured_client = routes.configured_client

ARCHIVE_TARGET = {**TARGET, "upstreamSource": fees.ARCHIVE_SOURCE}


def test_archive_reads_independent_pack_and_actual_sources():
    payload = request("2023-08-22", "2023-08-29")
    payload["routeTarget"] = {**ARCHIVE_TARGET, "routeIndex": 1}
    result = fees.OfficialPublicFeeProvider().read(payload)
    assert result["routeTarget"] == payload["routeTarget"]
    assert result["segments"][0]["range"]["start"] == "2023-08-22"
    assert result["provenance"]["datasetId"] == "cn-xshg-stock-auction-public-fees-archive-2023-08"
    assert result["provenance"]["upstreamSource"] == fees.ARCHIVE_SOURCE
    docs = {item["id"]: item for item in result["sourceDocuments"]}
    assert docs["stamp-duty-2023-39"]["publishedDate"] == "2023-08-28"
    assert docs["handling-2023-government-news"]["kind"] == "governmentNewsRepublication"
    assert "wuhan.gov.cn" in docs["transfer-2022-republication"]["url"]
    with pytest.raises(fees.PublicFeeSourceError, match="未覆盖"):
        fees.OfficialPublicFeeProvider().read(request("2023-08-22", "2023-08-29"))


def test_archive_remains_readable_when_primary_pack_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fees, "DATA_PATH", tmp_path / "missing.json")
    payload = request()
    payload["routeTarget"] = {**ARCHIVE_TARGET, "routeIndex": 1}
    assert fees.public_fee_source_ready(ARCHIVE_TARGET)
    assert not fees.public_fee_source_ready(TARGET)
    assert fees.OfficialPublicFeeProvider().read(payload)["status"] == "supported"
    with pytest.raises(fees.PublicFeeSourceError) as caught:
        fees.OfficialPublicFeeProvider().read(request())
    assert caught.value.code == "upstream_failure"


def test_both_pinned_sources_report_outside_coverage(configured_client):
    client, _runtime, store = configured_client
    configured = policy()
    configured["routes"][0]["targets"].append(ARCHIVE_TARGET)
    store.apply_policy_v3(configured)
    for index, target in enumerate((TARGET, ARCHIVE_TARGET)):
        payload = wire_request()
        payload["range"]["start"] = "2023-08-21"
        payload["routeTarget"] = {**target, "routeIndex": index}
        response = client.post(DATA_URL, json=payload, headers=DATA_HEADERS)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "insufficient_coverage"


def test_catalog_two_targets_and_no_implicit_provider_switch(configured_client):
    client, _runtime, store = configured_client
    configured = policy()
    configured["routes"][0]["targets"].append(ARCHIVE_TARGET)
    saved = store.apply_policy_v3(configured)
    assert all(item["eligible"] for item in saved["effective"]["routes"][0]["targets"])
    catalog = client.get(
        "/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=3",
        headers={"Authorization": "Bearer fees-control-test-token"},
    ).json()
    assert {item["target"]["upstreamSource"] for item in catalog["entries"] if item["key"] == KEY} == {
        TARGET["upstreamSource"], fees.ARCHIVE_SOURCE,
    }
    payload = wire_request()
    payload["range"]["start"] = "2023-08-22"
    assert client.post(DATA_URL, json=payload, headers=DATA_HEADERS).status_code == 503
    payload["routeTarget"] = {**ARCHIVE_TARGET, "routeIndex": 1}
    response = client.post(DATA_URL, json=payload, headers=DATA_HEADERS)
    assert response.status_code == 200
    assert response.json()["range"] == payload["range"]
    assert response.json()["provenance"]["upstreamSource"] == fees.ARCHIVE_SOURCE
