"""目录集合、完整身份和失败时连接回收。"""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pandas as pd
import pytest

from data_provider.futu_catalog import COLLECTIONS, read_catalog
from data_provider.futu_market import FutuMarketError


def sdk_for():
    codes = ("HK.00700", "HK.02800", "US.AAPL", "US.SPY")
    frames = [pd.DataFrame([{"code": code, "name": code, "stock_type": asset, "delisting": False}])
              for code, (_, asset) in zip(codes, COLLECTIONS)]
    context = Mock()
    context.get_stock_basicinfo.side_effect = [(0, frame) for frame in frames]
    sdk = SimpleNamespace(OpenQuoteContext=Mock(return_value=context), RET_OK=0,
                          Market=SimpleNamespace(HK="HK", US="US"),
                          SecurityType=SimpleNamespace(STOCK="STOCK", ETF="ETF"))
    return sdk, context, frames


def test_all_collections_use_source_market_and_type_once():
    sdk, context, _ = sdk_for()
    items = read_catalog(sdk, "localhost", 11111)
    assert [(i["canonicalCode"], i["market"], i["instrumentType"]) for i in items] == [
        ("00700", "HK", "STOCK"), ("02800", "HK", "ETF"),
        ("AAPL", "US", "STOCK"), ("SPY", "US", "ETF")]
    assert context.get_stock_basicinfo.call_args_list == [
        call(market, stock_type=asset) for market, asset in COLLECTIONS]
    context.close.assert_called_once()


@pytest.mark.parametrize("changes", [
    {"code": "SH.00700"}, {"code": "HK.700"}, {"code": "HK.00000"},
    {"stock_type": "ETF"}, {"name": " "}, {"delisting": None}, {"delisting": "false"},
])
def test_invalid_identity_rejects_entire_result(changes):
    sdk, context, frames = sdk_for()
    for key, value in changes.items():
        frames[0][key] = value
    with pytest.raises(FutuMarketError, match="完整性"):
        read_catalog(sdk, "localhost", 11111)
    assert context.get_stock_basicinfo.call_count == 1
    context.close.assert_called_once()


def test_validation_diagnostic_contains_only_collection_field_and_counts():
    sdk, context, frames = sdk_for()
    frames[0]["code"] = "HK.0\n00"
    with pytest.raises(FutuMarketError) as error:
        read_catalog(sdk, "localhost", 11111)
    text = str(error.value)
    assert "HK/STOCK" in text and "代码格式" in text and "rows=1" in text and "row=1" in text
    assert "HK.0\n00" not in text and "localhost" not in text


def test_native_outside_supported_identity_is_counted_without_guessing(caplog):
    sdk, context, frames = sdk_for()
    frames[0] = pd.concat([frames[0], pd.DataFrame([
        {"code": "HK.123456", "name": "来源记录", "stock_type": "STOCK", "delisting": False},
        {"code": "HK.00700A", "name": "来源记录", "stock_type": "STOCK", "delisting": True},
    ])])
    context.get_stock_basicinfo.side_effect = [(0, f) for f in frames]
    items = read_catalog(sdk, "localhost", 11111)
    assert len(items) == 4 and items[0]["canonicalCode"] == "00700"
    assert "rows=3 included=1 delisted=1 unsupported=1" in caplog.text


def test_unsupported_native_code_does_not_hide_wrong_type():
    sdk, context, frames = sdk_for()
    frames[0] = pd.concat([frames[0], pd.DataFrame([
        {"code": "HK.123456", "name": "来源记录", "stock_type": "ETF", "delisting": False},
    ])])
    context.get_stock_basicinfo.side_effect = [(0, f) for f in frames]
    with pytest.raises(FutuMarketError, match="证券类型"):
        read_catalog(sdk, "localhost", 11111)


def test_opaque_us_identifiers_do_not_expand_product_symbol_contract(caplog):
    sdk, context, frames = sdk_for()
    frames[2] = pd.concat([frames[2], pd.DataFrame([
        {"code": "US.ABC^P", "name": "来源记录", "stock_type": "STOCK", "delisting": False},
        {"code": "US.A/PR", "name": "来源记录", "stock_type": "STOCK", "delisting": False},
        {"code": "US.BRK.B", "name": "类别股", "stock_type": "STOCK", "delisting": False},
    ])])
    context.get_stock_basicinfo.side_effect = [(0, f) for f in frames]
    items = read_catalog(sdk, "localhost", 11111)
    assert {i["canonicalCode"] for i in items if i["market"] == "US"} == {"AAPL", "BRK.B", "SPY"}
    assert "rows=4 included=2 delisted=0 unsupported=2" in caplog.text


@pytest.mark.parametrize("code", ["US.AAPL", "HK.", "HK. ", "HK." + "X" * 65, "HK.A\x00B"])
def test_invalid_native_boundary_is_not_silently_excluded(code):
    sdk, context, frames = sdk_for()
    frames[0]["code"] = code
    with pytest.raises(FutuMarketError, match="原生代码"):
        read_catalog(sdk, "localhost", 11111)


@pytest.mark.parametrize("kind", ["empty", "duplicate", "missing", "late-error", "late-type", "cross-type"])
def test_incomplete_or_conflicting_collection_never_returns_partial(kind):
    sdk, context, frames = sdk_for()
    if kind == "empty":
        frames[3] = frames[3].iloc[:0]
    elif kind == "duplicate":
        frames[3] = pd.concat([frames[3], frames[3]])
    elif kind == "missing":
        frames[3] = frames[3].drop(columns=["delisting"])
    elif kind == "late-type":
        frames[3]["stock_type"] = "STOCK"
    elif kind == "cross-type":
        frames[3]["code"] = "US.AAPL"
    context.get_stock_basicinfo.side_effect = [(0, f) for f in frames]
    if kind == "late-error":
        context.get_stock_basicinfo.side_effect = [(0, f) for f in frames[:3]] + [(-1, "denied")]
    with pytest.raises(FutuMarketError):
        read_catalog(sdk, "localhost", 11111)
    assert context.get_stock_basicinfo.call_count == 4
    context.close.assert_called_once()


def test_delisted_records_are_excluded_after_validation():
    sdk, context, frames = sdk_for()
    frames[2] = pd.concat([frames[2], pd.DataFrame([{
        "code": "US.OLD", "name": "旧股票", "stock_type": "STOCK", "delisting": True}])])
    context.get_stock_basicinfo.side_effect = [(0, f) for f in frames]
    assert len(read_catalog(sdk, "localhost", 11111)) == 4


def test_sdk_exception_stops_without_retry_and_closes():
    sdk, context, _ = sdk_for()
    context.get_stock_basicinfo.side_effect = RuntimeError("synthetic failure")
    with pytest.raises(RuntimeError):
        read_catalog(sdk, "localhost", 11111)
    assert context.get_stock_basicinfo.call_count == 1
    context.close.assert_called_once()
