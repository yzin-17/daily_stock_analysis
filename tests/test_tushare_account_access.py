"""账户权限的离线协议/控制接缝验证；不代表供应商真实成功。"""

import json
from types import SimpleNamespace

import pytest
import requests

from data_provider.tushare_account_access import failure_result
from data_provider.tushare_http_client import TushareApiError
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_provider_exact_checks import check_provider


def envelope(**extra):
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "account-check", **extra}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "account.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-master-key")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://synthetic.example/api")
    monkeypatch.setattr("src.services.thesis_ledger_control._environment_credential_values", lambda _: {})
    runtime = ThesisLedgerProviderRuntime(ThesisLedgerControlStore())
    runtime.store.save_provider_config("tushare", envelope(credentials={"method": "token", "values": {
        "token": "synthetic-current-token"}}))
    return runtime


def db_state(runtime):
    with runtime.store._connect() as connection:
        return {table: [tuple(row) for row in connection.execute("SELECT * FROM " + table)] for table in
                ["thesis_ledger_provider_config", "thesis_ledger_provider_health", "thesis_ledger_policy_state", "thesis_ledger_route_admission_v3"]}


def response(code=0, msg=None):
    return SimpleNamespace(status_code=200, text=json.dumps({"code": code, "msg": msg,
        "data": {"fields": ["ts_code"], "items": []}}))


def states(result):
    return {(r["key"]["market"], r["key"]["assetType"], r["key"]["capability"]): r["access"]["state"]
            for r in result["accountAccess"]["routes"]}


def test_mixed_rights_use_six_exact_endpoints_without_admission_or_writes(runtime, monkeypatch):
    calls = []
    def post(url, **options):
        calls.append(options)
        assert url == "https://synthetic.example/api"
        assert options["allow_redirects"] is False and 0 < options["timeout"] <= 3
        name = options["json"]["api_name"]
        if name in {"fund_daily", "fund_div"}:
            return response(40203, "您没有访问该接口的权限")
        if name == "hk_daily":
            return response(2002, "权限不足")
        if name == "rt_k":
            raise requests.Timeout()
        if name == "cyq_chips":
            return SimpleNamespace(status_code=200, text="bad-json")
        return response()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    before = db_state(runtime)
    result = check_provider(runtime, "tushare", envelope(purpose="account_access"))
    assert len(calls) == 6
    assert states(result) == {("CN", "STOCK", "DAILY_BAR"): "allowed", ("HK", "STOCK", "DAILY_BAR"): "permission_missing",
        ("CN", "ETF", "DAILY_BAR"): "permission_missing", ("CN", "STOCK", "REALTIME_QUOTE"): "unknown",
        ("CN", "STOCK", "CHIP_SUMMARY"): "unknown", ("CN", "ETF", "CASH_DISTRIBUTION"): "permission_missing", ("CN", "STOCK", "MINUTE_BAR"): "not_included",
        ("CN", "ETF", "MINUTE_BAR"): "not_included"}
    assert result["capabilityResults"] == {} and "routeResults" not in result
    assert result["status"] == "degraded" and db_state(runtime) == before
    assert "synthetic-current-token" not in json.dumps(result)
    catalog = runtime.market_route_catalog_v3()
    entries = [e for e in catalog["entries"] if e["target"]["providerId"] == "tushare"]
    assert len(entries) == 16 and entries[0]["accountAccess"]["state"] == "allowed"
    assert next(e for e in entries if e["key"]["capability"] == "CASH_DISTRIBUTION")["state"] != "ready"


def test_draft_is_not_published_and_current_record_survives_time_or_rotates(runtime, monkeypatch):
    clock = [0.0]
    runtime.clock = lambda: clock[0]
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **k: response())
    before = db_state(runtime)
    current = check_provider(runtime, "tushare", envelope(purpose="account_access"))
    from src.services.thesis_ledger_account_access import _CACHES
    cached = _CACHES[runtime]["reports"]["tushare"]
    assert isinstance(cached[0], bytes) and len(cached[0]) == 32  # 缓存不保留凭证快照副本。
    draft = check_provider(runtime, "tushare", envelope(purpose="account_access", credentials={"method": "token",
        "values": {"token": "synthetic-draft-token"}}))
    assert draft["accountAccess"]["credentialContext"] == "draft" and db_state(runtime) == before
    clock[0] = 301
    entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare"]
    assert [e["accountAccess"]["state"] for e in entries] == [r["access"]["state"] for r in current["accountAccess"]["routes"]]
    assert all(e["accountAccess"]["persisted"] and "validUntil" not in e["accountAccess"] for e in entries)
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://changed.example/api")
    entry = next(e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare")
    assert entry["accountAccess"]["errorCode"] == "permission_not_checked"


def test_changed_credentials_reject_late_report_without_cache(runtime, monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        runtime.store.save_provider_config("tushare", envelope(credentials={"method": "token", "values": {"token": "synthetic-rotated"}}))
        return response()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    with pytest.raises(ControlContractError) as error:
        check_provider(runtime, "tushare", envelope(purpose="account_access"))
    assert error.value.code == "PROVIDER_CREDENTIAL_CHANGED" and len(calls) == 1
    entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare"]
    assert all(e["accountAccess"]["state"] == "unknown" for e in entries)


def test_budget_stops_remaining_requests_and_rejects_late_success(runtime, monkeypatch):
    clock, calls = [0.0], []
    runtime.clock = lambda: clock[0]
    def post(*args, **kwargs):
        calls.append(True)
        clock[0] = 19
        return response()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    result = check_provider(runtime, "tushare", envelope(purpose="account_access"))
    assert len(calls) == 1
    assert all(r["access"]["errorCode"] == "request_budget_exceeded" for r in result["accountAccess"]["routes"]
               if r["key"]["capability"] != "MINUTE_BAR")
    assert all(r["access"]["state"] == "not_included" for r in result["accountAccess"]["routes"]
               if r["key"]["capability"] == "MINUTE_BAR")
    assert result["status"] != "healthy"


def test_fixture_no_credentials_and_unsupported_source_never_request(runtime, monkeypatch):
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **k: pytest.fail("unexpected upstream"))
    result = check_provider(runtime, "tushare", envelope(purpose="account_access"), fixture=True)
    assert all(not r["access"]["attempted"] for r in result["accountAccess"]["routes"])
    runtime.store.save_provider_config("tushare", envelope(clearCredentials=True))
    result = check_provider(runtime, "tushare", envelope(purpose="account_access"))
    assert result["status"] == "unconfigured" and set(states(result).values()) == {"credential_missing", "not_included"}
    with pytest.raises(ControlContractError):
        check_provider(runtime, "tencent", envelope(purpose="account_access"))


def test_control_http_keeps_account_report_separate_with_offline_upstream(runtime, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger import router_v3
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "synthetic-control-token")
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **k: response(2002, "权限不足"))
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    before = db_state(runtime)
    with TestClient(app) as client:
        actual = client.post("/api/v3/thesis-ledger/control/providers/tushare/test",
            headers={"authorization": "Bearer synthetic-control-token"}, json=envelope(purpose="account_access"))
    assert actual.status_code == 200
    result = actual.json()
    assert result["purpose"] == "account_access" and set(states(result).values()) == {"permission_missing", "not_included"}
    assert result["capabilityResults"] == {} and db_state(runtime) == before


@pytest.mark.parametrize("error,state,code", [
    (TushareApiError("权限不足", api_code=2002), "permission_missing", "permission_denied"),
    (TushareApiError("您没有接口权限", api_code=40203), "permission_missing", "permission_denied"),
    (TushareApiError("未知错误", api_code=40203), "unknown", "upstream_failure"),
    (TushareApiError("每分钟访问次数已满，权限说明", api_code=40203), "unknown", "rate_limited"),
    (TushareApiError("token不对", api_code=2002), "credential_invalid", "authentication_failed"),
    (TushareApiError("HTTP 403", status_code=403), "unknown", "upstream_failure"),
    (requests.ConnectionError(), "unknown", "network_failure"),
    (ValueError(), "unknown", "invalid_response"),
])
def test_only_explicit_permission_denials_become_missing(error, state, code):
    assert failure_result(error) == {"state": state, "attempted": True, "errorCode": code}


def test_basic_account_skips_unincluded_interfaces(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
                        lambda *a, **kw: calls.append(kw["json"]["api_name"]) or response())
    result = check_provider(runtime, "tushare", envelope(purpose="account_access",
        accountLevel={"points": 120, "independentApis": []}))
    assert calls == ["daily"] and result["status"] == "healthy"
    assert list(states(result).values()).count("not_included") == 7
    assert all(not r["access"]["attempted"] for r in result["accountAccess"]["routes"]
               if r["access"]["state"] == "not_included")


def test_save_reuses_draft_and_persists_across_runtime_restart(runtime, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    calls = []
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
                        lambda *a, **kw: calls.append(kw["json"]["api_name"]) or response())
    level = {"points": 120, "independentApis": []}
    check_provider(runtime, "tushare", envelope(purpose="account_access", accountLevel=level))
    saved = configure_provider(runtime.store, "tushare", envelope(settings={"accountLevel": level}))
    assert calls == ["daily"] and saved["settings"]["accountLevel"] == level
    restarted = ThesisLedgerProviderRuntime(ThesisLedgerControlStore(runtime.store.database_path))
    entries = [e for e in restarted.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare"]
    assert len(entries) == 16 and sum(e["accountAccess"]["state"] == "allowed" for e in entries) == 1
    assert all(e["accountAccess"]["persisted"] for e in entries) and calls == ["daily"]
    with runtime.store._connect() as connection:
        rows = connection.execute("SELECT report_json FROM thesis_ledger_account_access").fetchall()
    assert "synthetic-current-token" not in str([tuple(row) for row in rows])
    calls.clear()
    checked = check_provider(runtime, "tushare", envelope(route={"key": entries[-1]["key"], "target": entries[-1]["target"]}))
    assert checked["routeResults"][-1]["result"]["attempted"] is False and calls == []
    runtime.store.save_provider_config("tushare", envelope(enabled=True))
    enabled_entries = [e for e in restarted.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare"]
    assert sum(e["accountAccess"]["state"] == "allowed" for e in enabled_entries) == 1 and calls == []
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://changed.example/api")
    entries = [e for e in restarted.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare"]
    assert all(e["accountAccess"]["state"] == "unknown" for e in entries)


def test_level_changes_keep_other_settings_and_clear_resets_level(runtime, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **kw: response())
    runtime.store.save_provider_config("tushare", envelope(settings={"existingSetting": "keep"}))
    saved = configure_provider(runtime.store, "tushare", envelope(settings={"accountLevel": {"points": 120, "independentApis": []}}))
    assert saved["settings"]["existingSetting"] == "keep"
    registry = next(p for p in runtime.store.provider_registry() if p["providerId"] == "tushare")
    assert set(registry["settings"]) == {"accountLevel"}
    removed = configure_provider(runtime.store, "tushare", envelope(clearCredentials=True))
    assert removed["settings"] == {"existingSetting": "keep"}


def test_permissions_write_failure_rolls_back_configuration(runtime, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **kw: response())
    before = db_state(runtime)
    def reject(*args):
        raise RuntimeError("synthetic-storage-failure")
    monkeypatch.setattr("src.services.thesis_ledger_account_access_configuration.write_record", reject)
    with pytest.raises(RuntimeError, match="synthetic-storage-failure"):
        configure_provider(runtime.store, "tushare", envelope(settings={
            "accountLevel": {"points": 120, "independentApis": []}}))
    assert db_state(runtime) == before


@pytest.mark.parametrize("level", [{"points": True}, {"points": -1},
    {"points": 120, "independentApis": ["fund_div"]}])
def test_bad_levels_never_request_or_save(runtime, monkeypatch, level):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **kw: pytest.fail("unexpected upstream"))
    before = db_state(runtime)
    with pytest.raises(ControlContractError):
        configure_provider(runtime.store, "tushare", envelope(settings={"accountLevel": level}))
    assert db_state(runtime) == before


def test_budget_failure_does_not_replace_level_exclusions(runtime, monkeypatch):
    clock, calls = [0.0], []
    runtime.clock = lambda: clock[0]
    def post(*args, **kwargs):
        calls.append(kwargs["json"]["api_name"])
        clock[0] = 19
        return response()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    result = check_provider(runtime, "tushare", envelope(purpose="account_access",
        accountLevel={"points": 120, "independentApis": []}))
    assert calls == ["daily"] and list(states(result).values()).count("not_included") == 7
    assert all(not r["access"]["attempted"] for r in result["accountAccess"]["routes"]
               if r["access"]["state"] == "not_included")


@pytest.mark.parametrize("broken", ["not_object", "missing_routes", "missing_key", "not_persisted"])
def test_malformed_local_record_is_not_published(runtime, monkeypatch, broken):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **kw: response())
    configure_provider(runtime.store, "tushare", envelope(settings={
        "accountLevel": {"points": 120, "independentApis": []}}))
    with runtime.store._connect() as connection:
        raw = json.loads(connection.execute(
            "SELECT report_json FROM thesis_ledger_account_access WHERE provider_id='tushare'").fetchone()[0])
        if broken == "not_object":
            raw = []
        elif broken == "missing_routes":
            raw.pop("routes")
        elif broken == "missing_key":
            raw["routes"][0].pop("key")
        else:
            raw["routes"][0]["access"]["persisted"] = False
        connection.execute("UPDATE thesis_ledger_account_access SET report_json=? WHERE provider_id='tushare'",
                           (json.dumps(raw),))
        connection.commit()
    restarted = ThesisLedgerProviderRuntime(ThesisLedgerControlStore(runtime.store.database_path))
    entries = [e for e in restarted.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare"]
    assert all(e["accountAccess"]["state"] == "unknown" for e in entries)
