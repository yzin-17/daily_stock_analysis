"""按原资料范围选择完整集合，拒绝重叠冲突，不累加费用。"""

from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def material_path(source, binding):
    from src.services.thesis_ledger_etf_fee_sources import etf_fee_path
    key = binding.key.route_key()
    target = {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}
    path = etf_fee_path(key, target)
    if path is not None:
        return path
    if source.provider_id in {'official-hk-rules', 'official-us-rules'}:
        from src.services.thesis_ledger_multimarket_rules import multimarket_rule_path
        return multimarket_rule_path(key, target)
    if source.provider_id == 'official-rules':
        from src.services.thesis_ledger_public_fees import DATA_PATH, ARCHIVE_DATA_PATH, ARCHIVE_SOURCE
        return ARCHIVE_DATA_PATH if binding.upstream_source == ARCHIVE_SOURCE else DATA_PATH
    if source.provider_id == 'official-states':
        from src.services.thesis_ledger_trading_status import DATA_PATH, RISK_DATA_PATH, RISK_UPSTREAM_SOURCE, LIFECYCLE_PATHS
        return LIFECYCLE_PATHS.get(binding.upstream_source,
                                   RISK_DATA_PATH if binding.upstream_source == RISK_UPSTREAM_SOURCE else DATA_PATH)
    if source.provider_id == 'official-dividends':
        from src.services.thesis_ledger_notice_dividends import DATA_PATH
        return DATA_PATH
    return None


def _scope_matches(pack, query, declared):
    key = query.routeKey
    if pack.get('market', pack.get('scope', {}).get('market', declared.market)) != key.market:
        return False
    if pack.get('assetType', pack.get('instrumentType', pack.get('scope', {}).get('assetType', declared.asset_type))) != key.assetType:
        return False
    symbol = pack.get('symbol')
    if symbol is not None:
        return symbol == query.symbol
    # 无具体标的的规则由原读取器校验交易所/交易方式；不能把未知范围提升为全市场。
    scope = pack.get('scope', {})
    if not all(scope.get(field) for field in ('market', 'exchange', 'assetType', 'tradingMethod')):
        return False
    if scope['exchange'] == 'XSHG' and not query.symbol.endswith('.SH'):
        return False
    return True


def _fee_dimensions(pack):
    return {(charge['code'], charge['side'], charge['basis'], charge['currency'])
            for segment in pack.get('segments', []) for charge in segment.get('charges', [])}


def _compatible_fees(left, right):
    for first in left.get('segments', []):
        for second in right.get('segments', []):
            if max(first['range']['start'], second['range']['start']) >= min(first['range']['endExclusive'], second['range']['endExclusive']):
                continue
            for charge in first['charges']:
                for other in second['charges']:
                    if (charge['code'], charge['side']) == (other['code'], other['side']):
                        if {key: value for key, value in charge.items() if key != 'sourceDocumentIds'} != {
                                key: value for key, value in other.items() if key != 'sourceDocumentIds'}:
                            return False
    return True


def _cn_stock_fee_primary(matches):
    from src.services.thesis_ledger_public_fees import ARCHIVE_SOURCE, PROVIDER_ID, UPSTREAM_SOURCE

    if len(matches) != 2:
        return None
    by_identity = {(source.provider_id, binding.upstream_source): (source, binding, pack)
                   for source, binding, pack in matches}
    main = by_identity.get((PROVIDER_ID, UPSTREAM_SOURCE))
    archive = by_identity.get((PROVIDER_ID, ARCHIVE_SOURCE))
    if main is None or archive is None:
        return None
    left, right = main[2], archive[2]
    basis = ('scope', 'calculationSettings', 'historicalAvailability')
    if (all(left.get(key) == right.get(key) for key in basis)
            and _fee_dimensions(left) == _fee_dimensions(right)
            and _compatible_fees(left, right)):
        return main[:2]
    return None


def select_material(candidates, query):
    import json
    matches = []
    in_scope = False
    for source, binding in candidates:
        path = material_path(source, binding)
        try:
            pack = json.loads(path.read_bytes()) if path is not None else None
        except (OSError, ValueError) as error:
            raise CapabilityContractError('invalid_response', '本地资料损坏或无法读取') from error
        if pack is None or not _scope_matches(pack, query, binding.key):
            continue
        in_scope = True
        coverage = pack.get('coverage', {})
        if not (coverage.get('start', '9999') <= query.range.start <= query.range.end <= coverage.get('end', '')):
            continue
        matches.append((source, binding, pack))
    if not matches:
        reason = 'insufficient_coverage' if in_scope else 'not_covered'
        raise CapabilityContractError(reason, '本地资料未覆盖请求的完整标的和日期范围')
    if len(matches) == 1:
        return matches[0][:2]
    if query.routeKey.capability == 'HISTORICAL_PUBLIC_FEES':
        # 只有同一交易方式的严格费项超集可以替代窄包；同等完整的冲突包拒绝。
        complete = [item for item in matches if all(
            item is other or (item[2].get('exchange') == other[2].get('exchange')
                             and item[2].get('tradingMethod') == other[2].get('tradingMethod')
                             and _compatible_fees(item[2], other[2])
                             and _fee_dimensions(item[2]) > _fee_dimensions(other[2]))
            for other in matches)]
        if len(complete) == 1:
            return complete[0][:2]
        primary = _cn_stock_fee_primary(matches)
        if primary is not None:
            return primary
    # 相同内容的归档别名可确定性复用；内容不同则需要维护资料消歧。
    contents = [{key: value for key, value in item[2].items()
                 if key not in {'datasetId', 'upstreamSource'}} for item in matches]
    if all(content == contents[0] for content in contents):
        return matches[0][:2]
    raise CapabilityContractError('basis_incompatible', '本地资料存在重叠冲突，未找到唯一完整资料')
