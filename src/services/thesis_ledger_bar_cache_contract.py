"""交互K线的只读控制身份；与主仓公共Schema对应。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .thesis_ledger_minute_contract_v1 import minute_market


class BarCacheRoute(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['bar']
    market: Literal['CN', 'HK', 'US']
    assetType: Literal['STOCK', 'ETF']
    capability: Literal['DAILY_BAR', 'MINUTE_BAR']
    timeframe: Literal['1m', '5m', '15m', '30m', '60m', '1d']
    adjustment: Literal['none', 'qfq', 'hfq']


class BarCacheTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    providerId: str = Field(min_length=1)
    upstreamSource: str = Field(min_length=1)
    routeIndex: int = Field(ge=0, le=1)


class BarCacheRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    contractVersion: Literal[1]
    consumer: Literal['thesis-ledger']
    requestId: str = Field(min_length=1)
    purpose: Literal['interactive-chart']
    capability: Literal['DAILY_BAR', 'MINUTE_BAR']
    symbol: str = Field(min_length=1, max_length=64)
    routeKey: BarCacheRoute
    routeTarget: BarCacheTarget
    start: str
    end: str

    @model_validator(mode='after')
    def identity(self):
        key, target = self.routeKey, self.routeTarget
        for value in [self.requestId, self.symbol, target.providerId, target.upstreamSource]:
            if value.strip() != value:
                raise ValueError('K线控制身份未规范化')
        if minute_market(self.symbol) != key.market or self.capability != key.capability:
            raise ValueError('K线控制市场或能力错配')
        if (key.timeframe == '1d') != (key.capability == 'DAILY_BAR'):
            raise ValueError('K线控制能力与周期错配')
        if key.timeframe != '1d' and key.adjustment != 'none':
            raise ValueError('分钟交互仅支持不复权')
        if (date.fromisoformat(self.start).isoformat() != self.start
                or date.fromisoformat(self.end).isoformat() != self.end or self.start > self.end):
            raise ValueError('K线控制需要有效日期闭区间')
        return self
