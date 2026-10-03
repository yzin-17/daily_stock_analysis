"""比较已校验 HiThink ETF 窗口的观测交集，不生成合并行情。"""

from dataclasses import dataclass

from src.services.thesis_ledger_hithink_etf import HiThinkETFBarSeries


@dataclass(frozen=True, slots=True)
class HiThinkOverlapEvidence:
    status: str
    reason: str
    windows: tuple[tuple[str, str, str], tuple[str, str, str]]
    dates: tuple[str, ...] = ()
    conflict_dates: tuple[str, ...] = ()


def compare_hithink_etf_windows(
    left: HiThinkETFBarSeries, right: HiThinkETFBarSeries,
) -> HiThinkOverlapEvidence:
    """一致仅限定于这两次观测的交集，不证明供应商全局基准稳定。"""
    windows = tuple(
        (item.requested_start, item.requested_end, item.response_fingerprint)
        for item in (left, right)
    )
    identity_fields = (
        "symbol", "adjustment", "adjustment_method", "adjustment_method_version",
        "basis_scope", "adjustment_anchor", "price_currency", "volume_unit",
        "turnover_unit", "dividend_meaning",
    )
    if any(getattr(left, field) != getattr(right, field) for field in identity_fields):
        return HiThinkOverlapEvidence("unverified", "identity_mismatch", windows)
    start = max(left.requested_start, right.requested_start)
    end = min(left.requested_end, right.requested_end)
    if start > end:
        return HiThinkOverlapEvidence("unverified", "no_overlap", windows)
    observations = []
    for series in (left, right):
        rows = [bar for bar in series.bars if start <= bar.date <= end]
        by_date = {bar.date: bar for bar in rows}
        if len(by_date) != len(rows):
            return HiThinkOverlapEvidence("unverified", "duplicate_date", windows)
        observations.append(by_date)
    left_rows, right_rows = observations
    dates = tuple(sorted(left_rows))
    if not dates or not right_rows:
        return HiThinkOverlapEvidence("unverified", "no_observed_session", windows)
    if left_rows.keys() != right_rows.keys():
        return HiThinkOverlapEvidence("unverified", "session_mismatch", windows)
    conflicts = tuple(day for day in dates if left_rows[day] != right_rows[day])
    if conflicts:
        return HiThinkOverlapEvidence("conflict", "bar_mismatch", windows, dates, conflicts)
    return HiThinkOverlapEvidence("consistent", "observed_overlap_equal", windows, dates)
