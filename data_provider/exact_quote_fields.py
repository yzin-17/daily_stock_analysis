"""报价的实际抓取时间与来源时间；来源未给出时保持未知。"""

from datetime import datetime, timezone
import math


def finish_exact_quote(quote, timestamp):
    quote.fetched_at = datetime.now(timezone.utc).isoformat()
    observed = None
    try:
        if isinstance(timestamp, datetime) and timestamp.tzinfo is not None:
            observed = timestamp.astimezone(timezone.utc)
        elif not isinstance(timestamp, bool) and isinstance(timestamp, (int, float)) and math.isfinite(timestamp) and timestamp > 0:
            observed = datetime.fromtimestamp(timestamp, timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    quote.provider_timestamp = observed.isoformat() if observed else None
    return quote
