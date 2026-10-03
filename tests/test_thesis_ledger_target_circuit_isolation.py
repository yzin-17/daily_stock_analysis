"""验证 V3 共用执行面的真实来源熔断隔离。"""

from datetime import datetime, timezone

import pytest

from src.services.thesis_ledger_provider_runtime import (
    NoEligibleProviderError,
    ProviderCallError,
    RouteTarget,
    ThesisLedgerProviderRuntime,
)


class _HealthStore:
    def __init__(self):
        self.states = {}

    def health(self, provider, capability, instrument, *, upstream_source):
        return self.states.get((provider, capability, instrument, upstream_source))

    def record_health(self, provider, capability, instrument, **state):
        source = state.pop("upstream_source")
        self.states[(provider, capability, instrument, source)] = {
            **state,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


def _execute(runtime, source, operation):
    return runtime._execute_with_metadata(
        "DAILY_BAR",
        "ETF",
        operation,
        effective_policy_override={"contractVersion": 3, "revision": 17},
        route_targets_override=[RouteTarget("akshare", source, 0)],
    )


@pytest.mark.parametrize("error_code", ["upstream_unavailable", "rate_limited"])
@pytest.mark.parametrize("restart", [False, True])
def test_failed_source_does_not_block_or_reset_sibling_source(error_code, restart):
    store = _HealthStore()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": object()})
    calls = []

    def fail(_provider, _adapter, source):
        calls.append(source)
        raise ProviderCallError(error_code, "受控来源失败", retryable=True)

    for _ in range(3):
        with pytest.raises(ProviderCallError) as failure:
            _execute(runtime, "eastmoney", fail)
        assert failure.value.code == error_code
    assert calls == ["eastmoney"] * 3
    failed_key = ("akshare", "DAILY_BAR", "ETF", "eastmoney")
    assert store.states[failed_key]["circuit"] == "open"

    if restart:
        runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": object()})

    def succeed(_provider, _adapter, source):
        calls.append(source)
        return "独立来源结果"

    result = _execute(runtime, "sina", succeed)
    assert result.value == "独立来源结果"
    assert result.upstream_source == "sina"
    assert result.effective_revision == 17
    assert store.states[("akshare", "DAILY_BAR", "ETF", "sina")]["state"] == "healthy"
    assert store.states[failed_key]["circuit"] == "open"

    with pytest.raises(NoEligibleProviderError):
        _execute(runtime, "eastmoney", fail)
    assert calls == ["eastmoney"] * 3 + ["sina"]
