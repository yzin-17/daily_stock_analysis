"""HiThink 报价只在精确当前准入下执行，晚到结果不得回写。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS, ThesisLedgerControlStore,
    _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE, HITHINK_STOCK_SNAPSHOT_SOURCE, HiThinkQuoteError,
)
from src.services.thesis_ledger_hithink_quote_admission import (
    hithink_quote_revisions, hithink_quote_route_key,
)
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataGateway, ThesisLedgerDataRequest, ThesisLedgerGatewayError,
    ThesisLedgerProviderRuntime,
)


CASES = [
    ("STOCK", "600519.SH", HITHINK_STOCK_SNAPSHOT_SOURCE),
    ("ETF", "510300.SH", HITHINK_ETF_SNAPSHOT_SOURCE),
]


def test_hithink_quote_manifest_keeps_stock_and_etf_sources_separate():
    sources = {
        item["sourceId"]: item["capabilities"]
        for item in PROVIDER_MANIFESTS["hithink"]["upstreamSources"]
    }
    assert sources[HITHINK_STOCK_SNAPSHOT_SOURCE] == {"REALTIME_QUOTE": ["STOCK"]}
    assert sources[HITHINK_ETF_SNAPSHOT_SOURCE] == {"REALTIME_QUOTE": ["ETF"]}


def _policy(asset_type, source, revision=1):
    return {
        "contractVersion": 3, "consumer": "thesis-ledger",
        "requestId": f"quote-revision-{revision}", "revision": revision,
        "enabled": True,
        "routes": [{"key": hithink_quote_route_key(asset_type), "targets": [
            {"providerId": "hithink", "upstreamSource": source},
        ]}],
    }


def _admit(store, asset_type, symbol, source):
    now = datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    credential_revision = _provider_credential_revision_from_snapshot(
        store.provider_credential_snapshot("hithink")
    )
    revisions = hithink_quote_revisions(asset_type, source)
    return store.record_route_admission_v3(
        key=hithink_quote_route_key(asset_type),
        target={"providerId": "hithink", "upstreamSource": source},
        evidence_ref="fixture://quote-exact-source", evidence_sha256="a" * 64,
        scope_symbols=[symbol], scope_date_from=day, scope_date_to=day,
        adapter_revision=revisions["adapterRevision"],
        source_revision=revisions["sourceRevision"],
        credential_revision=credential_revision,
        valid_from=(now - timedelta(minutes=1)).isoformat(),
        valid_until=(now + timedelta(days=1)).isoformat(),
        recorded_by="pytest-fixture",
    )


@pytest.fixture(params=CASES)
def route(request, monkeypatch, tmp_path):
    asset_type, symbol, source = request.param
    monkeypatch.setenv("HITHINK_API_KEY", "synthetic-quote-key-v1")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-quote-master-key")
    store = ThesisLedgerControlStore(str(tmp_path / "quote.sqlite"))
    store.apply_policy_v3(_policy(asset_type, source))
    return store, asset_type, symbol, source


def _target_state(store, asset_type):
    return next(
        route for route in store.effective_policy_v3()["routes"]
        if route["key"] == hithink_quote_route_key(asset_type)
    )["targets"][0]


def _catalog_target_state(store, asset_type, source):
    catalog = ThesisLedgerProviderRuntime(store).market_route_catalog_v3()
    assert catalog["integrity"] == "complete"
    return next(
        entry["state"] for entry in catalog["entries"]
        if entry["key"] == hithink_quote_route_key(asset_type)
        and entry["target"] == {"providerId": "hithink", "upstreamSource": source}
    )


def test_exact_quote_catalog_requires_current_admission(route):
    store, asset_type, symbol, source = route
    assert _catalog_target_state(store, asset_type, source) == "not_admitted"
    _admit(store, asset_type, symbol, source)
    assert _catalog_target_state(store, asset_type, source) == "ready"
    store.revoke_route_admission_v3(
        key=hithink_quote_route_key(asset_type),
        target={"providerId": "hithink", "upstreamSource": source},
        reason="test",
    )
    assert _catalog_target_state(store, asset_type, source) == "not_admitted"


class _QuoteAdapter:
    def __init__(self, on_fetch=None):
        self.calls = []
        self.on_fetch = on_fetch

    def fetch_quote(self, symbol, asset_type):
        self.calls.append((symbol, asset_type))
        if self.on_fetch is not None:
            self.on_fetch()
        return {
            "price": 10, "open_price": 10, "high": 10, "low": 10,
            "pre_close": 10, "volume": 1, "amount": 10,
            "fetched_at": "2026-09-29T01:00:00+00:00",
            "provider_timestamp": None,
            "source": "hithink/" + (
                HITHINK_STOCK_SNAPSHOT_SOURCE if asset_type == "STOCK"
                else HITHINK_ETF_SNAPSHOT_SOURCE
            ),
            "units": (
                {"price_currency": "CNY", "volume": "share", "turnover_currency": "CNY"}
                if asset_type == "STOCK" else
                {"price_currency": "CNY", "volume": "unknown", "turnover_currency": "unknown"}
            ),
        }


def _request(asset_type, symbol):
    return ThesisLedgerDataRequest("REALTIME_QUOTE", symbol, instrument_type=asset_type)


def test_missing_then_exact_admission_and_out_of_scope(route):
    store, asset_type, symbol, source = route
    adapter = _QuoteAdapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"hithink": adapter})
    assert _target_state(store, asset_type)["reason"] == "not_admitted"
    with pytest.raises(ThesisLedgerGatewayError) as denied:
        runtime.execute_request(_request(asset_type, symbol))
    assert denied.value.code == "NO_ELIGIBLE_PROVIDER"
    assert adapter.calls == []

    _admit(store, asset_type, symbol, source)
    assert _target_state(store, asset_type)["eligible"] is True
    with pytest.raises(ThesisLedgerGatewayError) as outside:
        runtime.execute_request(_request(asset_type, "000001.SZ"))
    assert outside.value.code == "not_admitted"
    assert adapter.calls == []

    result = runtime.execute_request(_request(asset_type, symbol))
    assert adapter.calls == [(symbol, asset_type)]
    assert result.upstream_source == source
    assert result.value.source == "hithink/" + source
    assert result.value.provider_timestamp is None
    assert result.value.fetched_at == "2026-09-29T01:00:00+00:00"
    if asset_type == "ETF":
        with pytest.raises(ThesisLedgerGatewayError) as cooldown:
            runtime.execute_request(_request(asset_type, symbol))
        assert cooldown.value.code == "request_budget_cooldown"
        assert adapter.calls == [(symbol, asset_type)]


@pytest.mark.parametrize("mutation", ["revoke", "credential", "policy"])
def test_late_state_change_rejects_quote(route, monkeypatch, mutation):
    store, asset_type, symbol, source = route
    _admit(store, asset_type, symbol, source)
    key = hithink_quote_route_key(asset_type)
    target = {"providerId": "hithink", "upstreamSource": source}

    def change():
        if mutation == "revoke":
            store.revoke_route_admission_v3(key=key, target=target, reason="test")
        elif mutation == "credential":
            monkeypatch.setenv("HITHINK_API_KEY", "synthetic-quote-key-v2")
        else:
            store.apply_policy_v3(_policy(asset_type, source, revision=2))

    adapter = _QuoteAdapter(on_fetch=change)
    runtime = ThesisLedgerProviderRuntime(store, adapters={"hithink": adapter})
    with pytest.raises(ThesisLedgerGatewayError) as rejected:
        runtime.execute_request(_request(asset_type, symbol))
    assert rejected.value.code == "not_admitted"
    assert adapter.calls == [(symbol, asset_type)]


def test_source_error_keeps_code_and_one_call_per_target(route):
    store, asset_type, symbol, source = route
    _admit(store, asset_type, symbol, source)

    def fail():
        raise HiThinkQuoteError("rate_limited", "HiThink 快照触发限流", retryable=True)

    adapter = _QuoteAdapter(on_fetch=fail)
    runtime = ThesisLedgerProviderRuntime(store, adapters={"hithink": adapter})
    with pytest.raises(ThesisLedgerGatewayError) as rejected:
        runtime.execute_request(_request(asset_type, symbol))
    assert rejected.value.code == "rate_limited"
    assert rejected.value.retryable is True
    assert adapter.calls == [(symbol, asset_type)]


def test_current_source_catalog_removal_rejects_before_adapter(route, monkeypatch):
    store, asset_type, symbol, source = route
    _admit(store, asset_type, symbol, source)
    manifest = PROVIDER_MANIFESTS["hithink"]
    monkeypatch.setitem(PROVIDER_MANIFESTS, "hithink", {
        **manifest,
        "upstreamSources": [
            item for item in manifest["upstreamSources"] if item["sourceId"] != source
        ],
    })
    adapter = _QuoteAdapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"hithink": adapter})
    with pytest.raises(ThesisLedgerGatewayError) as rejected:
        runtime.execute_request(_request(asset_type, symbol))
    assert rejected.value.code == "NO_ELIGIBLE_PROVIDER"
    assert adapter.calls == []


def test_authenticated_quote_http_uses_exact_runtime_and_revocation(route, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger import router_v3 as router

    store, asset_type, symbol, source = route
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-quote-contract-token")
    monkeypatch.delenv("THESIS_LEDGER_FIXTURE_MODE", raising=False)
    adapter = _QuoteAdapter()
    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(store, adapters={"hithink": adapter}),
    )
    monkeypatch.setattr(
        "src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway",
        lambda: gateway,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        path = "/thesis-ledger/market/quote"
        assert client.get(path, params={"symbol": symbol}).status_code == 401
        headers = {"Authorization": "Bearer synthetic-quote-contract-token"}
        denied = client.get(path, params={"symbol": symbol}, headers=headers)
        assert denied.status_code == 503
        assert denied.json()["detail"]["code"] == "no_eligible_provider"
        assert denied.json()["detail"]["contractVersion"] == 3
        assert adapter.calls == []

        _admit(store, asset_type, symbol, source)
        accepted = client.get(path, params={"symbol": symbol}, headers=headers)
        assert accepted.status_code == 200
        assert accepted.json()["symbol"] == symbol
        assert accepted.json()["upstreamSource"] == source
        assert accepted.json()["marketTime"] is None
        assert accepted.json()["units"]["volume"] == (
            "share" if asset_type == "STOCK" else "unknown"
        )

        store.revoke_route_admission_v3(
            key=hithink_quote_route_key(asset_type),
            target={"providerId": "hithink", "upstreamSource": source},
            reason="test-revoke",
        )
        revoked = client.get(path, params={"symbol": symbol}, headers=headers)
        assert revoked.status_code == 503
        assert revoked.json()["detail"]["code"] == "no_eligible_provider"
        assert adapter.calls == [(symbol, asset_type)]
