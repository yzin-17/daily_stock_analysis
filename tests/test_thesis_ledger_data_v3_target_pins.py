from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middlewares.error_handler import add_error_handlers
from api.thesis_ledger import router_v3
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
from src.services.thesis_ledger_market_v3_facts import market_pagination_contract_v3
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError,
    ThesisLedgerDataRequest,
    ThesisLedgerProviderRuntime,
    market_v3_current_route_revisions,
)


ROUTE_KEY = {
    "kind": "bar",
    "market": "CN",
    "assetType": "ETF",
    "capability": "DAILY_BAR",
    "timeframe": "1d",
    "adjustment": "qfq",
}
PRIMARY = {"providerId": "akshare", "upstreamSource": "eastmoney"}
BACKUP = {"providerId": "tencent", "upstreamSource": "tencent"}
SYMBOL = "159516.SZ"
START = "2026-05-18"
END = "2026-05-20"
REQUEST_ID = "data-v3-target-pin-test"
REQUEST = {
    "contractVersion": 3,
    "requestId": REQUEST_ID,
    "symbol": SYMBOL,
    "routeKey": ROUTE_KEY,
    "start": START,
    "end": END,
}


def _sessions() -> tuple[str, ...]:
    start = date.fromisoformat(START)
    end = date.fromisoformat(END)
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(days)


def _frame(provider: str, source: str) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "date": day,
                "open": 1.0 + index / 100,
                "high": 1.02 + index / 100,
                "low": 0.99 + index / 100,
                "close": 1.01 + index / 100,
                "volume": 1000 + index,
                "amount": 1010 + index,
            }
            for index, day in enumerate(_sessions())
        ]
    )
    frame.attrs["upstream_source"] = source
    frame.attrs["has_more_before"] = False
    contract = market_pagination_contract_v3("ETF", provider, source)
    assert contract is not None
    frame.attrs["thesis_ledger_v3_pagination"] = {
        "status": "complete",
        "pagesFetched": 1,
        "continuationPending": False,
        **contract,
        "requestedStart": START,
        "requestedEnd": END,
    }
    if provider == "tencent" and source == "tencent":
        partitions = [{
            "start": START, "end": END, "rows": len(frame), "sha256": "a" * 64,
        }]
        frame.attrs["tencentDailyRetrieval"] = {
            "endpoint": "newfqkline/get", "adjustment": ROUTE_KEY["adjustment"],
            "partitionComplete": True, "partitions": partitions,
            "revision": sha256(json.dumps(
                partitions, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
        }
    return frame


def _admission(target: dict[str, str], **overrides) -> dict:
    revisions = market_v3_current_route_revisions(
        ROUTE_KEY,
        target,
        PROVIDER_MANIFESTS[target["providerId"]],
    )
    assert revisions is not None
    return {
        "consumer": "thesis-ledger",
        "admissionState": "admitted",
        "scopeSymbols": [SYMBOL],
        "scopeDateFrom": START,
        "scopeDateTo": END,
        **revisions,
        **overrides,
    }


def _identity(target: dict[str, str]) -> tuple[str, str]:
    return target["providerId"], target["upstreamSource"]


class _DataStore:
    def __init__(self, admissions=None, targets=None, revision=42, source_desired_revision=42):
        self.admissions = admissions or {}
        self.revision = revision
        self.source_desired_revision = source_desired_revision
        self.targets = targets or [
            {**PRIMARY, "routeIndex": 0, "eligible": True, "reason": None},
            {**BACKUP, "routeIndex": 1, "eligible": True, "reason": None},
        ]
        self.admission_reads: dict[tuple[str, str], int] = {}
        self.health_updates = []

    def effective_policy_v3(self):
        return {
            "contractVersion": 3,
            "enabled": True,
            "revision": self.revision,
            "sourceDesiredRevision": self.source_desired_revision,
            "routes": [{"key": ROUTE_KEY, "targets": deepcopy(self.targets)}],
        }

    def get_route_admission_v3(self, *, key, target):
        identity = _identity(target)
        read_index = self.admission_reads.get(identity, 0)
        self.admission_reads[identity] = read_index + 1
        values = self.admissions.get(identity)
        if isinstance(values, list):
            value = values[min(read_index, len(values) - 1)] if values else None
        else:
            value = values
        return deepcopy(value) if value is not None else None

    def health(self, *_args, **_kwargs):
        return None

    def record_health(self, *args, **kwargs):
        self.health_updates.append((args, kwargs))

    def provider_registry(self):
        return [
            {**PROVIDER_MANIFESTS[provider_id], "version": 1, "configVersion": 1}
            for provider_id in ("akshare", "tencent")
        ]


class _Adapter:
    def __init__(self, provider: str, source: str, error_code: str | None = None):
        self.provider = provider
        self.source = source
        self.error_code = error_code
        self.calls = []

    def get_daily_data_for_source(self, symbol, source, **options):
        self.calls.append((symbol, source, options))
        if self.error_code:
            raise ProviderCallError(self.error_code, "synthetic provider failure", retryable=True)
        return _frame(self.provider, self.source)


def _runtime_request() -> ThesisLedgerDataRequest:
    return ThesisLedgerDataRequest(
        capability="DAILY_BAR",
        symbol=SYMBOL,
        timeframe="1d",
        start=START,
        end=END,
        instrument_type="ETF",
        adjustment="qfq",
        request_id=REQUEST_ID,
    )


def _runtime(admissions=None, adapters=None, targets=None) -> ThesisLedgerProviderRuntime:
    return ThesisLedgerProviderRuntime(
        _DataStore(admissions=admissions, targets=targets),
        adapters=adapters,
    )


@pytest.mark.parametrize(
    ("pin", "provider", "source", "route_index", "fallback_used"),
    [
        (PRIMARY, "akshare", "eastmoney", 0, False),
        (BACKUP, "tencent", "tencent", 1, True),
    ],
    ids=["primary", "backup"],
)
def test_pinned_runtime_calls_only_the_exact_effective_target(
    pin, provider, source, route_index, fallback_used
):
    admissions = {
        _identity(PRIMARY): _admission(PRIMARY),
        _identity(BACKUP): _admission(BACKUP),
    }
    primary_adapter = _Adapter("akshare", "eastmoney")
    backup_adapter = _Adapter("tencent", "tencent")
    runtime = _runtime(
        admissions,
        adapters={"akshare": primary_adapter, "tencent": backup_adapter},
    )

    execution = runtime.execute_market_bars_v3(
        _runtime_request(), ROUTE_KEY, route_target={**pin, "routeIndex": route_index}
    )

    selected_adapter = primary_adapter if provider == "akshare" else backup_adapter
    other_adapter = backup_adapter if provider == "akshare" else primary_adapter
    assert len(selected_adapter.calls) == 1
    assert other_adapter.calls == []
    assert execution.provider == provider
    assert execution.upstream_source == source
    assert execution.route_index == route_index
    assert execution.fallback_used is fallback_used
    assert execution.effective_revision == 42
    assert [target.provider_id for target in execution.route_targets] == [provider]


@pytest.mark.parametrize(
    ("pin", "selected_provider", "other_provider"),
    [
        ({**PRIMARY, "routeIndex": 0}, "akshare", "tencent"),
        ({**BACKUP, "routeIndex": 1}, "tencent", "akshare"),
    ],
    ids=["primary-failure", "backup-failure"],
)
@pytest.mark.parametrize('error_code', ['upstream_failure', 'rate_limited', 'timeout'])
def test_pinned_target_failure_does_not_fallback_to_another_eligible_target(
    pin, selected_provider, other_provider, error_code
):
    admissions = {
        _identity(PRIMARY): _admission(PRIMARY),
        _identity(BACKUP): _admission(BACKUP),
    }
    primary_adapter = _Adapter(
        "akshare",
        "eastmoney",
        error_code=error_code if selected_provider == "akshare" else None,
    )
    backup_adapter = _Adapter(
        "tencent",
        "tencent",
        error_code=error_code if selected_provider == "tencent" else None,
    )
    runtime = _runtime(
        admissions,
        adapters={"akshare": primary_adapter, "tencent": backup_adapter},
    )

    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(
            _runtime_request(),
            ROUTE_KEY,
            route_target=pin,
        )

    assert caught.value.code == error_code
    selected_adapter = primary_adapter if selected_provider == "akshare" else backup_adapter
    other_adapter = primary_adapter if other_provider == "akshare" else backup_adapter
    assert len(selected_adapter.calls) == 1
    assert other_adapter.calls == []


@pytest.mark.parametrize(
    ("pin", "admissions", "expected_code"),
    [
        (
            {"providerId": "tencent", "upstreamSource": "other", "routeIndex": 1},
            None,
            "NO_ELIGIBLE_PROVIDER",
        ),
        (
            {**BACKUP, "routeIndex": 0},
            None,
            "NO_ELIGIBLE_PROVIDER",
        ),
        (
            {**BACKUP, "routeIndex": 1},
            {
                _identity(BACKUP): _admission(
                    BACKUP, sourceRevision="stale-source-contract-v0"
                )
            },
            None,
        ),
        (
            {**BACKUP, "routeIndex": 1},
            {
                _identity(BACKUP): _admission(BACKUP, admissionState="revoked")
            },
            None,
        ),
        (
            {**BACKUP, "routeIndex": 1},
            {
                _identity(BACKUP): _admission(
                    BACKUP, scopeDateFrom="2026-05-19"
                )
            },
            None,
        ),
        (
            {**PRIMARY, "routeIndex": 0},
            None,
            "NO_ELIGIBLE_PROVIDER",
        ),
    ],
    ids=[
        "wrong-target",
        "wrong-index",
        "stale-revision",
        "revoked",
        "scope",
        "selected-target-unavailable",
    ],
)
def test_pinned_runtime_validates_target_identity_without_basic_manual_admission(
    pin, admissions, expected_code
):
    primary_adapter = _Adapter("akshare", "eastmoney")
    backup_adapter = _Adapter("tencent", "tencent")
    targets = None
    if expected_code == "NO_ELIGIBLE_PROVIDER" and pin["providerId"] == "akshare":
        targets = [
            {**PRIMARY, "routeIndex": 0, "eligible": False, "reason": "disabled"},
            {**BACKUP, "routeIndex": 1, "eligible": True, "reason": None},
        ]
    runtime = _runtime(
        admissions,
        adapters={"akshare": primary_adapter, "tencent": backup_adapter},
        targets=targets,
    )

    if expected_code is None:
        result = runtime.execute_market_bars_v3(_runtime_request(), ROUTE_KEY, route_target=pin)
        assert result.provider == "tencent"
        assert result.route_index == 1
        assert primary_adapter.calls == []
        assert len(backup_adapter.calls) == 1
        return
    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(_runtime_request(), ROUTE_KEY, route_target=pin)

    assert caught.value.code == expected_code
    assert primary_adapter.calls == []
    assert backup_adapter.calls == []


def test_pinned_basic_runtime_does_not_reread_manual_revisions():
    admissions = {
        _identity(BACKUP): [
            _admission(BACKUP),
            _admission(BACKUP, adapterRevision="stale-after-preflight"),
        ]
    }
    backup_adapter = _Adapter("tencent", "tencent")
    store = _DataStore(admissions=admissions)
    runtime = ThesisLedgerProviderRuntime(store, adapters={"tencent": backup_adapter})

    result = runtime.execute_market_bars_v3(
        _runtime_request(), ROUTE_KEY, route_target={**BACKUP, "routeIndex": 1},
    )
    assert result.provider == "tencent"
    assert store.admission_reads.get(_identity(BACKUP), 0) == 0
    assert len(backup_adapter.calls) == 1


def test_pinned_runtime_rejects_inconsistent_effective_revision_before_adapter_call():
    backup_adapter = _Adapter("tencent", "tencent")
    store = _DataStore(
        admissions={_identity(BACKUP): _admission(BACKUP)},
        source_desired_revision=43,
    )
    runtime = ThesisLedgerProviderRuntime(store, adapters={"tencent": backup_adapter})

    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(
            _runtime_request(),
            ROUTE_KEY,
            route_target={**BACKUP, "routeIndex": 1},
        )

    assert caught.value.code == "invalid_response"
    assert backup_adapter.calls == []


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    add_error_handlers(app)
    return TestClient(app)


class _ApiRuntime:
    def __init__(self, execution):
        self.execution = execution
        self.calls = []

    def execute_market_bars_v3(self, request, route_key, *, route_target=None):
        self.calls.append((request, route_key, route_target))
        return self.execution


def _execution(provider="tencent", source="tencent", route_index=1):
    frame = _frame(provider, source)
    return SimpleNamespace(
        value=frame,
        provider=provider,
        upstream_source=source,
        route_index=route_index,
        effective_revision=42,
    )


def test_api_parses_optional_pin_passes_it_and_echoes_matching_provenance(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    target = {**BACKUP, "routeIndex": 1}
    runtime = _ApiRuntime(_execution())
    monkeypatch.setattr(provider_runtime, "get_thesis_ledger_runtime", lambda: runtime)

    response = _api_client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer data-token"},
        json={**REQUEST, "routeTarget": target},
    )

    assert response.status_code == 200, response.text
    assert runtime.calls[0][2] == target
    assert response.json()["provenance"] == {
        "providerId": "tencent",
        "upstreamSource": "tencent",
        "routeIndex": 1,
        "effectivePolicyRevision": 42,
    }


@pytest.mark.parametrize(
    "target",
    [
        None,
        {"providerId": "tencent", "routeIndex": 1},
        {"providerId": "tencent", "upstreamSource": "tencent", "routeIndex": 2},
        {
            "providerId": "tencent",
            "upstreamSource": "tencent",
            "routeIndex": 1,
            "unexpected": True,
        },
        {"providerId": "tencent", "upstreamSource": "tencent", "routeIndex": True},
    ],
)
def test_api_rejects_malformed_route_target_pin_before_runtime(monkeypatch, target):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    runtime = _ApiRuntime(_execution())
    monkeypatch.setattr(provider_runtime, "get_thesis_ledger_runtime", lambda: runtime)

    response = _api_client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer data-token"},
        json={**REQUEST, "routeTarget": target},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_response"
    assert runtime.calls == []


def test_api_rejects_success_response_whose_provenance_disagrees_with_pin(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    runtime = _ApiRuntime(_execution(provider="akshare", source="eastmoney", route_index=0))
    monkeypatch.setattr(provider_runtime, "get_thesis_ledger_runtime", lambda: runtime)

    response = _api_client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer data-token"},
        json={**REQUEST, "routeTarget": {**BACKUP, "routeIndex": 1}},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_response"


def test_api_maps_unavailable_pin_to_a_stable_safe_error(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")

    class _UnavailableRuntime:
        def execute_market_bars_v3(self, *_args, **_kwargs):
            raise ProviderCallError(
                "NO_ELIGIBLE_PROVIDER", "internal eligibility detail must not escape"
            )

    monkeypatch.setattr(
        provider_runtime,
        "get_thesis_ledger_runtime",
        lambda: _UnavailableRuntime(),
    )
    response = _api_client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer data-token"},
        json={**REQUEST, "routeTarget": {**BACKUP, "routeIndex": 1}},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_failure"
    assert "internal eligibility detail" not in response.text


def test_api_keeps_no_pin_v3_runtime_call_compatible(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")

    class _UnpinnedRuntime:
        def __init__(self):
            self.calls = []

        def execute_market_bars_v3(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return _execution(provider="akshare", source="eastmoney", route_index=0)

    runtime = _UnpinnedRuntime()
    monkeypatch.setattr(provider_runtime, "get_thesis_ledger_runtime", lambda: runtime)

    response = _api_client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer data-token"},
        json=REQUEST,
    )

    assert response.status_code == 200
    assert len(runtime.calls) == 1
    assert len(runtime.calls[0][0]) == 2
    assert runtime.calls[0][1] == {}
    assert "routeTarget" not in response.json()
