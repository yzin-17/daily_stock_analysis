"""HiThink 页面凭证通过实际 Control HTTP、加密存储和运行时诊断接缝。"""

import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient as HttpClient
import pandas as pd
import pytest

from api.thesis_ledger import router_v3
from src.services.thesis_ledger_control import (
    ControlContractError, ThesisLedgerControlStore, _encrypt_secret, _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


PAGE_KEY = "synthetic-page-hithink-api-key"
ENV_KEY = "synthetic-environment-hithink-api-key"
BASE = "/api/v3/thesis-ledger/control/providers/hithink"
HEADERS = {"authorization": "Bearer synthetic-control-token"}
ETF_KEY = {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR",
           "timeframe": "1d", "adjustment": "qfq"}
ETF_TARGET = {"providerId": "hithink", "upstreamSource": "fund-market-historical"}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    database = tmp_path / "control.db"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "synthetic-control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-control-encryption-key")
    monkeypatch.setenv("HITHINK_API_KEY", ENV_KEY)
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    store = ThesisLedgerControlStore(str(database))
    with HttpClient(app) as client:
        yield client, store, database


def post(client, **values):
    return client.post(BASE + "/config", headers=HEADERS, json={
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "page-key-test", **values,
    })


def save(client, key=PAGE_KEY):
    return post(client, credentials={"method": "api_key", "values": {"apiKey": key}})


def redacted(value):
    assert PAGE_KEY not in json.dumps(value)
    assert ENV_KEY not in json.dumps(value)


def test_actual_http_exposes_write_only_key_and_encrypts_reopened_value(runtime):
    client, store, database = runtime
    providers = client.get("/api/v3/thesis-ledger/control/providers", headers=HEADERS).json()
    provider = next(p for p in providers["providers"] if p["providerId"] == "hithink")
    assert provider["credentialSchema"]["methods"] == [{
        "method": "api_key", "fields": [{"name": "apiKey", "secret": True, "required": True}],
    }]
    response = save(client)
    assert response.status_code == 200
    assert response.json()["credentialSource"] == "control"
    assert response.json()["credentialFieldsConfigured"] == {"apiKey": True}
    redacted(response.json())
    with sqlite3.connect(database) as connection:
        ciphertext = connection.execute(
            "SELECT credential_ciphertext FROM thesis_ledger_provider_config WHERE provider_id='hithink'",
        ).fetchone()[0]
    redacted(ciphertext)
    snapshot = ThesisLedgerControlStore(str(database)).provider_credential_snapshot("hithink")
    assert snapshot.source == "control" and snapshot.values == {"apiKey": PAGE_KEY}
    assert PAGE_KEY not in repr(snapshot)
    assert _provider_credential_revision_from_snapshot(snapshot) is not None
    adapter = ThesisLedgerProviderRuntime(store)
    assert adapter._catalog_market_v3_admission_is_current(ETF_KEY, ETF_TARGET, provider)
    assert adapter._catalog_market_v3_admission_is_current(
        {**ETF_KEY, "assetType": "STOCK"},
        {"providerId": "hithink", "upstreamSource": "hithink-financial-api"}, provider,
    )


def test_blank_preserves_page_key_rotation_changes_revision_and_clear_restores_environment(runtime):
    client, store, _database = runtime
    assert save(client).status_code == 200
    initial = store.provider_credential_snapshot("hithink")
    assert post(client, enabled=False).status_code == 200
    assert save(client, "").status_code == 200
    assert store.provider_credential_snapshot("hithink").values == initial.values
    assert save(client, "synthetic-page-key-rotated").json()["enabled"] is False
    assert _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("hithink")) != (
        _provider_credential_revision_from_snapshot(initial)
    )
    cleared = post(client, clearCredentials=True)
    assert cleared.status_code == 200
    assert cleared.json()["credentialSource"] == "environment"
    assert store.provider_credential_snapshot("hithink").values == {"apiKey": ENV_KEY}


def test_invalid_page_key_never_falls_back_to_environment_and_missing_initial_key_is_rejected(runtime):
    client, store, _database = runtime
    assert save(client, " ").status_code == 422
    assert save(client).status_code == 200
    version, ciphertext = _encrypt_secret(
        '{"kind":"provider_credentials","version":99,"method":"api_key","values":{"apiKey":"x"}}',
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE thesis_ledger_provider_config SET credential_ciphertext=?,secret_key_version=? WHERE provider_id='hithink'",
            (ciphertext, version),
        )
    with pytest.raises(ControlContractError, match="无法安全解析"):
        store.provider_credential_snapshot("hithink")


def test_draft_http_probe_uses_input_for_all_capabilities_without_saving_or_admitting(runtime, monkeypatch):
    client, store, database = runtime
    import api.thesis_ledger as api
    calls = []

    def precise(_runtime, key, target, snapshot):
        assert snapshot.values["apiKey"] == PAGE_KEY
        calls.append((key["capability"], target["upstreamSource"]))
        return {"symbol": "600519.SH", "start": "2026-09-28", "end": "2026-09-30"}

    monkeypatch.setattr("src.services.thesis_ledger_provider_exact_checks.read_exact_probe", precise)
    monkeypatch.setattr(api, "_fixture_mode", lambda: False)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime",
                        lambda: ThesisLedgerProviderRuntime(store))
    response = client.post(BASE + "/test", headers=HEADERS, json={
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "probe-input",
        "credentials": {"method": "api_key", "values": {"apiKey": PAGE_KEY}},
    })
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert len(calls) == 3
    redacted(response.json())
    assert store.provider_credential_snapshot("hithink").source == "environment"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM thesis_ledger_provider_config").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM thesis_ledger_route_admission_v3").fetchone()[0] == 0


def test_probe_preserves_authentication_error_code(runtime, monkeypatch):
    _client, store, _database = runtime
    import src.services.thesis_ledger_hithink_probe as probe
    from src.services.thesis_ledger_hithink_quote import HiThinkQuoteError
    from src.services.thesis_ledger_provider_runtime import ProviderCallError

    def denied(**_options):
        raise HiThinkQuoteError("authentication_failed", "synthetic-key rejected")

    monkeypatch.setattr(probe, "HiThinkSnapshotAdapter", denied)
    with pytest.raises(ProviderCallError) as caught:
        ThesisLedgerProviderRuntime(store).smoke("hithink", "REALTIME_QUOTE", draft_probe=True)
    assert caught.value.code == "authentication_failed"
    redacted(str(caught.value))
