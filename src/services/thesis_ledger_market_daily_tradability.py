"""精确行情响应的缺日分区；普通价格读取仍要求全部会话有 Bar。"""

from datetime import datetime
from typing import Any, Mapping

from src.services.thesis_ledger_daily_tradability import daily_tradability_evidence
from src.services.thesis_ledger_market_tradability_input import market_tradability_frame
from src.services.thesis_ledger_provider_runtime import ProviderCallError


def market_daily_tradability(request: Mapping, execution: Any, context: Mapping,
                             actual_dates: list[str], fetched_at: str) -> list[dict] | None:
    expected = context["expectedPostListingSessionDates"]
    if request.get("tradabilityMode") != "assume-untradable-no-bar":
        if not actual_dates or sorted(set(actual_dates)) != expected or len(set(actual_dates)) != len(actual_dates):
            raise ProviderCallError("insufficient_coverage", "上市后 Bar 未完整覆盖预期会话")
        return None
    try:
        frame = market_tradability_frame(request, execution.value, context, actual_dates, fetched_at)
        evidence = daily_tradability_evidence(
            bars=frame, symbol=request["symbol"], instrument_type=request["routeKey"]["assetType"],
            start=request["start"], end=request["end"], data_as_of=datetime.fromisoformat(fetched_at.replace("Z", "+00:00")),
            route_key=request["routeKey"], route_target={
                "providerId": execution.provider, "upstreamSource": execution.upstream_source,
            }, provider_revision=execution.provider_revision,
        )
        if evidence["calendar"]["expectedSessions"] != expected:
            raise ValueError("来源日历不符")
        listing = context["listing"]
        if evidence["listing"] != {"listedOn": listing["firstTradingDate"], "source": {
            "provider": listing["source"], "revision": listing["revision"], "availableAt": listing["knownAt"],
        }} or evidence["calendar"]["source"]["provider"] != context["calendar"]["source"] or evidence["calendar"]["source"]["revision"] != context["calendar"]["revision"]:
            raise ValueError("独立范围事实不符")
        observed = [day["date"] for day in evidence["days"] if day["state"] == "observed-traded"]
        if sorted(actual_dates) != observed or len(set(actual_dates)) != len(actual_dates):
            raise ValueError("实际价格日期与日级状态不符")
        return [evidence]
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise ProviderCallError("invalid_response", "缺少有效的行情日级证据") from error


def combined_observed_dates(windows: list[dict], expected: list[str]) -> list[str]:
    states: dict[str, str] = {}
    for evidence in windows:
        for day in evidence["days"]:
            previous = states.get(day["date"])
            if previous is not None and previous != day["state"]:
                raise ValueError("重叠来源窗口日级状态冲突")
            states[day["date"]] = day["state"]
    if sorted(states) != expected:
        raise ValueError("逐窗证据未覆盖全部预期交易日")
    return [day for day in expected if states[day] == "observed-traded"]
