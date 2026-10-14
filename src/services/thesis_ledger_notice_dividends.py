"""读取已核对的管理人公告现金分红；来源观察不签发 V3 准入。"""

from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlparse

DATA_PATH = Path(__file__).resolve().parents[2] / "data_provider/cash_distributions/510300-2023-01.json"
PROVIDER_ID = "official-dividends"
UPSTREAM_SOURCE = "cn-etf-cash-notices"
ROUTE_KEY = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"}
ROUTE_TARGET = {"providerId": PROVIDER_ID, "upstreamSource": UPSTREAM_SOURCE}


def build_notice_dividend_manifest():
    from src.services.provider_manifest import build_provider_manifest
    manifest = build_provider_manifest(
        PROVIDER_ID, "管理人现金分红公告（本地）", {"CASH_DISTRIBUTION": ("ETF",)},
        upstream_sources=((UPSTREAM_SOURCE, "基金管理人现金分红公告"),),
        markets=("CN",), configuration_mode="built_in",
    )
    from src.services.thesis_ledger_local_history_manifest import attach_local_history
    return attach_local_history(manifest, {UPSTREAM_SOURCE: DATA_PATH})


def notice_dividend_route_matches(key, target):
    return key == ROUTE_KEY and all(target.get(field) == value for field, value in ROUTE_TARGET.items())


def notice_dividend_revisions():
    try:
        raw = DATA_PATH.read_bytes()
        _validate_pack(json.loads(raw, object_pairs_hook=_unique_object))
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return None
    return {"adapterRevision": "official-cash-notices-v1",
            "sourceRevision": f"official-cash-notices-sha256:{sha256(raw).hexdigest()}",
            "credentialRevision": "not-required"}


class NoticeDividendError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NoticeDividendError("invalid_response", message)


def _day(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期必须为 YYYY-MM-DD")
    return date.fromisoformat(value)


def _instant(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise ValueError("时刻必须明确时区")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "来源包存在重复 JSON 字段")
        result[key] = value
    return result


def _validate_pack(pack):
    _require(type(pack["schemaVersion"]) is int and pack["schemaVersion"] == 1, "来源包版本无效")
    _require(pack["market"] == "CN" and pack["instrumentType"] == "ETF", "来源包不是境内 ETF")
    _require(isinstance(pack["symbol"], str) and bool(re.fullmatch(r"\d{6}\.SH", pack["symbol"])), "缺完整证券身份")
    _require(pack["currency"] == "CNY" and isinstance(pack["datasetId"], str) and bool(pack["datasetId"].strip()), "缺显式人民币或数据集身份")
    coverage = pack["coverage"]
    start, end = _day(coverage["start"]), _day(coverage["end"])
    _require(_day(coverage["reportStart"]) <= start <= end <= _day(coverage["reportEnd"]), "覆盖窗口超出报告期")
    _require(coverage["basis"] == "annual-report-cash-distributions", "缺独立现金分配覆盖依据")
    documents = {}
    _require(isinstance(pack["sourceDocuments"], list) and isinstance(pack["events"], list), "来源文档与事件必须为列表")
    for document in pack["sourceDocuments"]:
        doc_id = document["id"]
        _require(isinstance(doc_id, str) and bool(doc_id) and doc_id not in documents, "来源文档身份重复或缺失")
        url = urlparse(document["url"])
        _require(url.scheme == "https" and url.hostname in {"static.cninfo.com.cn", "www.sse.com.cn"}, "来源不是已支持的披露地址")
        _require(isinstance(document["title"], str) and bool(document["title"].strip()) and bool(re.fullmatch(r"[a-f0-9]{64}", document["sha256"])), "缺来源标题或原件摘要")
        _require(document["pages"] and all(type(page) is int and page > 0 for page in document["pages"]), "缺已核验页码")
        _require(_day(document["publishedDate"]) <= _instant(document["observedAt"]).date(), "来源核验早于发布日期")
        documents[doc_id] = document
    report = documents[coverage["sourceDocumentId"]]
    _require(report["kind"] == "issuerAnnualReport" and _day(report["publishedDate"]) > _day(coverage["reportEnd"]), "覆盖依据不是期后年报")
    seen = set()
    for event in pack["events"]:
        effective = _day(event["effectiveDate"])
        _require(start <= effective <= end and effective not in seen, "事件生效日重复或超出覆盖")
        seen.add(effective)
        _require(_day(event["announcementDate"]) <= _day(event["recordDate"]) < effective <= _day(event["paymentDate"]), "分红日期次序无效")
        _require(event["currency"] == pack["currency"] and event["unit"] == "CNY/10-fund-units", "币种或每十份单位无效")
        amount = event["cashAmount"]
        _require(isinstance(amount, str) and bool(re.fullmatch(r"(?:0|[1-9]\d{0,17})(?:\.\d{1,12})?", amount)), "分红金额不是十进制字符串")
        _require(Decimal(amount) > 0, "分红金额必须为正数")
        notice = documents[event["sourceDocumentId"]]
        _require(notice["kind"] == "issuerDisclosure" and notice["publishedDate"] == event["announcementDate"], "事件未绑定对应公告")
    _require(isinstance(coverage["eventDates"], list), "独立事件清单必须为列表")
    expected = [_day(value) for value in coverage["eventDates"]]
    _require(len(set(expected)) == len(expected) and seen == set(expected), "事件集合与独立覆盖清单不一致")
    _require(bool(documents), "缺来源文档")
    return start, end, max(_instant(doc["observedAt"]) for doc in documents.values())


class OfficialNoticeDividendProvider:
    """本地来源观察；完整性仅针对包内独立核验的现金事件窗口。"""

    def __init__(self, data_path: Path | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        self.data_path = data_path or DATA_PATH
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def read(self, symbol: str, start: str, end: str, data_as_of: str) -> dict[str, Any]:
        try:
            first, last, cutoff = _day(start), _day(end), _instant(data_as_of)
            if first > last:
                raise ValueError("起点晚于终点")
        except (TypeError, ValueError) as exc:
            raise NoticeDividendError("invalid_request", "分红查询日期或冻结截点无效") from exc
        try:
            raw = self.data_path.read_bytes()
        except OSError as exc:
            raise NoticeDividendError("upstream_failure", "分红来源包无法读取") from exc
        try:
            _require(len(raw) <= 256 * 1024, "分红来源包超出字节上限")
            pack = json.loads(raw, object_pairs_hook=_unique_object)
            covered_start, covered_end, observed = _validate_pack(pack)
            fetched = self.clock()
            _require(fetched.tzinfo is not None and fetched >= observed, "当前读取时刻无效或早于来源核验")
        except NoticeDividendError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise NoticeDividendError("invalid_response", "分红来源包结构无效") from exc
        if symbol != pack["symbol"]:
            raise NoticeDividendError("not_covered", "分红来源包未覆盖该完整证券代码")
        if first < covered_start or last > covered_end:
            raise NoticeDividendError("insufficient_coverage", "分红来源包未覆盖请求窗口")
        if cutoff < observed:
            raise NoticeDividendError("not_available", "分红来源核验晚于冻结截点")
        revision = f"official-cash-notices-sha256:{sha256(raw).hexdigest()}"
        facts = []
        for event in pack["events"]:
            if not start <= event["effectiveDate"] <= end:
                continue
            with localcontext() as context:
                context.prec = 40
                amount = format(Decimal(event["cashAmount"]) / Decimal(10), "f")
                if "." in amount:
                    amount = amount.rstrip("0").rstrip(".")
            facts.append({
                "symbol": symbol, "market": "CN", "instrumentType": "ETF", "type": "CASH_DIVIDEND",
                "cashAmount": amount, "currency": "CNY", "effectiveDate": event["effectiveDate"],
                "recordDate": event["recordDate"], "paymentDate": event["paymentDate"],
                "occurredAt": f"{event['effectiveDate']}T00:00:00+08:00",
                "availableAt": observed.isoformat(), "provider": PROVIDER_ID, "providerRevision": revision,
            })
        return {
            "symbol": symbol, "range": {"start": start, "end": end}, "facts": facts,
            "coverage": {"complete": True, "capability": "CASH_DISTRIBUTION", "basis": pack["coverage"]},
            "sourceDocuments": pack["sourceDocuments"],
            "noticeDividendEvidence": {"ref": f"sha256:{sha256(raw).hexdigest()}",
                                       "sha256": sha256(raw).hexdigest(), "content": raw.decode("utf-8")},
            "provenance": {"providerId": PROVIDER_ID, "upstreamSource": UPSTREAM_SOURCE,
                           "providerRevision": revision, "datasetId": pack["datasetId"],
                           "fetchedAt": fetched.astimezone(timezone.utc).isoformat(),
                           "historicalAvailability": "notVerified"},
        }
