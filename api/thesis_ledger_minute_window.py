"""沪深股票原生分钟研究窗口：按真实日历核验连续时段和逐日来源证明。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from api.thesis_ledger_exact_bar_fields import minute_chart_point
from api.thesis_ledger_market_errors import market_data_error_v3
from src.services.thesis_ledger_daily_tradability import DAILY_TRADABILITY_CONTRACT
from src.services.thesis_ledger_dependency_facts import calendar_fact
from src.services.thesis_ledger_listing_information import daily_listing_information
from src.services.thesis_ledger_minute_contract_v1 import MinutePagination


def minute_window_inputs(request, execution, context, number):
    attrs = execution.value.attrs
    request_id = request["requestId"]
    start, end = request["start"], request["end"]
    if (attrs.get("timestampConvention") != "interval-end" or attrs.get("session") != "regular"
            or attrs.get("intradayFieldUnits", {}).get("volume") != "share" or attrs.get("timezone") != "Asia/Shanghai"):
        market_data_error_v3("insufficient_coverage", 422, request_id)
    try:
        paging = MinutePagination.model_validate(attrs.get("pagination"))
        if paging.continuationPending or (paging.requestedStart, paging.requestedEnd) != (start, end):
            raise ValueError("分钟分页与请求不符")
        observed = attrs["observedAt"]
        clock = datetime.fromisoformat(observed)
        fact = calendar_fact(date.fromisoformat(start), date.fromisoformat(end), clock)
        if not fact or fact["sessionOverrides"]:
            raise ValueError("分钟时段事实不足")
        expected = context["expectedPostListingSessionDates"]
        minutes_per_day = sum(s["endMinute"] - s["startMinute"] for s in fact["sessions"])
        if not expected or minutes_per_day <= 0 or len(expected) * minutes_per_day > len(execution.value):
            raise ValueError("缺少完整连续时段")
        zone = ZoneInfo(fact["timezone"])
        labels, auctions = [], set()
        for day in expected:
            midnight = datetime.combine(date.fromisoformat(day), datetime.min.time(), zone)
            if midnight.weekday() >= 5 or day in fact["holidays"]:
                raise ValueError("独立交易日历不一致")
            auctions.add(midnight + timedelta(minutes=fact["sessions"][0]["startMinute"]))
            for session in fact["sessions"]:
                labels.extend(midnight + timedelta(minutes=minute)
                              for minute in range(session["startMinute"] + 1, session["endMinute"] + 1))
        points, actual, traded = [], [], set()
        for row in execution.value.to_dict("records"):
            point = minute_chart_point(row, "CN", observed, number, convention="interval-end")
            stamp = datetime.fromisoformat(point["timestamp"])
            if point["completionStatus"] != "complete":
                raise ValueError("分钟未完成")
            if stamp in auctions:
                auctions.remove(stamp)
                continue
            points.append(point)
            actual.append(stamp)
            if point["volume"] and point["volume"] > 0:
                traded.add(stamp.date().isoformat())
        if actual != labels or traded != set(expected):
            raise ValueError("连续分钟或已交易日期覆盖不足")
        revision = execution.provider_revision
        if not revision or revision == "unknown":
            raise ValueError("分钟来源缺少修订")
        calendar = context["calendar"]
        evidence = {
            "contract": DAILY_TRADABILITY_CONTRACT, "priceOnlyAllowed": False,
            "symbol": request["symbol"], "market": "CN", "instrumentType": "STOCK",
            "range": {"start": start, "end": end}, "listing": daily_listing_information(context["listing"]),
            "calendar": {"expectedSessions": expected, "source": {
                "provider": calendar["source"], "revision": calendar["revision"], "availableAt": observed}},
            "barSource": {"routeKey": request["routeKey"], "routeTarget": {
                "providerId": execution.provider, "upstreamSource": execution.upstream_source},
                "providerRevision": revision, "responseSha256": attrs["responseSha256"],
                "observedAt": observed, "requestComplete": True, "paginationComplete": True},
            "days": [{"date": day, "state": "observed-traded"} for day in expected],
        }
        pagination = {"status": "complete", "pagesFetched": paging.pagesFetched, "continuationPending": False}
        return execution.value, pagination, observed, points, expected, [evidence]
    except (KeyError, TypeError, ValueError):
        market_data_error_v3("insufficient_coverage", 422, request_id)
