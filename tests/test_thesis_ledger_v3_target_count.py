"""主备数量约束必须在任何 V3 适配器调用前执行。"""

from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError,
    ThesisLedgerDataRequest,
    ThesisLedgerProviderRuntime,
)


@pytest.mark.parametrize("pinned", [False, True])
def test_v3_rejects_third_target_before_admission_or_adapter(pinned):
    key = {
        "kind": "bar", "market": "CN", "assetType": "ETF",
        "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq",
    }
    targets = [
        {"providerId": provider, "upstreamSource": source, "routeIndex": index,
         "eligible": False, "reason": "disabled"}
        for index, (provider, source) in enumerate([
            ("tencent", "tencent"), ("akshare", "eastmoney"), ("akshare", "sina"),
        ])
    ]

    def forbidden(*_args, **_kwargs):
        pytest.fail("非法主备数量不得读取准入、健康或创建适配器")

    store = SimpleNamespace(
        effective_policy_v3=lambda: {
            "contractVersion": 3, "enabled": True, "revision": 17,
            "sourceDesiredRevision": 17, "routes": [{"key": key, "targets": targets}],
        },
        get_route_admission_v3=forbidden,
        health=forbidden,
        provider_registry=forbidden,
    )
    runtime = ThesisLedgerProviderRuntime(store, adapters={})
    request = ThesisLedgerDataRequest(
        "DAILY_BAR", "159516.SZ", timeframe="1d", instrument_type="ETF",
        adjustment="qfq", start="2025-01-06", end="2025-01-07",
    )
    pin = None
    if pinned:
        pin = {k: targets[0][k] for k in ("providerId", "upstreamSource", "routeIndex")}
    with pytest.raises(ProviderCallError) as failure:
        runtime.execute_market_bars_v3(request, key, route_target=pin)
    assert failure.value.code == "invalid_response"
    assert "数量非法" in str(failure.value)
