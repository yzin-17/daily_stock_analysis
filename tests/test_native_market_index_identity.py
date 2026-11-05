"""原生大盘消费者继续核对数据源的身份拒绝行为。"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from data_provider.base import DataFetcherManager
from src.market_analyzer import MarketAnalyzer
from tests import test_sina_index_identity as _sina_fixture
from tests.test_sina_index_identity import CODES, quote


class NativeMarketIndexIdentityTest(unittest.TestCase):
    setUp = _sina_fixture.TestSinaIndexIdentity.setUp

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
