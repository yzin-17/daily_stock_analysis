"""官方CN公共费用两来源的定义、工厂和精确绑定。"""

from data_provider.public_fee_projection import read_fees
from src.services.thesis_ledger_public_fees import build_public_fee_manifest, OfficialPublicFeeProvider, UPSTREAM_SOURCE, ARCHIVE_SOURCE
from src.services.thesis_ledger_public_fees_contract_v1 import CONTRACT
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_public_fees import DATA_PATH, ARCHIVE_DATA_PATH
import json


def factory(context):
    return OfficialPublicFeeProvider()


def catalog_readiness(context):
    from src.services.thesis_ledger_public_fees import public_fee_route_matches, public_fee_source_ready
    if public_fee_route_matches(context.key, context.target):
        return public_fee_source_ready(context.target)
    return None


def exact_probe(context):
    path = ARCHIVE_DATA_PATH if context.target['upstreamSource'] == ARCHIVE_SOURCE else DATA_PATH
    before = path.read_bytes()
    pack = json.loads(before)
    symbol = pack.get('symbol', '600519.SH')
    start, end = pack['coverage']['start'], pack['coverage']['end']
    OfficialPublicFeeProvider(path).read({'contractVersion': 3, 'consumer': 'thesis-ledger',
        'requestId': 'source-check', 'symbol': symbol, 'range': {'start': start, 'end': end},
        'routeKey': dict(context.key), 'routeTarget': {**context.target, 'routeIndex': 0}})
    if path.read_bytes() != before:
        raise CapabilityContractError('admission_invalid', '检查期间官方事实包发生变化')
    return {'symbol': symbol, 'start': start, 'end': end}


SOURCE = SourceDefinition('official-rules', build_public_fee_manifest, factory, tuple(
    QueryBinding(CONTRACT, ExactKey('CN', 'STOCK', 'HISTORICAL_PUBLIC_FEES'), source,
        'official-public-fees-adapter-v1', revision, read_fees, admission_required=False)
    for source, revision in ((UPSTREAM_SOURCE, 'cn-stock-auction-fees-2023-08-v1'),
                             (ARCHIVE_SOURCE, 'cn-stock-auction-fees-archive-2023-08-v1'))
), ('src.services.thesis_ledger_public_fees', 'OfficialPublicFeeProvider'), factory_scope='provider', exact_probe_read=exact_probe, catalog_readiness=catalog_readiness)
