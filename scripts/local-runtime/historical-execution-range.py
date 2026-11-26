"""已核验区间归档的保留数据扩展、精确准入与读回；不访问模型。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile

if 'canonical' not in globals():
    _range_helpers = runpy.run_path(str(Path(__file__).with_name('historical-execution-admission.py')))
else:
    _range_helpers = globals().copy()
canonical = _range_helpers['canonical']
require = _range_helpers['require']
store_at = _range_helpers['store_at']
protection = _range_helpers['protection']
policy_identity = _range_helpers['policy_identity']
KEY = _range_helpers['KEY']
TARGET = _range_helpers['TARGET']
SYMBOLS = _range_helpers['SYMBOLS']

RANGE_INDEX = 'dff009c696ce8e161268297fcf15c25ae69de72eb9ecb3aa0844cb7501cfc675'
PRIOR_INDEX = '42b117390db03f95d4c2e0bf5542ec640e84cf64cd85f14ba74242a417b944ca'
RANGE_START, RANGE_END = '2026-11-02', '2026-11-16'


def qualify_range(root):
    from data_provider.thesis_ledger_sources.historical_execution import ExecutionArchive, inventory
    from src.services.thesis_ledger_historical_execution import ExecutionRequest
    import exchange_calendars as xcals
    require(inventory(root) == RANGE_INDEX, '区间索引与核验修订不符')
    archive = ExecutionArchive(root, RANGE_INDEX)
    days = [x.date().isoformat() for x in xcals.get_calendar('XSHG').sessions_in_range(RANGE_START, RANGE_END)]
    require(not archive.entries and len(archive.daily) == len(days) * 2, '区间资料必须完整覆盖两证券交易日')
    results = []
    for day in days:
        for symbol in SYMBOLS:
            for time in ['01:31:00', '03:29:00', '03:30:00', '05:01:00', '06:59:00', '07:00:00']:
                query = ExecutionRequest.model_validate({'contractVersion': 1, 'consumer': 'thesis-ledger',
                    'requestId': 'range-independent-admission', 'routeKey': KEY,
                    'routeTarget': {**TARGET, 'routeIndex': 0}, 'policyRevision': 201,
                    'symbol': symbol, 'asOf': f'{day}T{time}Z', 'purpose': 'sampled'})
                context = archive.read(query).model_dump(mode='json')
                results.append(context)
    return results


def promote_range(source, destination, backup):
    from data_provider.thesis_ledger_sources.historical_execution import inventory
    source, destination, backup = map(Path, [source, destination, backup])
    require(all(not p.is_symlink() for p in [source, destination, backup]), '路径不得为软链接')
    qualify_range(source)
    require(destination.is_dir() and inventory(destination) == PRIOR_INDEX and not backup.exists(), '只扩展已核验旧归档且备份须为新路径')
    new = json.loads((source / 'index.json').read_bytes())
    old = json.loads((destination / 'index.json').read_bytes())
    require(not new['contexts'] and not old['contexts'] and all(new['dailyContexts'].get(k) == v
        for k, v in old['dailyContexts'].items()), '原索引条目必须保留')
    files = [Path('index.json'), *(Path('daily') / (k + '.json') for k in new['dailyContexts'])]
    for p in (source / 'originals').iterdir():
        require(p.is_file() and not p.is_symlink() and sha256(p.read_bytes()).hexdigest() == p.name, '原文摘要不符')
        files.append(Path('originals') / p.name)
    for p in destination.rglob('*'):
        if p.is_file() and p.name != 'index.json':
            relative = p.relative_to(destination)
            require(relative in files and not p.is_symlink() and p.read_bytes() == (source / relative).read_bytes(), '不得覆盖或删除原材料')
    lock = destination.parent / '.historical-execution-load.lock'
    lock.mkdir()
    staging = Path(tempfile.mkdtemp(prefix='.historical-execution-', dir=destination.parent))
    moved = False
    try:
        for relative in files:
            require(not (source / relative).is_symlink(), '载荷不能包含软链接')
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.chmod(0o755)
            target.write_bytes((source / relative).read_bytes())
            target.chmod(0o644)
        staging.chmod(0o755)
        qualify_range(staging)
        require(inventory(destination) == PRIOR_INDEX, '加载前旧索引变化')
        os.rename(destination, backup)
        moved = True
        os.rename(staging, destination)
        staging = None
    except Exception:
        if moved and not destination.exists():
            os.rename(backup, destination)
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        lock.rmdir()
    return {'status': 'loaded', 'indexSha256': RANGE_INDEX, 'files': len(files), 'backup': str(backup)}


def range_state(store, expected):
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from data_provider.thesis_ledger_sources.historical_execution import ADAPTER_REVISION
    provider = next(p for p in store.provider_registry() if p['providerId'] == TARGET['providerId'])
    revisions = market_v3_current_route_revisions(KEY, TARGET, provider)
    require(revisions == {'adapterRevision': ADAPTER_REVISION,
        'sourceRevision': 'archive-v2:' + expected, 'credentialRevision': 'not-required'}, '实际区间修订不符')
    require(provider.get('enabled') is True, '不自动启用来源')
    with store._connect() as connection:
        configurations = sorted((dict(row) for row in connection.execute('SELECT * FROM thesis_ledger_provider_config')),
                                key=canonical)
    return {**protection(store), 'revisions': revisions, 'policy': policy_identity(store.effective_policy_v3()),
            'configurationDigestVersion': 'stored-config-v1',
            'providerConfigurationsSha256': sha256(canonical(configurations)).hexdigest()}


def range_main(payload):
    from data_provider.thesis_ledger_sources.historical_execution import ROOT
    if payload['mode'] == 'promote':
        require(set(payload) == {'mode', 'source', 'backup', 'confirm'} and payload['confirm'] == RANGE_INDEX,
                '加载须确认已核验区间摘要')
        require(Path.cwd().resolve() != Path('/app'), '区间材料仅在宿主正式载荷准备阶段加载')
        return promote_range(payload['source'], ROOT, payload['backup'])
    from src.services.thesis_ledger_control import _database_path
    database = Path(_database_path())
    require(database == Path('/app/data/stock_analysis.db') and not database.is_symlink(), '目标库不符')
    return admit_range(payload, database, ROOT, database.parent / 'thesis-ledger-historical-execution-evidence')


def range_policy_identity(policy):
    """只忽略本次准入目标的派生资格，保留全部配置与其他目标。"""
    from copy import deepcopy
    value = deepcopy(policy_identity(policy))
    if value is None:
        return None
    for route in value.get('routes', []):
        if route.get('key') != KEY:
            continue
        for target in route.get('targets', []):
            if all(target.get(k) == v for k, v in TARGET.items()):
                target.pop('eligible', None)
                target.pop('reason', None)
                route.pop('reason', None)
    return value


def admit_range(payload, database, root, directory):
    require(payload.get('mode') in ['state', 'collect', 'apply', 'verify'], '区间维护参数无效')
    store = store_at(database)
    if payload['mode'] == 'state':
        require(set(payload) == {'mode', 'expected'}, '状态参数无效')
        return range_state(store, payload['expected'])
    require(set(payload) == {'mode', 'digest'} and payload['mode'] in ['collect', 'apply', 'verify'], '区间维护参数无效')
    results = qualify_range(root)
    before = range_state(store, RANGE_INDEX)
    directory = Path(directory)
    now = datetime.now(timezone.utc)
    if payload['mode'] == 'collect':
        require(directory.is_dir() and not directory.is_symlink(), '采证目录必须已存在且不是软链接')
        record = store.get_route_admission_v3(key=KEY, target=TARGET)
        require(record and record['status'] == 'admitted' and record['sourceRevision'] in
                {'archive-v2:' + PRIOR_INDEX, 'archive-v2:' + RANGE_INDEX},
                '仅处理已知原归档，不覆盖其他修订或撤销')
        evidence = {'version': 'historical-execution-range-v2', 'at': now.isoformat(), 'control': before,
            'previousRecord': record, 'indexSha256': RANGE_INDEX, 'results': results,
            'scopeSymbols': SYMBOLS, 'scopeDateFrom': RANGE_START, 'scopeDateTo': RANGE_END}
        raw = canonical(evidence)
        digest = sha256(raw).hexdigest()
        with (directory / (digest + '.json')).open('xb') as output:
            output.write(raw)
        (directory / (digest + '.json')).chmod(0o600)
        return {'digest': digest, 'control': before, 'results': results}
    digest = payload['digest']
    require(isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), '证据摘要无效')
    path = directory / (digest + '.json')
    require(not directory.is_symlink() and not path.is_symlink(), '证据路径无效')
    raw = path.read_bytes()
    evidence = json.loads(raw)
    require(sha256(raw).hexdigest() == digest and evidence['results'] == results
        and evidence['indexSha256'] == RANGE_INDEX and evidence['scopeSymbols'] == SYMBOLS
        and evidence['scopeDateFrom'] == RANGE_START and evidence['scopeDateTo'] == RANGE_END, '证据与区间修订不符')
    legacy = evidence.get('version') == 'historical-execution-range-v1'
    # v1 把健康探测时间一并计入摘要；已写入的 v1 证据仅允许只读核验，不能据此再次应用。
    require((legacy and payload['mode'] == 'verify') or evidence.get('version') == 'historical-execution-range-v2',
            '旧采证只能只读核验，新写入需要存储配置摘要')
    require((legacy or before['providerConfigurationsSha256'] == evidence['control']['providerConfigurationsSha256'])
        and before['protectedSha256'] == evidence['control']['protectedSha256']
        and range_policy_identity(before['policy']) == range_policy_identity(evidence['control']['policy']),
        '其他来源或政策变化')
    if payload['mode'] == 'apply':
        require(timedelta() <= now - datetime.fromisoformat(evidence['at']) <= timedelta(minutes=30), '证据过期')
        require(before == evidence['control'], '采证后控制已变化')
        def guard():
            require(range_state(store, RANGE_INDEX) == before, '写入前控制变化')
            current = store.get_route_admission_v3(key=KEY, target=TARGET)
            require(current and {k: v for k, v in current.items() if k != 'admissionState'} ==
                    {k: v for k, v in evidence['previousRecord'].items() if k != 'admissionState'},
                    '准入控制已变化')
        store_at(database, True, guard).record_route_admission_v3(key=KEY, target=TARGET,
            evidence_ref='sha256:' + digest, evidence_sha256=digest, scope_symbols=SYMBOLS,
            scope_date_from=RANGE_START, scope_date_to=RANGE_END, **{
                'adapter_revision': before['revisions']['adapterRevision'], 'source_revision': before['revisions']['sourceRevision'],
                'credential_revision': before['revisions']['credentialRevision']},
            valid_from=now.isoformat(), valid_until=None,
            recorded_by='codex-arena-range-20261116')
    record = store.get_route_admission_v3(key=KEY, target=TARGET)
    require(record['recordVersion'] == evidence['previousRecord']['recordVersion'] + 1
        and record['admissionState'] == 'admitted'
        and record['scopeSymbols'] == SYMBOLS and record['scopeDateFrom'] == RANGE_START
        and record['scopeDateTo'] == RANGE_END and record['sourceRevision'] == 'archive-v2:' + RANGE_INDEX
        and record['evidenceSha256'] == digest, '扩展准入读回不符')
    after = range_state(store, RANGE_INDEX)
    require(after['providerConfigurationsSha256'] == before['providerConfigurationsSha256']
        and after['protectedSha256'] == before['protectedSha256']
        and range_policy_identity(after['policy']) == range_policy_identity(before['policy']), '旁侧来源或政策发生变化')
    return {'status': 'admitted', 'record': record, 'protectedSha256': after['protectedSha256'],
            'indexSha256': RANGE_INDEX, 'qualifiedContexts': len(results),
            'configurationDigestVersion': after['configurationDigestVersion'],
            'providerConfigurationsSha256': after['providerConfigurationsSha256'],
            'legacyHealthDigestReadOnly': legacy}


if __name__ == '__main__':
    print(json.dumps(range_main(json.loads(sys.stdin.read())), ensure_ascii=False))
