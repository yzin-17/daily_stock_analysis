"""按当前准入摘要校验逐事件日期映射，不授予历史覆盖或回填可见时间。"""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any
from urllib.parse import urlparse

from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot


def _strict_object(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("拆分映射字段不完整或包含未知字段")
    return value


def _date(value: Any) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("拆分映射日期无效")
    return value


def _ratio(value: Any) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise ValueError("拆分映射比例必须使用十进制文本")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("拆分映射比例无效") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("拆分映射比例必须为有限正数")
    return number


def resolve_split_mapping_observations(
    observations: list[dict[str, Any]], *, evidence_bytes: bytes,
    admission: dict[str, Any], observed_at: datetime,
) -> list[dict[str, Any]]:
    """调用方从受控证据存储读取原字节；摘要必须匹配当前准入记录。

    返回带证据引用的观测，不输出 canonical 事实。公告日期不冒充历史
    availableAt；HTTP 路由、当前策略及事件完整覆盖仍由调用方独立核验。
    """
    current = event_admission_snapshot(admission, observed_at=observed_at)
    key = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "SPLIT_EVENT"}
    target = {"providerId": "akshare", "upstreamSource": "eastmoney"}
    if current["routeKey"] != key or current["target"] != target:
        raise ValueError("拆分映射准入能力或来源不匹配")
    if len(evidence_bytes) > 1024 * 1024 or sha256(evidence_bytes).hexdigest() != current["evidenceSha256"]:
        raise ValueError("拆分映射证据摘要不匹配")

    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                raise ValueError("拆分映射存在重复字段")
            result[name] = value
        return result
    bundle = _strict_object(json.loads(evidence_bytes, object_pairs_hook=pairs),
                            {"contractVersion", "kind", "mappings"})
    if type(bundle["contractVersion"]) is not int or bundle["contractVersion"] != 1 or bundle["kind"] != "split-date-mapping":
        raise ValueError("拆分映射版本无效")
    if not isinstance(bundle["mappings"], list) or not 1 <= len(bundle["mappings"]) <= 1000:
        raise ValueError("拆分映射数量无效")
    mappings = {}
    for raw in bundle["mappings"]:
        item = _strict_object(raw, {"symbol", "sourceConversionDate", "sourceRatioPerUnit",
                                    "effectiveDate", "recordDate", "announcementDate", "documentUrl", "documentSha256"})
        symbol = item["symbol"]
        if not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", symbol):
            raise ValueError("拆分映射标的无效")
        source, effective, announcement = (_date(item[name]) for name in
                                           ("sourceConversionDate", "effectiveDate", "announcementDate"))
        if announcement > effective or effective < source:
            raise ValueError("拆分映射日期顺序无效")
        if item["recordDate"] is not None and _date(item["recordDate"]) > effective:
            raise ValueError("拆分登记日不能晚于除权日")
        if symbol not in current["scopeSymbols"] or not current["scopeDateFrom"] <= effective <= current["scopeDateTo"]:
            raise ValueError("拆分映射超出准入范围")
        url = urlparse(item["documentUrl"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("拆分映射公告引用无效")
        if not isinstance(item["documentSha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["documentSha256"]):
            raise ValueError("拆分映射公告摘要无效")
        identity = (symbol, source, _ratio(item["sourceRatioPerUnit"]))
        if identity in mappings:
            raise ValueError("拆分映射事件重复")
        mappings[identity] = item
    output = []
    for observation in observations:
        if observation.get("endpoint") != "fund_cf_em" or observation.get("upstreamSource") != "eastmoney":
            raise ValueError("拆分观测来源不匹配")
        identity = (observation["symbol"], observation["sourceConversionDate"], _ratio(observation["sourceRatioPerUnit"]))
        mapping = mappings.get(identity)
        if mapping is None:
            raise ValueError("拆分观测缺少逐事件日期映射")
        output.append({**deepcopy(observation), "effectiveDate": mapping["effectiveDate"],
                       "recordDate": mapping["recordDate"],
                       "effectivePhase": "ex-right-session", "announcementDate": mapping["announcementDate"],
                       "dateMappingEvidence": {"ref": current["evidenceRef"], "sha256": current["evidenceSha256"],
                                               "documentUrl": mapping["documentUrl"], "documentSha256": mapping["documentSha256"]}})
    return output
