import pandas as pd
import pytest

from data_provider.fund_holdings_source import read_recent_fund_holdings


def frame(period="2026年2季度", code="600519", weight=8):
    return pd.DataFrame(
        {
            "股票代码": [code],
            "股票名称": ["贵州茅台"],
            "占净值比例": [weight],
            "季度": [period],
        }
    )


def test_empty_current_year_falls_back_and_keeps_query_evidence():
    calls = []

    def fetch(symbol, year):
        calls.append((symbol, year))
        if year == "2026":
            return pd.DataFrame()
        return frame("2025年4季度")

    result = read_recent_fund_holdings(
        "000001",
        current_year=2026,
        fetch_year=fetch,
        observed_at="2026-09-28T10:00:00+00:00",
    )

    assert calls == [("000001", "2026"), ("000001", "2025")]
    assert result.attrs == {
        "sourceEndpoint": "akshare/eastmoney:fund_portfolio_hold_em",
        "sourceQueryYear": 2025,
        "sourceObservedAt": "2026-09-28T10:00:00+00:00",
    }


def test_current_year_success_does_not_probe_previous_year():
    calls = []

    def fetch(symbol, year):
        calls.append((symbol, year))
        return frame("2026年1季度")

    result = read_recent_fund_holdings(
        "000001",
        current_year=2026,
        fetch_year=fetch,
        observed_at="2026-09-28T10:00:00+00:00",
    )

    assert calls == [("000001", "2026")]
    assert result.attrs["sourceQueryYear"] == 2026


def test_nonempty_wrong_year_fails_closed_without_fallback():
    calls = []

    def fetch(symbol, year):
        calls.append((symbol, year))
        return frame("2025年4季度")

    with pytest.raises(ValueError, match="fund_holdings_query_year_mismatch"):
        read_recent_fund_holdings(
            "000001",
            current_year=2026,
            fetch_year=fetch,
            observed_at="2026-09-28T10:00:00+00:00",
        )

    assert calls == [("000001", "2026")]


@pytest.mark.parametrize(
    "bad_frame",
    [
        frame(period="未知"),
        frame(code=""),
        frame(weight=101),
    ],
)
def test_nonempty_invalid_rows_fail_closed_without_fallback(bad_frame):
    calls = []

    def fetch(symbol, year):
        calls.append((symbol, year))
        return bad_frame

    with pytest.raises(ValueError):
        read_recent_fund_holdings(
            "000001",
            current_year=2026,
            fetch_year=fetch,
            observed_at="2026-09-28T10:00:00+00:00",
        )

    assert calls == [("000001", "2026")]


def test_akshare_fetcher_uses_year_evidence_helper(monkeypatch):
    import sys
    from types import SimpleNamespace

    from data_provider import akshare_fetcher as module
    from data_provider.akshare_fetcher import AkshareFetcher

    calls = []

    def holdings(symbol, date):
        calls.append((symbol, date))
        if date == "2026":
            return pd.DataFrame()
        return frame("2025年4季度")

    class FixedDateTime:
        @classmethod
        def now(cls, *_args, **_kwargs):
            return SimpleNamespace(year=2026)

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_portfolio_hold_em=holdings),
    )
    monkeypatch.setattr(module, "datetime", FixedDateTime)

    result = AkshareFetcher(sleep_min=0, sleep_max=0).get_fund_holdings("000001")

    assert calls == [("000001", "2026"), ("000001", "2025")]
    assert result.attrs["sourceQueryYear"] == 2025
    assert result.attrs["sourceEndpoint"] == "akshare/eastmoney:fund_portfolio_hold_em"
