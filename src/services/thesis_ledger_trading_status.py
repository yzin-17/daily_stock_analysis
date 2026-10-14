"""读取真实发行人日级停复牌安排；未披露日期保持 unknown。"""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping

CAPABILITY = "HISTORICAL_TRADING_STATUS"
PROVIDER_ID = "official-states"
UPSTREAM_SOURCE = "cn-stock-daily-suspension"
DATA_PATH = Path(__file__).resolve().parents[2] / "data_provider/trading_status/000995-2023-08-suspension.json"
RISK_UPSTREAM_SOURCE = "cn-stock-risk-price-limits"
RISK_DATA_PATH = DATA_PATH.with_name("000995-2023-08-risk-limits.json")
LIFECYCLE_PATHS = {
    "cn-stock-initial-listing": DATA_PATH.with_name("603350-2024-07-listing.json"),
    "cn-stock-delisting-period": DATA_PATH.with_name("600462-2025-07-delisting.json"),
}
ROUTE_KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": CAPABILITY}


class TradingStatusSourceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def trading_status_route_matches(key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    return (key == ROUTE_KEY and target.get("providerId") == PROVIDER_ID
            and target.get("upstreamSource") in {UPSTREAM_SOURCE, RISK_UPSTREAM_SOURCE, *LIFECYCLE_PATHS})


def build_trading_status_manifest() -> dict[str, Any]:
    from src.services.provider_manifest import build_provider_manifest

    manifest = build_provider_manifest(
        PROVIDER_ID, "发行人停复牌公告（本地）", {CAPABILITY: ("STOCK",)},
        upstream_sources=((UPSTREAM_SOURCE, "发行人停复牌公告"),
                          (RISK_UPSTREAM_SOURCE, "发行人风险警示与涨跌幅公告"),
                          ("cn-stock-initial-listing", "发行人上市初期与日价格限制公告"),
                          ("cn-stock-delisting-period", "发行人退市整理与摘牌公告")),
        markets=("CN",), configuration_mode="built_in",
    )
    for source in manifest["upstreamSources"]:
        source["capabilities"] = {CAPABILITY: ["STOCK"]}
        source["documentCatalog"] = "announcements"
    from src.services.thesis_ledger_local_history_manifest import attach_local_history
    return attach_local_history(manifest, {UPSTREAM_SOURCE: DATA_PATH, RISK_UPSTREAM_SOURCE: RISK_DATA_PATH, **LIFECYCLE_PATHS})


def trading_status_source_ready(target: Mapping[str, Any] | None = None) -> bool:
    path = RISK_DATA_PATH if target and target.get("upstreamSource") == RISK_UPSTREAM_SOURCE else DATA_PATH
    if target and target.get("upstreamSource") in LIFECYCLE_PATHS:
        return LIFECYCLE_PATHS[target["upstreamSource"]].is_file()
    if path.is_file():
        return True
    from src.services.thesis_ledger_announcements import AnnouncementRepository
    return bool(AnnouncementRepository().records())


def _day(value: Any) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TradingStatusSourceError("invalid_request", "交易状态日期必须为 YYYY-MM-DD") from exc


class OfficialTradingStatusProvider:
    """只读当前明确来源，不抓公告或隐式切换其他来源。"""

    def __init__(self, data_path: Path | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        self.data_path = data_path or DATA_PATH
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def read(self, request: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return self._read(request)
        except TradingStatusSourceError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise TradingStatusSourceError("invalid_response", "交易状态数据包结构无效") from exc

    def _read(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if request.get("contractVersion") != 3 or request.get("consumer") != "thesis-ledger" or not request.get("requestId"):
            raise TradingStatusSourceError("invalid_request", "交易状态请求合同不匹配")
        key, target = request.get("routeKey"), request.get("routeTarget")
        if not isinstance(target, Mapping) or not trading_status_route_matches(key, target):
            raise TradingStatusSourceError("unsupported_capability", "交易状态请求未选择本来源")
        if type(target.get("routeIndex")) is not int or target["routeIndex"] not in (0, 1):
            raise TradingStatusSourceError("invalid_request", "交易状态目标序号不合法")
        window = request["range"]
        start, end = _day(window["start"]), _day(window["end"])
        if start > end:
            raise TradingStatusSourceError("invalid_request", "交易状态请求起点不得晚于终点")
        try:
            path = self.data_path
            if path == DATA_PATH and target["upstreamSource"] == RISK_UPSTREAM_SOURCE:
                path = RISK_DATA_PATH
            elif path == DATA_PATH and target["upstreamSource"] in LIFECYCLE_PATHS:
                path = LIFECYCLE_PATHS[target["upstreamSource"]]
            pack = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            from src.services.thesis_ledger_announcement_status import library_status_pack
            pack = library_status_pack({"symbol": None, "sourceDocuments": [], "days": []}, request["symbol"],
                                       start.isoformat(), end.isoformat(), target["upstreamSource"])
            if pack is None:
                raise TradingStatusSourceError("upstream_failure", "公告资料无法读取") from exc
        except (OSError, ValueError) as exc:
            raise TradingStatusSourceError("upstream_failure", "交易状态数据包无法读取") from exc
        if (target["upstreamSource"] in {RISK_UPSTREAM_SOURCE, *LIFECYCLE_PATHS}
                and pack.get("upstreamSource") != target["upstreamSource"]):
            raise TradingStatusSourceError("invalid_response", "状态扩展包来源身份不匹配")
        if request["symbol"] != pack["symbol"] or start < _day(pack["coverage"]["start"]) or end > _day(pack["coverage"]["end"]):
            if target["upstreamSource"] in LIFECYCLE_PATHS:
                raise TradingStatusSourceError("not_covered", "上市及退市来源不覆盖本标的或窗口")
            from src.services.thesis_ledger_announcement_status import library_status_pack
            pack = library_status_pack(pack, request["symbol"], start.isoformat(), end.isoformat(), target["upstreamSource"])
            if pack is None:
                raise TradingStatusSourceError("not_covered", "公告库尚未收录该标的")
        documents = pack["sourceDocuments"]
        from src.services.thesis_ledger_status_dimensions import validate_status_documents, validate_status_dimensions

        validate_status_documents(documents, self.clock())
        document_ids = {item["id"] for item in documents}
        by_date = {}
        for item in pack["days"]:
            day = _day(item["date"]).isoformat()
            if day in by_date or item["state"] not in {"trading", "suspended", "unknown"}:
                raise TradingStatusSourceError("invalid_response", "交易状态日期重复或值非法")
            validate_status_dimensions(item)
            refs = item["sourceDocumentIds"]
            if not refs or any(ref not in document_ids for ref in refs):
                raise TradingStatusSourceError("invalid_response", "已知交易状态缺少来源引用")
            by_date[day] = item
        days = []
        cursor = start
        while cursor <= end:
            day = cursor.isoformat()
            days.append(by_date.get(day, {"date": day, "state": "unknown"}))
            cursor += timedelta(days=1)
        used = {ref for item in days for ref in item.get("sourceDocumentIds", [])}
        digest = sha256(json.dumps(pack, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        return {
            "contractVersion": 3, "consumer": "thesis-ledger", "requestId": request["requestId"],
            "routeKey": dict(key), "routeTarget": dict(target), "symbol": request["symbol"],
            "range": {"start": start.isoformat(), "end": end.isoformat()}, "status": "supported",
            "days": days, "sourceDocuments": [item for item in documents if item["id"] in used],
            "provenance": {
                "providerId": PROVIDER_ID, "upstreamSource": target["upstreamSource"],
                "providerRevision": f"official-trading-status-{digest}", "datasetId": pack["datasetId"],
                "fetchedAt": self.clock().astimezone(timezone.utc).isoformat(),
                "granularity": "1d", "historicalAvailability": "notVerified",
            },
        }
