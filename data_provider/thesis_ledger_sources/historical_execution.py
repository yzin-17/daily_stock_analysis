"""经独立准入的历史执行归档；只读本地原文，不补造或下载制度事实。"""

from hashlib import sha256
import json
from pathlib import Path

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_historical_execution import (
    CAPABILITY, CONTRACT, ExecutionContext, ExecutionResult, sampled_context,
)
from src.services.thesis_ledger_historical_reconstruction import (
    DailyExecutionMaterial, reconstruct_context, sampled_daily_material,
)

PROVIDER = 'historical-execution-archive'
UPSTREAM = 'qualified-cn-execution'
ROOT = Path(__file__).resolve().parents[1] / 'market_reference' / 'historical_execution'


def inventory(root):
    return sha256(index_bytes(root)).hexdigest()


def index_bytes(root):
    path = root / 'index.json'
    if root.is_symlink() or path.is_symlink():
        raise CapabilityContractError('invalid_response', '执行归档索引不能使用符号链接')
    if not path.exists():
        return b'{"version":1,"contexts":{}}'
    return bounded_bytes(path, maximum=4 * 1024 * 1024)


def bounded_bytes(path, maximum=2 * 1024 * 1024):
    if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > maximum:
        raise CapabilityContractError('invalid_response', '执行归档路径或大小无效')
    with path.open('rb') as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise CapabilityContractError('invalid_response', '执行归档文件过大')
    return raw


def context_key(symbol, asset_type, as_of):
    from src.services.thesis_ledger_status_capabilities_v1 import instant
    from datetime import timezone
    at = instant(as_of).astimezone(timezone.utc).isoformat(timespec='microseconds')
    return sha256(json.dumps([symbol, asset_type, at], separators=(',', ':')).encode()).hexdigest()


def daily_context_key(symbol, asset_type, day):
    return sha256(json.dumps([symbol, asset_type, day], separators=(',', ':')).encode()).hexdigest()


class ExecutionArchive:
    def __init__(self, root=ROOT, revision=None):
        self.root = root
        self.revision = revision if revision is not None else inventory(root)
        raw = index_bytes(root)
        if sha256(raw).hexdigest() != self.revision:
            raise CapabilityContractError('admission_invalid', '执行归档索引修订变化')
        try:
            import re
            value = json.loads(raw)
            version = value.get('version')
            expected = {'version', 'contexts'} | ({'dailyContexts'} if version == 2 else set())
            if type(version) is not int or version not in (1, 2) or set(value) != expected:
                raise ValueError('执行归档索引版本无效')
            self.entries = value['contexts']
            self.daily = value.get('dailyContexts', {})
            if (not isinstance(self.entries, dict) or not isinstance(self.daily, dict)
                    or len(self.entries) + len(self.daily) > 40000):
                raise ValueError('执行归档索引大小无效')
            for key, entry in [*self.entries.items(), *self.daily.items()]:
                if (not re.fullmatch(r'[a-f0-9]{64}', key) or not isinstance(entry, dict)
                        or set(entry) != {'assetType', 'sha256'} or entry['assetType'] not in ('STOCK', 'ETF')
                        or not re.fullmatch(r'[a-f0-9]{64}', str(entry['sha256']))):
                    raise ValueError('执行归档索引内容无效')
        except (ValueError, AttributeError):
            raise CapabilityContractError('invalid_response', '执行归档索引校验失败') from None

    def current(self):
        if inventory(self.root) != self.revision:
            raise CapabilityContractError('admission_invalid', '执行归档修订变化，须重新加载并准入')

    def read(self, query):
        self.current()
        name = context_key(query.symbol, query.routeKey.assetType, query.asOf)
        path = self.root / 'contexts' / (name + '.json')
        entry = self.entries.get(name)
        daily = False
        if entry is None:
            name = daily_context_key(query.symbol, query.routeKey.assetType, query.range.start)
            entry = self.daily.get(name)
            path = self.root / 'daily' / (name + '.json')
            daily = True
        if not entry or entry['assetType'] != query.routeKey.assetType or not path.is_file():
            raise CapabilityContractError('insufficient_coverage', '缺少该证券与模拟时点的合格执行归档')
        try:
            raw = bounded_bytes(path)
            if sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('执行归档与已准入索引摘要不符')
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('执行归档必须是对象')
            if query.purpose == 'full' and (daily and body.get('version') == 2
                    or not daily and 'identityEvidenceRefs' in body):
                raise CapabilityContractError('insufficient_coverage', '该归档仅覆盖采样用途')
            if daily:
                material = (sampled_daily_material(body) if query.purpose == 'sampled'
                            else DailyExecutionMaterial.model_validate(body))
                context = reconstruct_context(material, query.asOf, self.original)
            else:
                context = (sampled_context(body) if query.purpose == 'sampled'
                           else ExecutionContext.model_validate(body))
            for evidence in context.evidence:
                self.original(evidence.sha256)
            result = ExecutionResult.model_validate({**query.model_dump(), 'context': context.model_dump()})
        except CapabilityContractError:
            raise
        except (ValueError, OSError, KeyError, TypeError):
            raise CapabilityContractError('invalid_response', '执行归档或原文校验失败') from None
        self.current()
        return result

    def original(self, digest):
        path = self.root / 'originals' / digest
        if not path.is_file():
            raise ValueError('执行证据原文缺失')
        raw = bounded_bytes(path)
        if sha256(raw).hexdigest() != digest:
            raise ValueError('执行证据原文字节摘要不符')
        return raw


# 固定本进程所见修订；新增或更换资料必须重载及重新准入，不能复用旧准入。
try:
    REVISION = inventory(ROOT)
except (OSError, CapabilityContractError):
    REVISION = 'unreadable'


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    value = build_provider_manifest(PROVIDER, '历史执行归档', {CAPABILITY: ('STOCK', 'ETF')},
        upstream_sources=((UPSTREAM, '经核验的盘中执行事实'),), markets=('CN',), configuration_mode='built_in')
    value['sourceKind'] = 'local_history'
    for source in value['upstreamSources']:
        source['capabilities'] = {CAPABILITY: ['STOCK', 'ETF']}
    return value


def probe(context):
    adapter = ExecutionArchive(ROOT, REVISION)
    adapter.current()
    checked = 0
    candidates = ((daily, key, entry) for daily, entries in [(False, adapter.entries), (True, adapter.daily)]
                  for key, entry in sorted(entries.items()))
    for daily, key, entry in candidates:
        path = ROOT / ('daily' if daily else 'contexts') / (key + '.json')
        try:
            if entry['assetType'] != context.key['assetType']:
                continue
            if daily:
                raw = bounded_bytes(path)
                material = sampled_daily_material(json.loads(raw))
                value = reconstruct_context(material, material.sessions[0].startAt, adapter.original)
            else:
                raw = json.loads(bounded_bytes(path))
                value = sampled_context(raw)
            query = CONTRACT.request_schema.model_validate({
                'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'execution-source-check',
                'routeKey': dict(context.key), 'routeTarget': {**context.target, 'routeIndex': 0},
                'policyRevision': context.policy_revision, 'symbol': value.fact.symbol, 'asOf': value.asOf,
                'purpose': 'sampled'})
            expected = (daily_context_key(query.symbol, query.routeKey.assetType, query.range.start) if daily
                        else context_key(query.symbol, query.routeKey.assetType, query.asOf))
            if path.stem != expected:
                raise ValueError('执行归档文件名与身份不符')
            adapter.read(query)
            checked += 1
            # 功能检查只证明精确绑定可执行；证券／日期覆盖仍由独立准入和逐次读取核验。
            break
        except (ValueError, OSError, KeyError, TypeError):
            raise CapabilityContractError('invalid_response', '执行归档功能检查失败') from None
    if not checked:
        raise CapabilityContractError('insufficient_coverage', '没有可准入的历史执行归档')
    return {'checkedContexts': checked, 'archiveRevision': REVISION, 'checkedPurpose': 'sampled'}


ADAPTER_REVISION = 'historical-execution-archive-v4'

SOURCE = SourceDefinition(PROVIDER, manifest, lambda context: ExecutionArchive(ROOT, REVISION), tuple(
    QueryBinding(CONTRACT, ExactKey('CN', asset, CAPABILITY), UPSTREAM,
        ADAPTER_REVISION, 'archive-v2:' + REVISION,
                 lambda query, context: context.adapter.read(query), admission_required=True)
    for asset in ('STOCK', 'ETF')), exact_probe_read=probe, default_enabled=False)
