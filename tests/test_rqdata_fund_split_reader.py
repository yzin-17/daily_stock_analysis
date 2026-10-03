"""拆分读取的精确查询、预算、日期指纹和来源失败边界。"""

from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider.rqdata_fund_split_reader import fetch_rqdata_fund_splits


def client():
    api = Mock()
    api.fund.get_split.return_value = pd.DataFrame(
        {"split_ratio": ["1.25"]}, index=pd.to_datetime(["2025-06-01"]),
    )
    return api


def read(api, **changes):
    args = dict(query_fund_code="000246", instrument_type="NAV_FUND", start="2025-01-01", end="2025-12-31",
                before_call=Mock(), after_call=Mock())
    args.update(changes)
    return fetch_rqdata_fund_splits(api, "000246.OF", **args)


def test_exact_query_and_observed_response_revision():
    api, before, after = client(), Mock(), Mock()
    result = read(api, before_call=before, after_call=after)
    api.fund.get_split.assert_called_once_with("000246", market="cn")
    before.assert_called_once()
    assert after.call_count == 2
    assert result["providerRevision"] == result["facts"][0]["providerRevision"]
    assert result["coverage"]["complete"] is False
    assert result["retrieval"]["upstreamPaginationVerified"] is False


def test_index_dates_participate_in_content_revision():
    api = client()
    first = read(api)
    api.fund.get_split.return_value.index = pd.to_datetime(["2025-06-02"])
    second = read(api)
    assert first["providerRevision"] != second["providerRevision"]


@pytest.mark.parametrize("changes", [
    {"maximum_rows": True}, {"maximum_rows": 0}, {"query_fund_code": ""},
    {"before_call": None}, {"after_call": None},
])
def test_invalid_input_never_requests(changes):
    api = client()
    with pytest.raises(ValueError):
        read(api, **changes)
    api.fund.get_split.assert_not_called()


def test_oversized_response_is_not_silently_truncated():
    api = client()
    api.fund.get_split.return_value = pd.concat([api.fund.get_split.return_value] * 2)
    with pytest.raises(ValueError, match="budget"):
        read(api, maximum_rows=1)


def test_upstream_failure_never_retries_or_uses_stock_endpoint():
    api = client()
    api.fund.get_split.side_effect = PermissionError("fixture denied")
    with pytest.raises(PermissionError):
        read(api)
    api.fund.get_split.assert_called_once()
    api.get_split.assert_not_called()


def test_expired_budget_discards_late_response():
    with pytest.raises(TimeoutError):
        read(client(), after_call=Mock(side_effect=TimeoutError))


def test_empty_response_has_stable_revision_and_unknown_coverage():
    api = client()
    api.fund.get_split.return_value = api.fund.get_split.return_value.iloc[:0]
    first, second = read(api), read(api)
    assert first["facts"] == []
    assert first["providerRevision"] == second["providerRevision"]
    assert first["coverage"]["complete"] is False


def test_conflicting_index_and_column_dates_rejected():
    api = client()
    api.fund.get_split.return_value["ex_dividend_date"] = "2025-06-02"
    with pytest.raises(ValueError, match="conflicting date"):
        read(api)
