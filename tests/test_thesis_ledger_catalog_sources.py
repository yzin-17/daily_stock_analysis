"""目录来源字段与资产身份回归；全部使用明确离线响应。"""

import sys
from types import SimpleNamespace

import pandas as pd
import pytest
from src.services import thesis_ledger_catalog as catalog

from src.services.thesis_ledger_catalog import (
    CatalogBuildError, _akshare_catalog, _normalize_frame,
)


@pytest.mark.parametrize("code,market", [
    ("920123", "BJ"), ("837023", "BJ"), ("430047", "BJ"),
    ("600519", "SH"), ("900901", "SH"), ("000001", "SZ"),
])
def test_stock_catalog_market_identity(code, market):
    result = _normalize_frame(pd.DataFrame({"code": [code], "name": ["目录样本"]}),
                              instrument_type="STOCK")
    assert result == [{"canonicalCode": code, "displayName": "目录样本",
                       "market": market, "instrumentType": "STOCK"}]


def test_akshare_endpoints_keep_asset_contracts(monkeypatch):
    calls = []

    def frame(endpoint, value):
        calls.append(endpoint)
        return pd.DataFrame(value)

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_info_a_code_name=lambda: frame("stock_info_a_code_name", {"code": ["920123"], "name": ["股票样本"]}),
        fund_etf_spot_em=lambda: frame("fund_etf_spot_em", {"代码": ["159516"], "名称": ["ETF样本"]}),
        fund_name_em=lambda: frame("fund_name_em", {"基金代码": ["000001"], "基金简称": ["基金样本"]}),
    ))
    def etf_catalog():
        calls.append("sina_etf_catalog")
        return {'rows': [{'canonicalCode': '159516', 'displayName': 'ETF样本',
                          'instrumentType': 'ETF', 'market': 'SZ'}]}

    monkeypatch.setattr(catalog, 'read_sina_etf_catalog', etf_catalog)
    result = _akshare_catalog()
    assert calls == ["stock_info_a_code_name", "sina_etf_catalog", "fund_name_em"]
    assert [(item["canonicalCode"], item["instrumentType"], item["market"]) for item in result] == [
        ("920123", "STOCK", "BJ"), ("159516", "ETF", "SZ"), ("000001", "MUTUAL_FUND", "OF"),
    ]


def test_missing_fields_are_not_an_empty_success():
    with pytest.raises(CatalogBuildError) as error:
        _normalize_frame(pd.DataFrame({"code": ["600519"]}), instrument_type="STOCK")
    assert error.value.code == "catalog_provider_invalid_response"
    assert _normalize_frame(pd.DataFrame(), instrument_type="STOCK") == []


def test_efinance_stock_endpoint_owns_asset_type(monkeypatch):
    monkeypatch.setitem(sys.modules, "efinance", SimpleNamespace(
        stock=SimpleNamespace(get_realtime_quotes=lambda: pd.DataFrame({
            "股票代码": ["920123", "600519", "000001"],
            "股票名称": ["北交所样本", "沪市样本", "深市样本"],
        })),
        fund=SimpleNamespace(get_realtime_quotes=lambda: pd.DataFrame()),
    ))
    result = catalog._efinance_catalog()
    assert [(row["instrumentType"], row["market"]) for row in result] == [
        ("STOCK", "BJ"), ("STOCK", "SH"), ("STOCK", "SZ"),
    ]


def test_catalog_requires_explicit_asset_type():
    with pytest.raises(TypeError):
        _normalize_frame(pd.DataFrame({"code": ["159516"], "name": ["名称"]}))


@pytest.mark.parametrize("code,name", [
    (None, "缺代码"), ("", "空代码"), ("000000", "无效代码"),
    ("６００５１９", "非ASCII"), (True, "布尔值"),
    ("600519", float("nan")), ("600519", pd.NA), ("600519", "  "),
])
def test_invalid_identity_never_becomes_partial_or_synthetic_catalog(code, name):
    frame = pd.DataFrame({"code": ["000001", code], "name": ["有效项", name]}, dtype=object)
    with pytest.raises(CatalogBuildError) as error:
        _normalize_frame(frame, instrument_type="STOCK")
    assert error.value.code == "catalog_provider_invalid_response"


def test_identical_provider_duplicates_are_idempotent(monkeypatch):
    monkeypatch.setattr(catalog, "_bounded_call", lambda operation, **_kwargs: operation())
    item = {"canonicalCode": "920123", "market": "BJ", "instrumentType": "STOCK", "displayName": "样本"}
    items, failures = catalog.build_catalog({"one": lambda: [item, dict(item)]})
    assert items == [item]
    assert failures == {}


def test_conflicting_provider_is_rejected_atomically(monkeypatch):
    monkeypatch.setattr(catalog, "_bounded_call", lambda operation, **_kwargs: operation())
    item = {"canonicalCode": "920123", "market": "BJ", "instrumentType": "STOCK", "displayName": "样本"}
    unrelated = {**item, "canonicalCode": "600519", "market": "SH"}
    fallback = {**item, "canonicalCode": "000001", "market": "SZ"}
    items, failures = catalog.build_catalog({
        "broken": lambda: [unrelated, item, {**item, "displayName": "冲突名称"}],
        "healthy": lambda: [fallback],
    })
    assert items == [fallback]
    assert failures == {"broken": "catalog_provider_invalid_response"}


def test_cross_provider_precedence_remains_explicit_loader_order(monkeypatch):
    monkeypatch.setattr(catalog, "_bounded_call", lambda operation, **_kwargs: operation())
    item = {"canonicalCode": "920123", "market": "BJ", "instrumentType": "STOCK", "displayName": "首源名称"}
    items, failures = catalog.build_catalog({"first": lambda: [item], "second": lambda: [{**item, "displayName": "次源名称"}]})
    assert items == [item]
    assert failures == {}


def test_efinance_missing_fund_sdk_uses_explicit_open_fund_catalog(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "efinance", SimpleNamespace(
        stock=SimpleNamespace(get_realtime_quotes=lambda: pd.DataFrame({
            "股票代码": ["600519"],
            "股票名称": ["股票样本"],
        })),
    ))

    def read_catalog(**kwargs):
        calls.append(kwargs)
        return {
            "rows": (("000001", "开放基金甲"), ("000002", "开放基金乙")),
            "retrieval": {"protocol": "eastmoney-rankhandler-pages-v1"},
            "contentFingerprint": "a" * 64,
            "observedAt": "2026-09-28T10:00:00+00:00",
        }

    monkeypatch.setattr(catalog, "read_fund_catalog", read_catalog, raising=False)
    result = catalog._efinance_catalog()

    assert calls == [{"timeout_seconds": 45}]
    assert result == [
        {
            "canonicalCode": "600519",
            "instrumentType": "STOCK",
            "market": "SH",
            "displayName": "股票样本",
        },
        {
            "canonicalCode": "000001",
            "instrumentType": "MUTUAL_FUND",
            "market": "OF",
            "displayName": "开放基金甲",
        },
        {
            "canonicalCode": "000002",
            "instrumentType": "MUTUAL_FUND",
            "market": "OF",
            "displayName": "开放基金乙",
        },
    ]


@pytest.mark.parametrize(
    "error,code,retryable",
    [
        (ValueError("bad page"), "catalog_provider_invalid_response", False),
        (TimeoutError("catalog_deadline"), "catalog_provider_timeout", True),
    ],
)
def test_efinance_open_fund_catalog_maps_reader_failures(
    monkeypatch, error, code, retryable
):
    monkeypatch.setitem(sys.modules, "efinance", SimpleNamespace())

    def fail_reader(**_kwargs):
        raise error

    monkeypatch.setattr(catalog, "read_fund_catalog", fail_reader, raising=False)
    with pytest.raises(CatalogBuildError) as raised:
        catalog._efinance_catalog()

    assert raised.value.code == code
    assert raised.value.retryable is retryable


def test_efinance_native_fund_catalog_remains_preferred(monkeypatch):
    monkeypatch.setitem(sys.modules, "efinance", SimpleNamespace(
        fund=SimpleNamespace(get_realtime_quotes=lambda: pd.DataFrame({
            "基金代码": ["000001"],
            "基金简称": ["SDK基金"],
        })),
    ))

    def unexpected_reader(**_kwargs):
        raise AssertionError("rankhandler fallback must not run")

    monkeypatch.setattr(catalog, "read_fund_catalog", unexpected_reader, raising=False)
    assert catalog._efinance_catalog() == [
        {
            "canonicalCode": "000001",
            "instrumentType": "MUTUAL_FUND",
            "market": "OF",
            "displayName": "SDK基金",
        }
    ]


@pytest.mark.parametrize(
    "rows",
    [
        [("000001", "列表不是冻结结果")],
        (("00001", "坏代码"),),
        (("000001", "  "),),
        (("000001", "基金甲"), ("000001", "基金甲")),
    ],
)
def test_efinance_open_fund_catalog_revalidates_reader_boundary(monkeypatch, rows):
    monkeypatch.setitem(sys.modules, "efinance", SimpleNamespace())
    monkeypatch.setattr(
        catalog,
        "read_fund_catalog",
        lambda **_kwargs: {"rows": rows},
        raising=False,
    )

    with pytest.raises(CatalogBuildError) as raised:
        catalog._efinance_catalog()

    assert raised.value.code == "catalog_provider_invalid_response"
    assert raised.value.retryable is False
