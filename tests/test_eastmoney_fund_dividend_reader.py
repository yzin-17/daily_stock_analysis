"""基金分红分页预算、完整性、脚本解析及错误传播。"""

import json

import pytest
import requests

from data_provider import eastmoney_fund_dividend_reader as reader


ROW = ["510300", "基金", "2025-06-17", "2025-06-18", "0.088", "2025-06-27", ""]


def page(number=1, total=1, rows=None, size=1):
    return f"var pageinfo = {json.dumps([total, size, number])}; var jjfh_data={json.dumps(rows or [ROW])};"


def fetch():
    return reader.fetch_fund_dividend_observations(
        "510300.SH", start="2025-06-01", end="2025-06-30", max_pages=2,
    )


def test_pages_are_collected_with_observation_provenance(monkeypatch):
    calls = []

    def read(_session, year, number):
        calls.append((year, number))
        rows = [ROW] if number == 1 else [["159516", *ROW[1:]]]
        return page(number, 2, rows)

    monkeypatch.setattr(reader, "_read_page", read)
    result = fetch()
    assert calls == [(2025, 1), (2025, 2)]
    assert len(result["facts"]) == 1
    assert result["facts"][0]["cashAmount"] == "0.088"
    assert result["retrieval"]["paginationComplete"] is True
    assert result["coverage"]["complete"] is False
    assert len(result["retrieval"]["pages"]) == 2


def test_budget_rejects_before_requesting_excess_pages(monkeypatch):
    calls = []

    def read(_session, year, number):
        calls.append(number)
        return page(total=75)

    monkeypatch.setattr(reader, "_read_page", read)
    with pytest.raises(ValueError, match="预算"):
        fetch()
    assert calls == [1]


@pytest.mark.parametrize("response", [
    "var pageinfo=[1,1,1]; var jjfh_data=__import__('os').system('echo unsafe');",
    "var pageinfo=[1,1,1]; var jjfh_data=[]; var jjfh_data=[];",
    "var pageinfo=[2,100,1]; var jjfh_data=[];",
    "var pageinfo=[1,1,true]; var jjfh_data=[];",
    "var pageinfo=[1,1,1]; var jjfh_data=[[1,2]];",
    "var pageinfo=[1,1,2]; var jjfh_data=[];",
])
def test_invalid_or_executable_payload_is_rejected(response):
    with pytest.raises(ValueError):
        reader.parse_dividend_page(response, 1)


@pytest.mark.parametrize("changed", [False, True])
def test_repeated_page_or_changed_total_is_rejected(monkeypatch, changed):
    monkeypatch.setattr(reader, "_read_page", lambda _s, _y, n:
                        page(n, 3 if changed and n == 2 else 2))
    with pytest.raises(ValueError, match="变化|重复"):
        fetch()


def test_network_failure_is_not_empty_success(monkeypatch):
    def read(*_args):
        raise requests.Timeout("fixture")

    monkeypatch.setattr(reader, "_read_page", read)
    with pytest.raises(requests.Timeout):
        fetch()


@pytest.mark.parametrize("oversize", [False, True])
def test_transport_limits_response_and_disables_redirects(monkeypatch, oversize):
    monkeypatch.setattr(reader, "MAX_RESPONSE_BYTES", 200)
    payload = b"x" * 201 if oversize else page().encode()

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield payload

    class Session:
        def get(self, url, **kwargs):
            assert url == reader.ENDPOINT
            assert kwargs["timeout"] == (5, 15)
            assert kwargs["allow_redirects"] is False
            assert kwargs["stream"] is True
            assert kwargs["params"]["page"] == "1"
            return Response()

    if oversize:
        with pytest.raises(ValueError, match="上限"):
            reader._read_page(Session(), 2025, 1)
    else:
        assert reader._read_page(Session(), 2025, 1) == payload.decode()
