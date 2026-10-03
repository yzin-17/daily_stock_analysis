"""Yahoo 财务研究字段不得跨季度或与 TTM 汇总混成同一报告期。"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from data_provider.yfinance_fundamental_adapter import YfinanceFundamentalAdapter, _yoy_from_row
from data_provider.base import DataFetcherManager


def _ticker(*, info=None, income=None, cashflow=None):
    ticker = MagicMock()
    ticker.get_info.return_value = info or {}
    ticker.quarterly_income_stmt = income if income is not None else pd.DataFrame()
    ticker.quarterly_cashflow = cashflow if cashflow is not None else pd.DataFrame()
    ticker.dividends = pd.Series(dtype="float64")
    return ticker


class YahooFinancialPeriodTest(unittest.TestCase):
    def _read(self, ticker):
        with patch("yfinance.Ticker", return_value=ticker):
            return YfinanceFundamentalAdapter().get_fundamental_bundle("AAPL")

    def test_other_quarter_cashflow_and_info_ttm_cannot_fill_report(self):
        income = pd.DataFrame({pd.Timestamp("2026-03-31"):
                               {"Total Revenue": 100.0, "Net Income": 20.0}})
        cashflow = pd.DataFrame({pd.Timestamp("2025-12-31"):
                                 {"Operating Cash Flow": 70.0}})
        result = self._read(_ticker(info={"financialCurrency": "USD",
                                          "operatingCashflow": 999.0, "returnOnEquity": 0.2},
                                    income=income, cashflow=cashflow))
        report = result["earnings"]["financial_report"]
        self.assertEqual(report["report_date"], "2026-03-31")
        self.assertEqual(report["period_basis"], "quarterly_statement")
        self.assertEqual(report["revenue"], 100.0)
        self.assertEqual(report["net_profit_parent"], 20.0)
        self.assertIsNone(report["operating_cash_flow"])
        self.assertIsNone(report["roe"])
        self.assertIsNone(report["source_available_at"])

    def test_cashflow_uses_same_period_even_when_columns_are_reordered(self):
        income = pd.DataFrame({pd.Timestamp("2026-03-31"):
                               {"Total Revenue": 100.0}})
        cashflow = pd.DataFrame({
            pd.Timestamp("2026-06-30"): {"Operating Cash Flow": 80.0},
            pd.Timestamp("2026-03-31"): {"Operating Cash Flow": 40.0},
        })
        report = self._read(_ticker(income=income, cashflow=cashflow))[
            "earnings"]["financial_report"]
        self.assertEqual(report["report_date"], "2026-03-31")
        self.assertEqual(report["operating_cash_flow"], 40.0)

    def test_info_aggregates_have_unknown_report_date_and_period(self):
        info = {"financialCurrency": "USD", "totalRevenue": 100.0,
                "operatingCashflow": 30.0, "profitMargins": 0.2}
        report = self._read(_ticker(info=info))["earnings"]["financial_report"]
        self.assertIsNone(report["report_date"])
        self.assertEqual(report["period_basis"], "info_aggregate_period_unknown")
        self.assertEqual(report["revenue"], 100.0)
        self.assertEqual(report["net_profit_parent"], 20.0)
        self.assertEqual(report["operating_cash_flow"], 30.0)
        self.assertIsNone(report["source_available_at"])

    def test_currency_alone_does_not_claim_financial_report(self):
        result = self._read(_ticker(info={"financialCurrency": "USD"}))
        self.assertEqual(result["earnings"], {})
        self.assertEqual(result["status"], "not_supported")

    def test_dated_statement_without_mapped_values_uses_separate_info_aggregate(self):
        income = pd.DataFrame({pd.Timestamp("2026-03-31"): {"Other Metric": 7.0}})
        report = self._read(_ticker(info={"totalRevenue": 100.0}, income=income))[
            "earnings"]["financial_report"]
        self.assertIsNone(report["report_date"])
        self.assertEqual(report["period_basis"], "info_aggregate_period_unknown")
        self.assertEqual(report["revenue"], 100.0)

    def test_existing_research_context_preserves_period_and_visibility(self):
        manager = DataFetcherManager(fetchers=[])
        config = SimpleNamespace(enable_fundamental_pipeline=True,
                                 fundamental_cache_ttl_seconds=0,
                                 fundamental_stage_timeout_seconds=3.0,
                                 fundamental_fetch_timeout_seconds=2.0,
                                 fundamental_retry_max=1)
        income = pd.DataFrame({pd.Timestamp("2026-03-31"):
                               {"Total Revenue": 100.0}})
        with patch("src.config.get_config", return_value=config), \
                patch.object(manager, "get_realtime_quote", return_value=None), \
                patch("yfinance.Ticker", return_value=_ticker(income=income)):
            context = manager.get_fundamental_context("AAPL")
        report = context["earnings"]["data"]["financial_report"]
        self.assertEqual(context["market"], "us")
        self.assertEqual(report["report_date"], "2026-03-31")
        self.assertEqual(report["period_basis"], "quarterly_statement")
        self.assertIsNone(report["source_available_at"])

    def test_yoy_does_not_use_fifth_column_when_prior_year_is_missing(self):
        row = pd.Series([200, 180, 160, 140, 100], index=pd.to_datetime([
            "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2024-12-31",
        ]))
        self.assertIsNone(_yoy_from_row(row))

    def test_yoy_finds_prior_year_by_date_not_column_position(self):
        row = pd.Series([200, 180, 160, 100, 90], index=pd.to_datetime([
            "2026-03-31", "2025-12-31", "2025-09-30", "2025-03-31", "2024-12-31",
        ]))
        self.assertEqual(_yoy_from_row(row), 100.0)

    def test_yoy_rejects_duplicate_or_unknown_period_columns(self):
        duplicate = pd.Series([200, 180, 160, 100, 90], index=pd.to_datetime([
            "2026-03-31", "2025-12-31", "2025-09-30", "2025-03-31", "2025-03-31",
        ]))
        unknown = pd.Series([200, 180, 160, 100, 90], index=[
            "latest", "q-1", "q-2", "q-3", "year-ago",
        ])
        self.assertIsNone(_yoy_from_row(duplicate))
        self.assertIsNone(_yoy_from_row(unknown))

    def test_missing_yoy_period_falls_back_to_info_growth(self):
        income = pd.DataFrame({
            pd.Timestamp("2026-03-31"): {"Total Revenue": 200.0},
            pd.Timestamp("2025-12-31"): {"Total Revenue": 180.0},
            pd.Timestamp("2025-09-30"): {"Total Revenue": 160.0},
            pd.Timestamp("2025-06-30"): {"Total Revenue": 140.0},
            pd.Timestamp("2024-12-31"): {"Total Revenue": 100.0},
        })
        result = self._read(_ticker(info={"revenueGrowth": 0.12}, income=income))
        self.assertEqual(result["growth"]["revenue_yoy"], 12.0)


if __name__ == "__main__":
    unittest.main()
