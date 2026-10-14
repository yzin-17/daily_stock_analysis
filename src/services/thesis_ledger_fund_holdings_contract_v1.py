"""当前基金持仓标准结果；观察摘要不表示披露原文证据。"""

from datetime import datetime
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_fund_identity_v1 import fund_symbol

CAPABILITY = 'FUND_HOLDINGS'


class Holding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False, revalidate_instances='always')
    symbol: str = Field(min_length=1)
    name: str = Field(min_length=1)
    weight: float = Field(ge=0, le=1)

    @model_validator(mode='after')
    def strings(self):
        if self.symbol.strip().upper() != self.symbol or self.name.strip() != self.name:
            raise ValueError('持仓代码或名称未规范化')
        return self


def holdings_digest(holdings) -> str:
    values = [holding.model_dump() for holding in holdings]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


class HoldingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    symbol: str
    instrument_type: Literal['MUTUAL_FUND'] = 'MUTUAL_FUND'
    _symbol = field_validator('symbol')(fund_symbol)

    def route_key(self):
        return {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': CAPABILITY}

    @classmethod
    def from_request(cls, request):
        if request.parameters or any(getattr(request, name) is not None for name in ('start', 'end', 'limit', 'timeframe', 'adjustment')):
            raise ValueError('当前持仓不接受历史、年份、限量、周期、复权或额外参数')
        return cls(symbol=request.symbol, instrument_type=request.instrument_type or 'MUTUAL_FUND')


class HoldingsResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')
    symbol: str
    capability: Literal['FUND_HOLDINGS'] = CAPABILITY
    report_period: str = Field(pattern=r'^[0-9]{4}-Q[1-4]$')
    disclosure_date: str | None
    holdings: tuple[Holding, ...] = Field(min_length=1)
    provider: str = Field(min_length=1)
    upstream_source: str = Field(min_length=1)
    observed_at: str
    source_endpoint: str | None = None
    source_query_year: int | None = Field(default=None, ge=2000)
    source_observed_at: str | None = None
    evidence_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    identity_basis: Literal['request_bound'] = 'request_bound'
    _symbol = field_validator('symbol')(fund_symbol)

    @model_validator(mode='after')
    def integrity(self):
        if len({holding.symbol for holding in self.holdings}) != len(self.holdings):
            raise ValueError('同季度持仓代码重复')
        if sum(holding.weight for holding in self.holdings) > 1.000001:
            raise ValueError('持仓权重合计超过1')
        if tuple(sorted(self.holdings, key=lambda holding: (-holding.weight, holding.symbol))) != self.holdings:
            raise ValueError('持仓排序不符合合同')
        if holdings_digest(self.holdings) != self.evidence_version:
            raise ValueError('持仓观察摘要不匹配')
        if self.source_query_year is not None and int(self.report_period[:4]) != self.source_query_year:
            raise ValueError('持仓报告年与来源查询年不匹配')
        for clock in (self.observed_at, self.source_observed_at, self.disclosure_date):
            if clock is not None and datetime.fromisoformat(clock).utcoffset() is None:
                raise ValueError('持仓观察/披露时间须含时区')
        return self


def validate_binding(request, key, target):
    if request.route_key() != dict(key):
        raise ValueError('持仓请求与能力身份不匹配')


def correlate(request, result, target):
    if (result.symbol, result.provider, result.upstream_source) != (
            request.symbol, target.get('providerId'), target.get('upstreamSource')):
        raise ValueError('持仓结果与基金或来源不匹配')
    return result


CONTRACT = CapabilityContract(CAPABILITY, HoldingsRequest, HoldingsResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
