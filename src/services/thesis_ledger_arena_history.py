"""自主比赛的历史重建；采集时钟与证券生效区间分别保留。"""

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from typing import Literal

from .thesis_ledger_feature_policy import feature_provider_epoch
from .thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest
from .thesis_ledger_minute_contract_v1 import MinuteResult
from .thesis_ledger_provider_errors import classify_provider_failure


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contractVersion: Literal[3]
    consumer: Literal["thesis-ledger"]
    requestId: str
    market: Literal["CN"]
    date: str

    def day(self):
        if date.fromisoformat(self.date).isoformat() != self.date:
            raise ValueError("历史日期格式无效")
        if self.date > datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat():
            raise ValueError("不能读取未来日期")
        if not self.requestId.strip():
            raise ValueError("缺少请求标识")
        return self.date


class MinuteHistoryRequest(HistoryRequest):
    symbol: str
    assetType: Literal["STOCK", "ETF"]
    timeframe: Literal["1m", "5m", "15m", "30m", "60m"] = "1m"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def source_failure(error, endpoint):
    code = classify_provider_failure(error)
    message = "历史目录接口不可用（" + endpoint + "）"
    # 只投影固定频率字段，不透传 SDK 原文或连接信息。
    limit = re.search(r"每(分钟|小时|天|日)最多访问该接口\s*(\d+)\s*次", str(error))
    if not limit and getattr(error, 'api_code', None) in (2002, 40203):
        code = 'permission_denied'
    api_code = getattr(error, 'api_code', None)
    if type(api_code) is int:
        message += "，来源代码 " + str(api_code)
    if code == 'rate_limited' and limit:
        message += "，每" + limit[1] + "最多" + limit[2] + "次"
    return ProviderCallError(code, message)


def archive(runtime, value):
    """内容寻址原始重建资料，保存在控制库同一持久化目录。"""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    key = sha256(raw).hexdigest()
    directory = Path(runtime.store.database_path).parent / "arena-history"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (key + ".json")
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ProviderCallError("invalid_response", "历史资料内容摘要冲突") from None
    return "arena-history/" + key + ".json"


def _date(value, required=False):
    if value is None or str(value) in {"", "None", "nan", "NaT"}:
        if required:
            raise ProviderCallError("invalid_response", "历史目录缺少生效日期")
        return None
    raw = str(value)
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        raise ProviderCallError("invalid_response", "历史目录日期无效") from None


def project_catalog(datasets, day):
    """完整代码关联 ETF 类型与基金日期；名称回退代码，避免引入现名。"""
    funds = {row["ts_code"]: row for key in ("fund_L", "fund_D") for row in datasets[key]}
    items, seen = [], set()
    groups = [("STOCK", row, state) for state in ("L", "D", "P") for row in datasets["stock_" + state]]
    for state in ("L", "D"):
        for row in datasets["etf_" + state]:
            fund = funds.get(row["ts_code"])
            if not fund or row.get("list_date") != fund.get("list_date"):
                raise ProviderCallError("insufficient_coverage", "ETF 生命周期关联不完整")
            groups.append(("ETF", fund, state))
    for asset_type, row, state in groups:
        symbol = row.get("ts_code")
        if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise ProviderCallError("invalid_response", "历史目录证券身份无效")
        if symbol in seen:
            raise ProviderCallError("invalid_response", "历史目录证券重复")
        seen.add(symbol)
        listed, delisted = _date(row.get("list_date"), True), _date(row.get("delist_date"), state == "D")
        if delisted and delisted < listed:
            raise ProviderCallError("invalid_response", "历史目录生效区间倒置")
        if listed <= day and (not delisted or day < delisted):
            items.append({"symbol": symbol, "assetType": asset_type, "name": symbol,
                          "listedOn": listed})
    if not items or not any(item["symbol"].endswith(".BJ") for item in items if item["assetType"] == "STOCK"):
        raise ProviderCallError("insufficient_coverage", "历史全市场目录缺少北交所或证券集合")
    return sorted(items, key=lambda item: item["symbol"])


def read_catalog(runtime, request):
    day = request.day()
    epoch = feature_provider_epoch(runtime.store, ["tushare"])
    if epoch["tushare"][1] is not True or runtime.store.effective_policy_v3().get("enabled") is not True:
        raise ProviderCallError("disabled", "历史目录来源未启用")
    snapshot = runtime.store.provider_credential_snapshot("tushare")
    adapter = runtime._adapter("tushare", snapshot=snapshot, cache=False)
    from data_provider.tushare_history_request import TushareHistoryRequest
    try:
        budget = TushareHistoryRequest(adapter, 25)
    except Exception:
        raise ProviderCallError("credential_missing", "历史目录来源缺少有效凭证") from None
    datasets = {}
    for api, states, fields, limit in (
        ("stock", ("L", "D", "P"), "ts_code,list_date,delist_date,list_status", 6000),
        ("etf", ("L", "D"), "ts_code,list_date,list_status", 5000),
        ("fund", ("L", "D"), "ts_code,list_date,delist_date,status", 15000),
    ):
        for state in states:
            budget.before_call()
            parameters = {"fields": fields}
            parameters["status" if api == "fund" else "list_status"] = state
            if api == "fund":
                parameters["market"] = "E"
            try:
                frame = budget.client.query(api + "_basic", **parameters)
            except Exception as error:
                raise source_failure(error, api + "_basic") from None
            budget.check_complete()
            if frame is None or not set(fields.split(",")) <= set(frame.columns) or len(frame) >= limit:
                raise ProviderCallError("insufficient_coverage", "历史目录字段或单次容量不足")
            if state == "L" and frame.empty:
                raise ProviderCallError("empty_response", "历史目录上市集合为空")
            datasets[api + "_" + state] = json.loads(frame.to_json(orient="records", force_ascii=False))
    if (feature_provider_epoch(runtime.store, ["tushare"]) != epoch
            or runtime.store.provider_credential_snapshot("tushare") != snapshot):
        raise ProviderCallError("not_admitted", "历史目录读取期间来源配置已变化")
    observed = datetime.now(timezone.utc).isoformat()
    ref = archive(runtime, {"kind": "catalog", "observedAt": observed, "providerVersion": epoch["tushare"][0], "datasets": datasets})
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": request.requestId,
            "kind": "reconstructed-catalog", "market": "CN", "date": day,
            "observedAt": observed, "providerId": "tushare", "archiveRef": ref,
            "providerVersion": epoch["tushare"][0], "responseSha256": digest(datasets),
            "items": project_catalog(datasets, day)}


def read_minutes(runtime, request):
    day = request.day()
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", request.symbol):
        raise ValueError("历史分钟证券身份无效")
    query = ThesisLedgerDataRequest("MINUTE_BAR", request.symbol, timeframe=request.timeframe, start=day, end=day,
        instrument_type=request.assetType, adjustment="none", request_id=request.requestId,
        parameters={"historical_reconstruction": True})
    key = {"kind": "bar", "market": "CN", "assetType": request.assetType,
           "capability": "MINUTE_BAR", "timeframe": request.timeframe, "adjustment": "none"}
    execution = runtime.execute_market_bars_v3(query, key)
    frame = execution.value
    value = MinuteResult.model_validate({"symbol": request.symbol, "assetType": request.assetType,
        "market": "CN", "source": execution.upstream_source, "timeframe": request.timeframe,
        "rows": frame.to_dict("records"), **{key: frame.attrs[key] for key in (
            "observedAt", "timezone", "session", "timestampConvention", "responseSha256")},
        "fieldUnits": frame.attrs["intradayFieldUnits"]})
    if value.timestampConvention == "source-native":
        raise ProviderCallError("insufficient_coverage", "来源未声明分钟时间约定")
    ref = archive(runtime, {"kind": "minutes", "providerId": execution.provider, "policyRevision": execution.effective_revision, "value": value.model_dump()})
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": request.requestId, "archiveRef": ref,
            "kind": "reconstructed-minutes", "date": day, "providerId": execution.provider,
            "policyRevision": execution.effective_revision, "value": value.model_dump()}
