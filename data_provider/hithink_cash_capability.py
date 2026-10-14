"""HiThink现金来源固定执行；原认证、单位、日期和安全生命周期保持。"""

from datetime import datetime, timezone

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT, CashRequest, CashObservation
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_hithink_mapped_dividend_read import read_hithink_mapped_fund_dividends
from src.services.hithink_fund_identity_evidence import resolve_hithink_fund_identity
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, HITHINK_DIVIDEND_SOURCE,
    HITHINK_DIVIDEND_ADAPTER_REVISION, HITHINK_DIVIDEND_SOURCE_REVISION,
    hithink_dividend_route_revisions,
)

REVISION_FACTS = {'adapterRevision': HITHINK_DIVIDEND_ADAPTER_REVISION,
                  'sourceRevision': HITHINK_DIVIDEND_SOURCE_REVISION}


def revisions(credential_revision):
    return hithink_dividend_route_revisions(HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, credential_revision)


def current_admission(store, key, target, manifest):
    from src.services.thesis_ledger_hithink_event_v3 import current_hithink_event_admission
    return current_hithink_event_admission(store, key, target, manifest)


def guard(context, request):
    from src.services.thesis_ledger_hithink_event_v3 import bound_identity_guard
    try:
        credentials = context.credentials
        return bound_identity_guard(credentials.database_path, credentials.read_local_admission, request)
    except Exception:
        raise CapabilityContractError('not_admitted', '现金身份原文未获准入') from None


def read_cash(query: CashRequest, context: EventExecutionContext):
    from src.services.thesis_ledger_hithink_event_v3 import _response, HiThinkEventError
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
    options = {'fetch': context.reader_override} if context.reader_override is not None else {}
    try:
        mapped = read_hithink_mapped_fund_dividends(
            query.symbol, database_path=credentials.database_path, start=query.start, end=query.end,
            data_as_of=query.dataAsOf, read_admission=credentials.read_admission,
            read_credentials=credentials.read_credentials, read_master_key=credentials.read_master_key,
            timeout_seconds=30, **options,
        )
    except Exception:
        raise CapabilityContractError('upstream_failure', 'HiThink现金来源读取失败') from None
    if guard(context, request) != initial or mapped.identity != initial[1]:
        raise CapabilityContractError('policy_not_applied', '现金读取期间身份变化')
    after = context.admitted_state()
    if after != before:
        raise CapabilityContractError('policy_not_applied', '现金读取期间政策变化')
    fetched = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    try:
        response = _response(request, after, mapped, fetched)
    except HiThinkEventError as error:
        raise CapabilityContractError(error.code, error.code) from None
    if guard(context, request) != initial:
        raise CapabilityContractError('policy_not_applied', '现金最终准入变化')
    if context.check_security:
        context.check_security()
    response['identityEvidence'] = response.pop('hithinkIdentityEvidence')
    return response


def wire_project(result: CashObservation):
    if result.identityEvidence is None or result.coverage.complete or result.coverage.reason != 'historical_coverage_unverified' or hasattr(result, 'evidence'):
        raise CapabilityContractError('invalid_response', '现金缺少身份原文或覆盖未知状态')
    try:
        identity = resolve_hithink_fund_identity(
            result.identityEvidence.content.encode('utf-8'), result.admission.model_dump(),
            symbol=result.symbol, start=result.start, end=result.end, data_as_of=result.dataAsOf,
            observed_at=datetime.fromisoformat(result.fetchedAt),
        )
        if any(fact.currency != identity.currency or fact.strategyVisibility is not None for fact in result.facts):
            raise ValueError('币种或可见性不符')
    except (ValueError, TypeError):
        raise CapabilityContractError('invalid_response', '现金身份或币种不符') from None
    wire = result.model_dump(exclude_unset=True)
    wire['hithinkIdentityEvidence'] = wire.pop('identityEvidence')
    return wire
