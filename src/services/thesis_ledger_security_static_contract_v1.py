"""当前证券资料的标准能力合同；不承诺历史身份或交易规则。"""

from datetime import date, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .thesis_ledger_capability_registry import CapabilityContract

CAPABILITY = "SECURITY_STATIC_INFO"


def security_market(symbol: str) -> str:
    if re.fullmatch(r"[0-9]{5}\.HK", symbol) and int(symbol[:5]) > 0:
        return "HK"
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,31}\.US", symbol) and not symbol[:-3].endswith(
            (".HK", ".SH", ".SZ", ".BJ", ".OF")):
        return "US"
    raise ValueError("当前资料需要规范港美证券代码")


class StaticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str
    instrument_type: Literal["STOCK", "ETF"]

    @model_validator(mode="after")
    def identity(self):
        security_market(self.symbol)
        return self

    def route_key(self):
        return {"kind": "data", "market": security_market(self.symbol),
                "assetType": self.instrument_type, "capability": CAPABILITY}

    @classmethod
    def from_request(cls, request):
        if request.parameters or any(getattr(request, field) is not None
                for field in ("start", "end", "timeframe", "adjustment")):
            raise ValueError("当前资料不接受历史、复权或额外查询参数")
        return cls(symbol=request.symbol, instrument_type=request.instrument_type)


class StaticResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str
    market: Literal["HK", "US"]
    assetType: Literal["STOCK", "ETF"]
    name: str | None
    securityId: str | None = Field(pattern=r"^[1-9][0-9]*$")
    sharesPerLot: str | None = Field(pattern=r"^[1-9][0-9]{0,14}$")
    listingDate: str | None
    delisted: bool | None
    exchange: str | None
    provider: str = Field(min_length=1)
    observedAt: str
    historicalChanges: Literal["unverified"]

    @model_validator(mode="after")
    def observation(self):
        if security_market(self.symbol) != self.market:
            raise ValueError("资料市场与证券身份不匹配")
        if self.listingDate is not None and date.fromisoformat(self.listingDate).isoformat() != self.listingDate:
            raise ValueError("上市日期必须为 ISO 日期")
        if datetime.fromisoformat(self.observedAt).utcoffset() is None:
            raise ValueError("当前资料观察时间须包含时区")
        return self


def validate_binding(request, key, target):
    if request.route_key() != dict(key):
        raise ValueError("当前资料请求与精确能力键不匹配")


def correlate(request, result, target):
    if (result.symbol, result.market, result.assetType, result.provider) != (
            request.symbol, security_market(request.symbol), request.instrument_type, target.get("providerId")):
        raise ValueError("当前资料结果与请求或来源不匹配")
    return result


CONTRACT = CapabilityContract(CAPABILITY, StaticRequest, StaticResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
