"""精确日线绑定的多窗口调度；全部子请求沿用生产准入检查。"""

from copy import deepcopy
from datetime import datetime, timezone
import time
from typing import Callable
from zoneinfo import ZoneInfo

from src.services.thesis_ledger_multi_window_response import build_multi_window_response
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import DailyBinding
from src.services.thesis_ledger_daily_window_contract import DailyWindowPlanningContext, validate_window_plan


def execute_market_window_v3(request_data: dict, runtime, timeout_seconds: float | None = None):
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest

    key = request_data["routeKey"]
    request = ThesisLedgerDataRequest(
        capability=key["capability"], symbol=request_data["symbol"], timeframe=key["timeframe"],
        start=request_data["start"], end=request_data["end"], instrument_type=key["assetType"],
        adjustment=key["adjustment"], request_id=request_data["requestId"],
        parameters={**({} if timeout_seconds is None else {"target_timeout_seconds": timeout_seconds}),
                    **({"historical_tradability": True} if request_data.get("tradabilityMode") else {})},
    )
    if "routeTarget" in request_data:
        return runtime.execute_market_bars_v3(request, key, route_target=request_data["routeTarget"])
    return runtime.execute_market_bars_v3(request, key)


def try_daily_multi_window_v3(
    request_data: dict, coverage_context: dict, runtime,
    render: Callable, coverage: Callable, *, max_requests: int, timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict | None:
    from src.services.thesis_ledger_provider_runtime import ProviderCallError

    target = request_data.get("routeTarget", {})
    key = request_data["routeKey"]
    binding = CAPABILITY_SOURCES.resolve(key, target)
    if not isinstance(binding, DailyBinding) or binding.window_planner is None:
        return None
    sessions = [day for day in coverage_context["calendar"]["expectedSessionDates"]
                if day >= coverage_context["listing"]["firstTradingDate"]]
    context = DailyWindowPlanningContext(request_data['start'], request_data['end'], tuple(sessions), max_requests)
    try:
        raw_plan = binding.window_planner(context)
    except ValueError as error:
        raise ProviderCallError("insufficient_coverage", str(error)) from None
    try:
        plan = validate_window_plan(raw_plan, context)
    except ValueError:
        raise ProviderCallError('invalid_response', '来源窗口计划无效') from None
    windows = [(window.start, window.end) for window in plan.windows]
    if len(windows) == 1:
        return None
    children = []
    for index, (start, end) in enumerate(windows):
        child_request = {**request_data, "start": start, "end": end,
                         "requestId": f"{request_data['requestId']}:window-{index}"}
        child_coverage = coverage(child_request)
        if child_coverage is None:
            raise ProviderCallError("insufficient_coverage", "子窗口覆盖证明不可用")
        children.append((child_request, child_coverage))
    deadline = monotonic() + timeout_seconds
    observations = []
    for child_request, child_coverage in children:
        started = datetime.now(timezone.utc).isoformat()
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ProviderCallError("timeout", "多窗口总预算耗尽")
        execution = execute_market_window_v3(child_request, runtime, remaining)
        response = render(child_request, execution, child_coverage)
        if monotonic() >= deadline:
            raise ProviderCallError("timeout", "多窗口响应超过总预算")
        observations.append({"startedAt": started, "completedAt": datetime.now(timezone.utc).isoformat(),
                             "response": response})
        # 每次新响应立即核对全部既有交集，冲突后不请求后续窗口。
        if len(observations) > 1:
            _combine(request_data, coverage_context, observations, complete=False)
    return _combine(request_data, coverage_context, observations, complete=True)


def _combine(request: dict, context: dict, observations: list[dict], *, complete: bool) -> dict:
    from src.services.thesis_ledger_provider_runtime import ProviderCallError

    parent = deepcopy(observations[0]["response"])
    rows = {}
    for item in observations:
        for row in item["response"]["bars"]:
            rows.setdefault(row["timestamp"], row)
    parent["bars"] = [rows[key] for key in sorted(rows)]
    parent["requestId"] = request["requestId"]
    end = request["end"] if complete else observations[-1]["response"]["coverage"]["requestedEnd"]
    parent["coverage"].update(requestedStart=request["start"], requestedEnd=end,
                              actualStart=parent["bars"][0]["timestamp"] if parent["bars"] else None, actualEnd=parent["bars"][-1]["timestamp"] if parent["bars"] else None,
                              latestCompleteTradingDate=max((item["response"]["coverage"]["latestCompleteTradingDate"] for item in observations if item["response"]["coverage"]["latestCompleteTradingDate"]), default=None))
    parent["coverageProof"] = {**{key: deepcopy(context[key]) for key in ("calendar", "listing", "window")}, "pagination": {"status": "complete",
        "pagesFetched": sum(item["response"]["coverageProof"]["pagination"]["pagesFetched"] for item in observations),
        "continuationPending": False}}
    parent["coverageProof"]["window"]["requestedEnd"] = end
    expected = [day for day in context["calendar"]["expectedSessionDates"]
                if request["start"] <= day <= end and day >= context["listing"]["firstTradingDate"]]
    actual = [datetime.fromisoformat(point["timestamp"].replace("Z", "+00:00"))
              .astimezone(ZoneInfo(context["calendar"]["timezone"])).date().isoformat() for point in parent["bars"]]
    if request.get("tradabilityMode"):
        from src.services.thesis_ledger_market_daily_tradability import combined_observed_dates
        parent["historicalTradabilityWindows"] = [evidence for item in observations for evidence in item["response"]["historicalTradabilityWindows"]]
        expected = combined_observed_dates(parent["historicalTradabilityWindows"], expected)
    if actual != expected:
        raise ProviderCallError("insufficient_coverage", "多窗口并集未覆盖完整交易日历")
    parent["coverageProof"]["calendar"]["expectedSessionDates"] = [
        day for day in context["calendar"]["expectedSessionDates"] if request["start"] <= day <= end]
    try:
        return build_multi_window_response(parent, observations)
    except (ValueError, KeyError, TypeError) as error:
        raise ProviderCallError("invalid_response", "多窗口观测不一致") from error
