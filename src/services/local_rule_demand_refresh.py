"""本地收费范围外按需检查登记资料，空变更集合推进覆盖；不把失败当成空。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from src.services.local_rule_coverage_store import RuleCoverageStore
from src.services.rule_monitor_runtime import create_rule_monitor_runtime
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services import thesis_ledger_local_material_selection as materials


class MaterialBytes:
    def __init__(self, material):
        self.raw = json.dumps(material, ensure_ascii=False, sort_keys=True).encode()

    def read_bytes(self):
        return self.raw

    def read_text(self, encoding='utf-8'):
        return self.raw.decode(encoding)


def material_adapter(source, material):
    if source.provider_id == 'official-rules':
        from src.services.thesis_ledger_public_fees import OfficialPublicFeeProvider
        return OfficialPublicFeeProvider(MaterialBytes(material))
    from src.services.thesis_ledger_multimarket_rules import MultimarketRuleProvider
    return MultimarketRuleProvider(MaterialBytes(material))


def _unavailable(reason):
    return CapabilityContractError(
        'insufficient_coverage', '资料按需检查尚未取得可用的完整规则覆盖', diagnostics={'ruleRefresh': reason}
    )


def _contains(material, query):
    return (material is not None and material['coverage']['start'] <= query.range.start
            <= query.range.end <= material['coverage']['end'])


def _candidates(candidates, query):
    items = []
    for source, binding in candidates:
        if source.provider_id not in {'official-rules', 'official-hk-rules', 'official-us-rules'}:
            continue
        path = materials.material_path(source, binding)
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            raise _unavailable('material_too_large')
        material = json.loads(raw)
        if materials._scope_matches(material, query, binding.key):
            items.append((source, binding, path, raw, material))
    if any(not materials._compatible_fees(left[4], right[4]) for left in items for right in items):
        raise CapabilityContractError('basis_incompatible', '本地收费资料存在冲突，不能刷新覆盖')
    # 优先完整费项集合；同等完整的原资料主源保持登记顺序。
    return sorted(items, key=lambda item: -len(materials._fee_dimensions(item[4])))


def _validate(source, binding, material, query):
    target = {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source,
              'routeIndex': query.routeTarget.routeIndex}
    legacy = binding.contract.request_schema.model_validate({**query.model_dump(), 'routeTarget': target})
    raw = material_adapter(source, material).read(legacy.model_dump(mode='json'))
    result = binding.contract.validate_result(raw)
    binding.contract.correlate_result(legacy, result, target)


def refresh_selection(candidates, query, context):
    if query.routeKey.capability != 'HISTORICAL_PUBLIC_FEES' or not context.database_path or not context.publish_guard:
        raise _unavailable('refresh_context_unavailable')
    items = _candidates(candidates, query)
    if not items:
        raise _unavailable('scope_uncovered')
    source, binding, path, raw, original = items[0]
    if query.range.start < original['coverage']['start']:
        raise _unavailable('historical_window_unverified')
    identity, digest = source.provider_id + '/' + binding.upstream_source, sha256(raw).hexdigest()
    monitor = create_rule_monitor_runtime(context.database_path)
    today = datetime.fromtimestamp(monitor.store.clock(), timezone.utc).astimezone(ZoneInfo({
        'CN': 'Asia/Shanghai', 'HK': 'Asia/Hong_Kong', 'US': 'America/New_York'}[query.routeKey.market])).date()
    if query.range.end > today.isoformat():
        raise _unavailable('requested_window_unverified')
    storage = RuleCoverageStore(monitor.store)
    registered = monitor.registry().get(identity)
    if registered is None or registered.digest != digest:
        raise _unavailable('source_registry_changed')
    cache_identity = [digest, context.control_epoch]
    if registered.refresh_baseline is not None:
        cache_identity.append(registered.refresh_baseline)
    cache_key = sha256(json.dumps(cache_identity, sort_keys=True).encode()).hexdigest()

    def guard():
        return context.publish_guard() and path.read_bytes() == raw

    if not guard():
        raise _unavailable('source_context_changed')
    cached = storage.read(identity, cache_key)
    material = json.loads(cached['material']) if cached and cached['material'] else None
    if _contains(material, query):
        return source, binding, material
    if cached and cached['next_check'] > monitor.store.clock():
        raise _unavailable(cached['outcome'])
    if not storage.begin_check(identity, cache_key, guard):
        raise _unavailable('refresh_in_progress')
    evidence = {}
    try:
        job, documents = monitor.inspect_once(identity, budget_seconds=min(10, context.timeout_seconds))
        evidence['jobId'] = job
        expected = {doc['url'] for doc in registered.documents}
        if not expected or set(documents) != expected or any(
            not item or not item.get('snapshot', {}).get('text') or item['snapshot'].get('reason')
            for item in documents.values()
        ):
            raise _unavailable('rule_refresh_incomplete')
        # 基线必须与当前资料包绑定；缺基线的首次正文属于新增，不会被下次相同抓取洗成无变化。
        baseline = original.get('refreshBaseline', registered.refresh_baseline or {})
        changed = [url for url, value in documents.items() if baseline.get(url) != value['contentHash']]
        # 指南引用的正文附件必须进入登记资料集合；不能仅凭引用页未变认证附件未变。
        attachments = set()
        for url, value in documents.items():
            text = value['snapshot']['text']
            links = text.split('引用链接：\n', 1)[1].splitlines() if '引用链接：\n' in text else []
            attachments.update(urljoin(url, link) for link in links if re.search(r'\.(?:pdf|docx)$', urlsplit(link).path, re.I))
        changed.extend(sorted(attachments - expected))
        evidence.update(documents={url: value['contentHash'] for url, value in documents.items()}, changes=changed)
        if changed:
            storage.publish(identity, cache_key, 'updates_pending', evidence, guard)
            raise _unavailable('updates_pending')
        end = today.isoformat()
        projected = deepcopy(material or original)
        if query.range.start < original['coverage']['start'] or end <= projected['coverage']['end']:
            raise _unavailable('requested_window_unverified')
        projected['coverage']['end'] = end
        projected['segments'][-1]['range']['endExclusive'] = (datetime.fromisoformat(end).date() + timedelta(days=1)).isoformat()
        projected['coverageVerification'] = {'baseHash': digest, 'checkedAt': datetime.fromtimestamp(
            monitor.store.clock(), timezone.utc).isoformat(), **evidence}
        validation_query = query.model_copy(update={'range': query.range.model_copy(update={
            'start': original['coverage']['start'], 'end': end})})
        _validate(source, binding, projected, validation_query)
        if not storage.publish(identity, cache_key, 'unchanged', evidence, guard, projected):
            raise _unavailable('source_context_changed')
        if not _contains(projected, query):
            raise _unavailable('requested_window_unverified')
        return source, binding, projected
    except CapabilityContractError as error:
        reason = error.diagnostics.get('ruleRefresh')
        if reason and reason not in {'updates_pending', 'source_context_changed'}:
            storage.publish(identity, cache_key, reason, evidence, guard)
        raise
    except Exception as error:
        reason = str(error) if str(error) in {'monitor_busy', 'rule_refresh_incomplete'} else 'rule_refresh_failed'
        storage.publish(identity, cache_key, reason, evidence, guard)
        raise _unavailable(reason) from None
