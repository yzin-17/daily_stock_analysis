"""独立状态与生命周期能力合同；Schema 与目标相关性在来源调用前后校验。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Mapping, TypeAlias
import re

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from .thesis_ledger_capability_registry import CapabilityContract

Text = Annotated[str, Field(min_length=1)]
Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
DecimalString = Annotated[str, Field(max_length=64, pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")]
Ratio = Annotated[str, Field(max_length=64, pattern=r"^0\.[0-9]*[1-9][0-9]*$")]
MAX_INTEGER = 9007199254740991
TIMEZONES = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}
NAMES = ("DAILY_TRADING_STATUS", "RISK_WARNING", "DAILY_PRICE_RULE", "SECURITY_BASIC_DATES", "LISTING_PHASE")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("*")
    @classmethod
    def trim(cls, value):
        if isinstance(value, str) and value.strip() != value:
            raise ValueError("标识不得包含首尾空白")
        return value

    @field_validator("start", "end", "date", "ipoDate", "outDate", check_fields=False)
    @classmethod
    def validate_date_fields(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator("observedAt", "upstreamAt", "availableAt", "startedAt", "endedAt", check_fields=False)
    @classmethod
    def instants(cls, value):
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
                raise ValueError("时钟必须使用带时区的 ISO 格式")
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Range(Model):
    start: Day
    end: Day

    @model_validator(mode="after")
    def ordered(self):
        if self.start > self.end:
            raise ValueError("窗口起点晚于终点")
        return self


class Target(Model):
    providerId: Text
    upstreamSource: Text
    routeIndex: int = Field(ge=0, le=1)


class Key(Model):
    kind: Literal["data"]
    market: Literal["CN", "HK", "US"]
    assetType: Literal["STOCK"]


class TradingKey(Key):
    capability: Literal["DAILY_TRADING_STATUS"]


class RiskKey(Key):
    capability: Literal["RISK_WARNING"]


class PriceKey(Key):
    capability: Literal["DAILY_PRICE_RULE"]


class DatesKey(Key):
    capability: Literal["SECURITY_BASIC_DATES"]


class PhaseKey(Key):
    capability: Literal["LISTING_PHASE"]


class Request(Model):
    contractVersion: Literal[3]
    capabilityVersion: Literal[1]
    consumer: Literal["thesis-ledger"]
    requestId: Text
    symbol: Text
    routeTarget: Target
    policyRevision: int = Field(ge=1, le=MAX_INTEGER)

    @field_validator("contractVersion", "capabilityVersion", mode="before")
    @classmethod
    def integer_versions(cls, value):
        if type(value) is not int:
            raise ValueError("合同版本须为整数")
        return value

    @model_validator(mode="after")
    def identity(self):
        if not re.fullmatch(r"(?:[0-9]{6}\.(?:SH|SZ|BJ)|[0-9]{5}\.HK|[A-Z][A-Z0-9.-]{0,31}\.US)", self.symbol) or self.symbol == "00000.HK":
            raise ValueError("证券代码必须使用规范市场格式")
        suffix = self.symbol.rsplit(".", 1)[1]
        market = suffix if suffix in {"HK", "US"} else "CN"
        if market != self.routeKey.market:
            raise ValueError("证券代码与市场不一致")
        if hasattr(self, "clock") and self.clock.timezone != TIMEZONES[market]:
            raise ValueError("数据时区与市场不一致")
        return self


class TradingRequest(Request):
    routeKey: TradingKey
    range: Range


class CoarseRiskRequirement(Model):
    kind: Literal["coarseST"]


class RiskCategoriesRequirement(Model):
    kind: Literal["riskCategories"]
    categories: list[Literal["delisting", "other"]] = Field(min_length=1, max_length=2)

    @field_validator("categories")
    @classmethod
    def unique_categories(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("风险需求类别重复")
        return values


class RiskRequest(Request):
    routeKey: RiskKey
    range: Range
    detail: Annotated[CoarseRiskRequirement | RiskCategoriesRequirement, Field(discriminator="kind")]


class PriceRequest(Request):
    routeKey: PriceKey
    range: Range
    representation: Literal["rule", "exactPrices"]


class DatesRequest(Request):
    routeKey: DatesKey


class PhaseRequest(Request):
    routeKey: PhaseKey
    range: Range


class Evidence(Model):
    id: Text
    kind: Literal["document", "record", "calendar"]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None
    observedAt: str | None
    availableAt: str | None
    reference: str | None
    hashScope: Literal["originalBytes", "normalizedResponse", "unknown"]

    @model_validator(mode="after")
    def hash_nature(self):
        if ((self.sha256 is None) != (self.hashScope == "unknown") or
                self.kind != "document" and (self.hashScope != "normalizedResponse" or self.observedAt is None) or
                self.kind == "document" and (self.reference is None or self.hashScope == "normalizedResponse")):
            raise ValueError("摘要性质、观察时钟与原文引用不一致")
        if self.reference is not None:
            from urllib.parse import urlparse
            parsed = urlparse(self.reference)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError("原文引用不是有效 URL")
        return self


class Clock(Model):
    observedAt: str
    upstreamAt: str | None
    timezone: Literal["Asia/Shanghai", "Asia/Hong_Kong", "America/New_York"]
    basis: Literal["sessionDate", "effectiveDate", "unknown"]


class Provenance(Model):
    adapterRevision: Text
    sourceRevision: Text


class Fact(Model):
    date: Day
    evidenceRefs: list[Text]

    @field_validator("evidenceRefs")
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("事实引用重复")
        return values


class Halt(Model):
    startedAt: str
    endedAt: str | None

    @model_validator(mode="after")
    def ordered(self):
        if self.endedAt is not None and instant(self.startedAt) >= instant(self.endedAt):
            raise ValueError("日内停牌区间倒置")
        return self


class TradingFact(Fact):
    state: Literal["trading", "suspended", "unknown"]
    intradayHalts: list[Halt]


class RiskFact(Fact):
    coarseST: Literal["flagged", "notFlagged"] | None
    delisting: Literal["active", "none", "unknown"]
    other: Literal["active", "none", "unknown"]


class UnknownRule(Model):
    kind: Literal["unknown"]


class RatioRule(Model):
    kind: Literal["ratio"]
    reference: Literal["previousRawClose"]
    maxUpRatio: Ratio
    maxDownRatio: Ratio


class UnlimitedRule(Model):
    kind: Literal["unlimited"]
    reason: Text


class ExactRule(Model):
    kind: Literal["exact"]
    currency: Literal["CNY", "HKD", "USD"]
    lower: DecimalString
    upper: DecimalString

    @model_validator(mode="after")
    def bounds(self):
        if not Decimal(self.upper) >= Decimal(self.lower) > 0:
            raise ValueError("精确价格上下限无效")
        return self


class PriceFact(Fact):
    rule: Annotated[UnknownRule | RatioRule | UnlimitedRule | ExactRule, Field(discriminator="kind")]


class PhaseFact(Fact):
    phase: Literal["unknown", "notListed", "initialListing", "listed", "delistingPeriod", "delisted"]


class Coverage(Model):
    range: Range
    expectedDates: list[Day]
    calendarUnknownDates: list[Day]
    unknownDates: list[Day]
    calendarEvidenceRefs: list[Text]
    complete: bool


class ResultMetadata(Model):
    provenance: Provenance
    clock: Clock
    evidence: list[Evidence]

    @model_validator(mode="after")
    def evidence_clock(self):
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("证据标识重复")
        observed = instant(self.clock.observedAt)
        if self.clock.upstreamAt is not None and instant(self.clock.upstreamAt) > observed:
            raise ValueError("上游时钟晚于观察")
        for item in self.evidence:
            if (item.observedAt is not None and instant(item.observedAt) > observed) or (item.availableAt is not None and instant(item.availableAt) > observed):
                raise ValueError("证据时钟未到")
        return self


def known(value):
    if isinstance(value, TradingFact):
        return value.state != "unknown"
    if isinstance(value, RiskFact):
        return value.coarseST is not None or value.delisting != "unknown" or value.other != "unknown"
    if isinstance(value, PriceFact):
        return value.rule.kind != "unknown"
    return value.phase != "unknown"


def satisfies(value, request):
    if isinstance(value, RiskFact):
        if request.detail.kind == "coarseST":
            return value.coarseST is not None
        return all(getattr(value, category) != "unknown" for category in request.detail.categories)
    if isinstance(value, PriceFact) and request.representation == "exactPrices":
        return value.rule.kind in {"exact", "unlimited"}
    return known(value)


class WindowMetadata(ResultMetadata):
    coverage: Coverage

    @model_validator(mode="after")
    def window(self):
        start, end = self.range.start, self.range.end
        proof = self.coverage
        refs = {item.id: item.kind for item in self.evidence}
        by_date = {item.date: item for item in self.days}
        if proof.range != self.range:
            raise ValueError("覆盖窗口与请求不一致")
        for dates in ([item.date for item in self.days], proof.expectedDates, proof.calendarUnknownDates, proof.unknownDates):
            if dates != sorted(set(dates)) or any(not start <= date.fromisoformat(item).isoformat() <= end for item in dates):
                raise ValueError("日期越界、重复或乱序")
        for item in self.days:
            precise = ((isinstance(item, RiskFact) and (item.delisting != "unknown" or item.other != "unknown")) or
                       isinstance(item, (PhaseFact, PriceFact)) and known(item) or
                       isinstance(item, TradingFact) and bool(item.intradayHalts))
            if (any(ref not in refs for ref in item.evidenceRefs) or (known(item) or precise) and not item.evidenceRefs or
                    precise and not any(refs.get(ref) == "document" for ref in item.evidenceRefs)):
                raise ValueError("已知事实缺少对应证据或精细规则原文")
        unknown = [item for item in proof.expectedDates if item not in by_date or not satisfies(by_date[item], self)]
        complete = bool(proof.calendarEvidenceRefs) and not proof.calendarUnknownDates and not unknown
        if (set(proof.calendarUnknownDates) & set(proof.expectedDates) or
                len(set(proof.calendarEvidenceRefs)) != len(proof.calendarEvidenceRefs) or
                any(refs.get(ref) != "calendar" for ref in proof.calendarEvidenceRefs) or
                unknown != proof.unknownDates or bool(complete) != proof.complete):
            raise ValueError("覆盖声明与未知日期或日历证据不一致")
        return self


class TradingResult(TradingRequest, WindowMetadata):
    days: list[TradingFact]


class RiskResult(RiskRequest, WindowMetadata):
    days: list[RiskFact]


class PriceResult(PriceRequest, WindowMetadata):
    days: list[PriceFact]


class PhaseResult(PhaseRequest, WindowMetadata):
    days: list[PhaseFact]


class BasicDates(Model):
    ipoDate: Day | None
    outDate: Day | None
    ipoDateStatus: Literal["known", "notProvided", "invalid", "unavailable"]
    outDateStatus: Literal["known", "notProvided", "invalid", "unavailable"]
    sourceRecordId: Text = Field(default=None)
    reason: Text = Field(default=None)

    @model_validator(mode="after")
    def valid_dates(self):
        if (self.ipoDate is not None) != (self.ipoDateStatus == "known") or (self.outDate is not None) != (self.outDateStatus == "known"):
            raise ValueError("基础日期与已知状态不一致")
        if self.ipoDate and self.outDate and self.outDate < self.ipoDate:
            raise ValueError("摘牌日早于上市日")
        unavailable = "unavailable" in (self.ipoDateStatus, self.outDateStatus)
        if unavailable:
            if self.ipoDateStatus != "unavailable" or self.outDateStatus != "unavailable" or not self.reason or self.sourceRecordId:
                raise ValueError("来源不可用时须保留未知与原因")
        elif not self.sourceRecordId:
            raise ValueError("基础日期须保留实际结构化来源")
        return self


class DatesResult(DatesRequest, ResultMetadata):
    dates: BasicDates

    @model_validator(mode="after")
    def record(self):
        if self.dates.sourceRecordId and not any(item.id == self.dates.sourceRecordId and item.kind == "record" for item in self.evidence):
            raise ValueError("基础日期缺少独立原始记录")
        return self


StatusRequest: TypeAlias = TradingRequest | RiskRequest | PriceRequest | DatesRequest | PhaseRequest
StatusResult: TypeAlias = TradingResult | RiskResult | PriceResult | DatesResult | PhaseResult
REQUESTS = TypeAdapter(StatusRequest)
RESULTS = TypeAdapter(StatusResult)


def validate_binding(request: StatusRequest, key: Mapping, target: Mapping):
    if request.routeKey.model_dump() != dict(key) or (request.routeTarget.providerId, request.routeTarget.upstreamSource) != (
            target.get("providerId"), target.get("upstreamSource")):
        raise ValueError("请求与精确能力或目标绑定不一致")


def correlate(request: StatusRequest, result: StatusResult, target: Mapping) -> StatusResult:
    validate_binding(request, result.routeKey.model_dump(), target)
    fields = ("requestId", "symbol", "policyRevision", "routeKey", "routeTarget")
    fields += ("range", "detail", "representation")
    if any(getattr(request, field, None) != getattr(result, field, None) for field in fields):
        raise ValueError("功能结果身份、窗口、代次或目标不一致")
    return result


def validator(model):
    def validate(value):
        return model.model_validate(value.model_dump(exclude_unset=True) if isinstance(value, BaseModel) else value)
    return validate


TRADING_CONTRACT = CapabilityContract("DAILY_TRADING_STATUS", TradingRequest, validator(TradingResult),
                                      validate_binding=validate_binding, correlate_result=correlate)
RISK_CONTRACT = CapabilityContract("RISK_WARNING", RiskRequest, validator(RiskResult),
                                   validate_binding=validate_binding, correlate_result=correlate)
PRICE_CONTRACT = CapabilityContract("DAILY_PRICE_RULE", PriceRequest, validator(PriceResult),
                                    validate_binding=validate_binding, correlate_result=correlate)
DATES_CONTRACT = CapabilityContract("SECURITY_BASIC_DATES", DatesRequest, validator(DatesResult),
                                    validate_binding=validate_binding, correlate_result=correlate)
PHASE_CONTRACT = CapabilityContract("LISTING_PHASE", PhaseRequest, validator(PhaseResult),
                                    validate_binding=validate_binding, correlate_result=correlate)
