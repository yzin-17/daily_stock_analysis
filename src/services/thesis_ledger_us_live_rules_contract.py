"""独立美国当前执行规则；来源观察与法规生效期、核查覆盖期分别保存。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class RuleSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    id: str
    url: str
    sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class UsPriceIncrements(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    atOrAboveOne: Literal['0.01']
    belowOne: Literal['0.0001']


class UsLiveRules(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    version: Literal[1]
    ruleSetId: str
    market: Literal['US']
    currency: Literal['USD']
    assetTypes: list[Literal['STOCK', 'ETF']]
    exchanges: list[Literal['US_NYSE', 'US_NASDAQ', 'US_AMEX']]
    effectiveFrom: str
    effectiveUntil: str
    verifiedThrough: str
    reviewedAt: str
    observedAt: str
    provider: Literal['official-us-rules']
    priceIncrements: UsPriceIncrements
    positionSettlementDays: Literal[1]
    sellCashSettlementDays: Literal[1]
    section31SellRate: Literal['0.0000206']
    tafSellRatePerShare: Literal['0']
    tafSellMaximum: Literal['0']
    sources: list[RuleSource]
