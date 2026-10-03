"""报价接口的来源时点判定，不能以获取时间替代来源新鲜度。"""

from datetime import datetime, timezone


def quote_time_state(value, source_stale, now: datetime):
    provider_time = None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                provider_time = parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    stale = source_stale is True
    if provider_time is not None:
        age = (now - provider_time).total_seconds()
        stale = stale or age > 600
        if stale:
            return provider_time.isoformat(), True, 'stale'
        if age >= 0:
            return provider_time.isoformat(), False, 'live'
        return provider_time.isoformat(), False, 'unknown'
    return None, stale, 'stale' if stale else 'unknown'
