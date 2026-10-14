"""读取有界公共收费数据包；金额计算设置由已选执行模型提供。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

CAPABILITY = "HISTORICAL_PUBLIC_FEES"
PROVIDER_ID = "official-rules"
UPSTREAM_SOURCE = "cn-stock-auction-fees"
FEE_CODES = {"stampDuty", "handlingFee", "regulatoryFee", "transferFee"}
DATA_PATH = Path(__file__).resolve().parents[2] / "data_provider/public_fees/cn-stock-auction-2023-08.json"
ARCHIVE_SOURCE = "cn-stock-auction-fees-archive"
ARCHIVE_DATA_PATH = DATA_PATH.with_name("cn-stock-auction-archive-2023-08.json")


def public_fee_route_matches(key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    return key == {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": CAPABILITY} and (
        target.get("providerId") == PROVIDER_ID and target.get("upstreamSource") in (UPSTREAM_SOURCE, ARCHIVE_SOURCE)
    )


def public_fee_source_ready(target: Mapping[str, Any] | None = None) -> bool:
    """目录只说明本地来源可调用，具体身份与窗口在实际读取时检查。"""
    path = ARCHIVE_DATA_PATH if target and target.get("upstreamSource") == ARCHIVE_SOURCE else DATA_PATH
    return path.is_file()


def build_public_fee_manifest() -> dict[str, Any]:
    from src.services.provider_manifest import build_provider_manifest

    manifest = build_provider_manifest(
        PROVIDER_ID, "官方公开规则（本地）", {CAPABILITY: ("STOCK",)},
        upstream_sources=(
            (UPSTREAM_SOURCE, "上交所 A 股竞价收费规则"),
            (ARCHIVE_SOURCE, "公开收费规则备份资料"),
        ),
        markets=("CN",), configuration_mode="built_in",
    )
    for source in manifest["upstreamSources"]:
        source["capabilities"] = {CAPABILITY: ["STOCK"]}
    from src.services.thesis_ledger_local_history_manifest import attach_local_history
    return attach_local_history(manifest, {UPSTREAM_SOURCE: DATA_PATH, ARCHIVE_SOURCE: ARCHIVE_DATA_PATH})


class PublicFeeSourceError(ValueError):
    """公共收费读取的计算相关错误，由调用方映射到既有网关错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _day(value: Any) -> date:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise PublicFeeSourceError("invalid_request", "收费日期必须是 YYYY-MM-DD") from exc


def _validate_charges(charges: Any, document_ids: set[str]) -> None:
    if not isinstance(charges, list) or {item.get("code") for item in charges if isinstance(item, dict)} != FEE_CODES:
        raise PublicFeeSourceError("invalid_response", "公共收费项目缺失或未知")
    if len(charges) != len(FEE_CODES):
        raise PublicFeeSourceError("invalid_response", "公共收费项目重复")
    for fee in charges:
        if not isinstance(fee.get("rate"), str) or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", fee["rate"]):
            raise PublicFeeSourceError("invalid_response", "公共收费费率必须是非负 Decimal 字符串")
        if fee.get("side") not in {"buy", "sell", "both"} or fee.get("basis") != "turnover" or fee.get("currency") != "CNY":
            raise PublicFeeSourceError("invalid_response", "公共收费计算口径不合法")
        sources = fee.get("sourceDocumentIds")
        if not isinstance(sources, list) or not sources or any(item not in document_ids for item in sources):
            raise PublicFeeSourceError("invalid_response", "公共收费来源引用缺失")


class OfficialPublicFeeProvider:
    """读取明确选中的本地规则包，不自行切换来源或联网补数据。"""

    def __init__(self, data_path: Path | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        self.data_path = data_path or DATA_PATH
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def read(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise PublicFeeSourceError("invalid_request", "公共收费请求必须是对象")
        try:
            return self._read(request)
        except PublicFeeSourceError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise PublicFeeSourceError("invalid_response", "公共收费数据包结构无效") from exc

    def _read(self, request: Mapping[str, Any]) -> dict[str, Any]:
        key = request.get("routeKey")
        target = request.get("routeTarget")
        expected_key = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": CAPABILITY}
        if key != expected_key:
            raise PublicFeeSourceError("unsupported_capability", "该公共收费源只覆盖 CN STOCK")
        if not isinstance(target, Mapping) or not public_fee_route_matches(key, target):
            raise PublicFeeSourceError("unsupported_capability", "公共收费请求未选择本来源")
        if type(target.get("routeIndex")) is not int or target.get("routeIndex") not in (0, 1):
            raise PublicFeeSourceError("invalid_request", "公共收费目标序号不合法")
        symbol = request.get("symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"6\d{5}\.SH", symbol):
            raise PublicFeeSourceError("not_covered", "该公共收费包只覆盖上交所 A 股竞价")
        requested_range = request.get("range")
        if not isinstance(requested_range, Mapping):
            raise PublicFeeSourceError("invalid_request", "公共收费请求缺少区间")
        start, end = _day(requested_range.get("start")), _day(requested_range.get("end"))
        if start > end:
            raise PublicFeeSourceError("invalid_request", "收费起点不得晚于终点")
        if request.get("contractVersion") != 3 or request.get("consumer") != "thesis-ledger" or not request.get("requestId"):
            raise PublicFeeSourceError("invalid_request", "公共收费请求合同无效")

        try:
            path = ARCHIVE_DATA_PATH if target["upstreamSource"] == ARCHIVE_SOURCE else self.data_path
            pack = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PublicFeeSourceError("upstream_failure", "公共收费数据包无法读取") from exc
        scope = pack["scope"]
        if scope != {"market": "CN", "exchange": "XSHG", "assetType": "STOCK", "tradingMethod": "auction"}:
            raise PublicFeeSourceError("invalid_response", "公共收费数据包身份不匹配")
        coverage = pack["coverage"]
        if start < _day(coverage["start"]) or end > _day(coverage["end"]):
            raise PublicFeeSourceError("insufficient_coverage", "公共收费包未覆盖请求日期")

        stop = end + timedelta(days=1)
        cursor = start
        documents = pack["sourceDocuments"]
        document_ids = {item["id"] for item in documents}
        segments: list[dict[str, Any]] = []
        for segment in pack["segments"]:
            left = max(start, _day(segment["range"]["start"]))
            right = min(stop, _day(segment["range"]["endExclusive"]))
            if left >= right:
                continue
            if left != cursor:
                raise PublicFeeSourceError("insufficient_coverage", "本次收费区间存在缺口或重叠")
            _validate_charges(segment["charges"], document_ids)
            segments.append({"range": {"start": left.isoformat(), "endExclusive": right.isoformat()}, "charges": segment["charges"]})
            cursor = right
        if cursor != stop:
            raise PublicFeeSourceError("insufficient_coverage", "公共收费规则未覆盖请求终点")
        digest = hashlib.sha256(json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        used_documents = {item for segment in segments for fee in segment["charges"] for item in fee["sourceDocumentIds"]}
        return {
            "contractVersion": 3,
            "consumer": "thesis-ledger",
            "requestId": request["requestId"],
            "routeKey": dict(key),
            "routeTarget": dict(target),
            "symbol": symbol,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "status": "supported",
            "segments": segments,
            "sourceDocuments": [item for item in documents if item["id"] in used_documents],
            "provenance": {
                "providerId": PROVIDER_ID,
                "upstreamSource": target["upstreamSource"],
                "providerRevision": f"official-public-fees-{digest}",
                "datasetId": pack["datasetId"],
                "fetchedAt": self.clock().astimezone(timezone.utc).isoformat(),
                "exchange": scope["exchange"],
                "tradingMethod": scope["tradingMethod"],
                "calculationSettings": pack["calculationSettings"],
                "historicalAvailability": pack["historicalAvailability"],
            },
        }
