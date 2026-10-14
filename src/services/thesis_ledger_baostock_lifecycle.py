"""BaoStock 单证券基础日期；复用既有 SDK 与日期解析，不推导最后交易日。"""
from contextlib import nullcontext
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from data_provider.base import DataFetchError
from src.services.thesis_ledger_tradability_provider import _result_frame, _safe_date

LIFECYCLE_REVISION = "baostock-basic-dates-v1"
BASIC_FIELDS = "code,ipoDate,outDate,type"


def _field(value):
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None, "notProvided"
    parsed = _safe_date(text)
    if parsed is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) or parsed.isoformat() != text:
        return None, "invalid"
    return text, "known"


def normalize_baostock_lifecycle(basic, *, code: str, observed_at: str):
    if len(basic) > 1:
        raise ValueError("基础日期响应不是唯一证券")
    if not len(basic):
        raise ValueError("基础日期没有证券行")
    raw = {}
    if len(basic):
        row = basic.iloc[0]
        if str(row.get("code", "")) != code or str(row.get("type", "")) != "1":
            raise ValueError("基础日期证券身份或类型不符")
        raw = {field: str(row.get(field, "")) for field in BASIC_FIELDS.split(',')}
    ipo, ipo_state = _field(raw.get("ipoDate"))
    out, out_state = _field(raw.get("outDate"))
    instant = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("基础日期观察时钟无时区")
    today = instant.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if ipo and ipo > today:
        ipo, ipo_state = None, "invalid"
    if out and (out > today or (ipo and out < ipo)):
        out, out_state = None, "invalid"
    digest = sha256(json.dumps({"code": code, "raw": raw, "normalizerRevision": LIFECYCLE_REVISION},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"ipoDate": ipo, "outDate": out, "ipoDateStatus": ipo_state, "outDateStatus": out_state,
            "responseSha256": digest, "observedAt": observed_at, "normalizerRevision": LIFECYCLE_REVISION}


def read_baostock_lifecycle(adapter: Any, symbol: str, *, clock=lambda: datetime.now(timezone.utc)):
    code = adapter._convert_stock_code(symbol)
    if not re.fullmatch(r"(?:sh\.(?:60|68)\d{4}|sz\.(?:00|30)\d{4})", code):
        raise ValueError("基础日期仅支持明确沪深股票")
    try:
        with adapter._baostock_session() as sdk:
            result = sdk.query_stock_basic(code=code)
            basic = _result_frame(result, "证券基础日期")
            if str(result.error_code) != "0":
                raise RuntimeError("基础日期分页读取失败")
        return normalize_baostock_lifecycle(basic, code=code, observed_at=clock().isoformat())
    except (DataFetchError, OSError, RuntimeError, ValueError, KeyError, TypeError):
        return {"ipoDate": None, "outDate": None, "ipoDateStatus": "unavailable",
                "outDateStatus": "unavailable", "reason": "基础日期来源不可用或身份校验失败"}


def read_status_with_lifecycle(adapter: Any, *, symbol: str, start: date, end: date):
    from src.services.thesis_ledger_baostock_daily_status import read_baostock_daily_status
    with adapter._baostock_session() as sdk:
        borrowed = SimpleNamespace(_convert_stock_code=adapter._convert_stock_code,
                                   _baostock_session=lambda: nullcontext(sdk))
        result = read_baostock_daily_status(borrowed, symbol=symbol, start=start, end=end)
        result["basicLifecycle"] = read_baostock_lifecycle(borrowed, symbol)
    return result


def attach_basic_lifecycle(response: dict, value: dict):
    life = value.get("basicLifecycle")
    if not life:
        return response
    field_names = ("ipoDate", "outDate", "ipoDateStatus", "outDateStatus", "reason")
    response["basicLifecycle"] = {k: life[k] for k in field_names if k in life}
    if life.get("responseSha256"):
        record = {**response["sourceRecords"][0], "id": "baostock-basic-" + life["responseSha256"],
            "queryMethod": "query_stock_basic", "queryFields": BASIC_FIELDS,
            "normalizerRevision": LIFECYCLE_REVISION, "responseSha256": life["responseSha256"],
            "observedAt": life["observedAt"]}
        response["sourceRecords"].append(record)
        response["basicLifecycle"]["sourceRecordId"] = record["id"]
    return response
