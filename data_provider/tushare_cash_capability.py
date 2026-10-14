"""Tushare现金绑定保持原原文先验/缓存/SDK/安全生命周期。"""

from datetime import datetime, timezone
import re

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT, CashRequest, CashObservation
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_tushare_mapped_read import (
    read_tushare_mapped_fund_dividends, TUSHARE_FUND_DIV_ADAPTER_REVISION, TUSHARE_FUND_DIV_SOURCE_REVISION,
)
from src.services.tushare_fund_identity_evidence import resolve_tushare_fund_identity

TARGET = {'providerId': 'tushare', 'upstreamSource': 'tushare'}
REVISION_FACTS = {'adapterRevision': TUSHARE_FUND_DIV_ADAPTER_REVISION,
                  'sourceRevision': TUSHARE_FUND_DIV_SOURCE_REVISION}


def revisions(credential_revision):
    if not isinstance(credential_revision, str) or re.fullmatch(r'hmac-sha256-v1:[a-f0-9]{64}', credential_revision) is None:
        return None
    return {**REVISION_FACTS, 'credentialRevision': credential_revision}


def current_admission(store, key, target, manifest):
    from src.services.thesis_ledger_tushare_event_v3 import current_tushare_event_admission
    return current_tushare_event_admission(store, key, target, manifest)


def guard(context, request):
    from src.services.thesis_ledger_tushare_event_v3 import bound_identity_guard
    try:
        credentials = context.credentials
        return bound_identity_guard(credentials.database_path, credentials.read_local_admission, request)
    except Exception:
        raise CapabilityContractError('not_admitted', '现金身份原文未获准入') from None


def read_cash(query: CashRequest, context: EventExecutionContext):
    from src.services.thesis_ledger_tushare_event_v3 import _response, TushareEventError
    request = query.model_dump()
    initial = guard(context, request)
    if context.check_security:
        context.check_security()
    before = context.admitted_state()
    if before['admission'] != initial[0] or guard(context, request) != initial:
        raise CapabilityContractError('policy_not_applied', '现金准入状态变化')
    if context.check_security:
        context.check_security()
    credentials = context.credentials
    try:
        mapped = read_tushare_mapped_fund_dividends(
            query.symbol, database_path=credentials.database_path, start=query.start, end=query.end,
            data_as_of=query.dataAsOf, read_admission=credentials.read_admission,
            read_credentials=credentials.read_credentials, read_master_key=credentials.read_master_key,
            build_fetcher=credentials.build_adapter, timeout_seconds=30,
        )
    except Exception:
        raise CapabilityContractError('upstream_failure', 'Tushare现金来源读取失败') from None
    if guard(context, request) != initial or mapped.identity != initial[1]:
        raise CapabilityContractError('policy_not_applied', '现金读取期间身份变化')
    after = context.admitted_state()
    if after != before:
        raise CapabilityContractError('policy_not_applied', '现金读取期间政策变化')
    fetched = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    try:
        response = _response(request, after, mapped, fetched)
    except TushareEventError as error:
        raise CapabilityContractError(error.code, error.code) from None
    if guard(context, request) != initial:
        raise CapabilityContractError('policy_not_applied', '现金最终准入变化')
    if context.check_security:
        context.check_security()
    response['identityEvidence'] = response.pop('tushareIdentityEvidence')
    return response


def wire_project(result: CashObservation):
    if result.identityEvidence is None or result.coverage.complete or result.coverage.reason != 'historical_coverage_unverified' or hasattr(result, 'evidence'):
        raise CapabilityContractError('invalid_response', '现金缺少身份原文或覆盖未知状态')
    try:
        identity = resolve_tushare_fund_identity(
            result.identityEvidence.content.encode('utf-8'), result.admission.model_dump(),
            symbol=result.symbol, start=result.start, end=result.end, data_as_of=result.dataAsOf,
            observed_at=datetime.fromisoformat(result.fetchedAt),
        )
        if any(fact.currency != identity.currency for fact in result.facts):
            raise ValueError('币种未绑定原文')
    except (ValueError, TypeError):
        raise CapabilityContractError('invalid_response', '现金身份或币种不符') from None
    wire = result.model_dump(exclude_unset=True)
    wire['tushareIdentityEvidence'] = wire.pop('identityEvidence')
    return wire
