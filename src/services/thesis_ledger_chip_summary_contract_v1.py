"""当前 CN 股票筹码估算摘要，不宣称原始持仓或跨源算法等价。"""

from datetime import date, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .thesis_ledger_capability_registry import CapabilityContract

CAPABILITY = 'CHIP_SUMMARY'


class ChipRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    symbol: str
    instrument_type: Literal['STOCK'] = 'STOCK'

    @field_validator('symbol')
    @classmethod
    def identity(cls, value):
        if not re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', value):
            raise ValueError('筹码摘要需要完整内地证券代码')
        return value

    def route_key(self):
        return {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': CAPABILITY}

    @classmethod
    def from_request(cls, request):
        if request.parameters or any(getattr(request, name) is not None
                for name in ('start', 'end', 'limit', 'timeframe', 'adjustment')):
            raise ValueError('当前筹码不接受历史、限量、周期、复权或额外参数')
        return cls(symbol=request.symbol, instrument_type=request.instrument_type or 'STOCK')


class ChipResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False, revalidate_instances='always')
    symbol: str
    capability: Literal['CHIP_SUMMARY'] = CAPABILITY
    average_cost: float = Field(gt=0)
    profit_ratio: float = Field(ge=0, le=1)
    range70: tuple[float, float]
    range90: tuple[float, float]
    concentration70: float = Field(ge=0, le=1)
    concentration90: float = Field(ge=0, le=1)
    currency: Literal['CNY'] = 'CNY'
    quality: Literal['estimated'] = 'estimated'
    methodology: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    upstream_source: str = Field(min_length=1)
    source_reported_date: str | None = None
    observed_at: str
    identity_basis: Literal['request_bound'] = 'request_bound'

    @model_validator(mode='after')
    def facts(self):
        ChipRequest(symbol=self.symbol)
        for low, high in (self.range70, self.range90):
            if not 0 < low <= high:
                raise ValueError('筹码成本区间无序或非正')
        if self.source_reported_date is not None and date.fromisoformat(self.source_reported_date).isoformat() != self.source_reported_date:
            raise ValueError('来源报告日期不是 ISO 日')
        if datetime.fromisoformat(self.observed_at).utcoffset() is None:
            raise ValueError('观察时间缺时区')
        return self


def binding(request, key, target):
    if request.route_key() != dict(key):
        raise ValueError('筹码请求与精确能力键不匹配')


def correlate(request, result, target):
    if result.symbol != request.symbol or result.provider != target.get('providerId') or result.upstream_source != target.get('upstreamSource'):
        raise ValueError('筹码结果证券或来源不匹配')
    return result


CONTRACT = CapabilityContract(CAPABILITY, ChipRequest, ChipResult.model_validate,
                              validate_binding=binding, correlate_result=correlate)
