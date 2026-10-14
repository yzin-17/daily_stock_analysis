"""全部来源库存与精确检查的真实 SQLite/HTTP 接缝及拒绝路径。"""

from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient as HttpClient

from api.thesis_ledger import router_v3
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore
from src.services.thesis_ledger_provider_manifests import PROVIDER_MANIFESTS
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, ProviderCallError
from src.services.thesis_ledger_provider_exact_checks import check_provider
from src.services.thesis_ledger_exact_probe_read import read_exact_probe


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "checks.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-master-key")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "synthetic-token")
    monkeypatch.setenv("HITHINK_API_KEY", "synthetic-environment-key")
    store = ThesisLedgerControlStore()
    return ThesisLedgerProviderRuntime(store)


def request(**values):
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "precise-check", **values}


def state(runtime):
    with runtime.store._connect() as connection:
        return {table: [tuple(row) for row in connection.execute("SELECT * FROM " + table)] for table in
                ["thesis_ledger_provider_config", "thesis_ledger_provider_health", "thesis_ledger_policy_state", "thesis_ledger_route_admission_v3"]}


@pytest.mark.parametrize("provider_id", list(PROVIDER_MANIFESTS))
def test_every_registered_source_has_exact_checks_without_empty_success(runtime, provider_id):
    before = state(runtime)
    expected = [e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == provider_id]
    result = check_provider(runtime, provider_id, request(), fixture=True)
    assert len(result["routeResults"]) == len(expected) > 0
    assert result["capabilityResults"]
    assert result["status"] != "healthy"
    assert all(not row["result"]["attempted"] for row in result["routeResults"])
    assert state(runtime) == before


def test_selected_precise_route_is_only_call_and_others_remain_untested(runtime, monkeypatch):
    entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "akshare"]
    selected = next(e for e in entries if e["key"].get("assetType") == "ETF" and e["key"].get("adjustment") == "qfq")
    calls = []
    def read(_runtime, key, target, snapshot):
        calls.append((deepcopy(key), deepcopy(target)))
        return {"symbol": "510300.SH", "start": "2026-09-28", "end": "2026-09-30"}
    monkeypatch.setattr("src.services.thesis_ledger_provider_exact_checks.read_exact_probe", read)
    before = state(runtime)
    result = check_provider(runtime, "akshare", request(route={k: selected[k] for k in ("key", "target")}))
    assert calls == [(selected["key"], selected["target"])]
    assert result["status"] == "healthy"
    checked = [e for e in result["routeResults"] if e["result"]["attempted"]]
    assert len(checked) == 1 and checked[0]["key"] == selected["key"]
    assert all(e["result"].get("errorCode") == "not_tested" for e in result["routeResults"] if not e["result"]["attempted"])
    assert state(runtime) == before


def test_wrong_target_and_price_basis_rejected_before_read(runtime, monkeypatch):
    monkeypatch.setattr("src.services.thesis_ledger_provider_exact_checks.read_exact_probe", lambda *args: pytest.fail("unexpected read"))
    route = {"key": {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "hfq"}, "target": {"providerId": "hithink", "upstreamSource": "fund-market-historical"}}
    with pytest.raises(ControlContractError):
        check_provider(runtime, "hithink", request(route=route))
    route["target"] = {"providerId": "tencent", "upstreamSource": "tencent"}
    with pytest.raises(ControlContractError):
        check_provider(runtime, "hithink", request(route=route))


def test_credential_rotation_during_read_rejects_late_success(runtime, monkeypatch):
    selected = next(e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "hithink" and e["key"]["capability"] == "REALTIME_QUOTE")
    def rotate(*args):
        monkeypatch.setenv("HITHINK_API_KEY", "synthetic-rotated-key")
        return {"symbol": "600519.SH", "start": "2026-09-28", "end": "2026-09-30"}
    monkeypatch.setattr("src.services.thesis_ledger_provider_exact_checks.read_exact_probe", rotate)
    result = check_provider(runtime, "hithink", request(route={k: selected[k] for k in ("key", "target")}))
    assert result["status"] == "degraded"
    assert result["capabilityResults"]["REALTIME_QUOTE"]["errorCode"] == "PROVIDER_CREDENTIAL_CHANGED"


@pytest.mark.parametrize("provider_id", ["official-rules", "official-states", "official-dividends"])
def test_official_sources_validate_actual_local_packages(runtime, provider_id):
    runtime.store.apply_policy_v3(request(revision=1, enabled=True, routes=[]))
    before = state(runtime)
    for entry in runtime.market_route_catalog_v3()["entries"]:
        if entry["target"]["providerId"] != provider_id:
            continue
        result = check_provider(runtime, provider_id, request(route={k: entry[k] for k in ("key", "target")}))
        assert result["status"] == "healthy", result
    assert state(runtime) == before


def test_unadmitted_event_does_not_call_upstream_or_grant_admission(runtime, monkeypatch):
    monkeypatch.setattr("data_provider.eastmoney_fund_dividend_reader.fetch_fund_dividend_observations", lambda *a, **k: pytest.fail("unadmitted upstream"))
    entry = next(e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "akshare" and e["key"]["capability"] == "CASH_DISTRIBUTION")
    before = state(runtime)
    result = check_provider(runtime, "akshare", request(route={k: entry[k] for k in ("key", "target")}))
    assert result["capabilityResults"]["CASH_DISTRIBUTION"] == {"status": "unavailable", "readOnly": True, "attempted": False, "errorCode": "not_admitted"}
    assert state(runtime) == before


def test_actual_http_reports_rqdata_exact_event_capabilities_without_credentials(runtime):
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    with HttpClient(app) as client:
        response = client.post("/api/v3/thesis-ledger/control/providers/rqdata/test", headers={"authorization": "Bearer synthetic-token"}, json=request())
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "unconfigured"
    assert set(result["capabilityResults"]) == {"CASH_DISTRIBUTION", "SPLIT_EVENT"}
    assert len(result["routeResults"]) == 2


def test_daily_probe_uses_exact_source_and_rejects_source_mismatch(runtime, monkeypatch):
    from src.services.thesis_ledger_exact_probe_read import probe_sample
    key = {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"}
    target = {"providerId": "tencent", "upstreamSource": "tencent"}
    _, start, end = probe_sample(key)
    calls = []
    frame = pd.DataFrame([{ "date": start, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1 }])
    frame.attrs["upstream_source"] = "tencent"
    from hashlib import sha256
    import json
    parts = [{'start': start, 'end': end, 'rows': 1, 'sha256': sha256(frame.to_json().encode()).hexdigest()}]
    frame.attrs['tencentDailyRetrieval'] = {'endpoint': 'newfqkline/get', 'adjustment': 'qfq',
        'partitionComplete': True, 'partitions': parts,
        'revision': sha256(json.dumps(parts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    def precise(*args, **kwargs):
        calls.append((args, kwargs))
        return frame
    adapter = SimpleNamespace(get_daily_data_for_source=precise, get_daily_data=lambda *a, **k: pytest.fail("generic fallback"))
    monkeypatch.setattr(runtime, "_adapter", lambda *a, **k: adapter)
    read_exact_probe(runtime, key, target, runtime.store.provider_credential_snapshot("tencent"))
    assert calls[0][1]["asset_type"] == "ETF" and calls[0][1]["adjustment"] == "qfq"
    frame.attrs["upstream_source"] = "sina"
    with pytest.raises(ProviderCallError, match="实际日线来源"):
        read_exact_probe(runtime, key, target, runtime.store.provider_credential_snapshot("tencent"))


def test_hithink_snapshot_probe_reuses_production_mapping_normalization(runtime, monkeypatch):
    class Snapshot:
        def __init__(self, **kwargs):
            pass
        def fetch_quote(self, symbol, asset):
            assert symbol == "600519.SH" and asset == "STOCK"
            return {"price": 10, "open_price": 9, "high": 11, "low": 8, "pre_close": 9, "volume": None, "amount": None}
    monkeypatch.setattr("src.services.thesis_ledger_hithink_quote.HiThinkSnapshotAdapter", Snapshot)
    key = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "REALTIME_QUOTE"}
    target = {"providerId": "hithink", "upstreamSource": "a-share-prices-snapshot"}
    assert read_exact_probe(runtime, key, target, runtime.store.provider_credential_snapshot("hithink")) == {"symbol": "600519.SH"}
