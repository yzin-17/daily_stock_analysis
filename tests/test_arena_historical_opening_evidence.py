"""历史重建必须拒绝来源、日期和覆盖不符；测试输入均为受控样本。"""
import copy
import unittest
from src.services.arena_historical_opening_evidence import qualify_receipts


class HistoricalOpeningEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {"symbol": "600036.SH", "day": "2026-11-09", "suspensionRequest": {
            "sqlId": "GW_PL_JYTS_TFPXX", "startStopDate": "20261109", "endStopDate": "20261109", "productCode": "", "keyWords": ""}}
        key = {"kind": "bar", "market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "none"}
        target = {"providerId": "hithink", "upstreamSource": "hithink-financial-api"}
        self.e = {"bars": {"request": {"symbol": "600036.SH", "start": "2026-11-09", "end": "2026-11-09", "requestId": "sample", "routeKey": key, "routeTarget": target},
            "response": {"symbol": "600036.SH", "requestId": "sample", "routeKey": key, "provenance": target,
                "coverageProof": {"calendar": {"expectedSessionDates": ["2026-11-09"]}, "window": {"status": "complete", "requestedStart": "2026-11-09", "requestedEnd": "2026-11-09"}, "pagination": {"status": "complete", "continuationPending": False}},
                "sourcePriceBasis": {"adjustment": "none"}, "bars": [{"timestamp": "2026-11-09T00:00:00Z", "completionStatus": "complete", "availableAt": "2026-11-09T08:00:00Z", "open": 42.55, "volume": 100}]}, "sourceFeeStatus": "unverified"},
            "profile": {"sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GSGK_C", "result": [{"A_STOCK_CODE": "600036", "COMPANY_CODE": "600036", "SEC_TYPE": "主板A", "LIST_BOARD": "1", "STATE_CODE_A_DESC": "上市", "A_LIST_DATE": "20020409", "SECURITY_ABBR_A_CN": "招商银行"}]},
            "suspensions": {"sqlId": "GW_PL_JYTS_TFPXX", "actionErrors": [], "pageHelp": {"pageNo": 1, "pageCount": 1, "total": 0, "data": []}, "result": []},
            "opening": {"calendar": "sample-calendar", "openAt": "2026-11-09T01:30:00.000Z", "quote": {"symbol": "600036.SH", "open": 42.55, "previousClose": 42.88, "stale": False, "marketTime": "2026-11-09T01:36:00Z", "fetchedAt": "2026-11-09T01:36:01Z", "units": {"priceCurrency": "CNY"}, "sessionContext": {"marketSession": {"sessions": [{"tradingDate": "2026-11-09", "startAt": "2026-11-09T01:30:00.000Z"}]}}}},
            "minutes": {"identity": {"symbol": "600036.SH", "assetType": "STOCK", "timeframe": "1m", "adjustment": "none"}, "points": [{"timestamp": "2026-11-09T09:31:00+08:00", "open": 42.55, "volume": 100}]}}

    def read(self, evidence=None, at="2026-11-09T08:30:00Z"):
        return qualify_receipts(evidence or self.e, self.bundle, qualified_at=at)

    def test_realistic_cross_source_receipts_preserve_later_availability(self):
        r = self.read()
        self.assertEqual((r["price"], r["lot"], r["tick"], r["upperLimit"], r["lowerLimit"]), ("42.55", "100", "0.01", "47.17", "38.59"))
        self.assertEqual(r["sourceAvailableAt"], "2026-11-09T08:00:00Z")
        self.assertEqual(r["purpose"], "initial-cash-buy-only-replay")

    def test_rejects_filtered_and_partial_suspension_lists(self):
        for field, value in [("total", 1), ("pageCount", 2)]:
            e = copy.deepcopy(self.e); e["suspensions"]["pageHelp"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.read(e)
        self.bundle["suspensionRequest"]["productCode"] = "600036"
        with self.assertRaises(ValueError): self.read()

    def test_rejects_actual_suspension_and_unknown_listing(self):
        e = copy.deepcopy(self.e)
        row = {"productCode": "600036"}
        e["suspensions"]["result"] = [row]; e["suspensions"]["pageHelp"].update(total=1, data=[row])
        with self.assertRaises(ValueError): self.read(e)
        self.e["profile"]["result"][0]["STATE_CODE_A_DESC"] = "未知"
        with self.assertRaises(ValueError): self.read()

    def test_rejects_later_first_trade_and_mismatched_price(self):
        for field, value in [("timestamp", "2026-11-09T09:32:00+08:00"), ("open", 42.56), ("volume", 0)]:
            e = copy.deepcopy(self.e); e["minutes"]["points"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.read(e)

    def test_rejects_adjusted_other_symbol_and_other_day(self):
        for change in [lambda e: e["bars"]["response"].update(symbol="600000.SH"),
                       lambda e: e["bars"]["response"]["sourcePriceBasis"].update(adjustment="qfq"),
                       lambda e: e["bars"]["response"]["bars"][0].update(completionStatus="unknown")]:
            e = copy.deepcopy(self.e); change(e)
            with self.assertRaises(ValueError): self.read(e)
        with self.assertRaises(ValueError): self.read(at="2026-11-10T08:30:00Z")

    def test_rejects_wrong_date_stale_or_future_quote(self):
        for field, value in [("marketTime", "2026-11-06T01:36:00Z"), ("stale", True), ("fetchedAt", "2026-11-10T01:36:00Z")]:
            e = copy.deepcopy(self.e); e["opening"]["quote"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.read(e)

    def test_rejects_unknown_rule_document_bytes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from src.services.arena_historical_opening_evidence import qualify
        with TemporaryDirectory() as directory:
            doc = Path(directory) / 'unreviewed'; doc.write_bytes(b'not a reviewed rule')
            with self.assertRaises(ValueError):
                qualify({"files": {"rules": str(doc), "deferred": str(doc)}}, qualified_at="2026-11-09T08:30:00Z")
