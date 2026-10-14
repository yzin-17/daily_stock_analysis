"""从历史适配器的传输边界核对错误含义和安全说明。"""
import pytest

from src.services.thesis_ledger_hithink_stock import HiThinkStockError, HiThinkStockHistoricalAdapter


@pytest.mark.parametrize("code,message,expected,retryable", [
    (1002, "Unknown thscode: 600112.SH", "symbol_not_found", False),
    (1002, "Unknown thscode: 600221.SH", "invalid_request", False),
    (1002, "invalid interval", "invalid_request", False),
    (1002, None, "invalid_request", False),
    (3001, "untrusted upstream text", "symbol_not_found", False),
    (3002, "untrusted upstream text", "data_not_ready", True),
    (9012, "untrusted upstream text", "provider_error", False),
])
def test_transport_business_error_keeps_requested_identity_and_original_failures(
    code, message, expected, retryable,
):
    secret = "only-a-test-secret"
    payload = {"code": code, "message": message, "debug": secret}
    response = type("Response", (), {"status_code": 200, "json": lambda self: payload})()
    adapter = HiThinkStockHistoricalAdapter(secret, http_get=lambda *args, **kwargs: response)
    with pytest.raises(HiThinkStockError) as caught:
        adapter.fetch_daily_bars("600112.SH", "2024-07-13", "2024-08-23", "qfq")
    assert caught.value.code == expected
    assert caught.value.retryable == retryable
    assert secret not in str(caught.value)
    assert "untrusted upstream text" not in str(caught.value)


def test_unknown_symbol_message_with_sensitive_suffix_is_not_forwarded():
    secret = "only-a-test-secret"
    payload = {"code": 1002, "message": f"Unknown thscode: 600112.SH {secret}"}
    response = type("Response", (), {"status_code": 200, "json": lambda self: payload})()
    adapter = HiThinkStockHistoricalAdapter(secret, http_get=lambda *args, **kwargs: response)
    with pytest.raises(HiThinkStockError) as caught:
        adapter.fetch_daily_bars("600112.SH", "2024-07-13", "2024-08-23", "qfq")
    assert caught.value.code == "invalid_request"
    assert secret not in str(caught.value)
