"""分红精确读取的单次调用、预算、金额精度及未知历史覆盖。"""

from unittest.mock import Mock, patch

import pandas as pd
import pytest

from data_provider.tushare_fetcher import TushareFetcher
from data_provider.tushare_fund_dividend_reader import FIELDS, fetch_tushare_fund_dividends


def frame():
    return pd.DataFrame([{
        "ts_code": "159516.SZ", "ann_date": "20260601", "imp_anndate": "20260602", "div_proc": "实施",
        "record_date": "20260619", "ex_date": "20260622", "pay_date": "20260624", "div_cash": "0.015",
    }])


def read(api, **changes):
    args = dict(symbol="159516.SZ", instrument_type="ETF", currency="CNY", start="2026-06-20",
                end="2026-06-30", before_call=Mock())
    args.update(changes)
    return fetch_tushare_fund_dividends(api, **args)


def test_request_uses_symbol_without_announcement_filter_and_never_claims_pagination_complete():
    api, before = Mock(), Mock()
    api.fund_div.return_value = frame()
    result = read(api, before_call=before)
    api.fund_div.assert_called_once_with(ts_code="159516.SZ", fields=FIELDS)
    before.assert_called_once()
    assert len(result["facts"]) == 1
    assert result["coverage"]["complete"] is False
    assert result["retrieval"]["upstreamPaginationVerified"] is False
    assert result["retrieval"]["upstreamDataRevision"] is None
    assert result["retrieval"]["requestCount"] == 1
    assert result["providerRevision"] == result["facts"][0]["providerRevision"]


@pytest.mark.parametrize("budget", [0, True, 10001])
def test_invalid_budget_rejects_before_request(budget):
    api, before = Mock(), Mock()
    with pytest.raises(ValueError):
        read(api, before_call=before, maximum_rows=budget)
    api.fund_div.assert_not_called()
    before.assert_not_called()


def test_empty_window_retains_response_revision_without_claiming_coverage():
    api = Mock()
    api.fund_div.return_value = frame()
    result = read(api, start="2026-07-01", end="2026-07-31")
    assert result["facts"] == []
    assert result["providerRevision"] == (
        "tushare-fund-div-content-v1:" + result["retrieval"]["contentFingerprint"]
    )
    assert result["coverage"]["complete"] is False


def test_empty_source_response_has_repeatable_content_revision():
    api = Mock()
    api.fund_div.return_value = frame().iloc[:0]
    first, second = read(api), read(api)
    assert first["facts"] == second["facts"] == []
    assert first["providerRevision"] == second["providerRevision"]
    assert first["providerRevision"].startswith("tushare-fund-div-content-v1:")
    assert first["coverage"]["complete"] is False


def test_outside_window_source_revision_change_still_changes_response_revision():
    api = Mock()
    api.fund_div.return_value = frame()
    first = read(api, start="2026-07-01", end="2026-07-31")
    api.fund_div.return_value.loc[0, "div_cash"] = "0.02"
    second = read(api, start="2026-07-01", end="2026-07-31")
    assert first["facts"] == second["facts"] == []
    assert first["providerRevision"] != second["providerRevision"]


def test_oversized_response_is_rejected_instead_of_truncated():
    api = Mock()
    api.fund_div.return_value = pd.concat([frame(), frame()])
    with pytest.raises(ValueError, match="budget"):
        read(api, maximum_rows=1)
    api.fund_div.assert_called_once()


def test_permission_or_transport_failure_does_not_retry_or_fallback():
    api = Mock()
    api.fund_div.side_effect = PermissionError("fixture denied")
    with pytest.raises(PermissionError):
        read(api)
    api.fund_div.assert_called_once()
    api.fund_daily.assert_not_called()
    api.dividend.assert_not_called()


def test_nullable_dates_have_stable_content_fingerprint():
    api = Mock()
    value = frame()
    value["record_date"] = None
    value["pay_date"] = float("nan")
    api.fund_div.return_value = value
    first = read(api)
    second = read(api)
    assert first["retrieval"]["contentFingerprint"] == second["retrieval"]["contentFingerprint"]
    assert "paymentDate" not in first["facts"][0]


def test_exact_http_entry_retains_decimal_cash_without_aliasing_or_other_endpoints():
    fetcher = TushareFetcher(token="fixture-token", http_url="https://fixture.example/api")
    response = Mock(status_code=200, text='{"code":0,"data":{"fields":["ts_code","ann_date","imp_anndate",'
                    '"div_proc","record_date","ex_date","pay_date","div_cash"],'
                    '"items":[["159516.SZ","20260601","20260602","实施","20260619","20260622",'
                    '"20260624",0.0151234567890123456789]]}}')
    with patch("data_provider.tushare_fetcher.requests.post", return_value=response) as post:
        result = fetcher.get_fund_dividends_for_source(
            "159516.SZ", "tushare", instrument_type="ETF", currency="CNY", start_date="2026-06-20", end_date="2026-06-30",
        )
    assert result["facts"][0]["cashAmount"] == "0.0151234567890123456789"
    assert post.call_args.kwargs["json"]["api_name"] == "fund_div"
    assert post.call_args.kwargs["allow_redirects"] is False
    assert fetcher._call_count == 1


def test_exact_source_redirect_is_rejected_without_following_another_endpoint():
    fetcher = TushareFetcher(token="fixture-token", http_url="https://fixture.example/api")
    redirect = Mock(status_code=307, text="", headers={"Location": "https://other.example/api"})
    with patch("data_provider.tushare_fetcher.requests.post", return_value=redirect) as post:
        with pytest.raises(Exception, match="HTTP 307"):
            fetcher.get_fund_dividends_for_source(
                "159516.SZ", "tushare", instrument_type="ETF", currency="CNY",
                start_date="2026-06-20", end_date="2026-06-30",
            )
    post.assert_called_once()
    assert post.call_args.kwargs["allow_redirects"] is False


def test_late_response_is_not_returned_as_success():
    fetcher = TushareFetcher(token="fixture-token", http_url="https://fixture.example/api")
    with patch("data_provider.tushare_history_request.time.monotonic", side_effect=[0, 0, 1, 16]), \
            patch("data_provider.tushare_fund_dividend_reader.fetch_tushare_fund_dividends") as retrieve:
        def fake(*args, before_call, **kwargs):
            before_call()
            return {"facts": []}
        retrieve.side_effect = fake
        with pytest.raises(TimeoutError):
            fetcher.get_fund_dividends_for_source(
                "159516.SZ", "tushare", instrument_type="ETF", currency="CNY",
                start_date="2026-06-20", end_date="2026-06-30",
            )
