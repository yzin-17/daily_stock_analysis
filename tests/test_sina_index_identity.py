"""离线验证新浪指数身份选择与既有大盘消费者接缝。"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.base import DataFetcherManager
from data_provider.sina_index_identity import select_unique_sina_index_row
from src.market_analyzer import MarketAnalyzer


CODES = ["sh000001", "sz399001", "sz399006", "sh000688", "sh000016", "sh000300"]
NAMES = ["上证指数", "深证成指", "创业板指", "科创50", "上证50", "沪深300"]


def quote(code):
    return {
        "代码": code, "最新价": 110, "昨收": 100, "最高": 120,
        "最低": 90, "涨跌额": 10, "涨跌幅": 10, "今开": 105,
        "成交量": 123, "成交额": 456,
    }


class TestSinaIndexIdentity(unittest.TestCase):
    def setUp(self):
        # 同时封锁 HTTP 与 socket；SDK 完全替换，不允许真实网络调用。
        for target in ("requests.sessions.Session.request", "socket.socket.connect"):
            blocker = patch(target, side_effect=AssertionError("禁止真实网络"))
            blocker.start()
            self.addCleanup(blocker.stop)
        self.fetcher = AkshareFetcher()

    def fetch(self, frame=None, error=None):
        endpoint = Mock(return_value=frame, side_effect=error)
        sdk = SimpleNamespace(stock_zh_index_spot_sina=endpoint)
        with patch.dict(sys.modules, {"akshare": sdk}), patch.object(
            self.fetcher, "_set_random_user_agent"
        ), patch.object(self.fetcher, "_enforce_rate_limit"):
            result = self.fetcher.get_main_indices()
        endpoint.assert_called_once_with()
        return result

    def test_unique_exact_rows_preserve_all_fields_units_and_order(self):
        frame = pd.DataFrame([quote(code) for code in reversed(CODES)])
        expected = []
        for code, name in zip(CODES, NAMES):
            expected.append({
                "code": code, "name": name, "current": 110.0, "change": 10.0,
                "change_pct": 10.0, "open": 105.0, "high": 120.0, "low": 90.0,
                "prev_close": 100.0, "volume": 123.0, "amount": 456.0,
                "amplitude": 30.0,
            })
        self.assertEqual(self.fetch(frame), expected)
        self.assertEqual(select_unique_sina_index_row(frame, CODES[0]).to_dict(), quote(CODES[0]))

    def test_rejects_ambiguous_absent_and_malformed_identity(self):
        cases = [
            pd.DataFrame([quote("prefixsh000001suffix")]),
            pd.DataFrame([quote("SH000001"), quote("000001")]),
            pd.DataFrame([quote(CODES[0]), quote(CODES[0])]),
            pd.DataFrame([{"最新价": 110}]),
            pd.DataFrame([quote(None), quote(pd.NA), quote(float("nan"))]),
            pd.DataFrame(), None,
            pd.DataFrame([quote([CODES[0]]), quote({"code": CODES[0]}), quote(1)]),
            pd.DataFrame([[CODES[0], CODES[0]]], columns=["代码", "代码"]),
        ]
        for frame in cases:
            with self.subTest(frame=repr(frame)):
                self.assertIsNone(select_unique_sina_index_row(frame, CODES[0]))
                self.assertEqual(self.fetch(frame), [])

    def test_duplicate_target_does_not_discard_other_unique_target(self):
        frame = pd.DataFrame([quote(CODES[0]), quote(CODES[1]), quote(CODES[0])])
        self.assertEqual([item["code"] for item in self.fetch(frame)], [CODES[1]])

    def test_source_failure_retains_failure_result(self):
        self.assertIsNone(self.fetch(error=RuntimeError("mock source failure")))

    def test_actual_manager_and_market_analyzer_consume_success_and_rejection(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = [self.fetcher]
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        analyzer.data_manager = manager
        analyzer.region = "cn"
        for frame, expected in [
            (pd.DataFrame([quote(CODES[0])]), [CODES[0]]),
            (pd.DataFrame([quote("wrongsh000001")]), []),
            (pd.DataFrame([quote(CODES[0]), quote(CODES[0])]), []),
        ]:
            with self.subTest(expected=expected), patch.dict(sys.modules, {
                "akshare": SimpleNamespace(stock_zh_index_spot_sina=Mock(return_value=frame))
            }), patch.object(self.fetcher, "_set_random_user_agent"), patch.object(
                self.fetcher, "_enforce_rate_limit"
            ), patch.object(manager, "_get_tickflow_fetcher", return_value=None), patch.object(
                analyzer, "_log_context", return_value="offline test"
            ):
                indices = analyzer._get_main_indices()
            self.assertEqual([index.code for index in indices], expected)
            if indices:
                self.assertEqual(indices[0].volume, 123.0)
                self.assertEqual(indices[0].amount, 456.0)


if __name__ == "__main__":
    unittest.main()
