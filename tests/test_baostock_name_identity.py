"""BaoStock 单标的名称响应与缓存身份回归。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data_provider.baostock_fetcher import BaostockFetcher
from data_provider.base import DataFetcherManager


class Result:
    def __init__(self, rows, fields=None, final_error="0"):
        self.fields = fields if fields is not None else ["code", "code_name"]
        self.error_code = "0"
        self.rows = rows
        self.index = -1
        self.final_error = final_error

    def next(self):
        self.index += 1
        if self.index < len(self.rows):
            return True
        self.error_code = self.final_error
        return False

    def get_row_data(self):
        return self.rows[self.index]


def fetcher_for(result):
    fetcher = BaostockFetcher()
    module = SimpleNamespace(
        login=Mock(return_value=SimpleNamespace(error_code="0")),
        logout=Mock(return_value=SimpleNamespace(error_code="0")),
        query_stock_basic=Mock(return_value=result),
    )
    fetcher._bs_module = module
    return fetcher, module


def test_verified_identity_only_is_cached_and_session_closes():
    fetcher, module = fetcher_for(Result([["sh.600519", " 贵州茅台 "]]))
    assert fetcher.get_stock_name("600519") == "贵州茅台"
    assert fetcher.get_stock_name("600519") == "贵州茅台"
    module.query_stock_basic.assert_called_once_with(code="sh.600519")
    module.logout.assert_called_once()


@pytest.mark.parametrize("result", [
    Result([["sz.000001", "另一标的"]]),
    Result([["sh.600519", "名称"], ["sh.600519", "重复"]]),
    Result([["sh.600519", "  "]]),
    Result([["sh.600519", None]]),
    Result([["sh.600519"]]),
    Result([["名称"]], fields=["code_name"]),
    Result([["sh.600519", "名称"]], final_error="-1"),
    Result([]),
])
def test_unverified_response_does_not_poison_cache(result):
    fetcher, module = fetcher_for(result)
    assert fetcher.get_stock_name("600519") is None
    assert "600519" not in fetcher._stock_name_cache
    module.logout.assert_called_once()


@pytest.mark.parametrize("input_code", ["600519", "sh600519", "SH600519"])
def test_manager_normalizes_identity_and_caches_verified_provider_name(monkeypatch, input_code):
    monkeypatch.setattr("data_provider.base.STOCK_NAME_MAP", {})
    monkeypatch.setattr("data_provider.base.get_index_stock_name", lambda _: None)
    fetcher, module = fetcher_for(Result([["sh.600519", " 贵州茅台 "]]))
    manager = DataFetcherManager(fetchers=[fetcher])
    manager.get_realtime_quote = Mock(side_effect=AssertionError("不应请求实时报价"))

    assert manager.get_stock_name(input_code, allow_realtime=False) == "贵州茅台"
    assert manager.get_stock_name("600519", allow_realtime=False) == "贵州茅台"
    module.query_stock_basic.assert_called_once_with(code="sh.600519")
    assert manager._get_cached_stock_name("600519") == "贵州茅台"
    module.logout.assert_called_once()


def test_manager_retries_after_wrong_identity_without_cache_poisoning(monkeypatch):
    monkeypatch.setattr("data_provider.base.STOCK_NAME_MAP", {})
    monkeypatch.setattr("data_provider.base.get_index_stock_name", lambda _: None)
    fetcher, module = fetcher_for(Result([["sz.000001", "另一标的"]]))
    manager = DataFetcherManager(fetchers=[fetcher])

    assert not manager.get_stock_name("600519", allow_realtime=False)
    assert manager._get_cached_stock_name("600519") is None
    module.query_stock_basic.return_value = Result([["sh.600519", "贵州茅台"]])
    assert manager.get_stock_name("600519", allow_realtime=False) == "贵州茅台"
    assert module.query_stock_basic.call_count == 2
    assert module.logout.call_count == 2


def test_bulk_names_validate_then_publish_and_deduplicate():
    fetcher, module = fetcher_for(Result([
        ["sh.600519", " 贵州茅台 "], ["sh.600519", "贵州茅台"], ["sz.000001", "平安银行"],
    ]))
    result = fetcher.get_stock_list()
    assert result.to_dict("records") == [
        {"code": "600519", "name": "贵州茅台"}, {"code": "000001", "name": "平安银行"},
    ]
    assert fetcher._stock_name_cache == {"600519": "贵州茅台", "000001": "平安银行"}
    module.logout.assert_called_once()


@pytest.mark.parametrize("result", [
    Result([["sh.600519", "名称"]], final_error="-1"),
    Result([["sh.600519", "名称"], ["sh.600519", "冲突"]]),
    Result([["sh.000001", "上证指数"], ["sz.000001", "平安银行"]]),
    Result([["sh.600519", "名称"], ["bad", "坏代码"]]),
    Result([["sh.600519", "名称"], ["sz.000001", None]]),
    Result([["sh.600519", "名称"], ["sz.000001", " "]]),
    Result([["sh.６００５１９", "名称"]]),
    Result([[None, "名称"]]),
    Result([["sh.600519"]]),
    Result([["sh.600519", "名称", "多余字段"]]),
    Result([["sh.600519", "名称"]], fields=["code", "code"]),
    Result([["名称"]], fields=["code_name"]),
    Result([]),
])
def test_bulk_failure_preserves_existing_cache_atomically(result):
    fetcher, module = fetcher_for(result)
    fetcher._stock_name_cache = {"600519": "已有名称"}
    assert fetcher.get_stock_list() is None
    assert fetcher._stock_name_cache == {"600519": "已有名称"}
    module.logout.assert_called_once()
