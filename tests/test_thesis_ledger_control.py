"""当前 Control Contract 的权限、配置和目录 fixture 回归。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "stock_analysis.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "0123456789abcdef-secret-key")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    return TestClient(app)


def _envelope(**values):
    return {
        "contractVersion": 1,
        "consumer": "thesis-ledger",
        "requestId": "control-test-request",
        **values,
    }


def _provider_envelope(**values):
    return {**_envelope(**values), "contractVersion": 3}


def test_control_token_is_independent_and_handshake_is_versioned(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(
        "/api/v1/thesis-ledger/control/handshake",
        headers={"authorization": "Bearer control-token"},
        json={**_envelope(supportedVersions=[3]), "contractVersion": 3},
    ).status_code == 404
    response = client.post(
        "/api/v3/thesis-ledger/control/handshake",
        headers={"authorization": "Bearer data-token"},
        json={**_envelope(supportedVersions=[3]), "contractVersion": 3},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"

    response = client.post(
        "/api/v3/thesis-ledger/control/handshake",
        headers={"authorization": "Bearer control-token"},
        json={**_envelope(supportedVersions=[3]), "contractVersion": 3},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["consumer"] == "thesis-ledger"


def test_data_routes_reject_missing_or_control_token(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    for headers in ({}, {"authorization": "Bearer control-token"}):
        response = client.get(
            "/api/v3/thesis-ledger/market/quote?symbol=600519.SH",
            headers=headers,
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "unauthorized"


def test_fund_nav_history_is_ordered_and_uses_of_identity(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get(
        "/api/v3/thesis-ledger/market/fund-nav/history?symbol=000001&limit=5",
        headers={"authorization": "Bearer data-token"},
    )
    assert response.status_code == 200
    points = response.json()
    assert len(points) == 5
    assert {point["symbol"] for point in points} == {"000001.OF"}
    assert [point["navDate"] for point in points] == sorted(
        point["navDate"] for point in points
    )


def test_fund_holdings_returns_unscaled_disclosure_weights(monkeypatch, tmp_path):
    """基金披露保留未披露仓位，不把已披露持仓归一到 100%。"""
    client = _client(monkeypatch, tmp_path)
    response = client.get(
        "/api/v3/thesis-ledger/market/fund-holdings?symbol=000001",
        headers={"authorization": "Bearer data-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 3
    assert payload["fundSymbol"] == "000001.OF"
    assert payload["reportPeriod"] == "2024-Q4"
    assert sum(row["weight"] for row in payload["holdings"]) == 0.14
    assert payload["evidenceVersion"]


def test_policy_apply_rejects_old_contracts_before_writing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    assert client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json=_envelope(revision=1, enabled=True, routes=[]),
    ).status_code == 404
    assert client.get(
        "/api/v1/thesis-ledger/control/policies/effective", headers=headers
    ).status_code == 404
    for policy in (
        _envelope(revision=1, enabled=True, routes={}),
        {**_envelope(revision=1, enabled=True, routes={}), "contractVersion": 2},
    ):
        rejected = client.post(
            "/api/v3/thesis-ledger/control/policies/apply", headers=headers, json=policy
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"
        assert rejected.json()["detail"]["supportedVersions"] == [3]
    effective = client.get(
        "/api/v3/thesis-ledger/control/policies/effective?contractVersion=3", headers=headers
    )
    assert effective.status_code == 200
    assert effective.json()["projection"] is None


def test_provider_config_patch_preserves_omitted_fields_and_defaults_new_rows(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}

    initial = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/config",
        headers=headers,
        json=_provider_envelope(
            enabled=False,
            settings={"source": "custom"},
            credentials={"method": "token", "values": {"token": "stored-secret"}},
        ),
    )
    assert initial.status_code == 200

    patch = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/config",
        headers=headers,
        json=_provider_envelope(enabled=True),
    )
    assert patch.status_code == 200
    assert patch.json()["enabled"] is True
    assert patch.json()["settings"] == {"source": "custom"}
    assert patch.json()["credentialConfigured"] is True
    assert "stored-secret" not in patch.text

    new_row = client.post(
        "/api/v3/thesis-ledger/control/providers/efinance/config",
        headers=headers,
        json=_provider_envelope(),
    )
    assert new_row.status_code == 200
    assert new_row.json()["enabled"] is True
    assert new_row.json()["settings"] == {}


def test_provider_config_credential_and_empty_patches_preserve_disabled_state(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}

    initial = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/config",
        headers=headers,
        json=_provider_envelope(enabled=False, settings={"source": "custom"}),
    )
    assert initial.status_code == 200

    credential_patch = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/config",
        headers=headers,
        json=_provider_envelope(credentials={"method": "token", "values": {"token": "stored-secret"}}),
    )
    assert credential_patch.status_code == 200
    assert credential_patch.json()["enabled"] is False
    assert credential_patch.json()["settings"] == {"source": "custom"}

    empty_patch = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/config",
        headers=headers,
        json=_provider_envelope(),
    )
    assert empty_patch.status_code == 200
    assert empty_patch.json()["enabled"] is False
    assert empty_patch.json()["settings"] == {"source": "custom"}
    assert empty_patch.json()["credentialConfigured"] is True
    assert "stored-secret" not in empty_patch.text


def test_provider_write_routes_reject_old_version_and_credential_alias(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    for suffix in ("config", "test", "remove"):
        old_url = f"/api/v1/thesis-ledger/control/providers/tushare/{suffix}"
        current_url = f"/api/v3/thesis-ledger/control/providers/tushare/{suffix}"
        assert client.post(old_url, headers=headers, json=_provider_envelope()).status_code == 404
        rejected = client.post(current_url, headers=headers, json=_envelope())
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"
        alias = client.post(current_url, headers=headers, json=_provider_envelope(credential="old"))
        assert alias.status_code == 422
        assert alias.json()["detail"]["code"] == "INVALID_PROVIDER_CREDENTIALS"
    registry = client.get("/api/v3/thesis-ledger/control/providers", headers=headers).json()
    tushare = next(item for item in registry["providers"] if item["providerId"] == "tushare")
    assert tushare["credentialConfigured"] is False


def test_chip_summary_manifest_declares_exact_provider(monkeypatch, tmp_path):
    """CHIP_SUMMARY 的 Provider 能力由目录声明。"""
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    registry = client.get("/api/v3/thesis-ledger/control/providers", headers=headers)
    assert registry.status_code == 200
    assert registry.json()["contractVersion"] == 3
    assert client.get("/api/v1/thesis-ledger/control/providers", headers=headers).status_code == 404
    providers = {item["providerId"]: item for item in registry.json()["providers"]}
    assert providers["akshare"]["capabilities"]["CHIP_SUMMARY"] == ["STOCK"]
    assert "CHIP_SUMMARY" not in providers["efinance"]["capabilities"]


def test_empty_v3_routes_are_valid(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    response = client.post(
        "/api/v3/thesis-ledger/control/policies/apply",
        headers=headers,
        json={**_envelope(revision=1, enabled=True, routes=[]), "contractVersion": 3},
    )
    assert response.status_code == 200
    assert response.json()["effective"]["routes"] == []


def test_catalog_snapshot_delta_and_expired_cursor(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer data-token"}
    assert client.get("/api/v1/thesis-ledger/catalog/snapshot", headers=headers).status_code == 404
    snapshot = client.get("/api/v3/thesis-ledger/catalog/snapshot", headers=headers)
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["complete"] is True
    assert payload["generation"] == 1
    assert payload["checksum"]
    assert payload["contractVersion"] == 3

    control_headers = {"authorization": "Bearer control-token"}
    ack_body = {
        "contractVersion": 3,
        "consumer": "thesis-ledger",
        "requestId": "catalog-ack-test",
        "generation": payload["generation"],
        "checksum": payload["checksum"],
    }
    assert client.post(
        "/api/v1/thesis-ledger/control/catalog/ack",
        headers=control_headers, json=ack_body,
    ).status_code == 404
    rejected = client.post(
        "/api/v3/thesis-ledger/control/catalog/ack",
        headers=control_headers, json={**ack_body, "contractVersion": 1},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"
    accepted = client.post(
        "/api/v3/thesis-ledger/control/catalog/ack",
        headers=control_headers, json=ack_body,
    )
    assert accepted.status_code == 200
    assert accepted.json()["acknowledged"] is True

    delta = client.get(
        f"/api/v3/thesis-ledger/catalog/delta?cursor={payload['cursor']}", headers=headers
    )
    assert delta.status_code == 200
    assert delta.json()["items"] == []

    expired = client.get(
        "/api/v3/thesis-ledger/catalog/delta?cursor=generation%3A999", headers=headers
    )
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "CATALOG_CURSOR_EXPIRED"


def test_capability_smoke_does_not_change_policy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    response = client.post(
        "/api/v3/thesis-ledger/control/providers/akshare/test",
        headers=headers,
        json=_provider_envelope(capabilities=["REALTIME_QUOTE"]),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["capabilityResults"]["REALTIME_QUOTE"]["attempted"] is False
    assert response.json()["capabilityResults"]["REALTIME_QUOTE"]["errorCode"] == "fixture_mode"
    assert response.json()["capabilityResults"]["FUND_NAV_HISTORY"]["status"] == "unavailable"
    assert response.json()["capabilityResults"]["FUND_NAV_HISTORY"]["attempted"] is False
    assert response.json()["capabilityResults"]["FUND_HOLDINGS"]["status"] == "unavailable"
    assert response.json()["capabilityResults"]["FUND_HOLDINGS"]["attempted"] is False
    assert response.json()["capabilityResults"]["CHIP_SUMMARY"]["status"] == "unavailable"
    assert response.json()["capabilityResults"]["CHIP_SUMMARY"]["attempted"] is False
    registry = client.get("/api/v3/thesis-ledger/control/providers", headers=headers)
    assert registry.json()["providers"][0]["credentialConfigured"] is False


def test_provider_test_uses_in_memory_structured_draft_without_persisting(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("THESIS_LEDGER_FIXTURE_MODE", raising=False)
    headers = {"authorization": "Bearer control-token"}
    saved = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/config",
        headers=headers,
        json=_provider_envelope(credentials={"method": "token", "values": {"token": "page-token"}}),
    )
    assert saved.status_code == 200
    calls = []

    class _Runtime:
        def smoke(self, provider_id, capability, *, credential_snapshot, draft_probe):
            calls.append((provider_id, capability, credential_snapshot, draft_probe))
            return {"status": "healthy", "readOnly": True, "attempted": True}

    monkeypatch.setattr(
        "src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime",
        lambda: _Runtime(),
    )
    response = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/test",
        headers=headers,
        json=_provider_envelope(credentials={"method": "token", "values": {"token": ""}}),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["capabilityResults"]["REALTIME_QUOTE"]["attempted"] is True
    assert calls
    assert all(snapshot.values["token"] == "page-token" for _, _, snapshot, _ in calls)
    assert all(draft_probe is True for _, _, _, draft_probe in calls)

    calls.clear()
    saved_response = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/test",
        headers=headers,
        json=_provider_envelope(),
    )
    assert saved_response.status_code == 200
    assert calls
    assert all(draft_probe is False for _, _, _, draft_probe in calls)
    assert client.get("/api/v3/thesis-ledger/control/providers", headers=headers).json()["providers"]


def test_provider_test_draft_does_not_merge_environment_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: type("EnvironmentConfig", (), {"tushare_token": "environment-token"})(),
    )
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/test",
        headers={"authorization": "Bearer control-token"},
        json=_provider_envelope(credentials={"method": "token", "values": {"token": ""}}),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_PROVIDER_CREDENTIALS"


def test_provider_removal_clears_runtime_config_but_keeps_tombstone(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/config",
        headers=headers,
        json=_provider_envelope(
            credentials={"method": "token", "values": {"token": "fixture-secret"}}, enabled=True,
        ),
    )
    response = client.post(
        "/api/v3/thesis-ledger/control/providers/tushare/remove",
        headers=headers,
        json=_provider_envelope(reason="test-removal"),
    )
    assert response.status_code == 200
    assert response.json()["tombstone"]["providerId"] == "tushare"
    registry = client.get("/api/v3/thesis-ledger/control/providers", headers=headers).json()
    removed = next(item for item in registry["providers"] if item["providerId"] == "tushare")
    assert removed["credentialConfigured"] is False
    assert removed["tombstone"]["reason"] == "test-removal"
