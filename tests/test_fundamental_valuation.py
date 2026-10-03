"""CN 研究估值来源接线只影响估值块，不改变报价的价格复用。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from data_provider.base import DataFetcherManager
from data_provider.fundamental_valuation import build_cn_valuation


def _observation():
    return {"symbol": "300766.SZ", "tradeDate": "2026-09-24",
            "observedAt": "2026-09-28T02:19:45+00:00", "sourceAvailableAt": None,
            "historicalVisibilityVerified": False, "marketCapUnit": "yuan",
            "currency": "CNY", "contentFingerprint": "sample-hash",
            "data": {"pe_ratio": -613.5, "pb_ratio": 5.4,
                     "total_mv": 8546332239.05, "circ_mv": 7748709022.7}}


class FundamentalValuationTest(unittest.TestCase):
    def setUp(self):
        self.manager = DataFetcherManager(fetchers=[])
        self.quote = SimpleNamespace(pe_ratio=21.0, pb_ratio=3.0,
                                     total_mv=100.0, circ_mv=90.0, price=15.0)

    def test_source_success_is_partial_and_keeps_quote_for_dividend_yield(self):
        with patch.object(self.manager, "get_realtime_quote", return_value=self.quote), \
                patch("data_provider.fundamental_valuation.read_current_stock_valuation",
                      return_value=_observation()) as reader:
            quote, block, cost = build_cn_valuation(
                self.manager, stock_code="300766", original_stock_code="300766.SZ",
                is_etf=False, timeout_seconds=3.0)
        self.assertIs(quote, self.quote)
        self.assertEqual(block["status"], "partial")
        self.assertEqual(block["data"]["pe_ratio"], -613.5)
        self.assertEqual(block["data"]["symbol"], "300766.SZ")
        self.assertEqual(block["data"]["trade_date"], "2026-09-24")
        self.assertIsNone(block["data"]["source_available_at"])
        self.assertFalse(block["data"]["historical_visibility_verified"])
        self.assertEqual(block["source_chain"][0]["provider"], "eastmoney_stock_value")
        self.assertGreaterEqual(cost, 0)
        reader.assert_called_once()

    def test_source_failure_falls_back_to_quote_with_diagnostic(self):
        with patch.object(self.manager, "get_realtime_quote", return_value=self.quote), \
                patch("data_provider.fundamental_valuation.read_current_stock_valuation",
                      side_effect=ValueError("valuation_identity_mismatch")):
            quote, block, _ = build_cn_valuation(
                self.manager, stock_code="300766", original_stock_code="300766",
                is_etf=False, timeout_seconds=3.0)
        self.assertIs(quote, self.quote)
        self.assertEqual(block["data"]["pe_ratio"], 21.0)
        self.assertEqual([entry["provider"] for entry in block["source_chain"]],
                         ["realtime_quote", "eastmoney_stock_value"])
        self.assertIn("valuation_identity_mismatch", block["errors"])

    def test_etf_and_invalid_explicit_venue_never_call_source(self):
        with patch.object(self.manager, "get_realtime_quote", return_value=self.quote), \
                patch("data_provider.fundamental_valuation.read_current_stock_valuation") as reader:
            for original, is_etf in (("159915.SZ", True), ("300766.XX", False)):
                with self.subTest(original=original):
                    quote, block, _ = build_cn_valuation(
                        self.manager, stock_code=original[:6], original_stock_code=original,
                        is_etf=is_etf, timeout_seconds=3.0)
                    self.assertIs(quote, self.quote)
                    self.assertEqual(block["data"]["pe_ratio"], 21.0)
        reader.assert_not_called()

    def test_prefixed_explicit_market_is_not_lost_during_normalization(self):
        with patch.object(self.manager, "get_realtime_quote", return_value=self.quote), \
                patch("data_provider.fundamental_valuation.read_current_stock_valuation",
                      return_value=_observation()) as reader:
            build_cn_valuation(self.manager, stock_code="300766",
                               original_stock_code="SZ300766", is_etf=False,
                               timeout_seconds=3.0)
        self.assertEqual(reader.call_args.args[0], "300766.SZ")

    def test_exhausted_budget_never_calls_source(self):
        with patch.object(self.manager, "get_realtime_quote", return_value=self.quote), \
                patch("data_provider.fundamental_valuation.read_current_stock_valuation") as reader:
            quote, block, cost = build_cn_valuation(
                self.manager, stock_code="300766", original_stock_code="300766.SZ",
                is_etf=False, timeout_seconds=0)
        self.assertIsNone(quote)
        self.assertEqual(block["status"], "not_supported")
        self.assertEqual(cost, 0)
        reader.assert_not_called()

    def test_full_context_uses_source_valuation_and_quote_dividend_price(self):
        config = SimpleNamespace(enable_fundamental_pipeline=True,
                                 fundamental_cache_ttl_seconds=0,
                                 fundamental_cache_max_entries=0,
                                 fundamental_stage_timeout_seconds=8.0,
                                 fundamental_fetch_timeout_seconds=5.0,
                                 fundamental_retry_max=1)
        bundle = {"status": "partial", "growth": {}, "institution": {},
                  "earnings": {"dividend": {"ttm_cash_dividend_per_share": 1.5}},
                  "source_chain": [], "errors": []}
        empty = self.manager._build_fundamental_block("not_supported", {}, [], [])
        with patch("src.config.get_config", return_value=config), \
                patch.object(self.manager, "get_realtime_quote", return_value=self.quote) as quote, \
                patch("data_provider.fundamental_valuation.read_current_stock_valuation",
                      return_value=_observation()), \
                patch.object(self.manager._fundamental_adapter, "get_fundamental_bundle",
                             return_value=bundle), \
                patch.object(self.manager, "get_capital_flow_context", return_value=empty), \
                patch.object(self.manager, "get_dragon_tiger_context", return_value=empty), \
                patch.object(self.manager, "get_board_context", return_value=empty):
            context = self.manager.get_fundamental_context("300766.SZ")
        self.assertEqual(context["valuation"]["data"]["pe_ratio"], -613.5)
        self.assertEqual(context["coverage"]["valuation"], "partial")
        self.assertEqual(context["earnings"]["data"]["dividend"]["ttm_dividend_yield_pct"], 10.0)
        quote.assert_called_once_with("300766")


if __name__ == "__main__":
    unittest.main()
