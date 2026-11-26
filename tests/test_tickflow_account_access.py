"""统一 TickFlow 来源的逐功能权限、免凭证隔离与错误分类。"""

from types import SimpleNamespace
import pytest

from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_provider_exact_checks import check_provider
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from data_provider.tickflow_account_access import failure_result


def envelope(**extra):
    return {"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "tickflow-check", **extra}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mixed.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "controlled-master")
    monkeypatch.setattr("src.services.thesis_ledger_control._environment_credential_values", lambda _: {})
    return ThesisLedgerProviderRuntime(ThesisLedgerControlStore())


def install_client(monkeypatch, failure=None):
    calls, closed = [], []
    def client(**options):
        def get(*args, **kwargs):
            calls.append((options, kwargs))
            if failure and options["api_key"]:
                raise failure
            return [] if "symbols" in kwargs else {"timestamp": []}
        return SimpleNamespace(klines=SimpleNamespace(get=get), quotes=SimpleNamespace(get=get),
                               close=lambda: closed.append(options))
    monkeypatch.setattr("tickflow.TickFlow", client)
    return calls, closed


def test_one_identity_and_one_target_per_exact_key():
    assert CAPABILITY_SOURCES.source("tickflow-free") is None
    source = CAPABILITY_SOURCES.source("tickflow")
    assert len(source.bindings) == 14
    assert len({binding.key for binding in source.bindings}) == 14
    assert {binding.upstream_source for binding in source.bindings} == {"tickflow"}
    assert source.manifest()["accountAccessSupported"]
    assert not source.manifest()["requiresCredential"]


def test_no_key_checks_daily_and_skips_other_functions(runtime, monkeypatch):
    calls, closed = install_client(monkeypatch)
    result = check_provider(runtime, "tickflow", envelope(purpose="account_access"))
    routes = result["accountAccess"]["routes"]
    assert len(calls) == len(closed) == 3
    assert all(options["api_key"] == "" and options["base_url"] == "https://free-api.tickflow.org"
               and options["max_retries"] == 0 for options, _ in calls)
    assert all(row["access"]["state"] == "allowed" for row in routes if row["key"]["capability"] == "DAILY_BAR")
    assert all(row["access"]["state"] == "credential_missing" and not row["access"]["attempted"]
               for row in routes if row["key"]["capability"] != "DAILY_BAR")
    assert result["status"] == "degraded" and not result["credentialConfigured"]
    entries = [entry for entry in runtime.market_route_catalog_v3()["entries"] if entry["target"]["providerId"] == "tickflow"]
    assert all(entry["state"] == "credential_missing" for entry in entries if entry["key"]["capability"] != "DAILY_BAR")
    from src.services.thesis_ledger_price_route_access import basic_price_route_configured
    assert all(not basic_price_route_configured(runtime.store, entry["key"], entry["target"])
               for entry in entries if entry["key"]["capability"] != "DAILY_BAR")


def test_partial_access_is_preserved_and_current_key_is_used(runtime, monkeypatch):
    from tickflow._exceptions import PermissionError
    runtime.store.save_provider_config("tickflow", envelope(credentials={"method": "api_key", "values": {"apiKey": "controlled-key"}}))
    calls, _ = install_client(monkeypatch, PermissionError("denied", code="FORBIDDEN", status_code=403))
    result = check_provider(runtime, "tickflow", envelope(purpose="account_access"))
    assert len(calls) == 14 and result["credentialConfigured"]
    assert {row["access"]["state"] for row in result["accountAccess"]["routes"]} == {"allowed", "permission_missing"}
    paid = [options for options, _ in calls if options["base_url"] == "https://api.tickflow.org"]
    assert len(paid) == 11 and all(options["api_key"] == "controlled-key" for options in paid)
    entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tickflow"]
    assert sum(e["accountAccess"]["state"] == "allowed" for e in entries) == 3


def test_fixture_is_zero_requests_and_draft_does_not_publish(runtime, monkeypatch):
    calls, _ = install_client(monkeypatch)
    result = check_provider(runtime, "tickflow", envelope(purpose="account_access"), fixture=True)
    assert calls == [] and all(not row["access"]["attempted"] for row in result["accountAccess"]["routes"])
    check_provider(runtime, "tickflow", envelope(purpose="account_access", credentials={
        "method": "api_key", "values": {"apiKey": "controlled-draft"}}))
    assert runtime.store.provider_credential_snapshot("tickflow").values == {}
    assert all(e["accountAccess"]["state"] != "allowed" for e in runtime.market_route_catalog_v3()["entries"]
               if e["target"]["providerId"] == "tickflow")


def test_current_check_survives_time_restart_and_key_rotation_invalidates(runtime, monkeypatch):
    calls, _ = install_client(monkeypatch)
    clock = [0]
    runtime.clock = lambda: clock[0]
    result = check_provider(runtime, "tickflow", envelope(purpose="account_access"))
    assert result["accountAccess"]["persisted"] is True
    assert "validUntil" not in result["accountAccess"] and "accountLevel" not in result["accountAccess"]
    clock[0] = 365 * 86400
    restarted = ThesisLedgerProviderRuntime(ThesisLedgerControlStore(runtime.store.database_path))
    entries = [e for e in restarted.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tickflow"]
    assert sum(e["accountAccess"]["state"] == "allowed" for e in entries) == 3
    assert all("validUntil" not in e["accountAccess"] for e in entries)
    assert all(e["accountAccess"]["persisted"] for e in entries if e["key"]["capability"] == "DAILY_BAR")
    assert len(calls) == 3
    runtime.store.save_provider_config("tickflow", envelope(credentials={"method": "api_key", "values": {"apiKey": "controlled-new-key"}}))
    entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tickflow"]
    assert all(e["accountAccess"]["state"] == "unknown" for e in entries)
    assert all(e["accountAccess"]["errorCode"] == "permission_not_checked" for e in entries)


def test_same_draft_is_persisted_on_save_without_rechecking(runtime, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    calls, _ = install_client(monkeypatch)
    credentials = {"method": "api_key", "values": {"apiKey": "controlled-draft-key"}}
    report = check_provider(runtime, "tickflow", envelope(purpose="account_access", credentials=credentials))["accountAccess"]
    assert report["credentialContext"] == "draft" and "persisted" not in report
    assert len(calls) == 14
    saved = configure_provider(runtime.store, "tickflow", envelope(credentials=credentials, expectedConfigVersion=0))
    assert len(calls) == 14
    restarted = ThesisLedgerProviderRuntime(ThesisLedgerControlStore(runtime.store.database_path))
    entries = [e for e in restarted.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tickflow"]
    assert all(e["accountAccess"]["state"] == "allowed" and e["accountAccess"]["persisted"] for e in entries)
    assert all(e["accountAccess"]["configVersion"] == saved["configVersion"] for e in entries)
    with runtime.store._connect() as connection:
        raw = connection.execute("SELECT report_json FROM thesis_ledger_account_access WHERE provider_id='tickflow'").fetchone()[0]
    assert "controlled-draft-key" not in raw and '"accountLevel": null' not in raw
    configure_provider(runtime.store, "tickflow", envelope(enabled=False))
    assert all(e["accountAccess"]["state"] == "allowed" for e in restarted.market_route_catalog_v3()["entries"]
               if e["target"]["providerId"] == "tickflow")


def test_unchecked_credentials_do_not_trigger_permission_calls(runtime, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    calls, _ = install_client(monkeypatch)
    configure_provider(runtime.store, "tickflow", envelope(credentials={"method": "api_key", "values": {"apiKey": "controlled-unchecked"}}))
    assert calls == []
    assert all(e["accountAccess"]["state"] == "unknown" for e in runtime.market_route_catalog_v3()["entries"]
               if e["target"]["providerId"] == "tickflow")


def test_changed_draft_is_not_adopted_and_stale_save_is_rejected(runtime, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    from src.services.thesis_ledger_control import ControlContractError
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    calls, _ = install_client(monkeypatch)
    first = {"method": "api_key", "values": {"apiKey": "controlled-first"}}
    second = {"method": "api_key", "values": {"apiKey": "controlled-second"}}
    check_provider(runtime, "tickflow", envelope(purpose="account_access", credentials=first))
    configure_provider(runtime.store, "tickflow", envelope(credentials=second, expectedConfigVersion=0))
    assert len(calls) == 14
    assert all(e["accountAccess"]["state"] == "unknown" for e in runtime.market_route_catalog_v3()["entries"]
               if e["target"]["providerId"] == "tickflow")
    with pytest.raises(ControlContractError) as caught:
        configure_provider(runtime.store, "tickflow", envelope(credentials=first, expectedConfigVersion=0))
    assert caught.value.code == "PROVIDER_CONFIG_VERSION_CONFLICT"
    assert runtime.store.provider_credential_snapshot("tickflow").values["apiKey"] == "controlled-second"


def test_checked_invalid_key_is_not_saved(runtime, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    from src.services.thesis_ledger_control import ControlContractError
    from tickflow._exceptions import AuthenticationError
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    calls, _ = install_client(monkeypatch, AuthenticationError("invalid", code="INVALID", status_code=401))
    credentials = {"method": "api_key", "values": {"apiKey": "controlled-invalid"}}
    check_provider(runtime, "tickflow", envelope(purpose="account_access", credentials=credentials))
    with pytest.raises(ControlContractError) as caught:
        configure_provider(runtime.store, "tickflow", envelope(credentials=credentials))
    assert caught.value.code == "PROVIDER_CREDENTIAL_INVALID"
    assert runtime.store.provider_credential_snapshot("tickflow").values == {} and len(calls) == 14


@pytest.mark.parametrize("kind,state,code", [
    ("AuthenticationError", "credential_invalid", "authentication_failed"),
    ("PermissionError", "permission_missing", "permission_denied"),
    ("RateLimitError", "unknown", "rate_limited"),
    ("ConnectionError", "unknown", "network_failure"),
    ("TimeoutError", "unknown", "network_failure"),
])
def test_error_classification(kind, state, code):
    from tickflow import _exceptions
    cls = getattr(_exceptions, kind)
    error = cls("controlled", code="CONTROLLED", status_code=403) if kind not in {"ConnectionError", "TimeoutError"} else cls("controlled")
    assert failure_result(error) == {"state": state, "attempted": True, "errorCode": code}
