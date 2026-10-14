"""公共费用标准合同；十进制费率、收费日政策与文档引用保持独立事实。"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from .thesis_ledger_capability_registry import CapabilityContract

CAPABILITY = 'HISTORICAL_PUBLIC_FEES'


def iso_day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('日期须为ISO日')
    return value


def aware_time(value):
    if datetime.fromisoformat(value).utcoffset() is None:
        raise ValueError('时刻缺少时区')
    return value


def text(value):
    if not value or value.strip() != value:
        raise ValueError('文本为空或未规范化')
    return value


Day = Annotated[str, AfterValidator(iso_day)]
Time = Annotated[str, AfterValidator(aware_time)]
Text = Annotated[str, AfterValidator(text)]
Amount = Annotated[str, Field(pattern=r'^(?:0|[1-9]\d*)(?:\.\d+)?$')]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')

    @model_validator(mode='after')
    def explicit_optional_fields(self):
        if any(getattr(self, field) is None and field != 'publishedDate' for field in self.model_fields_set):
            raise ValueError('可选费用字段不能显式为null')
        return self


class FeeKey(Model):
    kind: Literal['data']
    market: Literal['CN', 'HK', 'US']
    assetType: Literal['STOCK']
    capability: Literal['HISTORICAL_PUBLIC_FEES']


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
            raise ValueError('费用请求区间倒置')
        return self


class FeeRequest(Model):
    contractVersion: Literal[3]
    consumer: Literal['thesis-ledger']
    requestId: Text
    routeKey: FeeKey
    routeTarget: Target
    symbol: Text
    range: Window

    @classmethod
    def from_request(cls, request):
        if request.instrument_type != 'STOCK' or any(getattr(request, field) is not None for field in ('limit', 'timeframe', 'adjustment')):
            raise ValueError('公共费用仅接受股票日期窗口')
        query = cls.model_validate({**dict(request.parameters or {}), 'symbol':request.symbol})
        if query.requestId != request.request_id or query.range.start != request.start or query.range.end != request.end:
            raise ValueError('公共费用参数与外层请求不一致')
        return query


class FeeRounding(Model):
    mode: Literal['ceil']
    decimalPlaces: Literal[0]


class Charge(Model):
    code: Literal['stampDuty', 'handlingFee', 'regulatoryFee', 'transferFee', 'afrcLevy', 'tradingActivityFee']
    side: Literal['buy', 'sell', 'both']
    basis: Literal['turnover', 'actualShares']
    rate: Amount
    currency: Literal['CNY', 'HKD', 'USD']
    sourceDocumentIds: list[Text] = Field(min_length=1)
    amountRounding: FeeRounding | None = None
    maximumAmount: Amount | None = None
    lowPriceExemption: Literal['belowPerShareRate'] | None = None

    @model_validator(mode='after')
    def dimensions(self):
        if self.code == 'tradingActivityFee':
            if (self.basis, self.currency, self.side) != ('actualShares', 'USD', 'sell') or self.maximumAmount is None or Decimal(self.maximumAmount) <= 0 or self.lowPriceExemption is None:
                raise ValueError('TAF股数/美元卖侧/上限/豁免不完整')
        elif self.basis != 'turnover' or self.maximumAmount is not None or self.lowPriceExemption is not None:
            raise ValueError('其他公共费项仅使用成交额基数')
        if self.amountRounding is not None and (self.code != 'stampDuty' or self.currency != 'HKD'):
            raise ValueError('仅港股印花税支持来源金额进位')
        if self.code == 'afrcLevy' and (self.currency, self.side) != ('HKD', 'both'):
            raise ValueError('AFRC仅港元双侧')
        return self


class SegmentRange(Model):
    start: Day
    endExclusive: Day


class Segment(Model):
    range: SegmentRange
    charges: list[Charge] = Field(min_length=1)


class Document(Model):
    id: Text
    title: Text
    url: Text
    publishedDate: Day | None
    kind: Literal['officialPublication', 'governmentNewsRepublication']
    sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')] | None = None
    observedAt: Time | None = None

    @model_validator(mode='after')
    def reference(self):
        parsed = urlsplit(self.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError('费用来源URL无效')
        return self


class ChargeDatePolicy(Model):
    basis: Literal['exchangeSettlement']
    settlementLagSessions: Literal[2]
    rateChangeChargeDate: Day
    effectiveTradeDate: Day
    calendarRevision: Text


class Provenance(Model):
    providerId: Text
    upstreamSource: Text
    providerRevision: Text
    datasetId: Text
    fetchedAt: Time
    exchange: Text
    tradingMethod: Literal['auction', 'exchangeTrading']
    calculationSettings: Literal['executionModel']
    historicalAvailability: Literal['notVerified']
    coverageDescription: Text | None = None
    chargeDatePolicy: ChargeDatePolicy | None = None


class FeeResult(FeeRequest):
    status: Literal['supported']
    segments: list[Segment] = Field(min_length=1)
    sourceDocuments: list[Document] = Field(min_length=1)
    provenance: Provenance

    @model_validator(mode='after')
    def complete(self):
        if (self.provenance.providerId, self.provenance.upstreamSource) != (self.routeTarget.providerId, self.routeTarget.upstreamSource):
            raise ValueError('费用目标与实际来源不一致')
        ids = [document.id for document in self.sourceDocuments]
        if len(ids) != len(set(ids)):
            raise ValueError('费用文档ID重复')
        cursor = self.range.start
        stop = (date.fromisoformat(self.range.end) + timedelta(days=1)).isoformat()
        used = set()
        for segment in self.segments:
            if segment.range.start != cursor or not cursor < segment.range.endExclusive <= stop:
                raise ValueError('费用分段缺口、重叠或越界')
            codes = [(charge.code, charge.side) for charge in segment.charges]
            if len(codes) != len(set(codes)):
                raise ValueError('同一费用分段费项重复')
            used.update(ref for charge in segment.charges for ref in charge.sourceDocumentIds)
            cursor = segment.range.endExclusive
        if cursor != stop or used != set(ids):
            raise ValueError('费用窗口或原文引用不完整')
        return self


def binding(request, key, target):
    if request.routeKey.model_dump() != dict(key) or (request.routeTarget.providerId, request.routeTarget.upstreamSource) != (target.get('providerId'), target.get('upstreamSource')):
        raise ValueError('费用请求与精确绑定不一致')


def correlate(request, result, target):
    for field in FeeRequest.model_fields:
        if getattr(result, field) != getattr(request, field):
            raise ValueError('费用结果与请求身份/窗口不一致')
    return result


CONTRACT = CapabilityContract(CAPABILITY, FeeRequest, FeeResult.model_validate,
                              validate_binding=binding, correlate_result=correlate)
