"""每手股数只读适配器：真实 SDK 调用形状用替身固定，不连接券商。"""

from types import SimpleNamespace
from unittest.mock import Mock
import json
import subprocess
import pandas as pd
import pytest

from data_provider.security_lot_size import LotSizeError, hk_symbol, positive_lot, read_security_lot_size
from data_provider.futu_lot_size import FutuLotSizeAdapter, RESULT_PREFIX, read_futu_static, validate_opend_endpoint


@pytest.mark.parametrize("value", ["00700.HK", "700.HK", "HK.00700"])
def test_hk_symbol(value):
    assert hk_symbol(value) == "00700.HK"


@pytest.mark.parametrize("value", ["AAPL.US", "00000.HK", "000700.HK", "", None])
def test_rejects_wrong_symbol(value):
    with pytest.raises(LotSizeError):
        hk_symbol(value)


@pytest.mark.parametrize("value", [0, -1, "NaN", "Infinity", True, None, "1.5", "1000000000000000"])
def test_rejects_invalid_lot(value):
    with pytest.raises(LotSizeError):
        positive_lot(value)


def sdk_for(rows, status=0):
    context = Mock()
    context.get_stock_basicinfo.return_value = status, pd.DataFrame(rows)
    sdk = SimpleNamespace(OpenQuoteContext=Mock(return_value=context), Market=SimpleNamespace(HK="HK"),
                          SecurityType=SimpleNamespace(STOCK="STOCK"), RET_OK=0)
    return sdk, context


def test_futu_queries_only_static_info_and_closes():
    sdk, ctx = sdk_for([{"code": "HK.00700", "stock_type": "STOCK", "lot_size": 100}])
    assert read_futu_static(sdk, "host.docker.internal", 11111, "00700.HK") == "100"
    ctx.get_stock_basicinfo.assert_called_once_with("HK", stock_type="STOCK", code_list=["HK.00700"])
    ctx.close.assert_called_once()


@pytest.mark.parametrize("rows,status", [
    ([], 0), ([{"code": "HK.00147", "stock_type": "STOCK", "lot_size": 100}], 0),
    ([{"code": "HK.00700", "stock_type": "ETF", "lot_size": 100}], 0),
    ([{"code": "HK.00700", "stock_type": "STOCK", "lot_size": 0}], 0), ([], -1),
])
def test_futu_closes_on_bad_result(rows, status):
    sdk, ctx = sdk_for(rows, status)
    with pytest.raises(LotSizeError):
        read_futu_static(sdk, "localhost", 11111, "00700.HK")
    ctx.close.assert_called_once()


def test_futu_closes_on_sdk_failure():
    sdk, ctx = sdk_for([])
    ctx.get_stock_basicinfo.side_effect = OSError("connection lost")
    with pytest.raises(OSError):
        read_futu_static(sdk, "localhost", 11111, "00700.HK")
    ctx.close.assert_called_once()


def test_futu_deadline(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 4.5
        raise subprocess.TimeoutExpired("futu", 4.5)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(LotSizeError, match="超时"):
        FutuLotSizeAdapter(host="localhost", port="11111").get_security_lot_size("00700.HK")


def test_futu_cache_keeps_original_observation(monkeypatch):
    payload = {"sharesPerLot": "100", "observedAt": "2026-10-08T10:00:00+00:00"}
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="SDK log\n" + RESULT_PREFIX + json.dumps(payload)))
    monkeypatch.setattr(subprocess, "run", run)
    adapter = FutuLotSizeAdapter(host="localhost", port="11111")
    first = adapter.get_security_lot_size("00700.HK")
    first["source"]["provider"] = "modified"
    second = adapter.get_security_lot_size("00700.HK")
    assert second["source"]["provider"] == "futu"
    assert second["source"]["observedAt"] == payload["observedAt"]
    run.assert_called_once()


@pytest.mark.parametrize("host,port", [("https://localhost", "11111"), ("localhost", "0"),
                                      ("user@host", "11111"), ("localhost", "65536")])
def test_bad_connection_config(host, port):
    with pytest.raises(LotSizeError):
        validate_opend_endpoint(host, port)


def test_longbridge_reuses_static_observation():
    info = SimpleNamespace(symbol="700.HK", board="HKEquity", lot_size=100)
    adapter = SimpleNamespace(_get_static_info=Mock(return_value=info), _static_cache={"700.HK": (info, 1000)})
    value = read_security_lot_size("longbridge", adapter, "00700.HK")
    assert value["sharesPerLot"] == "100"
    assert value["historicalChanges"] == "unverified"
    assert value["source"]["observedAt"].startswith("1970-01-01")
    adapter._get_static_info.assert_called_once_with("700.HK")


@pytest.mark.parametrize("info", [None, SimpleNamespace(symbol="147.HK", board="HKEquity", lot_size=100),
                                   SimpleNamespace(symbol="700.HK", board="HKWarrant", lot_size=100)])
def test_longbridge_rejects_missing_or_wrong_identity(info):
    with pytest.raises(LotSizeError):
        read_security_lot_size("longbridge", SimpleNamespace(_get_static_info=lambda _: info), "00700.HK")
