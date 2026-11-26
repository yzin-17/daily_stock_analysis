"""两证券单日归档的显式加载、首次准入与只读核验。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'HISTORICAL_EXECUTION_CONTEXT'}
TARGET = {'providerId': 'historical-execution-archive', 'upstreamSource': 'qualified-cn-execution'}
SYMBOLS = ['510300.SH', '588000.SH']
DAY = '2026-11-10'
INDEX = '42b117390db03f95d4c2e0bf5542ec640e84cf64cd85f14ba74242a417b944ca'
AS_OF = '2026-11-10T05:01:00.000Z'
VERSION = 'historical-execution-admission-v2'
RECORDER = 'codex-arena-execution-20261116'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def qualify(root):
    from data_provider.thesis_ledger_sources.historical_execution import ExecutionArchive, inventory
    from src.services.thesis_ledger_historical_execution import ExecutionRequest
    require(inventory(root) == INDEX, '材料索引与已核验修订不符')
    archive = ExecutionArchive(root, INDEX)
    require(not archive.entries and len(archive.daily) == 2, '只允许两证券的 v2 日材料')
    results = []
    for symbol in SYMBOLS:
        query = ExecutionRequest.model_validate({'contractVersion': 1, 'consumer': 'thesis-ledger',
            'requestId': 'execution-admission-20261116', 'routeKey': KEY,
            'routeTarget': {**TARGET, 'routeIndex': 0}, 'policyRevision': 200,
            'symbol': symbol, 'asOf': AS_OF, 'purpose': 'sampled'})
        result = archive.read(query).model_dump(mode='json')
        require(result['context']['fact']['symbol'] == symbol, '重建身份不符')
        results.append(result)
    return results


def promote(source, destination):
    """仅安装固定修订；不同的既有归档必须另行审查，不能覆盖。"""
    source, destination = Path(source), Path(destination)
    require(not source.is_symlink() and not destination.is_symlink(), '材料路径不得为软链接')
    results = qualify(source)
    index = json.loads((source / 'index.json').read_bytes())
    files = [Path('index.json'), *(Path('daily') / (key + '.json') for key in index['dailyContexts'])]
    originals = sorted((source / 'originals').iterdir())
    require(len(originals) == 15, '原文数量与核验包不符')
    for path in originals:
        require(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == path.name,
                '原文字节摘要或类型不符')
        files.append(Path('originals') / path.name)
    for relative in files:
        path = source / relative
        require(not path.is_symlink() and not path.parent.is_symlink(), '材料文件不得为软链接')
    if destination.exists():
        existing = {p.relative_to(destination) for p in destination.rglob('*') if p.is_file()}
        require(existing == set(files) and all(not (destination / p).is_symlink()
            and (destination / p).read_bytes() == (source / p).read_bytes() for p in files), '已有不同归档，禁止覆盖')
        qualify(destination)
        return {'status': 'already_loaded', 'indexSha256': INDEX, 'files': len(files)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / '.historical-execution-load.lock'
    lock.mkdir()
    staging = None
    try:
        require(not destination.exists(), '目标归档已被其他操作创建')
        staging = Path(tempfile.mkdtemp(prefix='.historical-execution-', dir=destination.parent))
        staging.chmod(0o755)
        for relative in files:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.chmod(0o755)
            target.write_bytes((source / relative).read_bytes())
            target.chmod(0o644)
        require(qualify(staging) == results, '暂存材料重建读回不符')
        os.rename(staging, destination)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        lock.rmdir()
    return {'status': 'loaded', 'indexSha256': INDEX, 'files': len(files)}


def store_at(database, write=False, guard=None):
    from src.services.thesis_ledger_control import ThesisLedgerControlStore

    class GuardedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.strip().upper() == 'BEGIN IMMEDIATE' and guard:
                guard()
            return result

    class Store(ThesisLedgerControlStore):
        def __init__(self):
            self.database_path = str(database)

        def _connect(self):
            connection = sqlite3.connect(Path(database).as_uri() + ('?mode=rw' if write else '?mode=ro'),
                uri=True, timeout=10, isolation_level=None, factory=GuardedConnection)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            return connection

    return Store()


def protection(store):
    with store._connect() as connection:
        protected = {}
        for table in ['thesis_ledger_provider_config', 'thesis_ledger_route_admission_v3']:
            rows = [dict(row) for row in connection.execute('SELECT * FROM ' + table)]
            protected[table] = sorted((row for row in rows if row['provider_id'] != TARGET['providerId']),
                                      key=lambda row: canonical(row))
        target = [dict(row) for row in connection.execute(
            'SELECT * FROM thesis_ledger_route_admission_v3 WHERE provider_id=?', (TARGET['providerId'],))]
    return {'protectedSha256': sha256(canonical(protected)).hexdigest(), 'targetAdmission': target}


def policy_identity(policy):
    # Server 定时重申同一修订会刷新 appliedAt；修订、路由及其他字段仍逐项比较。
    return {key: value for key, value in policy.items() if key != 'appliedAt'} if policy else policy


def state(store):
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from data_provider.thesis_ledger_sources.historical_execution import ADAPTER_REVISION
    provider = next(row for row in store.provider_registry() if row['providerId'] == TARGET['providerId'])
    revisions = market_v3_current_route_revisions(KEY, TARGET, provider)
    require(revisions == {'adapterRevision': ADAPTER_REVISION,
        'sourceRevision': 'archive-v2:' + INDEX, 'credentialRevision': 'not-required'}, '实际加载修订不兼容')
    return {**protection(store), 'revisions': revisions, 'policy': policy_identity(store.effective_policy_v3()),
        'sourceEnabled': provider.get('enabled') is True}


def expected_record(digest, revisions):
    return {'scopeSymbols': SYMBOLS, 'scopeDateFrom': DAY, 'scopeDateTo': DAY, **revisions,
        'evidenceRef': 'sha256:' + digest, 'evidenceSha256': digest, 'recordedBy': RECORDER,
        'recordVersion': 1, 'admissionState': 'admitted'}


def admit(payload, database, directory, root):
    mode = payload.get('mode')
    require(mode in ('collect', 'apply', 'verify') and set(payload) == {'mode', 'digest'}, '维护请求无效')
    results = qualify(root)
    store = store_at(database)
    before = state(store)
    now = datetime.now(timezone.utc)
    if mode == 'collect':
        require(not before['targetAdmission'] and not before['sourceEnabled'], '只办理关闭来源的首次准入')
        evidence = {'version': VERSION, 'at': now.isoformat(), 'indexSha256': INDEX,
            'key': KEY, 'target': TARGET, 'symbols': SYMBOLS, 'day': DAY, 'control': before, 'results': results}
        raw = canonical(evidence)
        digest = sha256(raw).hexdigest()
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (digest + '.json')
        require(not directory.is_symlink(), '证据目录不得为软链接')
        with path.open('xb') as output:
            output.write(raw)
        path.chmod(0o600)
        require(path.read_bytes() == raw, '证据读回不符')
        return {'digest': digest, 'control': before, 'indexSha256': INDEX, 'symbols': SYMBOLS, 'results': results}
    digest = payload['digest']
    require(isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), '摘要无效')
    path = directory / (digest + '.json')
    require(not path.is_symlink() and not directory.is_symlink(), '证据路径无效')
    raw = path.read_bytes()
    require(sha256(raw).hexdigest() == digest, '证据摘要变化')
    evidence = json.loads(raw)
    require(evidence['version'] == VERSION and evidence['results'] == results and evidence['key'] == KEY
        and evidence['target'] == TARGET and evidence['symbols'] == SYMBOLS and evidence['day'] == DAY
        and evidence['indexSha256'] == INDEX, '证据身份、范围或重建结果变化')
    require(before['protectedSha256'] == evidence['control']['protectedSha256'], '其他来源或准入已变化')
    expected = expected_record(digest, before['revisions'])
    if mode == 'apply':
        age = now - datetime.fromisoformat(evidence['at'])
        require(timedelta() <= age <= timedelta(minutes=30), '采证超过30分钟或时钟无效')
        require(before == evidence['control'], '采证后控制状态变化')
        def guard():
            require(state(store) == before, '写入前控制状态变化')
        writer = store_at(database, True, guard)
        writer.record_route_admission_v3(key=KEY, target=TARGET,
            evidence_ref=expected['evidenceRef'], evidence_sha256=digest, scope_symbols=SYMBOLS,
            scope_date_from=DAY, scope_date_to=DAY, adapter_revision=before['revisions']['adapterRevision'],
            source_revision=before['revisions']['sourceRevision'], credential_revision='not-required',
            valid_from=now.isoformat(), valid_until=None, recorded_by=RECORDER)
        record = store.get_route_admission_v3(key=KEY, target=TARGET)
        require(record and all(record.get(k) == v for k, v in expected.items()), '准入写入后读回不符，不自动重试')
        store_at(database, True).save_provider_config(TARGET['providerId'],
            {'requestId': 'execution-enable-20261116', 'enabled': True})
    record = store.get_route_admission_v3(key=KEY, target=TARGET)
    after = state(store)
    require(record and all(record.get(k) == v for k, v in expected.items()), '准入记录无效或修订范围不符')
    require(after['sourceEnabled'] and after['protectedSha256'] == before['protectedSha256'], '来源启用或其他控制保护不符')
    require(record['validUntil'] is None or mode == 'verify' and
            datetime.fromisoformat(record['validUntil']) - datetime.fromisoformat(record['validFrom']) == timedelta(hours=24),
            '准入有效性不符')
    return {'status': 'admitted', 'digest': digest, 'record': record,
        'protectedSha256': after['protectedSha256'], 'indexSha256': INDEX, 'symbols': SYMBOLS}


def main(payload):
    from data_provider.thesis_ledger_sources.historical_execution import ROOT
    if payload.get('mode') == 'promote':
        require(set(payload) == {'mode', 'source', 'confirm'} and payload['confirm'] == INDEX,
                '加载需要显式确认固定索引')
        require(Path('/app').resolve() != Path.cwd().resolve(), '材料应在宿主正式载荷准备阶段加载')
        return promote(payload['source'], ROOT)
    from src.services.thesis_ledger_control import _database_path
    configured = Path(_database_path())
    require(configured == Path('/app/data/stock_analysis.db') and not configured.is_symlink() and configured.is_file(), '目标数据库路径不符')
    if payload == {'mode': 'protection'}:
        return protection(store_at(configured))
    return admit(payload, configured, configured.parent / 'thesis-ledger-historical-execution-evidence', ROOT)


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.loads(sys.stdin.read())), ensure_ascii=False))
    except Exception as exc:
        print('历史执行维护未完成；保留证据，仅核对后续状态，不自动重试：' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        sys.exit(1)
