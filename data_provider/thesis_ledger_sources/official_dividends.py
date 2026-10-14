"""官方现金公告精确绑定；保留原包、单位转换和事件生命周期。"""

from datetime import datetime, timezone
import json

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import EventBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT, CashRequest, CashResult
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_notice_dividends import (
    OfficialNoticeDividendProvider, build_notice_dividend_manifest, notice_dividend_revisions,
    DATA_PATH,
)
from src.services.thesis_ledger_notice_events_v3 import execute_notice_events_v3


def factory(context):
    return OfficialNoticeDividendProvider()


def exact_probe(context):
    before = DATA_PATH.read_bytes()
    pack = json.loads(before)
    symbol = pack.get('symbol', '600519.SH')
    start, end = pack['coverage']['start'], pack['coverage']['end']
    OfficialNoticeDividendProvider().read(symbol, start, end, datetime.now(timezone.utc).isoformat())
    if DATA_PATH.read_bytes() != before:
        raise CapabilityContractError('admission_invalid', '检查期间官方事实包发生变化')
    return {'symbol': symbol, 'start': start, 'end': end}


def revisions(credential_revision):
    return notice_dividend_revisions()


def read_cash(query: CashRequest, context: EventExecutionContext):
    def read(*args):
        return context.adapter_factory().read(*args)

    wire = execute_notice_events_v3(
        None, query.model_dump(), admitted_state=lambda _runtime, _request: context.admitted_state(),
        error_type=lambda code: CapabilityContractError(code, code),
        check_security=context.check_security, reader=context.reader_override or read,
    )
    evidence = wire.pop('noticeDividendEvidence')
    return {**wire, 'evidence': [{'role': 'cashPublication', **evidence}]}


def wire_project(result: CashResult):
    if any(fact.strategyVisibility is not None for fact in result.facts):
        raise CapabilityContractError('invalid_response', '公告原包未验证历史可见性')
    wire = result.model_dump(exclude_unset=True)
    evidence = wire.pop('evidence')
    if len(evidence) != 1 or evidence[0].pop('role') != 'cashPublication':
        raise CapabilityContractError('invalid_response', '公告现金原文证据不唯一')
    return {**wire, 'noticeDividendEvidence': evidence[0]}


SOURCE = SourceDefinition(
    'official-dividends', build_notice_dividend_manifest, factory,
    (EventBinding(CONTRACT, ExactKey('CN', 'ETF', 'CASH_DISTRIBUTION'), 'cn-etf-cash-notices',
                  read_cash, revisions, wire_project),),
    ('src.services.thesis_ledger_notice_dividends', 'OfficialNoticeDividendProvider'),
    exact_probe_read=exact_probe,
)
