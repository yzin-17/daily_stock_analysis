"""限频暂停及接续的受控接口验证，不访问真实账号。"""

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore
from src.services.thesis_ledger_provider_exact_checks import check_provider
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


def envelope(**extra):
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "progress-test", **extra}


def response(limited=False):
    return SimpleNamespace(status_code=200, text=json.dumps({"code": 40203 if limited else 0,
        "msg": "每分钟访问次数已满" if limited else None, "data": {"fields": ["ts_code"], "items": []}}))


@pytest.fixture
def case(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "progress.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-progress-master")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://synthetic.example/api")
    monkeypatch.setattr("src.services.thesis_ledger_control._environment_credential_values", lambda _: {})
    # 本组原有测试验证无分组声明时的通用来源契约，专属分组另测。
    source_lookup = type(CAPABILITY_SOURCES).source
    def generic_source(registry, provider_id):
        source = source_lookup(registry, provider_id)
        return replace(source, account_access_group=None, account_access_cooldown_seconds=60) if provider_id == "tushare" else source
    monkeypatch.setattr(type(CAPABILITY_SOURCES), "source", generic_source)
    runtime = ThesisLedgerProviderRuntime(ThesisLedgerControlStore())
    level = {"points": 120, "independentApis": ["hk_daily", "rt_k", "stk_mins", "etf_mins"]}
    runtime.store.save_provider_config("tushare", envelope(credentials={"method": "token", "values": {
        "token": "synthetic-token"}}, settings={"accountLevel": level}))
    now, calls = [datetime(2026, 1, 1, tzinfo=timezone.utc)], []
    monkeypatch.setattr("src.services.thesis_ledger_account_access_progress.utc_now", lambda: now[0])
    def post(*args, **options):
        api = options["json"]["api_name"]
        calls.append(api)
        return response(api != "daily")
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    return SimpleNamespace(runtime=runtime, now=now, calls=calls, level=level, source_lookup=source_lookup)


def check(runtime, **values):
    return check_provider(runtime, "tushare", envelope(purpose="account_access", **values))["accountAccess"]


def test_first_limit_stops_remaining_and_repeat_does_not_extend_cooldown(case):
    report = check(case.runtime)
    assert case.calls == ["daily", "hk_daily"]
    assert sum(r["access"]["state"] == "allowed" for r in report["routes"]) == 1
    assert sum(r["access"]["state"] == "not_included" for r in report["routes"]) == 3
    limited = [r for r in report["routes"] if r["access"].get("errorCode") == "rate_limited"]
    assert len(limited) == 12 and sum(r["access"]["attempted"] for r in limited) == 1
    case.now[0] += timedelta(seconds=59)
    assert check(case.runtime) == report
    assert case.calls == ["daily", "hk_daily"]


def test_cooldown_survives_restart_and_unrelated_enable(case):
    before = check(case.runtime)
    case.runtime.store.save_provider_config("tushare", envelope(enabled=True))
    restarted = ThesisLedgerProviderRuntime(ThesisLedgerControlStore(case.runtime.store.database_path))
    report = check(restarted)
    assert case.calls == ["daily", "hk_daily"]
    assert report["checkedAt"] == before["checkedAt"]
    assert report["configVersion"] == before["configVersion"] + 1


def test_resume_only_unknown_and_preserves_original_check_time(case, monkeypatch):
    report = check(case.runtime)
    first = report["routes"][0]["access"]
    case.calls.clear()
    case.now[0] += timedelta(seconds=60)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
        lambda *a, **kw: case.calls.append(kw["json"]["api_name"]) or response())
    resumed = check(case.runtime)
    assert "daily" not in case.calls and len(case.calls) == 6
    assert sum(r["access"]["state"] == "allowed" for r in resumed["routes"]) == 7
    assert resumed["routes"][0]["access"] == first
    case.calls.clear()
    complete = check(case.runtime)
    assert len(case.calls) == 6 and "daily" not in case.calls and "hk_daily" not in case.calls
    assert sum(r["access"]["state"] == "allowed" for r in complete["routes"]) == 13
    case.calls.clear()
    check(case.runtime)
    assert len(case.calls) == 6 and "daily" in case.calls  # 完整记录仍可手动全部更新，保留原请求上限。


@pytest.mark.parametrize("change", ["token", "endpoint", "level"])
def test_changed_identity_does_not_adopt_previous_results(case, monkeypatch, change):
    check(case.runtime)
    case.calls.clear()
    if change == "token":
        case.runtime.store.save_provider_config("tushare", envelope(credentials={"method": "token", "values": {"token": "synthetic-other"}}))
    elif change == "endpoint":
        monkeypatch.setenv("TUSHARE_HTTP_URL", "https://changed.example/api")
    else:
        case.runtime.store.save_provider_config("tushare", envelope(settings={"accountLevel": {**case.level, "points": 121}}))
    check(case.runtime)
    assert case.calls == ["daily", "hk_daily"]


def test_draft_cooldown_is_isolated_and_save_adopts_it(case, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: case.runtime)
    credentials = {"method": "token", "values": {"token": "synthetic-draft"}}
    report = check(case.runtime, credentials=credentials)
    assert check(case.runtime, credentials=credentials) == report
    assert case.calls == ["daily", "hk_daily"]
    assert all(e["accountAccess"]["state"] == "unknown" for e in case.runtime.market_route_catalog_v3()["entries"]
               if e["target"]["providerId"] == "tushare")
    configure_provider(case.runtime.store, "tushare", envelope(credentials=credentials))
    assert check(case.runtime)["persisted"] is True
    assert case.calls == ["daily", "hk_daily"]


def test_concurrent_check_is_rejected_and_slot_released(case, monkeypatch):
    rejected = []
    def post(*args, **options):
        with pytest.raises(ControlContractError) as caught:
            check(case.runtime)
        rejected.append(caught.value.code)
        return response()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    check(case.runtime)
    assert rejected == ["PROVIDER_CHECK_IN_PROGRESS"] * 6
    rejected.clear()
    check(case.runtime)
    assert len(rejected) == 6


def test_budget_resume_skips_successes_without_charging_retained_requests(case, monkeypatch):
    clock = [0.0]
    case.runtime.clock = lambda: clock[0]
    def post(*args, **options):
        case.calls.append(options["json"]["api_name"])
        if len(case.calls) == 2:
            clock[0] = 19
        return response()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    first = check(case.runtime)
    assert case.calls == ["daily", "hk_daily"]
    assert first["routes"][0]["access"]["state"] == "allowed"
    case.calls.clear()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
        lambda *a, **kw: case.calls.append(kw["json"]["api_name"]) or response())
    resumed = check(case.runtime)
    assert len(case.calls) == 6 and "daily" not in case.calls
    assert sum(r["access"]["state"] == "allowed" for r in resumed["routes"]) == 7
    case.calls.clear()
    complete = check(case.runtime)
    assert len(case.calls) == 6 and "daily" not in case.calls and "hk_daily" not in case.calls
    assert sum(r["access"]["state"] == "allowed" for r in complete["routes"]) == 13


def test_partial_resume_preserves_explicit_denial_as_well_as_success(case, monkeypatch):
    def post(*args, **options):
        api = options["json"]["api_name"]
        case.calls.append(api)
        if api == "hk_daily":
            return SimpleNamespace(status_code=200, text=json.dumps({"code": 2002, "msg": "权限不足", "data": None}))
        return response(api not in {"daily", "hk_daily"})
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    before = check(case.runtime)
    assert case.calls == ["daily", "hk_daily", "stk_mins"]
    denied = next(r for r in before["routes"] if r["access"]["state"] == "permission_missing")
    case.calls.clear()
    case.now[0] += timedelta(seconds=61)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
        lambda *a, **kw: case.calls.append(kw["json"]["api_name"]) or response())
    after = check(case.runtime)
    assert "daily" not in case.calls and "hk_daily" not in case.calls
    assert next(r for r in after["routes"] if r["access"]["state"] == "permission_missing") == denied


def test_failed_configuration_check_releases_execution_slot(case, monkeypatch):
    def post(*args, **options):
        case.runtime.store.save_provider_config("tushare", envelope(credentials={"method": "token", "values": {"token": "rotated-during-check"}}))
        return response()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    with pytest.raises(ControlContractError) as caught:
        check(case.runtime)
    assert caught.value.code == "PROVIDER_CREDENTIAL_CHANGED"
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", lambda *a, **kw: response(True))
    assert check(case.runtime)["routes"][0]["access"]["errorCode"] == "rate_limited"


def test_save_same_current_input_reuses_cooldown_without_new_probes(case, monkeypatch):
    from src.services.thesis_ledger_account_access_configuration import configure_provider
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: case.runtime)
    first = check(case.runtime)
    saved = configure_provider(case.runtime.store, "tushare", envelope(settings={"accountLevel": case.level}))
    assert saved["configVersion"] == first["configVersion"] + 1
    assert case.calls == ["daily", "hk_daily"]
    current = check(case.runtime)
    assert current["persisted"] is True and current["checkedAt"] == first["checkedAt"]
    assert case.calls == ["daily", "hk_daily"]


def test_tushare_groups_continue_other_apis_without_repeating_minute_frequencies(case, monkeypatch):
    monkeypatch.setattr(type(CAPABILITY_SOURCES), "source", case.source_lookup)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
        lambda *a, **kw: case.calls.append(kw["json"]["api_name"]) or response(kw["json"]["api_name"] == "hk_daily"))
    first = check(case.runtime)
    assert case.calls == ["daily", "hk_daily", "stk_mins", "etf_mins", "rt_k"]
    assert sum(r["access"]["state"] == "allowed" for r in first["routes"]) == 12
    assert sum(r["access"]["state"] == "unknown" for r in first["routes"]) == 1
    times = [r["access"]["checkedAt"] for r in first["routes"]]
    case.calls.clear()
    restarted = ThesisLedgerProviderRuntime(ThesisLedgerControlStore(case.runtime.store.database_path))
    repeated = check(restarted)
    assert case.calls == [] and [r["access"]["checkedAt"] for r in repeated["routes"]] == times
    case.now[0] += timedelta(seconds=3600)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
        lambda *a, **kw: case.calls.append(kw["json"]["api_name"]) or response())
    complete = check(restarted)
    assert case.calls == ["hk_daily"]
    assert sum(r["access"]["state"] == "allowed" for r in complete["routes"]) == 13


def test_tushare_legacy_paused_rows_do_not_create_fake_cooldowns_for_other_apis(case, monkeypatch):
    first = check(case.runtime)
    assert case.calls == ["daily", "hk_daily"]
    monkeypatch.setattr(type(CAPABILITY_SOURCES), "source", case.source_lookup)
    case.calls.clear()
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
        lambda *a, **kw: case.calls.append(kw["json"]["api_name"]) or response())
    resumed = check(case.runtime)
    assert case.calls == ["stk_mins", "etf_mins", "rt_k"]
    assert sum(r["access"]["state"] == "allowed" for r in resumed["routes"]) == 12
    assert resumed["routes"][0]["access"] == first["routes"][0]["access"]


def test_tushare_global_http_limit_stops_all_groups(case, monkeypatch):
    monkeypatch.setattr(type(CAPABILITY_SOURCES), "source", case.source_lookup)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post",
        lambda *a, **kw: case.calls.append(kw["json"]["api_name"]) or SimpleNamespace(status_code=429))
    first = check(case.runtime)
    assert case.calls == ["daily"] and first["routes"][0]["access"]["errorCode"] == "rate_limited_global"
    check(case.runtime)
    assert case.calls == ["daily"]
