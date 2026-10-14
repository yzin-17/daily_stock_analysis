"""美股公开费用及有界盘中状态的来源定义与精确绑定。"""

from data_provider.public_fee_projection import read_fees
from data_provider.legacy_status_projection import read_intraday_status as read_status
from src.services.thesis_ledger_legacy_status_contract_v1 import CONTRACT as LEGACY_CONTRACT
from src.services.thesis_ledger_multimarket_rules import SOURCES, MultimarketRuleProvider, multimarket_rule_manifests, multimarket_rule_path
from src.services.thesis_ledger_public_fees_contract_v1 import CONTRACT
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
import json


def manifest():
    return multimarket_rule_manifests()['official-us-rules']


def factory(context):
    return MultimarketRuleProvider()


def catalog_readiness(context):
    path = multimarket_rule_path(context.key, context.target)
    return path.is_file() if path is not None else None


def exact_probe(context):
    key = dict(context.key)
    path = multimarket_rule_path(key, context.target)
    before = path.read_bytes()
    pack = json.loads(before)
    symbol = pack.get('symbol', '600519.SH')
    start, end = pack['coverage']['start'], pack['coverage']['end']
    MultimarketRuleProvider(path).read({'contractVersion': 3, 'consumer': 'thesis-ledger',
        'requestId': 'source-check', 'symbol': symbol, 'range': {'start': start, 'end': end},
        'routeKey': key, 'routeTarget': {**context.target, 'routeIndex': 0}})
    if path.read_bytes() != before:
        raise CapabilityContractError('admission_invalid', '检查期间官方事实包发生变化')
    return {'symbol': symbol, 'start': start, 'end': end}


SOURCE = SourceDefinition('official-us-rules', manifest, factory, tuple(
    QueryBinding(CONTRACT if capability == 'HISTORICAL_PUBLIC_FEES' else LEGACY_CONTRACT, ExactKey(market, 'STOCK', capability), source,
        'bounded-multimarket-rules-adapter-v1', filename + ':v1', read_fees if capability == 'HISTORICAL_PUBLIC_FEES' else read_status, admission_required=False)
    for (market, capability, provider, source), filename in SOURCES.items()
    if provider == 'official-us-rules'
), ('src.services.thesis_ledger_multimarket_rules', 'MultimarketRuleProvider'), factory_scope='provider', exact_probe_read=exact_probe, catalog_readiness=catalog_readiness)
