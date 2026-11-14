"""同花顺时间线的分页位置、逐页倒序校验及有界时间窗口。"""

from dataclasses import replace

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_news_identity import news_time
from src.services.thesis_ledger_news_pagination import NEWS_CURSORS, NewsPageState


def project_page(raw, state, *, scope, provider, source, published_start=None):
    if not raw.success:
        return raw
    reasons = set(state.missing_reasons if state else ())
    times = [record.published_at for record in raw.records]
    if any(value is None for value in times):
        reasons.add("published_time_unknown")
    known = [value for value in times if value is not None]
    previous = state.last_published if state else None
    ordered = ([previous] if previous else []) + known
    if any(news_time(left) < news_time(right) for left, right in zip(ordered, ordered[1:])):
        reasons.add("timeline_order_unknown")
    # 在已逐页验证倒序且全部时间已知时，来源已经走过闭区间起点。
    # 继续点仍按来源原样消费；未知或逆序时不能使用这一结束条件。
    window_end = bool(published_start and known and not reasons
                      and news_time(known[-1]) < news_time(published_start))
    current = state.offset if state else "first"
    seen = (state.seen_offsets if state else ()) + (current,)
    token = None
    if raw.truncated and not window_end:
        offset = raw.next_cursor
        if not isinstance(offset, str) or not offset or offset in seen:
            raise CapabilityContractError("upstream_failure", "新闻来源分页位置未前进")
        token = NEWS_CURSORS.put(NewsPageState(
            scope, provider, source, offset, seen, tuple(sorted(reasons)), known[-1] if known else previous,
        ))
    return replace(raw, truncated=raw.truncated and not window_end, next_cursor=token,
                   missing_reasons=tuple(sorted(set(raw.missing_reasons) | reasons)))
