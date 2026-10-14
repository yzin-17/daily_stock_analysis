"""东财拆分映射进入标准合同，保留原记录和消费格式。"""

from datetime import datetime, timezone
from decimal import Decimal

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot
from src.services.thesis_ledger_event_split_contract_v1 import CONTRACT, SplitRequest, SplitResult
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_split_event_reader_v3 import read_mapped_split_events
from src.services.thesis_ledger_split_mapping_v3 import resolve_split_mapping_observations
from data_provider.eastmoney_split_diagnostics import SplitDiagnostics, diagnostic_stage

REVISIONS = {'adapterRevision': 'dsa-eastmoney-fund-split-mapped-v1',
             'sourceRevision': 'eastmoney-fund-cf-pageinfo-json-v1', 'credentialRevision': 'not-required'}


def revisions(credential_revision):
    return dict(REVISIONS)


def read_split(query: SplitRequest, context: EventExecutionContext):
    diagnostics = SplitDiagnostics(query.requestId)
    with diagnostic_stage(diagnostics.report, 'admission_before'):
        before = context.admitted_state()
    try:
        raw = read_mapped_split_events(context.database_path, query.model_dump(), before['admission'],
                                       reader=context.reader_override, diagnostics=diagnostics.report)
    except Exception as error:
        diagnostics.report('source_read', error)
        raise CapabilityContractError('upstream_failure', '东财拆分读取或映射失败',
                                      diagnostics=diagnostics.records[-1] if diagnostics.records else {}) from None
    with diagnostic_stage(diagnostics.report, 'admission_after'):
        after = context.admitted_state()
    if after != before:
        error = CapabilityContractError('policy_not_applied', '拆分读取期间政策状态变化')
        diagnostics.report('admission_after', error)
        raise error
    fetched = datetime.now(timezone.utc)
    try:
        admission = event_admission_snapshot(after['admission'], observed_at=fetched)
    except (ValueError, TypeError) as error:
        diagnostics.report('admission_snapshot', error)
        raise CapabilityContractError('invalid_response', '拆分准入快照无效') from None
    response = {**query.model_dump(), 'fetchedAt': fetched.isoformat().replace('+00:00', 'Z'),
                'providerRevision': raw.get('providerRevision'), 'facts': raw.get('facts'), 'admission': admission,
                'dateMappingEvidence': raw.get('dateMappingEvidence'),
                'coverage': {'complete': False, 'reason': 'historical_coverage_unverified'}}
    try:
        # 在来源拥有的边界调用同一既有validator以记诊断，公共合同和返回字节不变。
        CONTRACT.validate_result(response)
    except (ValueError, TypeError) as error:
        diagnostics.report('response_validation', error, response)
        raise CapabilityContractError('invalid_response', '处理器结果未通过能力关联合同') from None
    return response


def wire_project(result: SplitResult):
    if result.dateMappingEvidence is None or result.identityEvidence is not None or result.coverage.complete or result.coverage.reason != 'historical_coverage_unverified':
        error = CapabilityContractError('invalid_response', '拆分缺少原映射或覆盖未知状态')
        SplitDiagnostics(result.requestId).report('wire_validation', error)
        raise error
    try:
        observations = [{'symbol': fact.symbol, 'sourceConversionDate': None, 'sourceRatioPerUnit': fact.ratio,
                         'endpoint': 'fund_cf_em', 'upstreamSource': 'eastmoney'} for fact in result.facts]
        # 校验完整原文后，逐事实使用明确生效日/比例寻找唯一源日期，不能从生效日猜日期。
        content = result.dateMappingEvidence.content.encode('utf-8')
        options = {'evidence_bytes': content, 'admission': result.admission.model_dump(),
                   'observed_at': datetime.fromisoformat(result.fetchedAt)}
        resolve_split_mapping_observations([], **options)
        import json
        mappings = json.loads(content)['mappings']
        for fact, observation in zip(result.facts, observations):
            matching = [item for item in mappings if item['symbol'] == fact.symbol and item['effectiveDate'] == fact.effectiveDate
                        and Decimal(item['sourceRatioPerUnit']) == Decimal(fact.ratio)]
            if len(matching) != 1 or matching[0]['recordDate'] != fact.recordDate or fact.type != 'SPLIT':
                raise ValueError('拆分事实与唯一映射不符')
            observation['sourceConversionDate'] = matching[0]['sourceConversionDate']
        resolve_split_mapping_observations(observations, **options)
    except (ValueError, TypeError, KeyError) as error:
        SplitDiagnostics(result.requestId).report('wire_validation', error)
        raise CapabilityContractError('invalid_response', '拆分映射证据或事实不匹配') from None
    return result.model_dump(exclude_unset=True)
