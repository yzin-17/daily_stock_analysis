"""ThesisLedger Data Contract V3 的严格路由和 BarSeries 边界。"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import create_app
from api.middlewares.error_handler import add_error_handlers
from api.thesis_ledger import router_v3
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError,
    ThesisLedgerDataRequest,
    ThesisLedgerProviderRuntime,
)
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS

ROUTE_KEY = {
    "kind": "bar",
    "market": "CN",
    "assetType": "ETF",
    "capability": "DAILY_BAR",
    "timeframe": "1d",
    "adjustment": "qfq",
}
REQUEST = {
    "contractVersion": 3,
    "requestId": "data-v3-test-1",
    "symbol": "159516.SZ",
    "routeKey": ROUTE_KEY,
    "start": "2026-05-16",
    "end": "2026-08-09",
}


def test_old_market_v2_routes_are_absent_and_indicator_uses_v3():
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/v2/thesis-ledger/market/bars").status_code == 404
    assert client.get("/api/v1/thesis-ledger/market/bars").status_code == 404
    assert client.get("/api/v1/thesis-ledger/market/indicators/MA").status_code == 404
    assert client.post("/api/v2/thesis-ledger/market/indicators/calculate").status_code in {404, 405}
    assert client.post("/api/v3/thesis-ledger/market/indicators/calculate").status_code not in {404, 405}


def _target_session_dates() -> tuple[str, ...]:
    start = date.fromisoformat(REQUEST["start"])
    end = date.fromisoformat(REQUEST["end"])
    sessions = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current.isoformat() != "2026-06-19":
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(sessions)


def _frame(days: tuple[str, ...] = ("2025-01-06", "2025-01-07")) -> pd.DataFrame:
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
            for index, day in enumerate(days)
        ]
    )
    frame.attrs["upstream_source"] = "eastmoney"
    frame.attrs["has_more_before"] = False
    frame.attrs["thesis_ledger_v3_pagination"] = {
        "status": "complete",
        "pagesFetched": 1,
        "continuationPending": False,
        "protocol": "akshare-etf-range-response-v1",
        "maximumRows": None,
    }
    return frame


def _target_frame(days: tuple[str, ...] | None = None) -> pd.DataFrame:
    frame = _frame(days or _target_session_dates())
    frame.attrs["thesis_ledger_v3_pagination"].update(
        requestedStart=REQUEST["start"],
        requestedEnd=REQUEST["end"],
    )
    return frame


def _execution(frame: pd.DataFrame | None = None, **overrides):
    values = {
        "value": frame if frame is not None else _frame(),
        "provider": "akshare",
        "upstream_source": "eastmoney",
        "route_index": 0,
        "effective_revision": 7,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    add_error_handlers(app)
    return TestClient(app)


def test_v3_capabilities_use_independent_data_versions():
    response = _client().get("/api/v3/thesis-ledger/capabilities")

    assert response.status_code == 200
    assert response.json() == {"dataContractVersions": [3],
                               "serviceCapabilities": {"fundNav": True},
                               "multiWindowProtocols": ["market-multi-window-content-v1"]}


def test_v3_routes_are_mounted_on_the_dsa_app(tmp_path):
    app = create_app(static_dir=tmp_path / "empty-static")

    response = TestClient(app).get("/api/v3/thesis-ledger/capabilities")

    assert response.status_code == 200
    assert response.json() == {"dataContractVersions": [3],
                               "serviceCapabilities": {"fundNav": True},
                               "multiWindowProtocols": ["market-multi-window-content-v1"]}


def test_v3_bars_reject_extra_request_fields_and_unsupported_versions(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    client = _client()
    headers = {"Authorization": "Bearer data-token"}

    extra = client.post(
        "/api/v3/thesis-ledger/market/bars",
        headers=headers,
        json={**REQUEST, "limit": 90},
    )
    unsupported = client.post(
        "/api/v3/thesis-ledger/market/bars",
        headers=headers,
        json={**REQUEST, "contractVersion": 2},
    )

    assert extra.status_code == 422
    assert extra.json() == {
        "contractVersion": 3,
        "requestId": REQUEST["requestId"],
        "error": {"code": "invalid_response", "message": "行情上游响应格式无效"},
    }
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "unsupported_data_contract_version"


def test_v3_bars_return_only_complete_covered_source_facts(monkeypatch):
    import api.thesis_ledger as routes
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setattr(routes, "_now_iso", lambda: "2026-09-25T00:00:00+00:00")
    changed_frame = _target_frame()
    changed_frame.loc[0, "volume"] += 1
    executions = iter(
        [
            _execution(_target_frame()),
            _execution(_target_frame()),
            _execution(changed_frame),
        ]
    )
    monkeypatch.setattr(
        provider_runtime,
        "get_thesis_ledger_runtime",
        lambda: SimpleNamespace(execute_market_bars_v3=lambda *_args: next(executions)),
    )

    client = _client()
    headers = {"Authorization": "Bearer data-token"}
    responses = [
        client.post("/api/v3/thesis-ledger/market/bars", headers=headers, json=REQUEST)
        for _ in range(3)
    ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    result, retry, changed = [response.json() for response in responses]
    assert all(point["availableAt"] == "2026-09-25T00:00:00+00:00" for point in result["bars"])
    assert all(point["completionStatus"] == "complete" for point in result["bars"])
    assert result["sourcePriceBasis"]["observedAt"] == "2026-09-25T00:00:00+00:00"
    assert result["routeKey"] == ROUTE_KEY
    assert result["requestId"] == REQUEST["requestId"]
    assert result["coverage"] == {
        "requestedStart": REQUEST["start"],
        "requestedEnd": REQUEST["end"],
        "actualStart": "2026-05-18T00:00:00+00:00",
        "actualEnd": "2026-08-07T00:00:00+00:00",
        "hasMoreBefore": False,
        "latestCompleteTradingDate": "2026-08-07",
    }
    assert result["coverageProof"] == {
        "calendar": {
            "market": "CN",
            "timezone": "Asia/Shanghai",
            "source": "https://www.szse.cn/disclosure/notice/t20251222_618087.html",
            "revision": "szse-2026-holidays-t20251222_618087-year-v1",
            "supportedRange": {"start": "2026-01-01", "end": "2026-12-31"},
            "expectedSessionDates": list(_target_session_dates()),
        },
        "listing": {
            "symbol": "159516.SZ",
            "firstTradingDate": "2023-07-27",
            "source": "https://www.szse.cn/disclosure/notice/fund/t20230724_602100.html",
            "revision": "szse-fund-listing-t20230724_602100",
            "knownAt": "2023-07-24T00:00:00+08:00",
        },
        "window": {
            "status": "complete",
            "requestedStart": REQUEST["start"],
            "requestedEnd": REQUEST["end"],
        },
        "pagination": {
            "status": "complete",
            "pagesFetched": 1,
            "continuationPending": False,
        },
    }
    assert result["sourcePriceBasis"] == {
        "adjustment": "qfq",
        "method": "provider-native",
        "methodVersion": "dsa-market-bars-v3-native-source-v1",
        "basisScope": "provider-defined",
        "anchor": None,
        "revision": {"origin": "local-observation", "contentHash": result["inputFingerprint"]},
        "volumeBasis": "unknown",
        "dividendMeaning": "provider-defined",
        "dividendEvidenceRef": None,
        "conversionAvailable": False,
        "conversionEvidenceRef": None,
        "derivation": None,
        "observedAt": result["sourcePriceBasis"]["observedAt"],
    }
    assert result["provenance"] == {
        "providerId": "akshare",
        "upstreamSource": "eastmoney",
        "routeIndex": 0,
        "effectivePolicyRevision": 7,
    }
    assert len(result["inputFingerprint"]) == 64
    assert retry["inputFingerprint"] == result["inputFingerprint"]
    assert retry["sourcePriceBasis"]["revision"] == result["sourcePriceBasis"]["revision"]
    assert changed["inputFingerprint"] != result["inputFingerprint"]
    assert changed["sourcePriceBasis"]["revision"] != result["sourcePriceBasis"]["revision"]
    assert set(result) == {
        "contractVersion", "requestId", "symbol", "routeKey", "bars", "coverage", "coverageProof",
        "sourcePriceBasis", "provenance", "inputFingerprint"
    }


def test_v3_bars_fail_closed_when_session_coverage_is_incomplete(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    executions = iter([_execution(_target_frame(_target_session_dates()[:-1]))])
    monkeypatch.setattr(
        provider_runtime,
        "get_thesis_ledger_runtime",
        lambda: SimpleNamespace(execute_market_bars_v3=lambda *_args: next(executions)),
    )
    client = _client()
    headers = {"Authorization": "Bearer data-token"}

    insufficient = client.post("/api/v3/thesis-ledger/market/bars", headers=headers, json=REQUEST)

    assert insufficient.status_code == 422
    assert insufficient.json()["error"]["code"] == "insufficient_coverage"


def test_v3_szse_calendar_evidence_is_versioned_and_limited_to_the_reviewed_year():
    from src.services.thesis_ledger_market_v3_facts import market_calendar_evidence_v3

    evidence = market_calendar_evidence_v3("CN", REQUEST["start"], REQUEST["end"])

    assert evidence is not None
    assert evidence["market"] == "CN"
    assert evidence["timezone"] == "Asia/Shanghai"
    assert evidence["source"] == "https://www.szse.cn/disclosure/notice/t20251222_618087.html"
    assert evidence["revision"].startswith("szse-2026-holidays-t20251222_618087-")
    assert evidence["supportedRange"] == {"start": "2026-01-01", "end": "2026-12-31"}
    assert len(evidence["expectedSessionDates"]) == 59
    assert evidence["expectedSessionDates"][0] == "2026-05-18"
    assert evidence["expectedSessionDates"][-1] == "2026-08-07"
    assert "2026-06-19" not in evidence["expectedSessionDates"]
    assert market_calendar_evidence_v3("CN", "2025-12-31", REQUEST["end"]) is None
    assert market_calendar_evidence_v3("CN", REQUEST["start"], "2027-01-01") is None


def test_v3_bars_refuse_missing_listing_or_calendar_facts_before_provider_call(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    calls = []

    class _Runtime:
        def execute_market_bars_v3(self, *_args):
            calls.append("called")
            return _execution(_target_frame())

    monkeypatch.setattr(provider_runtime, "get_thesis_ledger_runtime", lambda: _Runtime())
    client = _client()
    headers = {"Authorization": "Bearer data-token"}
    invalid_requests = [
        {**REQUEST, "symbol": "159919.SZ"},
        {**REQUEST, "end": "2027-01-01"},
        {**REQUEST, "start": "2023-07-01", "end": "2023-07-26"},
    ]

    responses = [
        client.post("/api/v3/thesis-ledger/market/bars", headers=headers, json=value)
        for value in invalid_requests
    ]

    assert [response.status_code for response in responses] == [422, 422, 422]
    assert all(response.json()["error"]["code"] == "insufficient_coverage" for response in responses)
    assert calls == []


def test_v3_bars_reject_unknown_calendar_state_before_provider_call(monkeypatch):
    import api.thesis_ledger as api_module
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setattr(
        api_module,
        "market_calendar_evidence_v3",
        lambda *_args: {"market": "CN", "status": "unknown"},
    )
    calls = []

    class _Runtime:
        def execute_market_bars_v3(self, *_args):
            calls.append("called")
            return _execution(_target_frame())

    monkeypatch.setattr(provider_runtime, "get_thesis_ledger_runtime", lambda: _Runtime())
    response = _client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer data-token"},
        json=REQUEST,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_coverage"
    assert calls == []


def test_v3_bars_reject_duplicate_sessions_and_unknown_pagination_state(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    sessions = list(_target_session_dates())
    duplicate_sessions = sessions.copy()
    duplicate_sessions[1] = duplicate_sessions[0]
    duplicate_frame = _target_frame(tuple(duplicate_sessions))
    unknown_pagination_frame = _target_frame()
    unknown_pagination_frame.attrs["thesis_ledger_v3_pagination"]["status"] = "unknown"
    executions = iter(
        [
            _execution(duplicate_frame),
            _execution(unknown_pagination_frame),
        ]
    )
    monkeypatch.setattr(
        provider_runtime,
        "get_thesis_ledger_runtime",
        lambda: SimpleNamespace(execute_market_bars_v3=lambda *_args: next(executions)),
    )
    headers = {"Authorization": "Bearer data-token"}

    duplicate = _client().post("/api/v3/thesis-ledger/market/bars", headers=headers, json=REQUEST)
    unknown_pagination = _client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers=headers,
        json=REQUEST,
    )

    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "insufficient_coverage"
    assert unknown_pagination.status_code == 502
    assert unknown_pagination.json()["error"]["code"] == "invalid_response"


@pytest.mark.parametrize("unit_contract", ["absent", "valid", "mismatched"])
def test_v3_wire_uses_runtime_pagination_evidence_for_the_exact_window(monkeypatch, tmp_path, unit_contract):
    import src.services.thesis_ledger_provider_runtime as provider_runtime
    from src.services.thesis_ledger_control import ThesisLedgerControlStore

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")

    class _AkShareAdapter:
        def __init__(self):
            self.calls = []

        def get_daily_data_for_source(self, symbol, source, **options):
            self.calls.append((symbol, source, options))
            frame = _target_frame()
            frame.attrs["upstream_source"] = source
            if unit_contract != "absent":
                from data_provider.akshare_daily_contract import annotate_exact_daily_contract
                annotate_exact_daily_contract(frame, "etf", source, options["adjustment"])
                if unit_contract == "mismatched":
                    frame.attrs["native_daily_contract"]["assetType"] = "STOCK"
            return frame

    store = ThesisLedgerControlStore(str(tmp_path / "control-v3.sqlite"))
    # The successful route is admitted explicitly for this fixture's symbol and dates.
    store.record_route_admission_v3(
        key=ROUTE_KEY,
        target={"providerId": "akshare", "upstreamSource": "eastmoney"},
        evidence_ref="fixture://market-data-v3-admission",
        evidence_sha256="a" * 64,
        scope_symbols=[REQUEST["symbol"]],
        scope_date_from=REQUEST["start"],
        scope_date_to=REQUEST["end"],
        adapter_revision="dsa-v3-etf-akshare-eastmoney-qfq-adapter-v1",
        source_revision="akshare-etf-range-response-v1",
        credential_revision="not-required",
        valid_from="2026-01-01T00:00:00+00:00",
        valid_until="2027-01-01T00:00:00+00:00",
        recorded_by="pytest-fixture",
    )
    policy = {
        "contractVersion": 3,
        "consumer": "thesis-ledger",
        "requestId": "coverage-proof-runtime-test",
        "enabled": True,
        "revision": 15,
        "routes": [
            {
                "key": ROUTE_KEY,
                "targets": [
                    {
                        "providerId": "akshare",
                        "upstreamSource": "eastmoney",
                    }
                ],
            }
        ],
    }
    applied = store.apply_policy_v3(policy)
    assert applied["effective"]["routes"][0]["targets"][0]["eligible"] is True
    adapter = _AkShareAdapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": adapter})
    monkeypatch.setattr(provider_runtime, "get_thesis_ledger_runtime", lambda: runtime)

    response = _client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer data-token"},
        json=REQUEST,
    )

    if unit_contract == "mismatched":
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "invalid_response"
        assert len(adapter.calls) == 1
        return
    assert response.status_code == 200
    assert len(response.json()["bars"]) == 59
    basis = response.json()["sourcePriceBasis"]
    if unit_contract == "valid":
        assert basis["fieldUnits"] == {"volume": "unknown", "amount": "unknown"}
    else:
        assert "fieldUnits" not in basis
    assert response.json()["coverageProof"]["pagination"] == {
        "status": "complete",
        "pagesFetched": 1,
        "continuationPending": False,
    }
    assert adapter.calls == [
        (
            "159516",
            "eastmoney",
            {
                "start_date": REQUEST["start"],
                "end_date": REQUEST["end"],
                "days": 86,
                "adjustment": "qfq",
                "timeout_seconds": 4.5,
            },
        )
    ]


def test_v3_bars_reject_unknown_price_basis_with_a_stable_error(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setattr(
        provider_runtime,
        "get_thesis_ledger_runtime",
        lambda: SimpleNamespace(
            execute_market_bars_v3=lambda *_args: (_ for _ in ()).throw(
                ProviderCallError("unsupported_adjustment", "provider detail must not escape")
            )
        ),
    )

    response = _client().post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer data-token"},
        json={**REQUEST, "routeKey": {**ROUTE_KEY, "adjustment": "hfq"}},
    )

    assert response.status_code == 422
    assert response.json() == {
        "contractVersion": 3,
        "requestId": REQUEST["requestId"],
        "error": {"code": "unsupported_price_basis", "message": "来源不支持请求的价格口径"},
    }
    assert "provider detail" not in response.text


_DEFAULT_ADMISSION = object()


class _RuntimeStore:
    def __init__(self, policy, admission=_DEFAULT_ADMISSION):
        self.policy = policy
        if admission is _DEFAULT_ADMISSION:
            admission = {
                "admissionState": "admitted",
                "scopeSymbols": ["159516.SZ"],
                "scopeDateFrom": "2000-01-01",
                "scopeDateTo": "2030-12-31",
            }
        if isinstance(admission, dict):
            admission = {
                "consumer": "thesis-ledger",
                "adapterRevision": "dsa-v3-etf-tencent-qfq-adapter-v2",
                "sourceRevision": "tencent-newfqkline-year-partitions-v2",
                "credentialRevision": "not-required",
                **admission,
            }
        self.admission = admission
        self.admission_reads = []
        self.health_updates = []

    def effective_policy_v3(self):
        return self.policy

    def effective_policy(self):
        raise AssertionError("Data V3 must not consult the V1/V2 policy")

    def health(self, *_args, **_kwargs):
        return None

    def record_health(self, *args, **kwargs):
        self.health_updates.append((args, kwargs))

    def provider_registry(self):
        return [{"providerId": "tencent", "version": 4, "configVersion": 2}]

    def get_route_admission_v3(self, *, key, target):
        self.admission_reads.append((key, target))
        return self.admission


class _TencentAdapter:
    def __init__(self):
        self.calls = []

    def get_daily_data_for_source(self, symbol, source, **options):
        self.calls.append((symbol, source, options))
        frame = _frame()
        frame.attrs["upstream_source"] = source
        frame.attrs["tencentDailyRetrieval"] = {"partitions": [{"rows": len(frame)}]}
        return frame


def test_runtime_uses_exact_v3_policy_target_and_preserves_backup_index():
    v3_route = {
        **ROUTE_KEY,
    }
    v3_policy = {
        "contractVersion": 3,
        "enabled": True,
        "revision": 12,
        "sourceDesiredRevision": 12,
        "routes": [
            {
                "key": v3_route,
                "targets": [
                    {
                        "providerId": "akshare",
                        "upstreamSource": "eastmoney",
                        "routeIndex": 0,
                        "eligible": False,
                        "reason": "disabled",
                    },
                    {
                        "providerId": "tencent",
                        "upstreamSource": "tencent",
                        "routeIndex": 1,
                        "eligible": True,
                        "reason": None,
                    },
                ],
            }
        ],
    }
    store = _RuntimeStore(v3_policy)
    adapter = _TencentAdapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"tencent": adapter})
    request = ThesisLedgerDataRequest(
        capability="DAILY_BAR",
        symbol="159516.SZ",
        timeframe="1d",
        start="2025-01-06",
        end="2025-01-07",
        instrument_type="ETF",
        adjustment="qfq",
        request_id="runtime-v3-test",
    )

    result = runtime.execute_market_bars_v3(request, v3_route)

    assert adapter.calls == [
        (
            "159516",
            "tencent",
            {
                "start_date": "2025-01-06",
                "end_date": "2025-01-07",
                "days": 2,
                "adjustment": "qfq",
                "timeout_seconds": 4.5,
                "asset_type": "ETF",
            },
        )
    ]
    assert result.route_index == 1
    assert result.fallback_used is True
    assert result.upstream_source == "tencent"
    assert result.effective_revision == 12
    assert result.value.attrs["thesis_ledger_v3_pagination"] == {
        "status": "complete",
        "pagesFetched": 1,
        "continuationPending": False,
        "requestedStart": "2025-01-06",
        "requestedEnd": "2025-01-07",
        "protocol": "tencent-newfqkline-year-partitions-v2",
        "maximumRows": 800,
    }
    assert store.health_updates
    assert len(store.admission_reads) == 0


@pytest.mark.parametrize(
    ("admission", "expected_code"),
    [
        (None, "NO_ELIGIBLE_PROVIDER"),
        ({"admissionState": "revoked"}, "NO_ELIGIBLE_PROVIDER"),
        ({"admissionState": "expired"}, "NO_ELIGIBLE_PROVIDER"),
        (
            {
                "admissionState": "admitted",
                "scopeSymbols": ["OTHER.SZ"],
                "scopeDateFrom": "2025-01-01",
                "scopeDateTo": "2025-12-31",
            },
            "insufficient_coverage",
        ),
        (
            {
                "admissionState": "admitted",
                "scopeSymbols": ["159516.SZ"],
                "scopeDateFrom": "2025-01-06",
                "scopeDateTo": "2025-01-06",
            },
            "insufficient_coverage",
        ),
    ],
    ids=["missing", "revoked", "expired", "symbol-out-of-scope", "window-out-of-scope"],
)
def test_basic_runtime_ignores_manual_scope_and_admission_state(admission, expected_code):
    policy = {
        "contractVersion": 3,
        "enabled": True,
        "revision": 12,
        "routes": [
            {
                "key": ROUTE_KEY,
                "targets": [
                    {
                        "providerId": "tencent",
                        "upstreamSource": "tencent",
                        "routeIndex": 0,
                        "eligible": True,
                        "reason": None,
                    }
                ],
            }
        ],
    }
    store = _RuntimeStore(policy, admission=admission)
    adapter = _TencentAdapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"tencent": adapter})
    request = ThesisLedgerDataRequest(
        capability="DAILY_BAR",
        symbol="159516.SZ",
        timeframe="1d",
        start="2025-01-06",
        end="2025-01-07",
        instrument_type="ETF",
        adjustment="qfq",
        request_id="runtime-v3-admission-scope-test",
    )

    result = runtime.execute_market_bars_v3(request, ROUTE_KEY)
    assert result.provider == "tencent"
    assert len(adapter.calls) == 1
    assert len(store.admission_reads) == 0


def test_runtime_rejects_unproven_v3_adjustment_without_calling_provider():
    policy = {
        "contractVersion": 3,
        "enabled": True,
        "revision": 12,
        "routes": [
            {
                "key": ROUTE_KEY,
                "targets": [
                    {
                        "providerId": "tencent",
                        "upstreamSource": "tencent",
                        "routeIndex": 0,
                        "eligible": False,
                        "reason": "unsupported_adjustment",
                    }
                ],
                "reason": "unsupported_adjustment",
            }
        ],
    }
    adapter = _TencentAdapter()
    runtime = ThesisLedgerProviderRuntime(_RuntimeStore(policy), adapters={"tencent": adapter})
    request = ThesisLedgerDataRequest(
        capability="DAILY_BAR",
        symbol="159516.SZ",
        timeframe="1d",
        start="2025-01-06",
        end="2025-01-07",
        instrument_type="ETF",
        adjustment="hfq",
        request_id="runtime-v3-unsupported-test",
    )
    request_route = {**ROUTE_KEY, "adjustment": "hfq"}
    policy["routes"][0]["key"] = request_route

    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(request, request_route)

    assert caught.value.code == "unsupported_adjustment"
    assert adapter.calls == []


def test_runtime_rejects_effective_policy_that_marks_unadapted_route_ready():
    unsupported_route_key = {**ROUTE_KEY, "adjustment": "qfq"}
    policy = {
        "contractVersion": 3,
        "enabled": True,
        "revision": 12,
        "sourceDesiredRevision": 12,
        "routes": [
            {
                "key": unsupported_route_key,
                "targets": [
                    {
                        "providerId": "akshare",
                        "upstreamSource": "tencent",
                        "routeIndex": 0,
                        "eligible": True,
                        "reason": None,
                    }
                ],
            }
        ],
    }
    adapter = _TencentAdapter()
    runtime = ThesisLedgerProviderRuntime(_RuntimeStore(policy), adapters={"akshare": adapter})
    request = ThesisLedgerDataRequest(
        capability="DAILY_BAR",
        symbol="159516.SZ",
        timeframe="1d",
        start="2025-01-06",
        end="2025-01-07",
        instrument_type="ETF",
        adjustment="qfq",
        request_id="runtime-v3-unadapted-ready-test",
    )

    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(request, unsupported_route_key)

    assert caught.value.code == "invalid_response"
    assert adapter.calls == []


class _CatalogStore:
    def __init__(self, providers):
        self.providers = providers

    def provider_registry(self):
        return self.providers


def _catalog_provider(provider_id: str, **overrides):
    manifest = PROVIDER_MANIFESTS[provider_id]
    value = {
        **manifest,
        "configured": True,
        "enabled": True,
        "credentialConfigured": False,
        "tombstone": None,
    }
    value.update(overrides)
    return value


def test_route_catalog_fails_closed_for_duplicate_inventory_and_provider_states(monkeypatch):
    import src.services.thesis_ledger_provider_runtime as provider_runtime_module

    store = _CatalogStore(
        [
            _catalog_provider("akshare", enabled=False),
            _catalog_provider(
                "tencent", requiresCredential=True, credentialConfigured=False
            ),
        ]
    )
    runtime = ThesisLedgerProviderRuntime(store)

    catalog = runtime.market_route_catalog_v3()
    state_by_target = {
        (entry["target"]["providerId"], entry["target"]["upstreamSource"]): entry["state"]
        for entry in catalog["entries"]
    }
    assert catalog["integrity"] == "complete"
    assert state_by_target[("akshare", "eastmoney")] == "not_admitted"
    assert state_by_target[("tencent", "tencent")] == "credential_missing"

    exact_route = next(provider_runtime_module.iter_market_v3_bar_adapters())
    monkeypatch.setattr(
        provider_runtime_module,
        "iter_market_v3_bar_adapters",
        lambda: iter([exact_route, exact_route]),
    )
    duplicate_catalog = runtime.market_route_catalog_v3()

    assert duplicate_catalog["integrity"] == "partial"
    assert duplicate_catalog["entries"] == []
