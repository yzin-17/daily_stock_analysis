"""拆分能力的标准经济事实；比例不是现金或价格复权因子。"""

from decimal import Decimal
from typing import Literal

from pydantic import model_validator
from .thesis_ledger_event_envelope_v1 import (
    Model, Text, Day, Time, Amount, ContentProof, EventRequest, EventResponse, binding, correlate,
)
from .thesis_ledger_capability_registry import CapabilityContract


class Key(Model):
    kind: Literal['data']
    market: Literal['CN']
    assetType: Literal['ETF']
    capability: Literal['SPLIT_EVENT']


class SplitRequest(EventRequest[Key]):
    pass


class SplitFact(Model):
    symbol: Text
    market: Literal['CN']
    instrumentType: Literal['ETF']
    type: Literal['SPLIT', 'REVERSE_SPLIT']
    ratio: Amount
    effectiveDate: Day
    recordDate: Day | None = None
    occurredAt: Time
    availableAt: Time
    provider: Text
    providerRevision: Text

    @model_validator(mode='after')
    def economic_ratio(self):
        ratio = Decimal(self.ratio)
        if ratio <= 0:
            raise ValueError('拆分比例必须为正')
        if 'recordDate' in self.model_fields_set and self.recordDate is None:
            raise ValueError('可选登记日不能显式null')
        return self


class SplitResult(EventResponse[Key, SplitFact]):
    dateMappingEvidence: ContentProof | None = None

    @model_validator(mode='after')
    def mapping_reference(self):
        if self.dateMappingEvidence and (self.dateMappingEvidence.ref, self.dateMappingEvidence.sha256) != (self.admission.evidenceRef, self.admission.evidenceSha256):
            raise ValueError('拆分映射原文与准入不匹配')
        return self


CONTRACT = CapabilityContract('SPLIT_EVENT', SplitRequest, SplitResult.model_validate,
                             validate_binding=binding, correlate_result=correlate)
