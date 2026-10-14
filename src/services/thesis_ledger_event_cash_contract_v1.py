"""现金能力经济事实；事件信封共用，公告证据保留来源资格。"""

from hashlib import sha256
from typing import Annotated, Literal

from pydantic import Field, model_validator
from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_event_envelope_v1 import (
    Model, Text, Hash, Day, Time, Amount, EventRequest, EventResponse,
    Admission as EventAdmission, binding, correlate,
)


class Key(Model):
    kind: Literal['data']
    market: Literal['CN']
    assetType: Literal['ETF']
    capability: Literal['CASH_DISTRIBUTION']


class CashRequest(EventRequest[Key]):
    pass


class AnnouncementVisibility(Model):
    kind: Literal['announcement']
    announcedAt: Time


class DayVisibility(Model):
    kind: Literal['conservative-day']
    visibleDate: Day


class CashFact(Model):
    symbol: Text
    market: Literal['CN']
    instrumentType: Literal['ETF']
    type: Literal['CASH_DIVIDEND']
    cashAmount: Amount
    currency: Literal['CNY','HKD','USD']
    effectiveDate: Day
    recordDate: Day|None=None
    paymentDate: Day|None=None
    occurredAt: Time
    availableAt: Time
    provider: Text
    providerRevision: Text
    strategyVisibility: Annotated[AnnouncementVisibility | DayVisibility, Field(discriminator='kind')] | None = None

    @model_validator(mode='after')
    def optional_dates(self):
        if any(getattr(self,name) is None for name in self.model_fields_set if name in {'recordDate','paymentDate','strategyVisibility'}):
            raise ValueError('可选事件日期不能显式null')
        return self


Admission = EventAdmission[Key]


class ContentEvidence(Model):
    role: Literal['cashPublication']
    ref: Annotated[str,Field(pattern=r'^sha256:[a-f0-9]{64}$')]
    sha256: Hash
    content: str=Field(min_length=1,max_length=256*1024)

    @model_validator(mode='after')
    def original_bytes(self):
        if self.ref!='sha256:'+self.sha256 or sha256(self.content.encode('utf-8')).hexdigest()!=self.sha256:
            raise ValueError('事件原文字节摘要不匹配')
        return self


class CashObservation(EventResponse[Key, CashFact]):
    pass


class CashResult(CashObservation):
    evidence: list[ContentEvidence]=Field(min_length=1,max_length=1)

    @model_validator(mode='after')
    def publication(self):
        publication=self.evidence[0]
        admission=self.admission
        if admission.sourceRevision!=self.providerRevision or (publication.ref,publication.sha256)!=(admission.evidenceRef,admission.evidenceSha256):
            raise ValueError('现金公告原文、准入和来源修订不匹配')
        return self


def validate_cash(value):
    if isinstance(value, CashResult) or isinstance(value, dict) and 'evidence' in value:
        return CashResult.model_validate(value)
    return CashObservation.model_validate(value)


CONTRACT=CapabilityContract('CASH_DISTRIBUTION',CashRequest,validate_cash,validate_binding=binding,correlate_result=correlate)
