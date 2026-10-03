"""东财估值原响应读取的身份、分页和时间边界。"""

import json
import unittest
from unittest.mock import patch

from data_provider.eastmoney_stock_valuation_reader import (
    _fetch_page,
    parse_stock_valuation_page,
    read_current_stock_valuation,
)


def _row(day, *, symbol="300766.SZ", market="069001002002", code="300766", pe=12.5):
    return {"SECURITY_CODE": code, "SECUCODE": symbol, "TRADE_MARKET": market,
            "TRADE_DATE": f"{day} 00:00:00", "PE_TTM": pe, "PB_MRQ": 2.5,
            "TOTAL_MARKET_CAP": 123456789.25, "NOTLIMITED_MARKETCAP_A": 90000000.5}


def _page(rows, *, count=None, pages=1):
    return json.dumps({"success": True, "code": 0,
                       "result": {"count": len(rows) if count is None else count,
                                  "pages": pages, "data": rows}},
                      separators=(",", ":")).encode()


class StockValuationReaderTest(unittest.TestCase):
    def test_complete_pages_preserve_identity_and_separate_clocks(self):
        bodies = [_page([_row("2026-09-24"), _row("2026-09-23")], count=3, pages=2),
                  _page([_row("2026-09-22")], count=3, pages=2)]
        calls = []

        def fetch(code, page, size, remaining):
            calls.append((code, page, size, remaining))
            return bodies[page - 1]

        result = read_current_stock_valuation("300766.SZ", page_size=2, fetch_page=fetch)
        self.assertEqual([(code, page, size) for code, page, size, _ in calls],
                         [("300766", 1, 2), ("300766", 2, 2)])
        self.assertEqual(result["symbol"], "300766.SZ")
        self.assertEqual(result["tradeDate"], "2026-09-24")
        self.assertEqual(result["data"], {"pe_ratio": 12.5, "pb_ratio": 2.5,
                                          "total_mv": 123456789.25, "circ_mv": 90000000.5})
        self.assertIsNone(result["sourceAvailableAt"])
        self.assertFalse(result["historicalVisibilityVerified"])
        self.assertEqual(result["retrieval"]["count"], 3)
        self.assertTrue(result["retrieval"]["paginationComplete"])
        self.assertEqual(len(result["retrieval"]["pageHashes"]), 2)

    def test_bare_request_only_accepts_raw_self_proven_symbol(self):
        result = read_current_stock_valuation(
            "300766", page_size=1, fetch_page=lambda *_: _page([_row("2026-09-24")]))
        self.assertEqual(result["symbol"], "300766.SZ")

    def test_explicit_venue_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "valuation_identity_mismatch"):
            read_current_stock_valuation(
                "300766.SH", page_size=1, fetch_page=lambda *_: _page([_row("2026-09-24")]))

    def test_wrong_code_or_market_fails_closed(self):
        for row, error in ((_row("2026-09-24", code="300767"), "identity_mismatch"),
                           (_row("2026-09-24", market=""), "market_missing")):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                read_current_stock_valuation("300766.SZ", page_size=1,
                                             fetch_page=lambda *_: _page([row]))

    def test_cross_page_identity_and_market_must_stay_stable(self):
        first = _page([_row("2026-09-24")], count=2, pages=2)
        for row, error in ((_row("2026-09-23", symbol="300766.SH"), "identity_mismatch"),
                           (_row("2026-09-23", market="different"), "market_changed")):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                bodies = [first, _page([row], count=2, pages=2)]
                read_current_stock_valuation("300766", page_size=1,
                                             fetch_page=lambda code, page, *args: bodies[page - 1])

    def test_duplicate_or_reordered_dates_fail_closed(self):
        for dates in (("2026-09-24", "2026-09-24"),
                      ("2026-09-23", "2026-09-24")):
            with self.subTest(dates=dates), self.assertRaisesRegex(ValueError, "unsorted_date"):
                read_current_stock_valuation(
                    "300766", page_size=2,
                    fetch_page=lambda *_: _page([_row(day) for day in dates]))

    def test_page_metadata_drift_or_missing_rows_fails_closed(self):
        first = _page([_row("2026-09-24")], count=2, pages=2)
        for second, error in ((_page([_row("2026-09-23")], count=3, pages=3),
                               "pagination_changed"),
                              (_page([], count=2, pages=2), "invalid_pagination")):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                read_current_stock_valuation("300766", page_size=1,
                                             fetch_page=lambda code, page, *args: first if page == 1 else second)

    def test_page_budget_rejects_before_second_request(self):
        calls = []

        def fetch(_, page, *__):
            calls.append(page)
            return _page([_row("2026-09-24")], count=2, pages=2)

        with self.assertRaisesRegex(ValueError, "page_budget_exceeded"):
            read_current_stock_valuation("300766", page_size=1, max_pages=1, fetch_page=fetch)
        self.assertEqual(calls, [1])

    def test_invalid_date_number_and_missing_latest_values(self):
        bad_rows = [(_row("2026-02-30"), "invalid_trade_date"),
                    (_row("2026-09-24", pe="nan"), "invalid_number"),
                    (_row("2026-09-24", pe="1e10000"), "invalid_number"),
                    (_row("2026-09-24", pe=True), "invalid_number")]
        empty = _row("2026-09-24", pe=None)
        empty.update({"PB_MRQ": None, "TOTAL_MARKET_CAP": None, "NOTLIMITED_MARKETCAP_A": None})
        bad_rows.append((empty, "empty_latest_values"))
        for row, error in bad_rows:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                read_current_stock_valuation("300766", page_size=1,
                                             fetch_page=lambda *_: _page([row]))

    def test_duplicate_json_key_and_source_failure(self):
        with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
            parse_stock_valuation_page(b'{"success":true,"success":true}', code="300766",
                                       page=1, page_size=1)
        with self.assertRaisesRegex(ValueError, "source_failure"):
            read_current_stock_valuation("300766", page_size=1,
                                         fetch_page=lambda *_: b'{"success":false,"code":0}')

    def test_deadline_after_slow_transport(self):
        clock = [0.0]

        def fetch(*_):
            clock[0] = 2.0
            return _page([_row("2026-09-24")])

        with self.assertRaisesRegex(TimeoutError, "valuation_deadline"):
            read_current_stock_valuation("300766", page_size=1, timeout_seconds=1,
                                         fetch_page=fetch, monotonic=lambda: clock[0])

    def test_transport_uses_fixed_source_contract_without_redirect(self):
        class Response:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def iter_content(self, _):
                yield b"{}"

        with patch("data_provider.eastmoney_stock_valuation_reader.requests.get",
                   return_value=Response()) as get:
            self.assertEqual(_fetch_page("300766", 1, 5000, 10), b"{}")
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["params"]["reportName"], "RPT_VALUEANALYSIS_DET")
        self.assertEqual(kwargs["params"]["filter"], '(SECURITY_CODE="300766")')
        self.assertEqual(kwargs["params"]["pageNumber"], "1")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (5, 10))

    def test_streaming_response_cannot_extend_total_deadline(self):
        class Response:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def iter_content(self, _):
                yield b"{}"

        with patch("data_provider.eastmoney_stock_valuation_reader.requests.get",
                   return_value=Response()), \
                patch("data_provider.eastmoney_stock_valuation_reader.time.monotonic",
                      side_effect=[0, 2]):
            with self.assertRaisesRegex(TimeoutError, "valuation_deadline"):
                _fetch_page("300766", 1, 5000, 1)


if __name__ == "__main__":
    unittest.main()
