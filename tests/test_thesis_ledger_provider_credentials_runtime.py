import os
import json
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest
import requests

from data_provider.alphavantage_fetcher import AlphaVantageFetcher
from data_provider.base import ProviderHTTPError
from data_provider.finnhub_fetcher import FinnhubFetcher
from data_provider.longbridge_fetcher import LongbridgeFetcher
from data_provider.tushare_fetcher import TushareFetcher
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore
from src.services.provider_oauth_contract import OAuthStateError
from src.services.provider_oauth_store import ProviderOAuthStore
from src.services.thesis_ledger_control import _encrypt_secret
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError,
    ThesisLedgerProviderRuntime,
    classify_provider_exception,
)


def _oauth_snapshot_store(monkeypatch, tmp_path, access_token="fixture-access"):
    store = _store(monkeypatch, tmp_path)
    oauth = ProviderOAuthStore(store.database_path, encrypt=_encrypt_secret)
    session, _ = oauth.create("client")
    token = json.dumps(
        {
            "client_id": "client",
            "access_token": access_token,
            "refresh_token": "fixture-refresh",
            "expires_at": 1900000000,
        }
    )
    version = oauth.save_authorized_token(session["sessionId"], token)
    return store, oauth, token, version


def _store(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "0123456789abcdef-secret-key")
    return ThesisLedgerControlStore(str(tmp_path / "runtime.db"))


def test_explicit_fetcher_credentials_do_not_read_or_mutate_environment(monkeypatch):
    for name in (
        "TUSHARE_TOKEN",
        "FINNHUB_API_KEY",
        "ALPHAVANTAGE_API_KEY",
        "LONGBRIDGE_APP_KEY",
        "LONGBRIDGE_APP_SECRET",
        "LONGBRIDGE_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    tushare = TushareFetcher(token="page-tushare")
    finnhub = FinnhubFetcher(api_key="page-finnhub")
    alphavantage = AlphaVantageFetcher(api_key="page-alpha")
    longbridge = LongbridgeFetcher(
        app_key="page-key",
        app_secret="page-secret",
        access_token="page-token",
    )

    assert tushare._token == "page-tushare"
    assert tushare._api._token == "page-tushare"
    assert finnhub._api_key == "page-finnhub"
    assert alphavantage._api_key == "page-alpha"
    assert longbridge._is_available() is True
    assert all(name not in os.environ for name in (
        "LONGBRIDGE_APP_KEY",
        "LONGBRIDGE_APP_SECRET",
        "LONGBRIDGE_ACCESS_TOKEN",
    ))


def test_longbridge_oauth_snapshot_is_structured_and_manifest_exposes_client_id(
    monkeypatch, tmp_path
):
    store, _oauth, token, version = _oauth_snapshot_store(monkeypatch, tmp_path)
    snapshot = store.provider_credential_snapshot("longbridge")
    manifest = next(item for item in store.provider_registry() if item["providerId"] == "longbridge")

    assert snapshot.source == "control"
    assert snapshot.method == "oauth"
    assert snapshot.values == {"clientId": "client", "tokenJson": token}
    assert snapshot.credential_version == version
    oauth_method = next(
        item for item in manifest["credentialSchema"]["methods"] if item["method"] == "oauth"
    )
    assert oauth_method["fields"] == [
        {"name": "clientId", "secret": False, "required": True}
    ]
    assert next(
        item for item in store.provider_registry() if item["providerId"] == "longbridge"
    )["credentialFieldsConfigured"] == {"clientId": True}


def test_runtime_injects_page_oauth_without_default_sdk_cache(monkeypatch, tmp_path):
    store, _oauth, token, _version = _oauth_snapshot_store(monkeypatch, tmp_path)
    marker = object()
    monkeypatch.setattr(
        "src.services.provider_oauth_runtime.build_page_oauth",
        lambda snapshot, database_path: marker,
    )

    class _Adapter:
        def __init__(self, *, oauth_token=None):
            self.oauth_token = oauth_token

    monkeypatch.setattr("data_provider.longbridge_fetcher.LongbridgeFetcher", _Adapter)
    adapter = ThesisLedgerProviderRuntime(store)._adapter("longbridge")

    assert adapter.oauth_token is marker
    assert token not in repr(adapter)


def test_runtime_uses_actual_sdk_oauth_factory_without_authorization(monkeypatch, tmp_path):
    store, _oauth, token, _version = _oauth_snapshot_store(monkeypatch, tmp_path)
    fake_openapi = ModuleType("longbridge.openapi")
    built = {}

    class _OAuthBuilder:
        THESIS_LEDGER_STORAGE_VERSION = 1

        def __init__(self, client_id, *, token_json, on_token_save, allow_authorization):
            built.update(
                client_id=client_id,
                token_json=token_json,
                on_token_save=on_token_save,
                allow_authorization=allow_authorization,
            )

        def build(self, on_open_url):
            built["on_open_url"] = on_open_url
            return "sdk-oauth-token"

    fake_openapi.OAuthBuilder = _OAuthBuilder
    fake_package = ModuleType("longbridge")
    fake_package.openapi = fake_openapi
    monkeypatch.setitem(sys.modules, "longbridge", fake_package)
    monkeypatch.setitem(sys.modules, "longbridge.openapi", fake_openapi)

    class _Adapter:
        def __init__(self, *, oauth_token=None):
            self.oauth_token = oauth_token

    monkeypatch.setattr("data_provider.longbridge_fetcher.LongbridgeFetcher", _Adapter)
    adapter = ThesisLedgerProviderRuntime(store)._adapter("longbridge")

    assert adapter.oauth_token == "sdk-oauth-token"
    assert built["client_id"] == "client"
    assert built["token_json"] == token
    assert built["allow_authorization"] is False
    assert callable(built["on_token_save"])
    assert callable(built["on_open_url"])


def test_longbridge_explicit_oauth_token_does_not_read_browser_or_file_cache(monkeypatch):
    marker = object()
    fake_openapi = ModuleType("longbridge.openapi")

    class _Config:
        @staticmethod
        def from_oauth(value):
            assert value is marker
            return "oauth-config"

    class _QuoteContext:
        def __init__(self, config):
            self.config = config

    fake_openapi.Config = _Config
    fake_openapi.QuoteContext = _QuoteContext
    fake_package = ModuleType("longbridge")
    fake_package.openapi = fake_openapi
    monkeypatch.setitem(sys.modules, "longbridge", fake_package)
    monkeypatch.setitem(sys.modules, "longbridge.openapi", fake_openapi)
    for name in (
        "LONGBRIDGE_OAUTH_CLIENT_ID",
        "LONGBRIDGE_OAUTH_TOKEN_CACHE",
        "LONGBRIDGE_OAUTH_TOKEN_CACHE_B64",
        "LONGBRIDGE_APP_KEY",
        "LONGBRIDGE_APP_SECRET",
        "LONGBRIDGE_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    fetcher = LongbridgeFetcher(oauth_token=marker)
    context = fetcher._get_ctx()

    assert fetcher._is_available() is True
    assert context.config == "oauth-config"


def test_longbridge_explicit_legacy_errors_do_not_log_page_secrets(monkeypatch, caplog):
    fake_openapi = ModuleType("longbridge.openapi")

    class _Config:
        @staticmethod
        def from_apikey_env():
            return "legacy-config"

    class _QuoteContext:
        def __init__(self, _config):
            raise RuntimeError("SDK rejected page-secret during initialization")

    fake_openapi.Config = _Config
    fake_openapi.QuoteContext = _QuoteContext
    fake_package = ModuleType("longbridge")
    fake_package.openapi = fake_openapi
    monkeypatch.setitem(sys.modules, "longbridge", fake_package)
    monkeypatch.setitem(sys.modules, "longbridge.openapi", fake_openapi)

    fetcher = LongbridgeFetcher(
        app_key="page-key",
        app_secret="page-secret",
        access_token="page-token",
    )
    with caplog.at_level("DEBUG"):
        assert fetcher._get_ctx() is None
    assert "page-secret" not in caplog.text


def test_runtime_oauth_refresh_rebuilds_and_sdk_marker_failure_is_fail_closed(
    monkeypatch, tmp_path
):
    store, oauth, _token, version = _oauth_snapshot_store(monkeypatch, tmp_path)
    built = []
    monkeypatch.setattr(
        "src.services.provider_oauth_runtime.build_page_oauth",
        lambda snapshot, database_path: built.append(snapshot.values["tokenJson"]) or built[-1],
    )

    class _Adapter:
        def __init__(self, *, oauth_token=None):
            self.oauth_token = oauth_token

    monkeypatch.setattr("data_provider.longbridge_fetcher.LongbridgeFetcher", _Adapter)
    runtime = ThesisLedgerProviderRuntime(store)
    first = runtime._adapter("longbridge")
    next_version = oauth.save_refreshed_token(
        "client",
        json.dumps(
            {
                "client_id": "client",
                "access_token": "renewed",
                "refresh_token": "fixture-refresh",
                "expires_at": 1900000000,
            }
        ),
        version,
    )
    second = runtime._adapter("longbridge")

    assert first is not second
    assert first.oauth_token != second.oauth_token
    assert next_version > version
    monkeypatch.setattr(
        "src.services.provider_oauth_runtime.build_page_oauth",
        lambda snapshot, database_path: (_ for _ in ()).throw(
            OAuthStateError("OAUTH_SDK_UNAVAILABLE")
        ),
    )
    oauth.save_refreshed_token(
        "client",
        json.dumps(
            {
                "client_id": "client",
                "access_token": "marker-check",
                "refresh_token": "fixture-refresh",
                "expires_at": 1900000000,
            }
        ),
        next_version,
    )
    with pytest.raises(ProviderCallError) as error:
        runtime._adapter("longbridge")
    assert error.value.code == "OAUTH_SDK_UNAVAILABLE"


def test_runtime_snapshot_is_immutable_and_rebuilds_on_version_change(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_provider_config(
        "tushare",
        {"requestId": "first", "credentials": {"method": "token", "values": {"token": "one"}}},
    )
    snapshot = store.provider_credential_snapshot("tushare")
    assert snapshot.source == "control"
    assert snapshot.method == "token"
    assert snapshot.values["token"] == "one"
    assert "one" not in repr(snapshot)
    with pytest.raises(TypeError):
        snapshot.values["token"] = "changed"  # type: ignore[index]

    runtime = ThesisLedgerProviderRuntime(store)
    first = runtime._adapter("tushare")
    store.save_provider_config(
        "tushare",
        {"requestId": "second", "credentials": {"method": "token", "values": {"token": "two"}}},
    )
    second = runtime._adapter("tushare")
    assert first is not second
    assert first._api._token == "one"
    assert second._api._token == "two"


def test_refresh_does_not_close_adapter_held_by_inflight_request(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_provider_config(
        "tushare",
        {"requestId": "first", "credentials": {"method": "token", "values": {"token": "one"}}},
    )
    runtime = ThesisLedgerProviderRuntime(store)
    first = runtime._adapter("tushare")
    closed = []
    first.close = lambda: closed.append(True)
    started = threading.Event()
    release = threading.Event()

    def old_request(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return None

    first.get_daily_data = old_request
    request = threading.Thread(target=first.get_daily_data, args=("600519.SH",))
    request.start()
    assert started.wait(timeout=2)

    store.save_provider_config(
        "tushare",
        {"requestId": "second", "credentials": {"method": "token", "values": {"token": "two"}}},
    )
    second = runtime._adapter("tushare")
    release.set()
    request.join(timeout=2)

    assert second is not first
    assert first._api._token == "one"
    assert second._api._token == "two"
    assert closed == []


def test_corrupt_page_credential_fails_closed_without_environment_fallback(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_provider_config(
        "tushare",
        {"requestId": "page", "credentials": {"method": "token", "values": {"token": "page"}}},
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE thesis_ledger_provider_config SET credential_ciphertext='broken' "
            "WHERE provider_id='tushare'"
        )
    runtime = ThesisLedgerProviderRuntime(store)
    with pytest.raises(ProviderCallError) as error:
        runtime._adapter("tushare")
    assert error.value.code == "SECRET_CREDENTIAL_INVALID"


def test_clear_page_credential_restores_environment_runtime_snapshot(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: type("EnvironmentConfig", (), {"tushare_token": "environment-token"})(),
    )
    store.save_provider_config(
        "tushare",
        {"requestId": "page", "credentials": {"method": "token", "values": {"token": "page"}}},
    )
    store.save_provider_config("tushare", {"requestId": "clear", "clearCredentials": True})

    snapshot = store.provider_credential_snapshot("tushare")
    assert snapshot.source == "environment"
    assert snapshot.values["token"] == "environment-token"
    adapter = ThesisLedgerProviderRuntime(store)._adapter("tushare")
    assert adapter._api._token == "environment-token"


def test_explicit_single_key_credentials_reach_deterministic_http_requests(monkeypatch):
    class _Response:
        status_code = 200
        text = '{"code":0,"data":{"fields":["ts_code"],"items":[["600519.SH"]]}}'

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "s": "ok",
                "c": [100.0],
                "h": [101.0],
                "l": [99.0],
                "o": [99.5],
                "t": [1704067200],
                "v": [100],
            }

    tushare_calls = []
    monkeypatch.setattr(
        "data_provider.tushare_fetcher.requests.post",
        lambda url, **kwargs: (tushare_calls.append((url, kwargs)) or _Response()),
    )
    tushare = TushareFetcher(token="page-tushare")
    tushare._api.query("daily", ts_code="600519.SH")
    assert tushare_calls[0][1]["json"]["token"] == "page-tushare"

    finnhub_calls = []
    monkeypatch.setattr(
        "data_provider.finnhub_fetcher.requests.get",
        lambda url, **kwargs: (finnhub_calls.append((url, kwargs)) or _Response()),
    )
    finnhub = FinnhubFetcher(api_key="page-finnhub")
    finnhub.random_sleep = lambda *_args: None
    finnhub.get_realtime_quote("AAPL")
    assert finnhub_calls[0][1]["params"]["token"] == "page-finnhub"

    alpha_calls = []
    monkeypatch.setattr(
        "data_provider.alphavantage_fetcher.requests.get",
        lambda url, **kwargs: (alpha_calls.append((url, kwargs)) or _Response()),
    )
    alpha = AlphaVantageFetcher(api_key="page-alpha")
    alpha.random_sleep = lambda *_args: None
    alpha.get_realtime_quote("AAPL")
    assert alpha_calls[0][1]["params"]["apikey"] == "page-alpha"


@pytest.mark.parametrize(
    ("provider_id", "module_name", "fetcher", "key_name"),
    [
        ("finnhub", "data_provider.finnhub_fetcher", FinnhubFetcher, "token"),
        ("alphavantage", "data_provider.alphavantage_fetcher", AlphaVantageFetcher, "apikey"),
    ],
)
@pytest.mark.parametrize("status_code, expected", [(401, "authentication_failed"), (403, "permission_denied"), (429, "rate_limited")])
def test_strict_probe_classifies_http_status_without_logging_secret(
    monkeypatch,
    caplog,
    provider_id,
    module_name,
    fetcher,
    key_name,
    status_code,
    expected,
):
    response = type("_Response", (), {})()
    response.status_code = status_code

    def raise_for_status():
        raise requests.HTTPError("upstream status", response=response)

    response.raise_for_status = raise_for_status

    requests_seen = []
    monkeypatch.setattr(
        f"{module_name}.requests.get",
        lambda *_args, **kwargs: (requests_seen.append(kwargs) or response),
    )
    adapter = fetcher(api_key="page-secret")
    adapter.random_sleep = lambda *_args: None
    runtime = ThesisLedgerProviderRuntime()

    with pytest.raises(ProviderHTTPError) as error:
        runtime._realtime_quote(adapter, provider_id, "AAPL")

    assert classify_provider_exception(error.value) == expected
    assert "page-secret" not in caplog.text
    assert requests_seen[0]["timeout"] == 5
    assert key_name in ("token", "apikey")


@pytest.mark.parametrize(
    ("provider_id", "module_name", "fetcher"),
    [
        ("finnhub", "data_provider.finnhub_fetcher", FinnhubFetcher),
        ("alphavantage", "data_provider.alphavantage_fetcher", AlphaVantageFetcher),
    ],
)
def test_strict_probe_maps_timeout_to_network_failure(monkeypatch, provider_id, module_name, fetcher):
    monkeypatch.setattr(
        f"{module_name}.requests.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout("request timed out")),
    )
    adapter = fetcher(api_key="page-secret")
    adapter.random_sleep = lambda *_args: None

    with pytest.raises(ProviderHTTPError) as error:
        ThesisLedgerProviderRuntime()._realtime_quote(adapter, provider_id, "AAPL")

    assert classify_provider_exception(error.value) == "network_failure"


@pytest.mark.parametrize(
    ("provider_id", "module_name", "fetcher"),
    [
        ("finnhub", "data_provider.finnhub_fetcher", FinnhubFetcher),
        ("alphavantage", "data_provider.alphavantage_fetcher", AlphaVantageFetcher),
    ],
)
def test_strict_daily_probe_preserves_http_status_without_raw_url(
    monkeypatch, caplog, provider_id, module_name, fetcher
):
    response = type("_Response", (), {})()
    response.status_code = 401

    def raise_for_status():
        raise requests.HTTPError("upstream status", response=response)

    response.raise_for_status = raise_for_status
    monkeypatch.setattr(f"{module_name}.requests.get", lambda *_args, **_kwargs: response)
    adapter = fetcher(api_key="page-secret")
    adapter.random_sleep = lambda *_args: None

    with pytest.raises(ProviderHTTPError) as error:
        adapter.get_daily_data("AAPL", days=5, strict=True)

    assert classify_provider_exception(error.value) == "authentication_failed"
    assert "page-secret" not in caplog.text
    assert "finnhub.io" not in caplog.text
    assert "alphavantage.co" not in caplog.text


@pytest.mark.parametrize(
    ("provider_id", "module_name", "fetcher"),
    [
        ("finnhub", "data_provider.finnhub_fetcher", FinnhubFetcher),
        ("alphavantage", "data_provider.alphavantage_fetcher", AlphaVantageFetcher),
    ],
)
def test_page_credential_daily_failure_is_safe_without_strict_argument(
    monkeypatch, caplog, tmp_path, provider_id, module_name, fetcher
):
    store = _store(monkeypatch, tmp_path)
    store.save_provider_config(
        provider_id,
        {
            "requestId": "page",
            "credentials": {"method": "api_key", "values": {"apiKey": "page-secret"}},
        },
    )
    response = type("_Response", (), {})()
    response.status_code = 401

    def raise_for_status():
        raise requests.HTTPError("upstream status", response=response)

    response.raise_for_status = raise_for_status
    monkeypatch.setattr(f"{module_name}.requests.get", lambda *_args, **_kwargs: response)
    adapter = ThesisLedgerProviderRuntime(store)._adapter(provider_id)

    with pytest.raises(ProviderHTTPError) as error:
        adapter.get_daily_data("AAPL", days=5)

    assert classify_provider_exception(error.value) == "authentication_failed"
    assert "page-secret" not in caplog.text
    assert "finnhub.io" not in caplog.text
    assert "alphavantage.co" not in caplog.text


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"Note": "limited"}, "rate_limited"),
        ({"Information": "limited"}, "rate_limited"),
        ({"Information": "This is a premium endpoint"}, "permission_denied"),
        ({"Information": "Invalid API key"}, "authentication_failed"),
        ({"Error Message": "Invalid API call"}, "permission_denied"),
    ],
)
def test_alphavantage_200_error_payloads_are_classified_without_secret(
    monkeypatch, caplog, payload, expected
):
    response = type("_Response", (), {})()
    response.status_code = 200
    response.raise_for_status = lambda: None
    response.json = lambda: payload
    monkeypatch.setattr(
        "data_provider.alphavantage_fetcher.requests.get",
        lambda *_args, **_kwargs: response,
    )
    adapter = AlphaVantageFetcher(api_key="page-secret", strict_errors=True)
    adapter.random_sleep = lambda *_args: None

    with pytest.raises(ProviderHTTPError) as error:
        adapter.get_realtime_quote("AAPL")

    assert classify_provider_exception(error.value) == expected
    assert "page-secret" not in caplog.text


def test_draft_probe_does_not_persist_health_or_circuit_state(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_provider_config(
        "tushare",
        {"requestId": "page", "credentials": {"method": "token", "values": {"token": "page"}}},
    )

    class _Adapter:
        def get_realtime_quote(self, _symbol):
            return SimpleNamespace(price=100.0)

    runtime = ThesisLedgerProviderRuntime(store, adapters={"tushare": _Adapter()})
    snapshot = store.provider_credential_snapshot("tushare")
    result = runtime.smoke("tushare", "REALTIME_QUOTE", credential_snapshot=snapshot)

    assert result["status"] == "healthy"
    assert store.health("tushare", "REALTIME_QUOTE", "STOCK") is None
    assert runtime.circuit.state("thesis-ledger:tushare:REALTIME_QUOTE:STOCK", runtime.clock()) == "closed"
