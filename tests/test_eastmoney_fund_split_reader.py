"""基金拆分分页预算、精度和观测边界。"""

import json
from decimal import Decimal

import pytest
import requests

from data_provider import eastmoney_fund_split_reader as reader

ROW = ["159596", "A50ETF", "2025-10-17", "份额分拆", "2", ""]


def page(rows, info=(1, 100, 1)):
    return f"var pageinfo={json.dumps(info)};var jjcf_data={json.dumps(rows)};"


def fetch():
    return reader.fetch_fund_split_observations("159596.SZ", start="2025-01-01", end="2025-12-31")


def test_native_numeric_ratio_remains_decimal():
    text = page([ROW]).replace('"2"', '1.123456789012345678901')
    _, rows = reader.parse_split_page(text, 1)
    assert rows[0][4] == Decimal("1.123456789012345678901")


def test_complete_transport_does_not_grant_event_coverage(monkeypatch):
    calls = []

    def read(session, **kwargs):
        calls.append(kwargs)
        return page([ROW])
    monkeypatch.setattr(reader, "read_event_page", read)
    result = fetch()
    assert result["retrieval"]["paginationComplete"] is True
    assert result["coverage"]["complete"] is False
    assert result["facts"] == []
    assert result["observations"][0]["effectivePhase"] == "unknown"
    assert calls == [{"year": 2025, "page": 1, "data_type": "9", "max_bytes": 1024 * 1024}]


@pytest.mark.parametrize("text", [
    page([], (2, 100, 1)), page([ROW], (0, 100, 1)), page([ROW], (1, 100, 2)),
    page([ROW]) + "var jjcf_data=[];", 'var pageinfo=[1,100,1];var jjcf_data=alert(1);',
    page([[*ROW[:4], True, ""]]),
])
def test_invalid_page_is_rejected(text):
    with pytest.raises(ValueError):
        reader.parse_split_page(text, 1)


def test_budget_rejects_before_extra_requests(monkeypatch):
    calls = []

    def read(session, **kwargs):
        calls.append(kwargs)
        return page([ROW], (11, 1, 1))
    monkeypatch.setattr(reader, "read_event_page", read)
    with pytest.raises(ValueError, match="预算"):
        fetch()
    assert len(calls) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_repeated_page_or_metadata_drift_rejected(monkeypatch, changed):
    def read(session, **kwargs):
        n = kwargs["page"]
        return page([ROW], (3 if changed and n == 2 else 2, 1, n))
    monkeypatch.setattr(reader, "read_event_page", read)
    with pytest.raises(ValueError, match="变化|重复"):
        fetch()


def test_network_failure_propagates(monkeypatch):
    def read(*args, **kwargs):
        raise requests.Timeout("fixture")
    monkeypatch.setattr(reader, "read_event_page", read)
    with pytest.raises(requests.Timeout):
        fetch()
