"""来源能力与精确库存同源，OAuth 配置具有完整令牌且诊断只读。"""

import base64
import json
from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_capability_projection import exact_provider_manifests
from src.services.thesis_ledger_provider_manifests import PROVIDER_MANIFESTS
from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
from src.services.provider_oauth_runtime import build_page_oauth


def test_every_source_capability_and_upstream_is_projected_from_exact_inventory():
    from copy import deepcopy
    original = deepcopy(PROVIDER_MANIFESTS)
    actual = exact_provider_manifests(PROVIDER_MANIFESTS)
    inventory = exact_route_inventory()
    for provider_id, manifest in actual.items():
        expected = {(key["market"], key["capability"], key["assetType"], target["upstreamSource"])
                    for key, target, _ in inventory if target["providerId"] == provider_id}
        assert expected
        assert set(manifest["markets"]) == {row[0] for row in expected}
        assert {(cap, asset) for cap, assets in manifest["capabilities"].items() for asset in assets} == {(row[1], row[2]) for row in expected}
        assert {(source["sourceId"], cap, asset) for source in manifest["upstreamSources"] for cap, assets in source["capabilities"].items() for asset in assets} == {(row[3], row[1], row[2]) for row in expected}
    assert actual["rqdata"]["capabilities"] == {"CASH_DISTRIBUTION": ["ETF"], "SPLIT_EVENT": ["ETF"]}
    assert PROVIDER_MANIFESTS == original


@pytest.fixture
def oauth_environment(monkeypatch, tmp_path):
    from data_provider import longbridge_fetcher
    monkeypatch.setattr("src.config.get_config", lambda: SimpleNamespace(longbridge_oauth_client_id="synthetic-client"))
    for name in ["LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN", "LONGBRIDGE_OAUTH_TOKEN_CACHE_B64", "LONGBRIDGE_OAUTH_CLIENT_ID"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(longbridge_fetcher, "_oauth_token_cache_path", lambda _: tmp_path / "missing-token")
    return ThesisLedgerControlStore(str(tmp_path / "config.db"))


def token(access="synthetic-access"):
    return json.dumps({"client_id": "synthetic-client", "access_token": access, "refresh_token": "synthetic-refresh", "expires_at": 1900000000})


def test_client_id_alone_is_not_complete_credential(oauth_environment):
    provider = next(p for p in oauth_environment.provider_registry() if p["providerId"] == "longbridge")
    assert provider["credentialConfigured"] is False
    assert provider["enabled"] is False
    assert not oauth_environment.provider_credential_snapshot("longbridge").values


def test_environment_oauth_token_is_validated_pinned_and_rotates_without_config_version(oauth_environment, monkeypatch):
    monkeypatch.setenv("LONGBRIDGE_OAUTH_TOKEN_CACHE_B64", base64.b64encode(token().encode()).decode())
    first = oauth_environment.provider_credential_snapshot("longbridge")
    assert first.method == "oauth" and first.source == "environment"
    assert first.values == {"clientId": "synthetic-client", "tokenJson": token()}
    assert "synthetic-access" not in repr(first)
    monkeypatch.setenv("LONGBRIDGE_OAUTH_TOKEN_CACHE_B64", base64.b64encode(token("rotated-synthetic").encode()).decode())
    second = oauth_environment.provider_credential_snapshot("longbridge")
    assert first.config_version == second.config_version and first != second


def test_wrong_oauth_account_and_invalid_cache_are_not_configured(oauth_environment, monkeypatch):
    wrong = json.dumps({"client_id": "another-client", "access_token": "synthetic", "expires_at": 1900000000})
    for value in ["invalid-base64", base64.b64encode(wrong.encode()).decode()]:
        monkeypatch.setenv("LONGBRIDGE_OAUTH_TOKEN_CACHE_B64", value)
        assert not oauth_environment.provider_credential_snapshot("longbridge").values


def test_read_only_oauth_refresh_does_not_write_credential_database(oauth_environment, monkeypatch):
    monkeypatch.setenv("LONGBRIDGE_OAUTH_TOKEN_CACHE_B64", base64.b64encode(token().encode()).decode())
    snapshot = oauth_environment.provider_credential_snapshot("longbridge")
    class Builder:
        def __init__(self, client_id, **kwargs):
            assert client_id == "synthetic-client" and kwargs["allow_authorization"] is False
            self.save = kwargs["on_token_save"]
        def build(self, open_url):
            return self
    builder = build_page_oauth(snapshot, oauth_environment.database_path, read_only=True, builder_factory=lambda: Builder)
    builder.save(token("refreshed-synthetic"))
    assert oauth_environment.provider_credential_snapshot("longbridge") == snapshot
    with oauth_environment._connect() as connection:
        assert connection.execute("SELECT count(*) FROM thesis_ledger_provider_config").fetchone()[0] == 0
