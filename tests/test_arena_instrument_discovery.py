"""AI 选定检索词后的证券身份读取与来源保护。"""

import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.thesis_ledger_arena_history import build_arena_history_router
from data_provider.hithink_instrument_search import search_instruments, InstrumentSearchError
from src.services import thesis_ledger_arena_instruments as service
from src.services.thesis_ledger_provider_runtime import ProviderCallError


def body():
    return {"code": 0, "data": {"timestamp": 1716105600000, "item": [
        {"thscode": "510300.SH", "exchange": "SH", "currency": "CNY", "asset_type": "fund-etf", "name": "当前基金名", "list_date": None},
    ]}}


def test_single_target_query_uses_fixed_domain_and_bounded_native_search():
    get = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: body()))
    result = search_instruments("synthetic", "510300", http_get=get)
    get.assert_called_once_with("https://fuyao.aicubes.cn/api/meta/tickers/search",
                                params={"q": "510300", "asset_type": "a-share,fund-etf", "limit": 20},
                                headers={"X-api-key": "synthetic"}, timeout=8, allow_redirects=False)
    assert result["items"] == [{"symbol": "510300.SH", "assetType": "ETF", "name": "510300.SH"}]
    assert result["raw"]["data"]["item"][0]["list_date"] is None


@pytest.mark.parametrize("change", ["duplicate", "wrong_type", "wrong_market", "too_many", "bad_clock"])
def test_ambiguous_or_invalid_identity_is_rejected(change):
    raw = body()
    if change == "duplicate":
        raw["data"]["item"] *= 2
    if change == "too_many":
        raw["data"]["item"] *= 21
    if change == "wrong_type":
        raw["data"]["item"][0]["asset_type"] = "fund-otc"
    if change == "wrong_market":
        raw["data"]["item"][0]["exchange"] = "SZ"
    if change == "bad_clock":
        raw["data"]["timestamp"] = True
    with pytest.raises(InstrumentSearchError):
        search_instruments("synthetic", "510300", http_get=lambda *_a, **_k: SimpleNamespace(status_code=200, json=lambda: raw))


def runtime(tmp_path):
    store = Mock(database_path=str(tmp_path / "control.db"))
    store.provider_registry.return_value = [{"providerId": "hithink", "enabled": True, "configVersion": 5}]
    store.effective_policy_v3.return_value = {"enabled": True}
    store.provider_credential_snapshot.return_value = SimpleNamespace(values={"apiKey": "synthetic"})
    return SimpleNamespace(store=store)


def request():
    return service.InstrumentDiscoveryRequest(contractVersion=1, consumer="thesis-ledger", requestId="search",
                                               market="CN", date="2026-11-02", query="510300")


@pytest.mark.parametrize("disabled", ["provider", "global", "credential"])
def test_disabled_or_credential_missing_never_calls_upstream(monkeypatch, tmp_path, disabled):
    rt = runtime(tmp_path)
    if disabled == "provider":
        rt.store.provider_registry.return_value[0]["enabled"] = False
    if disabled == "global":
        rt.store.effective_policy_v3.return_value = {"enabled": False}
    if disabled == "credential":
        rt.store.provider_credential_snapshot.return_value = SimpleNamespace(values={})
    read = Mock()
    monkeypatch.setattr(service, "search_instruments", read)
    with pytest.raises(ProviderCallError):
        service.read_instruments(rt, request())
    read.assert_not_called()


def test_http_auth_archive_and_changed_credentials(monkeypatch, tmp_path):
    rt = runtime(tmp_path)
    read = Mock(return_value=search_instruments("synthetic", "510300", http_get=lambda *_a, **_k: SimpleNamespace(status_code=200, json=lambda: body())))
    monkeypatch.setattr(service, "search_instruments", read)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-discovery-token")
    app = FastAPI()
    app.include_router(build_arena_history_router(lambda: rt))
    client = TestClient(app)
    payload = request().model_dump()
    assert client.post("/arena/history/instruments", json=payload).status_code == 401
    read.assert_not_called()
    response = client.post("/arena/history/instruments", json=payload, headers={"authorization": "Bearer synthetic-discovery-token"})
    assert response.status_code == 200
    result = response.json()
    assert result["historicalEligibility"] == "unverified" and result["items"][0]["assetType"] == "ETF"
    assert result["providerVersion"] == 5 and result["observedAt"] > result["date"]
    assert json.loads((tmp_path / result["archiveRef"]).read_text())["raw"] == body()
    first = rt.store.provider_credential_snapshot.return_value
    rt.store.provider_credential_snapshot.side_effect = [first, SimpleNamespace(values={"apiKey": "changed"})]
    with pytest.raises(ProviderCallError, match="配置已变化"):
        service.read_instruments(rt, request())


def test_failure_is_single_attempt_and_does_not_expose_upstream_message(monkeypatch, tmp_path):
    get = Mock(side_effect=RuntimeError("synthetic secret"))
    with pytest.raises(InstrumentSearchError) as error:
        search_instruments("synthetic", "510300", http_get=get)
    assert str(error.value) == "upstream_unavailable" and get.call_count == 1
    monkeypatch.setattr(service, "search_instruments", Mock(side_effect=InstrumentSearchError("rate_limited")))
    with pytest.raises(ProviderCallError) as failure:
        service.read_instruments(runtime(tmp_path), request())
    assert failure.value.code == "rate_limited"
