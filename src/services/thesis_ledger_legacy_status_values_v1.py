"""旧聚合状态的强类型字段；证据角色不从其他事实推定。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('状态日期须为ISO日')
    return value


def time(value):
    if datetime.fromisoformat(value).utcoffset() is None:
        raise ValueError('状态时刻缺少时区')
    return value


def text(value):
    if not value or value.strip() != value:
        raise ValueError('状态文本为空或未规范化')
    return value


def ratio(value):
    if not 0 < Decimal(value) < 1:
        raise ValueError('日限比例须为0与1之间的十进制字符串')
    return value


Day = Annotated[str, AfterValidator(day)]
Time = Annotated[str, AfterValidator(time)]
Text = Annotated[str, AfterValidator(text)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Ratio = Annotated[str, Field(pattern=r'^(?:0|[1-9]\d*)(?:\.\d+)?$'), AfterValidator(ratio)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')

    @model_validator(mode='after')
    def null_fields(self):
        if any(getattr(self, name) is None and name not in {'publishedDate', 'endedAt', 'ipoDate', 'outDate'} for name in self.model_fields_set):
            raise ValueError('可选状态字段不能显式为null')
        return self


class Key(Model):
    kind: Literal['data']
    market: Literal['CN', 'HK', 'US']
    assetType: Literal['STOCK']
    capability: Literal['HISTORICAL_TRADING_STATUS']


class Target(Model):
    providerId: Text
    upstreamSource: Text
    routeIndex: int = Field(ge=0, le=1)


class Window(Model):
    start: Day
    end: Day

    @model_validator(mode='after')
    def ordered(self):
        if self.start > self.end:
            raise ValueError('状态窗口倒置')
        return self


class Risk(Model):
    delisting: Literal['active', 'none', 'unknown']
    other: Literal['active', 'none', 'unknown']

    @model_validator(mode='after')
    def known(self):
        if self.delisting == self.other == 'unknown':
            raise ValueError('未知风险不得声明为精细事实')
        return self


class PriceLimit(Model):
    reference: Literal['previousRawClose']
    maxUpRatio: Ratio
    maxDownRatio: Ratio


class NoLimit(Model):
    reason: Text


class Halt(Model):
    startedAt: Time
    endedAt: Time | None
    reason: Text

    @model_validator(mode='after')
    def ordered(self):
        if self.endedAt is not None and datetime.fromisoformat(self.endedAt) <= datetime.fromisoformat(self.startedAt):
            raise ValueError('恢复时刻必须晚于暂停')
        return self


class StatusDay(Model):
    date: Day
    state: Literal['unknown', 'suspended', 'trading']
    sourceDocumentIds: list[Text] | None = Field(default=None, min_length=1)
    sourceRecordIds: list[Text] | None = Field(default=None, min_length=1)
    coarseST: Literal['flagged', 'notFlagged'] | None = None
    listingPhase: Literal['notListed', 'initialListing', 'listed', 'delistingPeriod', 'delisted'] | None = None
    noDailyPriceLimit: NoLimit | None = None
    riskWarning: Risk | None = None
    dailyPriceLimit: PriceLimit | None = None
    intradayHalts: list[Halt] | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def facts(self):
        precise = any(getattr(self, name) is not None for name in ('riskWarning', 'dailyPriceLimit', 'noDailyPriceLimit', 'listingPhase', 'intradayHalts'))
        known = self.state != 'unknown' or precise or self.coarseST is not None
        if known != bool(self.sourceDocumentIds or self.sourceRecordIds):
            raise ValueError('日事实与实际来源引用不匹配')
        if precise and not self.sourceDocumentIds:
            raise ValueError('精细或盘中事实须引用公告')
        if self.coarseST is not None and not self.sourceRecordIds:
            raise ValueError('粗ST须引用结构化来源')
        if self.dailyPriceLimit is not None and self.noDailyPriceLimit is not None:
            raise ValueError('日限与无日限互斥')
        if self.state == 'trading' and self.listingPhase in {'notListed', 'delisted'}:
            raise ValueError('未上市/已摘牌不能声明可交易')
        if any(halt.startedAt[:10] != self.date for halt in self.intradayHalts or []):
            raise ValueError('盘中暂停未绑定当地日期')
        return self


class Document(Model):
    id: Text
    title: Text
    url: Text
    publishedDate: Day | None
    kind: Literal['issuerDisclosure', 'exchangePublication']
    sha256: Hash | None = None
    observedAt: Time | None = None

    @model_validator(mode='after')
    def reference(self):
        parsed = urlsplit(self.url)
        if not parsed.scheme or not parsed.netloc or (self.sha256 is None) != (self.observedAt is None):
            raise ValueError('文档URL或摘要/观察时钟不匹配')
        return self


class Record(Model):
    id: Text
    kind: Literal['normalizedQueryResponse']
    providerId: Text
    upstreamSource: Text
    symbol: Text
    range: Window
    queryMethod: Text
    queryFields: Text
    normalizerRevision: Text
    responseSha256: Hash
    observedAt: Time


class Coverage(Model):
    expectedSessions: list[Day]
    calendarUnknownDates: list[Day]
    statusUnknownDates: list[Day]
    stUnknownDates: list[Day]
    complete: bool


class Lifecycle(Model):
    ipoDate: Day | None
    outDate: Day | None
    ipoDateStatus: Literal['known', 'notProvided', 'invalid', 'unavailable']
    outDateStatus: Literal['known', 'notProvided', 'invalid', 'unavailable']
    sourceRecordId: Text | None = None
    reason: Text | None = None

    @model_validator(mode='after')
    def facts(self):
        for name in ('ipoDate', 'outDate'):
            if (getattr(self, name) is not None) != (getattr(self, name + 'Status') == 'known'):
                raise ValueError('基础日期与状态不一致')
        if self.ipoDate and self.outDate and self.outDate < self.ipoDate:
            raise ValueError('摘牌日早于上市日')
        unavailable = 'unavailable' in (self.ipoDateStatus, self.outDateStatus)
        if unavailable and (self.ipoDateStatus != 'unavailable' or self.outDateStatus != 'unavailable'):
            raise ValueError('整体不可用时两日期均须未知')
        if (unavailable and (not self.reason or self.sourceRecordId)) or (not unavailable and not self.sourceRecordId):
            raise ValueError('基础日期实际来源/不可用理由不完整')
        return self


class Provenance(Model):
    providerId: Text
    upstreamSource: Text
    providerRevision: Text
    datasetId: Text
    fetchedAt: Time
    granularity: Literal['1d', 'intraday']
    timezone: Literal['Asia/Shanghai', 'Asia/Hong_Kong', 'America/New_York'] | None = None
    historicalAvailability: Literal['notVerified']
