"""当前每手股数能力合同；历史变化和价格单位依据保持独立。"""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_security_lot_values import hk_symbol, reference_digest

CAPABILITY = "SECURITY_LOT_SIZE"


class LotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str
    instrument_type: Literal["STOCK"] = "STOCK"

    @field_validator("symbol", mode="before")
    @classmethod
    def canonical(cls, value):
        return hk_symbol(value)

    def route_key(self):
        return {"kind": "data", "market": "HK", "assetType": "STOCK", "capability": CAPABILITY}

    @classmethod
    def from_request(cls, request):
        if request.parameters or any(getattr(request, field) is not None
                for field in ("start", "end", "timeframe", "adjustment")):
            raise ValueError("每手仅查询当前参考，不接受历史或额外参数")
        return cls(symbol=request.symbol, instrument_type=request.instrument_type)


class LotSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    provider: str = Field(min_length=1)
    sourceUrl: str
    observedAt: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def observation(self):
        url = urlsplit(self.sourceUrl)
        if url.scheme != "https" or not url.netloc or datetime.fromisoformat(self.observedAt).utcoffset() is None:
            raise ValueError("每手参考须保留来源 URL 和带时区的观察时刻")
        return self


class LotResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str
    sharesPerLot: str = Field(pattern=r"^[1-9][0-9]{0,14}$")
    historicalChanges: Literal["unverified"]
    source: LotSource

    @model_validator(mode="after")
    def reference(self):
        if self.symbol != hk_symbol(self.symbol) or self.source.sha256 != reference_digest(
                self.symbol, self.sharesPerLot, self.source.provider, self.source.observedAt):
            raise ValueError("每手参考证券或观察摘要不匹配")
        return self


def validate_binding(request, key, target):
    if request.route_key() != dict(key):
        raise ValueError("每手参考请求与精确能力键不匹配")


def correlate(request, result, target):
    if result.symbol != request.symbol or result.source.provider != target.get("providerId"):
        raise ValueError("每手参考结果与证券或来源不匹配")
    return result


CONTRACT = CapabilityContract(CAPABILITY, LotRequest, LotResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
