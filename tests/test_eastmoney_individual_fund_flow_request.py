"""Offline request scope tests using the real AKShare candidate caller."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from data_provider.eastmoney_individual_fund_flow_request import resolve_individual_fund_flow_request
from data_provider.fundamental_adapter import AkshareFundamentalAdapter


class TestIndividualFundFlowRequest(unittest.TestCase):
    def test_explicit_exchange_parameters(self):
        for symbol, market in (("600519.SH", "sh"), ("000001.SZ", "sz")):
            with self.subTest(symbol=symbol):
                self.assertEqual(resolve_individual_fund_flow_request(symbol),
                                 {"stock": symbol[:6], "market": market})

    def test_unknown_scope_is_rejected(self):
        for symbol in ("600519", "000001", "", "12345.SH", "1234567.SH", "600519.BJ",
                       "600519.HK", "600519.SH.SZ", "SH600519", "600519.sh", " 600519.SH",
                       "６００５１９.SH", "600519.SK", None):
            with self.subTest(symbol=symbol):
                self.assertIsNone(resolve_individual_fund_flow_request(symbol))

    def _sdk(self, outcome):
        individual = Mock()
        if isinstance(outcome, Exception):
            individual.side_effect = outcome
        else:
            individual.return_value = outcome
        return SimpleNamespace(
            stock_individual_fund_flow=individual,
            stock_main_fund_flow=Mock(side_effect=AssertionError("unexpected stock fallback")),
            stock_sector_fund_flow_rank=Mock(return_value=pd.DataFrame({
                "名称": ["行业"], "今日主力净流入-净额": [1.0],
            })),
            stock_sector_fund_flow_summary=Mock(side_effect=AssertionError("unexpected sector fallback")),
        )

    def test_adapter_passes_explicit_scope_once(self):
        for symbol, market in (("600519.SH", "sh"), ("000001.SZ", "sz")):
            with self.subTest(symbol=symbol):
                sdk = self._sdk(pd.DataFrame({"日期": ["2026-09-25"], "主力净流入-净额": [1.0]}))
                with patch.dict(sys.modules, {"akshare": sdk}):
                    result = AkshareFundamentalAdapter().get_capital_flow(symbol)
                sdk.stock_individual_fund_flow.assert_called_once_with(stock=symbol[:6], market=market)
                sdk.stock_main_fund_flow.assert_not_called()
                self.assertEqual(result["stock_flow"]["main_net_inflow"], 1.0)
                self.assertIn("capital_stock:stock_individual_fund_flow", result["source_chain"])

    def test_missing_scope_keeps_sector_but_never_requests_stock(self):
        for symbol in ("600519", "000001", "", "600519.BJ", "600519.SH.SZ", "６００５１９.SH"):
            with self.subTest(symbol=symbol):
                sdk = self._sdk(pd.DataFrame({"主力净流入-净额": [1.0]}))
                with patch.dict(sys.modules, {"akshare": sdk}):
                    result = AkshareFundamentalAdapter().get_capital_flow(symbol)
                sdk.stock_individual_fund_flow.assert_not_called()
                sdk.stock_main_fund_flow.assert_not_called()
                sdk.stock_sector_fund_flow_rank.assert_called_once_with(
                    indicator="今日", sector_type="行业资金流",
                )
                self.assertEqual(result["stock_flow"], {})
                self.assertTrue(result["sector_rankings"]["top"])
                self.assertIn("capital_stock:missing_explicit_exchange", result["errors"])

    def test_failed_or_empty_request_never_falls_back_to_stock(self):
        for outcome in (RuntimeError("offline failure"), pd.DataFrame(), None):
            with self.subTest(outcome=type(outcome).__name__):
                sdk = self._sdk(outcome)
                with patch.dict(sys.modules, {"akshare": sdk}):
                    result = AkshareFundamentalAdapter().get_capital_flow("600519.SH")
                sdk.stock_individual_fund_flow.assert_called_once_with(stock="600519", market="sh")
                sdk.stock_main_fund_flow.assert_not_called()
                self.assertEqual(result["stock_flow"], {})
                self.assertTrue(result["sector_rankings"]["top"])
