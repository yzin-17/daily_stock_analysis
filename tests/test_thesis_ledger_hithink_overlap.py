"""交集一致不等于全局序列可合并。"""

from dataclasses import replace

import pytest

from src.services.thesis_ledger_hithink_etf import HiThinkETFBar, HiThinkETFBarSeries
from src.services.thesis_ledger_hithink_overlap import compare_hithink_etf_windows


def _series(start, end, fingerprint):
    bars = tuple(HiThinkETFBar(day, 10, 11, 9, 10, 100, 1000)
                 for day in ("2025-01-06", "2025-01-07", "2025-01-08")
                 if start <= day <= end)
    return HiThinkETFBarSeries("159516.SZ", start, end, bars, {}, fingerprint)


def _pair():
    return (_series("2025-01-06", "2025-01-07", "left-response"),
            _series("2025-01-07", "2025-01-08", "right-response"))


def test_consistency_is_scoped_to_observed_intersection_and_retains_fingerprints():
    left, right = _pair()
    result = compare_hithink_etf_windows(left, right)
    assert result.status == "consistent"
    assert result.dates == ("2025-01-07",)
    assert result.windows == ((left.requested_start, left.requested_end, "left-response"),
                              (right.requested_start, right.requested_end, "right-response"))
    assert left.adjustment_method_version is None
    assert left.adjustment_anchor is None


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume", "amount"])
def test_conflicting_observation_is_not_merged(field):
    left, right = _pair()
    changed = replace(right.bars[0], **{field: getattr(right.bars[0], field) + 1})
    result = compare_hithink_etf_windows(left, replace(right, bars=(changed, *right.bars[1:])))
    assert result.status == "conflict"
    assert result.conflict_dates == ("2025-01-07",)


@pytest.mark.parametrize("field,value", [
    ("symbol", "510300.SH"), ("adjustment", "hfq"), ("volume_unit", "shares"),
    ("adjustment_anchor", "2025-01-08"), ("adjustment_method_version", "new"),
])
def test_incompatible_identity_is_unverified(field, value):
    left, right = _pair()
    assert compare_hithink_etf_windows(left, replace(right, **{field: value})).reason == "identity_mismatch"


def test_disjoint_empty_missing_and_duplicate_sessions_fail_closed():
    left, right = _pair()
    assert compare_hithink_etf_windows(left, replace(right, requested_start="2025-01-08")).reason == "no_overlap"
    assert compare_hithink_etf_windows(left, replace(right, bars=())).reason == "no_observed_session"
    assert compare_hithink_etf_windows(left, replace(left, bars=left.bars[:1])).reason == "session_mismatch"
    assert compare_hithink_etf_windows(left, replace(left, bars=left.bars * 2)).reason == "duplicate_date"
