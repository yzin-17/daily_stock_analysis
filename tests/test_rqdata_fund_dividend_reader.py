"""分红读取独立能力、内容版本和请求预算。"""

from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider.rqdata_fund_dividend_reader import fetch_rqdata_fund_dividends


def client():
    api = Mock()
    api.fund.get_dividend.return_value = pd.DataFrame({
        "book_closure_date": ["2025-06-01"], "payable_date": ["2025-06-03"], "dividend_before_tax": ["0.012"],
    }, index=pd.to_datetime(["2025-06-02"]))
    return api


def read(api, **changes):
    args = dict(query_fund_code="050116", instrument_type="NAV_FUND", currency="CNY",
                start="2025-01-01", end="2025-12-31", before_call=Mock(), after_call=Mock())
    args.update(changes)
    return fetch_rqdata_fund_dividends(api, "050116.OF", **args)


def test_exact_fund_endpoint_preserves_amount_and_revision():
    api = client()
    result = read(api)
    api.fund.get_dividend.assert_called_once_with("050116", market="cn")
    api.fund.get_split.assert_not_called()
    api.get_dividend.assert_not_called()
    assert result["facts"][0]["cashAmount"] == "0.012"
    assert result["providerRevision"] == result["facts"][0]["providerRevision"]
    assert result["coverage"]["complete"] is False


@pytest.mark.parametrize("changes", [{"currency": None}, {"maximum_rows": True}, {"maximum_rows": 0},
                                     {"query_fund_code": ""}, {"after_call": None}])
def test_preflight_failure_never_calls_provider(changes):
    api = client()
    with pytest.raises(ValueError):
        read(api, **changes)
    api.fund.get_dividend.assert_not_called()


def test_late_result_rejected():
    with pytest.raises(TimeoutError):
        read(client(), after_call=Mock(side_effect=TimeoutError))


def test_oversized_response_rejected():
    api = client()
    api.fund.get_dividend.return_value = pd.concat([api.fund.get_dividend.return_value] * 2)
    with pytest.raises(ValueError, match="budget"):
        read(api, maximum_rows=1)


def test_upstream_failure_not_retried():
    api = client()
    api.fund.get_dividend.side_effect = PermissionError("fixture denied")
    with pytest.raises(PermissionError):
        read(api)
    api.fund.get_dividend.assert_called_once()


def test_empty_response_retains_version():
    api = client()
    api.fund.get_dividend.return_value = api.fund.get_dividend.return_value.iloc[:0]
    result = read(api)
    assert result["facts"] == []
    assert result["providerRevision"].startswith("rqdata-fund-dividend-content-v1:")
    assert result["retrieval"]["upstreamPaginationVerified"] is False


def test_index_date_change_changes_revision():
    api = client()
    before = read(api)
    api.fund.get_dividend.return_value.index = pd.to_datetime(["2025-06-03"])
    assert read(api)["providerRevision"] != before["providerRevision"]
