"""RQ事件通过原映射/凭据/隔离执行，不接收Runtime或供应商SDK DTO。"""

from datetime import datetime, timezone
from decimal import Decimal

from src.services.rqdata_fund_identity_evidence import resolve_rqdata_fund_identity
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot
from src.services.thesis_ledger_rqdata_mapped_read import read_rqdata_mapped_fund_event, RqDataIdentityNotAdmitted
from src.services.thesis_ledger_rqdata_read import RqDataCredentialNotAdmitted
from src.services.thesis_ledger_event_cash_contract_v1 import CashRequest, CashObservation
from src.services.thesis_ledger_event_split_contract_v1 import SplitRequest, SplitResult
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext

RQDATA_EVENT_TIMEOUT_SECONDS = 30


def read_event(query: CashRequest | SplitRequest, context: EventExecutionContext):
    before = context.admitted_state()
    credentials = context.credentials
    if credentials is None:
        raise CapabilityContractError('invalid_request', '事件缺少绑定账号执行上下文')
    kind = 'split' if query.routeKey.capability == 'SPLIT_EVENT' else 'dividend'
    try:
        raw = read_rqdata_mapped_fund_event(
            kind, query.symbol, database_path=credentials.database_path,
            start=query.start, end=query.end, data_as_of=query.dataAsOf,
            read_admission=credentials.read_admission, read_credentials=credentials.read_credentials,
            read_master_key=credentials.read_master_key, build_factory=credentials.build_adapter,
            timeout_seconds=RQDATA_EVENT_TIMEOUT_SECONDS, maximum_rows=2000,
        )
    except (RqDataCredentialNotAdmitted, RqDataIdentityNotAdmitted):
        raise CapabilityContractError('not_admitted', 'RQData事件账号或身份准入已失效') from None
    except Exception:
        raise CapabilityContractError('upstream_failure', 'RQData事件读取失败') from None
    after = context.admitted_state()
    if after != before:
        raise CapabilityContractError('policy_not_applied', '事件读取期间政策状态变化')
    fetched = datetime.now(timezone.utc)
    try:
        admission = event_admission_snapshot(after['admission'], observed_at=fetched)
        facts = raw['facts']
        observed_revisions = {fact.get('providerRevision') for fact in facts}
        if len(observed_revisions) > 1:
            raise ValueError('观察修订不一致')
        revision = next(iter(observed_revisions), None) or raw.get('providerRevision')
        identity = raw['identityEvidence']
    except (KeyError, TypeError, ValueError, AttributeError):
        raise CapabilityContractError('invalid_response', '事件观察或准入快照无效') from None
    return {**query.model_dump(), 'fetchedAt': fetched.isoformat().replace('+00:00', 'Z'),
            'providerRevision': revision, 'facts': facts, 'admission': admission,
            'identityEvidence': identity,
            'coverage': {'complete': False, 'reason': 'historical_coverage_unverified'}}


def wire_project(result: CashObservation | SplitResult):
    if result.identityEvidence is None or result.coverage.complete or result.coverage.reason != 'historical_coverage_unverified' or hasattr(result, 'evidence'):
        raise CapabilityContractError('invalid_response', 'RQ事件缺少真实身份或覆盖未知状态')
    try:
        identity = resolve_rqdata_fund_identity(
            result.identityEvidence.content.encode('utf-8'), result.admission.model_dump(),
            kind='split' if result.routeKey.capability == 'SPLIT_EVENT' else 'dividend',
            symbol=result.symbol, start=result.start, end=result.end, data_as_of=result.dataAsOf,
            observed_at=datetime.fromisoformat(result.fetchedAt),
        )
        if result.routeKey.capability == 'CASH_DISTRIBUTION' and any(fact.currency != identity.currency for fact in result.facts):
            raise ValueError('现金币种未绑定身份文档')
        if result.routeKey.capability == 'CASH_DISTRIBUTION' and any(fact.strategyVisibility is not None for fact in result.facts):
            raise ValueError('RQ现金未提供历史可见性')
        if result.routeKey.capability == 'SPLIT_EVENT':
            if result.dateMappingEvidence is not None or any(Decimal(fact.ratio) == 1 or (Decimal(fact.ratio) > 1) != (fact.type == 'SPLIT') for fact in result.facts):
                raise ValueError('RQ拆分方向或证据角色不符')
    except (ValueError, TypeError):
        raise CapabilityContractError('invalid_response', '身份原文或币种不匹配') from None
    return result.model_dump(exclude_unset=True)
