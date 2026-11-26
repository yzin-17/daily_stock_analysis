"""两证券官方事件归档的有界加载、首次双能力准入与只读核验。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile

if 'store_at' not in globals():
    _helpers = runpy.run_path(str(Path(__file__).with_name('historical-execution-admission.py')))
else:
    _helpers = globals().copy()
store_at = _helpers['store_at']
canonical = _helpers['canonical']
require = _helpers['require']

PROOF = '4a3023187b35ae99719c8132ad227877894c80615890a60ad54d979641715987'
TARGET = {'providerId': 'official-event-archive', 'upstreamSource': 'sse-disclosure-history'}
SYMBOLS = ['510300.SH', '588000.SH']
START, END = '2026-11-02', '2026-11-16'
KEYS = [{'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': value}
        for value in ['CASH_DISTRIBUTION', 'SPLIT_EVENT']]
REVISIONS = {'adapterRevision': 'sse-event-absence-v1', 'sourceRevision': 'sse-disclosure-v1:' + PROOF,
             'credentialRevision': 'not-required'}


def qualify(root):
    import exchange_calendars as xcals
    from data_provider.sse_event_coverage import EventCoverageArchive, inventory
    require(inventory(root) == PROOF, '只接受已审阅的事件归档修订')
    archive = EventCoverageArchive(root, PROOF)
    pack = archive.material()
    require(pack['scopeSymbols'] == SYMBOLS and pack['scopeDateFrom'] == START and pack['scopeDateTo'] == END,
            '只接受已授权的两证券区间')
    results = []
    for session in xcals.get_calendar('XSHG').sessions_in_range(START, END):
        day = session.date().isoformat()
        for symbol in SYMBOLS:
            archive.scope(symbol, START, day, day + 'T05:01:00Z')
            for key in KEYS:
                results.append({'symbol': symbol, 'start': START, 'end': day,
                    'capability': key['capability'], 'proofSha256': PROOF, 'facts': []})
    return results


def promote(source, destination):
    source, destination = Path(source), Path(destination)
    require(not source.is_symlink() and not destination.is_symlink() and not destination.exists(),
            '新事件归档不得覆盖既有目录或使用软链接')
    qualify(source)
    pack = json.loads((source / 'proof.json').read_bytes())
    digests = {item['sha256'] for item in [*pack['indexes'], *pack['publications']]}
    files = [Path('proof.json'), *(Path('originals') / digest for digest in sorted(digests))]
    require(set(p.relative_to(source) for p in source.rglob('*') if p.is_file()) == set(files), '载荷必须只有已核验原文及证明')
    staging = Path(tempfile.mkdtemp(prefix='.sse-event-coverage-', dir=destination.parent))
    try:
        for relative in files:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.chmod(0o755)
            target.write_bytes((source / relative).read_bytes())
            target.chmod(0o644)
        staging.chmod(0o755)
        qualify(staging)
        require(not destination.exists(), '加载前目标目录出现，不覆盖')
        os.rename(staging, destination)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging)
    return {'status': 'loaded', 'proofSha256': PROOF, 'files': len(files)}


def control(store):
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    provider = next(p for p in store.provider_registry() if p['providerId'] == TARGET['providerId'])
    for key in KEYS:
        require(market_v3_current_route_revisions(key, TARGET, provider) == REVISIONS, '实际事件来源修订不符')
    with store._connect() as connection:
        protected, own = {}, {}
        for table in ['thesis_ledger_provider_config', 'thesis_ledger_route_admission_v3']:
            rows = [dict(row) for row in connection.execute('SELECT * FROM ' + table)]
            protected[table] = sorted((x for x in rows if x['provider_id'] != TARGET['providerId']), key=canonical)
            own[table] = sorted((x for x in rows if x['provider_id'] == TARGET['providerId']), key=canonical)
    policy = store.effective_policy_v3()
    return {'protectedSha256': sha256(canonical(protected)).hexdigest(), 'target': own,
        'targetSha256': sha256(canonical(own)).hexdigest(), 'sourceEnabled': provider['enabled'],
        'policy': {k: v for k, v in policy.items() if k != 'appliedAt'} if policy else None}


def verify_policy(before, current):
    require(before is not None and current is not None and before['revision'] == 201 and current['revision'] == 202,
            '只追加到已核验的政策 201')
    metadata = {'revision', 'sourceDesiredRevision', 'requestId', 'appliedAt', 'routes'}
    require({k: v for k, v in before.items() if k not in metadata}
        == {k: v for k, v in current.items() if k not in metadata}, '政策其他字段变化')
    require(current['sourceDesiredRevision'] == before['sourceDesiredRevision'] + 1
        and len(before['routes']) == 147 and len(current['routes']) == 149
        and current['routes'][:147] == before['routes'], '原 147 路由必须逐项保留')
    for key, route in zip(KEYS, current['routes'][147:]):
        require(route == {'key': key, 'enabled': True, 'reason': None,
            'targets': [{**TARGET, 'routeIndex': 0, 'eligible': True, 'reason': None}]}, '新增事件路由不符')


def admit(payload, database, directory, root):
    require(set(payload) == {'mode', 'digest'} and payload['mode'] in ['collect', 'apply', 'verify'], '事件维护参数无效')
    mode = payload['mode']
    results = qualify(root)
    store = store_at(database)
    before = control(store)
    now = datetime.now(timezone.utc)
    directory = Path(directory)
    require(not directory.is_symlink(), '证据目录不得为软链接')
    directory.mkdir(mode=0o700, exist_ok=True)
    if mode == 'collect':
        require(before['target'] == {'thesis_ledger_provider_config': [], 'thesis_ledger_route_admission_v3': []}
            and before['sourceEnabled'] is False and before['policy']['revision'] == 201, '只允许新来源首次准入')
        require(not any(r['key'] in KEYS for r in before['policy']['routes']), '已有事件路由，不覆盖')
        evidence = {'version': 'sse-event-qualification-v1', 'at': now.isoformat(), 'proofSha256': PROOF,
            'before': before, 'results': results}
        raw = canonical(evidence)
        digest = sha256(raw).hexdigest()
        with (directory / (digest + '.json')).open('xb') as stream:
            stream.write(raw)
        (directory / (digest + '.json')).chmod(0o600)
        return {'digest': digest, **evidence}
    digest = payload['digest']
    require(isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), '采证摘要无效')
    path = directory / (digest + '.json')
    require(not path.is_symlink(), '采证文件不得为软链接')
    raw = path.read_bytes()
    evidence = json.loads(raw)
    require(sha256(raw).hexdigest() == digest and evidence['version'] == 'sse-event-qualification-v1'
        and evidence['proofSha256'] == PROOF and evidence['results'] == results, '采证原文或资格范围变化')
    require(before['protectedSha256'] == evidence['before']['protectedSha256'], '其他来源或执行准入变化')
    applied_path = directory / (digest + '.applied.json')
    if mode == 'apply':
        require(not applied_path.exists() and before == evidence['before'], '采证后状态变化或已应用，不覆盖')
        require(timedelta() <= now - datetime.fromisoformat(evidence['at']) <= timedelta(minutes=30), '资格采证过期')
        from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
        proof = MappingEvidenceStore(Path(database).parent / 'thesis-ledger-mapping-evidence')
        content = (Path(root) / 'proof.json').read_bytes()
        require(proof.put(content) == 'sha256:' + PROOF and proof.read('sha256:' + PROOF) == content, '证明持久化读回失败')
        execution_key = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'HISTORICAL_EXECUTION_CONTEXT'}
        execution = store.get_route_admission_v3(key=execution_key,
            target={'providerId': 'historical-execution-archive', 'upstreamSource': 'qualified-cn-execution'})
        require(execution and execution['recordVersion'] == 2 and execution['scopeDateFrom'] == START
            and execution['scopeDateTo'] == END and execution['scopeSymbols'] == SYMBOLS
            and execution['admissionState'] == 'admitted' and execution['adapterRevision'] == 'historical-execution-archive-v3'
            and execution['sourceRevision'] == 'archive-v2:dff009c696ce8e161268297fcf15c25ae69de72eb9ecb3aa0844cb7501cfc675'
            and execution['credentialRevision'] == 'not-required'
            and datetime.fromisoformat(execution['validFrom']) <= now < datetime.fromisoformat(execution['validUntil']),
            '原执行准入不符')
        until = min(now + timedelta(hours=24), datetime.fromisoformat(execution['validUntil']))
        require(until - now >= timedelta(hours=1), '原执行准入即将到期，不签发新准备状态')
        expected = before
        def guard():
            require(control(store) == expected, '写入前来源或政策变化')
        writer = store_at(database, True, guard)
        for key in KEYS:
            writer.record_route_admission_v3(key=key, target=TARGET, evidence_ref='sha256:' + PROOF,
                evidence_sha256=PROOF, scope_symbols=SYMBOLS, scope_date_from=START, scope_date_to=END,
                adapter_revision=REVISIONS['adapterRevision'], source_revision=REVISIONS['sourceRevision'],
                credential_revision='not-required', valid_from=now.isoformat(), valid_until=until.isoformat(),
                recorded_by='codex-sse-events-20261116')
            expected = control(store)
            require(expected['protectedSha256'] == before['protectedSha256'] and expected['policy'] == before['policy'], '旁侧来源或政策变化')
        writer.save_provider_config(TARGET['providerId'], {'requestId': 'sse-events-enable-20261116', 'enabled': True})
        after = control(store)
        require(after['protectedSha256'] == before['protectedSha256'] and after['policy'] == before['policy'], '旁侧来源或政策变化')
        with applied_path.open('x') as stream:
            json.dump(after, stream, ensure_ascii=False)
        applied_path.chmod(0o600)
    require(applied_path.is_file() and not applied_path.is_symlink(), '缺少首次应用回执')
    applied = json.loads(applied_path.read_bytes())
    current = control(store)
    require(current['sourceEnabled'] is True and current['target'] == applied['target']
        and current['protectedSha256'] == evidence['before']['protectedSha256'], '目标或旁侧来源变化')
    records = [store.get_route_admission_v3(key=key, target=TARGET) for key in KEYS]
    for record in records:
        require(record and record['recordVersion'] == 1 and record['admissionState'] == 'admitted'
            and record['evidenceSha256'] == PROOF and record['scopeSymbols'] == SYMBOLS
            and record['scopeDateFrom'] == START and record['scopeDateTo'] == END
            and all(record.get(k) == v for k, v in REVISIONS.items())
            and datetime.fromisoformat(record['validFrom']) <= now < datetime.fromisoformat(record['validUntil']), '事件准入读回不符')
    if mode == 'verify':
        verify_policy(evidence['before']['policy'], current['policy'])
    return {'status': 'admitted', 'proofSha256': PROOF, 'records': records,
        'protectedSha256': current['protectedSha256'], 'qualifiedWindows': len(results)}


def event_main(payload):
    from data_provider.sse_event_coverage import ROOT
    if payload.get('mode') == 'promote':
        require(set(payload) == {'mode', 'source', 'confirm'} and payload['confirm'] == PROOF, '加载须确认固定证明摘要')
        require(Path.cwd().resolve() != Path('/app'), '资料仅在宿主正式载荷阶段加载')
        return promote(payload['source'], ROOT)
    from src.services.thesis_ledger_control import _database_path
    database = Path(_database_path())
    require(database == Path('/app/data/stock_analysis.db') and database.is_file() and not database.is_symlink(), '目标库不符')
    return admit(payload, database, database.parent / 'thesis-ledger-event-coverage-evidence', ROOT)


if __name__ == '__main__':
    print(json.dumps(event_main(json.loads(sys.stdin.read())), ensure_ascii=False))
