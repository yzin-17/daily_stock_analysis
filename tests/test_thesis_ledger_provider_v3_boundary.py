"""Provider 当前信封、只写凭证及旧请求无写入的 HTTP/SQLite 验证。"""

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3


def test_all_provider_envelopes_and_old_requests_preserve_sqlite(monkeypatch, tmp_path):
    database = tmp_path / "providers.db"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "provider-test")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "provider-secret-key")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    base = "/api/v3/thesis-ledger/control/providers"
    headers = {"authorization": "Bearer provider-test"}

    def snapshot():
        with sqlite3.connect(database) as connection:
            return tuple(connection.iterdump())

    with TestClient(app) as client:
        registry = client.get(base, headers=headers).json()
        assert registry["contractVersion"] == 3
        assert registry["consumer"] == "thesis-ledger"
        provider_ids = [item["providerId"] for item in registry["providers"]]
        assert len(provider_ids) == len(set(provider_ids))
        assert "longbridge" in provider_ids
        for provider_id in provider_ids:
            envelope = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": f"current-{provider_id}"}
            saved = client.post(f"{base}/{provider_id}/config", headers=headers, json=envelope)
            assert saved.status_code == 200, saved.text
            saved_body = saved.json()
            assert saved_body["contractVersion"] == 3
            assert saved_body["consumer"] == "thesis-ledger"
            assert saved_body["providerId"] == provider_id
            assert saved_body["requestId"] == envelope["requestId"]
            before = snapshot()
            tested = client.post(f"{base}/{provider_id}/test", headers=headers, json=envelope)
            assert tested.status_code == 200, tested.text
            assert tested.json()["contractVersion"] == 3
            assert tested.json()["providerId"] == provider_id
            assert tested.json()["requestId"] == envelope["requestId"]
            assert snapshot() == before
            for action in ("config", "test", "remove"):
                for version in (1, 2):
                    old = client.post(f"{base}/{provider_id}/{action}", headers=headers,
                                      json={**envelope, "contractVersion": version, "enabled": False})
                    assert old.status_code == 422
                    assert old.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"
                    assert snapshot() == before
            removed = client.post(f"{base}/{provider_id}/remove", headers=headers, json=envelope)
            assert removed.status_code == 200, removed.text
            assert removed.json()["contractVersion"] == 3
            assert removed.json()["providerId"] == provider_id
            assert removed.json()["tombstone"]["providerId"] == provider_id

        credentials = {"method": "token", "values": {"token": "never-echo-provider-token"}}
        saved = client.post(base + "/tushare/config", headers=headers,
                            json={**envelope, "credentials": credentials})
        assert saved.status_code == 200, saved.text
        assert "never-echo-provider-token" not in saved.text
        assert "never-echo-provider-token" not in client.get(base, headers=headers).text
        before = snapshot()
        draft = client.post(base + "/tushare/test", headers=headers,
                            json={**envelope, "credentials": {"method": "token", "values": {"token": "temporary-draft"}}})
        assert draft.status_code == 200
        assert "temporary-draft" not in draft.text
        assert snapshot() == before
