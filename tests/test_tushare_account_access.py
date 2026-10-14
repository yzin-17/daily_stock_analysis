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
        ("CN", "STOCK", "CHIP_SUMMARY"): "unknown", ("CN", "ETF", "CASH_DISTRIBUTION"): "permission_missing"}
    assert result["capabilityResults"] == {} and "routeResults" not in result
    assert result["status"] == "degraded" and db_state(runtime) == before
    assert "synthetic-current-token" not in json.dumps(result)
    catalog = runtime.market_route_catalog_v3()
    entries = [e for e in catalog["entries"] if e["target"]["providerId"] == "tushare"]
    assert len(entries) == 6 and entries[0]["accountAccess"]["state"] == "allowed"
    assert next(e for e in entries if e["key"]["capability"] == "CASH_DISTRIBUTION")["state"] != "ready"


def test_draft_is_not_published_and_current_cache_expires_or_rotates(runtime, monkeypatch):
    clock = [0.0]
    runtime.clock = lambda: clock[0]
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **k: response())
    before = db_state(runtime)
    check_provider(runtime, "tushare", envelope(purpose="account_access"))
    from src.services.thesis_ledger_account_access import _CACHES
    cached = _CACHES[runtime]["reports"]["tushare"]
    assert isinstance(cached[0], bytes) and len(cached[0]) == 32  # 缓存不保留凭证快照副本。
    draft = check_provider(runtime, "tushare", envelope(purpose="account_access", credentials={"method": "token",
        "values": {"token": "synthetic-draft-token"}}))
    assert draft["accountAccess"]["credentialContext"] == "draft" and db_state(runtime) == before
    clock[0] = 301
    entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare"]
    assert all(e["accountAccess"]["state"] == "unknown" for e in entries)
    assert all(e["accountAccess"]["errorCode"] == "permission_check_expired" for e in entries)
    assert _CACHES[runtime]["reports"]["tushare"][2] is None
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
    assert all(r["access"]["errorCode"] == "request_budget_exceeded" for r in result["accountAccess"]["routes"])
    assert result["status"] != "healthy"


def test_fixture_no_credentials_and_unsupported_source_never_request(runtime, monkeypatch):
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **k: pytest.fail("unexpected upstream"))
    result = check_provider(runtime, "tushare", envelope(purpose="account_access"), fixture=True)
    assert all(not r["access"]["attempted"] for r in result["accountAccess"]["routes"])
    runtime.store.save_provider_config("tushare", envelope(clearCredentials=True))
    result = check_provider(runtime, "tushare", envelope(purpose="account_access"))
    assert result["status"] == "unconfigured" and set(states(result).values()) == {"credential_missing"}
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
    assert result["purpose"] == "account_access" and set(states(result).values()) == {"permission_missing"}
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
