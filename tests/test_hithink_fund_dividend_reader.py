"""HiThink 基金分红端点的传输上限与失败关闭合同。"""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from data_provider.hithink_fund_dividend_reader import (
    DIVIDEND_URL, HiThinkFundDividendReadError, fetch_hithink_fund_dividends,
)
from data_provider.hithink_fund_dividends import normalize_hithink_fund_dividends


class Response:
    def __init__(self, body, status=200):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size):
        for position in range(0, len(self.body), 7):
            yield self.body[position:position + 7]

    def close(self):
        self.closed = True


PAYLOAD = {"code": 0, "data": {"timestamp": 1780000000000, "dividend_count": 1,
                               "item": [{"progress": "实施", "per_ten_cash_before_tax": 0.88}]}}


def read(response, *, symbol="510300.SH", **overrides):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    options = {"fund_type": "exchange", "api_key": "secret-test-key", "http_get": get,
               "clock": lambda: datetime(2026, 9, 28, tzinfo=timezone.utc)}
    result = fetch_hithink_fund_dividends(symbol, **{**options, **overrides})
    return result, calls


def test_single_precise_request_and_response_fingerprint():
    response = Response(PAYLOAD)
    result, calls = read(response)
    assert calls[0][0] == DIVIDEND_URL
    assert calls[0][1]["params"] == {"fund_type": "exchange", "thscode": "510300.SH"}
    assert calls[0][1]["headers"]["X-api-key"] == "secret-test-key"
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["stream"] is True
    assert result["responseSha256"] == hashlib.sha256(response.body).hexdigest()
    assert result["observedAt"] == "2026-09-28T00:00:00Z"
    assert result["fundType"] == "exchange"
    assert result["transportCountVerified"] is True
    assert result["historyComplete"] is False
    assert result["items"] == PAYLOAD["data"]["item"]
    assert response.closed


def test_missing_optional_count_never_claims_transport_count_proof():
    payload = {"code": 0, "data": {"item": []}}
    result, _ = read(Response(payload))
    assert result["transportCountVerified"] is False
    assert result["historyComplete"] is False


def test_otc_scope_is_explicit_and_keeps_full_fund_identity():
    result, calls = read(Response({"code": 0, "data": {"item": []}}),
                         symbol="000001.OF", fund_type="otc")
    assert calls[0][1]["params"] == {"fund_type": "otc", "thscode": "000001.OF"}
    assert result["symbol"] == "000001.OF"
    assert result["fundType"] == "otc"
    assert result["historyComplete"] is False


@pytest.mark.parametrize("status,code", [
    (401, "authentication_failed"), (403, "permission_denied"),
    (429, "rate_limited"), (503, "upstream_failure"), (302, "http_error"),
])
def test_http_failure_does_not_parse_or_retry(status, code):
    response = Response(b"not json", status)
    with pytest.raises(HiThinkFundDividendReadError) as error:
        read(response)
    assert error.value.code == code
    assert "secret-test-key" not in str(error.value)
    assert response.closed


@pytest.mark.parametrize("payload,code", [
    ({"code": 2003}, "permission_denied"),
    ({"code": 4001}, "rate_limited"),
    ({"code": 0, "data": {"dividend_count": 2, "item": [{}]}}, "count_mismatch"),
    ({"code": 0, "data": {"item": "wrong"}}, "invalid_response"),
    (b'{"code":0,"code":0,"data":{"item":[]}}', "invalid_response"),
])
def test_business_or_shape_failure(payload, code):
    response = Response(payload)
    with pytest.raises(HiThinkFundDividendReadError) as error:
        read(response)
    assert error.value.code == code
    assert response.closed


def test_response_byte_and_item_limits():
    with pytest.raises(HiThinkFundDividendReadError) as error:
        read(Response(PAYLOAD), max_bytes=20)
    assert error.value.code == "response_too_large"
    payload = {"code": 0, "data": {"item": [{}, {}]}}
    with pytest.raises(HiThinkFundDividendReadError) as error:
        read(Response(payload), max_items=1)
    assert error.value.code == "invalid_response"


def test_invalid_inputs_fail_before_request():
    def forbidden_get(*args, **kwargs):
        raise AssertionError("request must not happen")

    with pytest.raises(HiThinkFundDividendReadError) as error:
        fetch_hithink_fund_dividends("510300.SH", fund_type="exchange", api_key="", http_get=forbidden_get)
    assert error.value.code == "missing_credentials"
    with pytest.raises(HiThinkFundDividendReadError) as error:
        fetch_hithink_fund_dividends("510300", fund_type="exchange", api_key="secret", http_get=forbidden_get)
    assert error.value.code == "invalid_request"
    for symbol, fund_type in (("510300.SH", "otc"), ("000001.OF", "exchange"),
                              ("510300.SH", "reits")):
        with pytest.raises(HiThinkFundDividendReadError) as mismatch:
            fetch_hithink_fund_dividends(
                symbol, fund_type=fund_type, api_key="secret", http_get=forbidden_get,
            )
        assert mismatch.value.code == "invalid_request"


def test_transport_exception_is_sanitized_and_not_retried():
    calls = []

    def failing_get(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("secret-test-key in upstream URL")

    with pytest.raises(HiThinkFundDividendReadError) as error:
        fetch_hithink_fund_dividends(
            "510300.SH", fund_type="exchange", api_key="secret-test-key", http_get=failing_get,
        )
    assert error.value.code == "transport_error"
    assert "secret-test-key" not in str(error.value)
    assert len(calls) == 1


def test_read_items_feed_normalizer_without_claiming_history_coverage():
    item = {
        "progress": "实施", "per_ten_cash_before_tax": 0.8,
        "publish_date_ms": 1764547200000, "registration_date_ms": 1765152000000,
        "ex_dividend_date_ms": 1765238400000, "payment_date_ms": 1765324800000,
    }
    observed, _ = read(Response({"code": 0, "data": {"dividend_count": 1, "item": [item]}}))
    normalized = normalize_hithink_fund_dividends(
        observed["items"], observed["symbol"], instrument_type="ETF", currency="CNY",
        start="2025-12-01", end="2025-12-31",
        observed_at=datetime.fromisoformat(observed["observedAt"].replace("Z", "+00:00")),
        provider_revision="fixture-v1",
    )
    assert normalized["facts"][0]["cashAmount"] == "0.08"
    assert normalized["coverage"]["complete"] is False
    assert observed["historyComplete"] is False
