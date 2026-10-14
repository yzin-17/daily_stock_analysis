"""东财现金标准事件执行；原分页与观察事实保持覆盖未知。"""

from datetime import datetime, timezone

from data_provider.eastmoney_fund_dividend_reader import fetch_fund_dividend_observations
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT, CashRequest, CashObservation
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot

TARGET = {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}
REVISIONS = {'adapterRevision': 'dsa-eastmoney-fund-dividend-v1',
             'sourceRevision': 'eastmoney-fund-fh-pageinfo-json-v1', 'credentialRevision': 'not-required'}


def revisions(credential_revision):
    return dict(REVISIONS)


def read_cash(query: CashRequest, context: EventExecutionContext):
    before = context.admitted_state()
    try:
        read = context.reader_override or fetch_fund_dividend_observations
        raw = read(query.symbol, start=query.start, end=query.end)
    except Exception:
        raise CapabilityContractError('upstream_failure', '东财现金来源读取失败') from None
    after = context.admitted_state()
    if after != before:
        raise CapabilityContractError('policy_not_applied', '现金读取期间政策状态变化')
    fetched = datetime.now(timezone.utc)
    try:
        admission = event_admission_snapshot(after['admission'], observed_at=fetched)
        facts = raw['facts']
        observed_revisions = {fact.get('providerRevision') for fact in facts}
        if len(observed_revisions) > 1:
            raise ValueError('观察修订不一致')
        revision = next(iter(observed_revisions), None) or raw.get('providerRevision')
    except (KeyError, TypeError, ValueError, AttributeError):
        raise CapabilityContractError('invalid_response', '现金观察或准入快照无效') from None
    return {**query.model_dump(), 'fetchedAt': fetched.isoformat().replace('+00:00', 'Z'),
            'providerRevision': revision, 'facts': facts, 'admission': admission,
            'coverage': {'complete': False, 'reason': 'historical_coverage_unverified'}}


def wire_project(result: CashObservation):
    if any(fact.strategyVisibility is not None for fact in result.facts):
        raise CapabilityContractError('invalid_response', '东财现金观察不提供历史可见性')
    if result.coverage.complete or result.coverage.reason != 'historical_coverage_unverified' or hasattr(result, 'evidence') or result.identityEvidence is not None:
        raise CapabilityContractError('invalid_response', '东财现金观察不能签发公告覆盖')
    return result.model_dump(exclude_unset=True)
