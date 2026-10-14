"""从已完成读取的行情及公共日历自动整理日级状态输入。"""

from hashlib import sha256
import json
import math
from typing import Mapping

from src.services.thesis_ledger_listing_information import daily_listing_information


def market_tradability_frame(request: Mapping, frame, context: Mapping, dates: list[str], observed_at: str):
    """调用方已校验分页；摘要绑定规范化响应，不要求供应商私有元数据。"""
    from src.services.thesis_ledger_zero_volume_observations import executable_research_frame
    frame = executable_research_frame(request, frame)
    if isinstance(getattr(frame, "attrs", {}).get("daily_tradability_input"), dict):
        return frame
    if len(frame) != len(dates):
        raise ValueError("行情行与交易日期数量不一致")
    normalized = frame.copy()
    normalized["date"] = dates
    rows = [
        {"date": day, **{name: (None if row.get(name) is None or math.isnan(float(row[name])) else float(row[name]))
                         for name in ("open", "high", "low", "close", "volume", "amount")}}
        for day, (_, row) in zip(dates, normalized.iterrows())
    ]
    response_hash = sha256(json.dumps({
        "symbol": request["symbol"], "routeKey": request["routeKey"],
        "start": request["start"], "end": request["end"], "bars": rows,
    }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    listing, calendar = context["listing"], context["calendar"]
    expected = context["expectedPostListingSessionDates"]
    normalized.attrs["daily_tradability_input"] = {
        "symbol": request["symbol"], "range": {"start": request["start"], "end": request["end"]},
        "listing": daily_listing_information(listing),
        "calendar": {"expectedSessions": expected, "source": {
            "provider": calendar["source"], "revision": calendar["revision"], "availableAt": observed_at,
        }},
        "observedAt": observed_at, "responseSha256": response_hash,
        "missingSessions": [day for day in expected if day not in dates], "paginationComplete": True,
        "excludedZeroVolumeObservations": frame.attrs.get("excluded_zero_volume_observations", {}),
    }
    return normalized
