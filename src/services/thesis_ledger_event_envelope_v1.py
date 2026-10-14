"""事件能力的固定信封；经济事实由现金/拆分各自的模型校验。"""

from datetime import date
from hashlib import sha256
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from .thesis_ledger_event_time_v1 import instant


def day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('事件日期不是ISO日')
    return value


def time(value):
    instant(value)
    return value


def text(value):
    if not value or value.strip() != value:
        raise ValueError('事件文本为空或未规范化')
    return value


Day = Annotated[str, AfterValidator(day)]
Time = Annotated[str, AfterValidator(time)]
Text = Annotated[str, AfterValidator(text)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Amount = Annotated[str, Field(pattern=r'^(?:0|[1-9]\d*)(?:\.\d+)?$')]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')


KeyT = TypeVar('KeyT', bound=BaseModel)
FactT = TypeVar('FactT', bound=BaseModel)


class TargetIdentity(Model):
    providerId: Text
    upstreamSource: Text


class Target(TargetIdentity):
    routeIndex: int = Field(ge=0, le=1)


class EventRequest(Model, Generic[KeyT]):
    contractVersion: Literal[3]
    requestId: Text
    symbol: Annotated[str, Field(pattern=r'^[0-9]{6}\.(SH|SZ)$')]
    routeKey: KeyT
    routeTarget: Target
    desiredRevision: int = Field(gt=0)
    effectivePolicyRevision: int = Field(gt=0)
    catalogRevision: int = Field(gt=0)
    start: Day
    end: Day
    dataAsOf: Time

    @model_validator(mode='after')
    def requested_state(self):
        if self.start > self.end or self.desiredRevision != self.effectivePolicyRevision:
            raise ValueError('事件窗口或政策修订不匹配')
        return self


class Admission(Model, Generic[KeyT]):
    consumer: Literal['thesis-ledger']
    routeKey: KeyT
    target: TargetIdentity
    status: Literal['admitted']
    admissionState: Literal['admitted']
    evidenceRef: Text
    evidenceSha256: Hash
    scopeSymbols: list[Text] = Field(min_length=1)
    scopeDateFrom: Day
    scopeDateTo: Day
    adapterRevision: Text
    sourceRevision: Text
    credentialRevision: Text
    validFrom: Time
    validUntil: Time
    recordVersion: int = Field(gt=0)
    recordedAt: Time
    invalidatedAt: None
    invalidationReason: None


class ContentProof(Model):
    ref: Annotated[str, Field(pattern=r'^sha256:[a-f0-9]{64}$')]
    sha256: Hash
    content: str = Field(min_length=1, max_length=1024*1024)

    @model_validator(mode='after')
    def bytes(self):
        content = self.content.encode('utf-8')
        if len(content) > 1024*1024 or self.ref != 'sha256:'+self.sha256 or sha256(content).hexdigest() != self.sha256:
            raise ValueError('事件原文字节摘要不匹配')
        return self


class Coverage(Model):
    complete: bool
    admissionEvidenceRef: Text | None = None
    reason: Text | None = None

    @model_validator(mode='after')
    def state(self):
        if self.complete:
            if not self.admissionEvidenceRef or self.reason is not None:
                raise ValueError('完整事件覆盖缺准入引用')
        elif not self.reason or self.admissionEvidenceRef is not None:
            raise ValueError('事件覆盖未知须明确原因')
        return self


class EventResponse(EventRequest[KeyT], Generic[KeyT, FactT]):
    fetchedAt: Time
    providerRevision: Text
    admission: Admission[KeyT]
    coverage: Coverage
    facts: list[FactT] = Field(max_length=100000)
    identityEvidence: ContentProof | None = None

    @model_validator(mode='after')
    def correlate_facts(self):
        admission = self.admission
        if admission.routeKey != self.routeKey or (admission.target.providerId, admission.target.upstreamSource) != (self.routeTarget.providerId, self.routeTarget.upstreamSource):
            raise ValueError('事件准入来源不符')
        if self.symbol not in admission.scopeSymbols or admission.scopeDateFrom > self.start or admission.scopeDateTo < self.end:
            raise ValueError('事件准入窗口不符')
        fetched = instant(self.fetchedAt)
        if not instant(admission.validFrom) <= fetched < instant(admission.validUntil) or instant(admission.recordedAt) > fetched:
            raise ValueError('事件读取时准入失效')
        if self.coverage.complete and self.coverage.admissionEvidenceRef != admission.evidenceRef:
            raise ValueError('完整覆盖与当前准入证据不符')
        if self.identityEvidence and (self.identityEvidence.ref, self.identityEvidence.sha256) != (admission.evidenceRef, admission.evidenceSha256):
            raise ValueError('身份原文与准入证据不符')
        seen = set()
        for fact in self.facts:
            if (fact.symbol, fact.provider, fact.providerRevision) != (self.symbol, self.routeTarget.providerId, self.providerRevision) or not self.start <= fact.effectiveDate <= self.end:
                raise ValueError('事件身份/修订/日期不符')
            available = instant(fact.availableAt)
            if available > instant(self.dataAsOf) or available > fetched or fact.effectiveDate in seen:
                raise ValueError('事件事实时间或唯一性不符')
            seen.add(fact.effectiveDate)
        return self


def binding(query, key, target):
    if query.routeKey.model_dump() != dict(key) or (query.routeTarget.providerId, query.routeTarget.upstreamSource) != (target.get('providerId'), target.get('upstreamSource')):
        raise ValueError('事件请求与绑定不符')


def correlate(query, result, target):
    if any(getattr(query, name) != getattr(result, name) for name in EventRequest.model_fields):
        raise ValueError('事件响应未匹配请求')
    return result
