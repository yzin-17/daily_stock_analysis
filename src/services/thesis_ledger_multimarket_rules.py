"""港美股首个有界收费/盘中事实包；来源事实与日线执行解释分离。"""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from src.services.thesis_ledger_public_fees import PublicFeeSourceError
from src.services.thesis_ledger_status_dimensions import validate_status_documents
from src.services.thesis_ledger_trading_status import TradingStatusSourceError

BASE = Path(__file__).resolve().parents[2] / "data_provider/market_reference"
SOURCES = {
    ("HK", "HISTORICAL_PUBLIC_FEES", "official-hk-rules", "hkex-trading-fee"): "hk-00147-fees.json",
    ("HK", "HISTORICAL_PUBLIC_FEES", "official-hk-rules", "hk-stock-public-fees"): "hk-00147-public-fees.json",
    ("HK", "HISTORICAL_TRADING_STATUS", "official-hk-rules", "hkex-00147-halt"): "hk-00147-status.json",
    ("US", "HISTORICAL_PUBLIC_FEES", "official-us-rules", "us-section31-exchange-t2"): "us-gme-fees.json",
    ("US", "HISTORICAL_PUBLIC_FEES", "official-us-rules", "us-stock-public-fees"): "us-gme-public-fees.json",
    ("US", "HISTORICAL_TRADING_STATUS", "official-us-rules", "nyse-gme-halt-observations"): "us-gme-status.json",
}
SOURCE_LABELS = {
    "hkex-trading-fee": "港交所交易收费规则",
    "hk-stock-public-fees": "港股印花税与交易征费规则",
    "hkex-00147-halt": "港交所暂停及恢复通知",
    "us-section31-exchange-t2": "美股 Section31 与交易结算收费规则",
    "us-stock-public-fees": "美股 Section31 与实际股数交易活动费规则",
    "nyse-gme-halt-observations": "NYSE 盘中暂停记录",
}


def multimarket_rule_path(key, target):
    if not isinstance(key, dict) or key.get("kind") != "data" or key.get("assetType") != "STOCK":
        return None
    identity = (key.get("market"), key.get("capability"), target.get("providerId"), target.get("upstreamSource"))
    name = SOURCES.get(identity)
    return BASE / name if name else None


def multimarket_rule_manifests():
    from src.services.provider_manifest import build_provider_manifest

    manifests = {}
    for market in ("HK", "US"):
        provider = f"official-{market.lower()}-rules"
        entries = [(identity, name) for identity, name in SOURCES.items() if identity[0] == market]
        manifest = build_provider_manifest(provider, f"{'港股' if market == 'HK' else '美股'}公开规则（本地）",
            {identity[1]: ("STOCK",) for identity, _ in entries},
            upstream_sources=tuple((identity[3], SOURCE_LABELS[identity[3]]) for identity, _ in entries),
            markets=(market,), configuration_mode="built_in")
        for source in manifest["upstreamSources"]:
            capability = next(identity[1] for identity, _ in entries if identity[3] == source["sourceId"])
            source["capabilities"] = {capability: ["STOCK"]}
        from src.services.thesis_ledger_local_history_manifest import attach_local_history
        attach_local_history(manifest, {identity[3]: BASE / name for identity, name in entries})
        from src.services.thesis_ledger_local_history_manifest import attach_local_history
        manifests[provider] = attach_local_history(manifest, {
            identity[3]: BASE / name for identity, name in entries
        })
    return manifests


class MultimarketRuleProvider:
    def __init__(self, data_path=None, *, clock=None):
        self.data_path = data_path
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def read(self, request):
        key, target = request.get("routeKey", {}), request.get("routeTarget", {})
        error = PublicFeeSourceError if key.get("capability") == "HISTORICAL_PUBLIC_FEES" else TradingStatusSourceError
        try:
            path = multimarket_rule_path(key, target)
            if path is None:
                raise error("unsupported_capability", "请求未选择本精确市场规则来源")
            if (request.get("contractVersion") != 3 or request.get("consumer") != "thesis-ledger"
                    or not request.get("requestId") or type(target.get("routeIndex")) is not int
                    or target["routeIndex"] not in (0, 1)):
                raise error("invalid_request", "规则请求合同无效")
            raw = (self.data_path or path).read_bytes()
            pack = json.loads(raw)
            if (pack["market"] != key["market"] or pack["upstreamSource"] != target["upstreamSource"]):
                raise error("invalid_response", "规则包与精确来源身份不一致")
            if request["symbol"] != pack["symbol"]:
                raise error("not_covered", "首批规则包未覆盖该证券")
            start, end = (date.fromisoformat(request["range"][field]) for field in ("start", "end"))
            if start > end:
                raise error("invalid_request", "规则起点不得晚于终点")
            if start.isoformat() < pack["coverage"]["start"] or end.isoformat() > pack["coverage"]["end"]:
                raise error("insufficient_coverage", "首批规则包未覆盖请求范围")
            validate_status_documents(pack["sourceDocuments"], self.clock())
            ids = {item["id"] for item in pack["sourceDocuments"]}
            common = {field: request[field] for field in ("contractVersion", "consumer", "requestId", "routeKey", "routeTarget", "symbol", "range")}
            provenance = {"providerId": target["providerId"], "upstreamSource": target["upstreamSource"],
                "providerRevision": "bounded-market-rules-sha256:" + sha256(raw).hexdigest(),
                "datasetId": pack["datasetId"], "fetchedAt": self.clock().astimezone(timezone.utc).isoformat(),
                "historicalAvailability": "notVerified"}
            if key["capability"] == "HISTORICAL_PUBLIC_FEES":
                value = self._fees(pack, start, end, ids)
                provenance.update({field: pack[field] for field in ("exchange", "tradingMethod", "calculationSettings", "coverageDescription")})
                if "chargeDatePolicy" in pack:
                    provenance["chargeDatePolicy"] = self._charge_policy(pack)
            else:
                value = self._status(pack, start, end, ids)
                provenance.update({"granularity": "intraday", "timezone": pack["timezone"]})
            used = {ref for row in value.get("days", []) for ref in row.get("sourceDocumentIds", [])}
            if "segments" in value:
                used = {ref for segment in value["segments"] for fee in segment["charges"] for ref in fee["sourceDocumentIds"]}
            return {**common, "status": "supported", **value,
                "sourceDocuments": [doc for doc in pack["sourceDocuments"] if doc["id"] in used], "provenance": provenance}
        except (PublicFeeSourceError, TradingStatusSourceError):
            raise
        except OSError as exc:
            raise error("upstream_failure", "规则包无法读取") from exc
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise error("invalid_response", "规则包结构或来源依据无效") from exc

    def _charge_policy(self, pack):
        from src.services.thesis_ledger_multimarket_calendar import multimarket_calendar_evidence

        policy = pack["chargeDatePolicy"]
        calendar = multimarket_calendar_evidence("US", "2024-05-01", "2024-05-31", self.clock())
        if calendar is None or policy["settlementLagSessions"] != 2 or policy["basis"] != "exchangeSettlement":
            raise ValueError("结算日历或费用口径不可验证")
        sessions = calendar["expectedSessionDates"]
        index = sessions.index(policy["rateChangeChargeDate"])
        effective = sessions[index - 2]
        if policy["effectiveTradeDate"] != effective or pack["segments"][1]["range"]["start"] != effective:
            raise ValueError("成交日费率分界未按T+2结算核对")
        return {**policy, "calendarRevision": calendar["revision"]}

    def _fees(self, pack, start, end, ids):
        from src.services.thesis_ledger_multimarket_fee_dimensions import validate_multimarket_fee_charges

        stop, cursor, segments = end + timedelta(days=1), start, []
        for segment in pack["segments"]:
            left = max(start, date.fromisoformat(segment["range"]["start"]))
            right = min(stop, date.fromisoformat(segment["range"]["endExclusive"]))
            if left >= right:
                continue
            if left != cursor:
                raise ValueError("收费区间缺口")
            validate_multimarket_fee_charges(pack, segment["charges"], ids)
            segments.append({"range": {"start": left.isoformat(), "endExclusive": right.isoformat()}, "charges": segment["charges"]})
            cursor = right
        if cursor != stop:
            raise ValueError("收费未覆盖终点")
        return {"segments": segments}

    def _status(self, pack, start, end, ids):
        by_date = {}
        for item in pack["days"]:
            if item["date"] in by_date or item["state"] != "unknown" or not item["intradayHalts"]:
                raise ValueError("盘中来源不能改写为全天状态或重复日期")
            if not item["sourceDocumentIds"] or any(ref not in ids for ref in item["sourceDocumentIds"]):
                raise ValueError("盘中状态依据缺失")
            for halt in item["intradayHalts"]:
                first = datetime.fromisoformat(halt["startedAt"])
                last = datetime.fromisoformat(halt["endedAt"]) if halt["endedAt"] else None
                if first.tzinfo is None or first.date().isoformat() != item["date"] or not halt["reason"]:
                    raise ValueError("盘中时刻未绑定当地日期/时区")
                if last and (last.tzinfo is None or last <= first):
                    raise ValueError("恢复时刻不合法")
            by_date[item["date"]] = item
        days, cursor = [], start
        while cursor <= end:
            day = cursor.isoformat()
            days.append(by_date.get(day, {"date": day, "state": "unknown"}))
            cursor += timedelta(days=1)
        return {"days": days}
