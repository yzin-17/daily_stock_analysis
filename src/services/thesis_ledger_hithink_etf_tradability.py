"""HiThink ETF Bar 与独立范围事实的传递适配。"""

from datetime import datetime, timezone
from typing import Any, Mapping


def hithink_etf_frame(series: Any, *, listing: Mapping, calendar: Mapping,
                      upstream_source: str, historical_tradability: bool) -> Any:
    import pandas as pd

    columns = ("date", "open", "high", "low", "close", "volume", "amount")
    frame = pd.DataFrame(
        [{name: getattr(bar, name) for name in columns} for bar in series.bars], columns=columns,
    )
    frame.attrs.update({"upstream_source": upstream_source, "has_more_before": False})
    if historical_tradability:
        observed_at = datetime.now(timezone.utc).isoformat()
        frame.attrs["daily_tradability_input"] = {
            "symbol": series.symbol,
            "range": {"start": series.requested_start, "end": series.requested_end},
            "listing": {"listedOn": listing["firstTradingDate"], "source": {
                "provider": listing["source"], "revision": listing["revision"],
                "availableAt": listing["knownAt"],
            }},
            "calendar": {"source": {
                "provider": calendar["source"], "revision": calendar["revision"],
                "availableAt": observed_at,
            }, "expectedSessions": [
                day for day in calendar["expectedSessionDates"] if day >= listing["firstTradingDate"]
            ]},
            "responseSha256": series.response_fingerprint, "observedAt": observed_at,
            "missingSessions": list(series.missing_sessions),
            "paginationComplete": series.coverage.get("responsePagination") == "no_additional_page_signaled",
        }
    return frame
