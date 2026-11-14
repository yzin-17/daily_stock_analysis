"""精确能力一次真实成功的持久证据，与短时健康和账号检查分别维护。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from functools import partial
import hmac
import json
from secrets import token_bytes

TABLE = 'thesis_ledger_capability_verification'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def route_identity(key, target):
    return canonical([dict(key), {name: target[name] for name in ('providerId', 'upstreamSource')}])


def binding_revision(key, target):
    from .thesis_ledger_capability_sources import CAPABILITY_SOURCES

    binding = CAPABILITY_SOURCES.resolve(key, target)
    if binding is None:
        return None
    contract = getattr(binding, 'contract', None)
    reader = binding.read
    while isinstance(reader, partial):
        reader = reader.func
    return canonical({
        'adapter': getattr(binding, 'adapter_revision', None),
        'source': getattr(binding, 'source_revision', None),
        'facts': dict(getattr(binding, 'revision_facts', None) or {}),
        'contract': getattr(contract, 'name', None),
        'version': getattr(contract, 'version', getattr(binding, 'contract_version', None)),
        'reader': reader.__module__ + '.' + reader.__qualname__,
    })


@dataclass(frozen=True)
class VerificationAttempt:
    key_json: str
    target_json: str
    revision: str
    snapshot: object = field(repr=False)

    @property
    def route_id(self):
        return route_identity(json.loads(self.key_json), json.loads(self.target_json))


def begin_verification(store, key, target):
    """在上游读取前冻结配置上下文；不会调用来源或创建验证事实。"""
    revision = binding_revision(key, target)
    if revision is None:
        return None
    snapshot = store.provider_credential_snapshot(target['providerId'])
    return VerificationAttempt(canonical(dict(key)), canonical({
        name: target[name] for name in ('providerId', 'upstreamSource')
    }), revision, snapshot)


def _context_id(attempt, private_key):
    snapshot = attempt.snapshot
    payload = canonical([
        attempt.route_id, attempt.revision, snapshot.config_version, snapshot.credential_version,
        snapshot.source, snapshot.method, dict(snapshot.values),
    ]).encode()
    return hmac.digest(private_key, payload, 'sha256')


def _ensure_table(connection):
    connection.execute(f'CREATE TABLE IF NOT EXISTS {TABLE} '
                       '(route_id TEXT PRIMARY KEY, context_id BLOB NOT NULL, evidence_json TEXT NOT NULL)')
    connection.execute(f'INSERT OR IGNORE INTO {TABLE} VALUES (?, ?, ?)',
                       ('@identity-key', token_bytes(32), '{}'))


def _exists(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def _current(store, attempt):
    key, target = json.loads(attempt.key_json), json.loads(attempt.target_json)
    return (store.provider_credential_snapshot(target['providerId']) == attempt.snapshot
            and binding_revision(key, target) == attempt.revision)


def publish_verified(store, attempt, *, evidence, real_read=True):
    """调用方只在完整请求已验收后发布；并发幂等，旧配置的迟到结果不采用。"""
    if attempt is None or not real_read:
        return False
    with store._schema_lock, store._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not _current(store, attempt):
            return False
        _ensure_table(connection)
        private_key = bytes(connection.execute(
            f'SELECT context_id FROM {TABLE} WHERE route_id=?', ('@identity-key',),
        ).fetchone()[0])
        identity = _context_id(attempt, private_key)
        old = connection.execute(f'SELECT context_id FROM {TABLE} WHERE route_id=?',
                                 (attempt.route_id,)).fetchone()
        if old and hmac.compare_digest(bytes(old[0]), identity):
            return True
        record = {
            'state': 'verified', 'verifiedAt': datetime.now(timezone.utc).isoformat(),
            'configVersion': attempt.snapshot.config_version,
            'evidenceHash': sha256(canonical(evidence).encode()).hexdigest(),
        }
        connection.execute(f'INSERT INTO {TABLE} VALUES (?, ?, ?) ON CONFLICT(route_id) DO UPDATE SET '
                           'context_id=excluded.context_id, evidence_json=excluded.evidence_json',
                           (attempt.route_id, identity, canonical(record)))
        connection.commit()
    return True


class VerificationCatalog:
    """一次批量读取验证表；各来源的配置快照在同次目录投影中复用。"""

    def __init__(self, store):
        self.store, self.snapshots, self.records, self.private_key = store, {}, {}, None
        with store._connect() as connection:
            if _exists(connection):
                for row in connection.execute(f'SELECT route_id, context_id, evidence_json FROM {TABLE}'):
                    if row[0] == '@identity-key':
                        self.private_key = bytes(row[1])
                    else:
                        self.records[row[0]] = (bytes(row[1]), row[2])

    def read(self, key, target):
        unknown = {'state': 'unverified'}
        record = self.records.get(route_identity(key, target))
        if not record or self.private_key is None:
            return unknown
        provider = target['providerId']
        if provider not in self.snapshots:
            self.snapshots[provider] = self.store.provider_credential_snapshot(provider)
        attempt = VerificationAttempt(canonical(dict(key)), canonical({
            name: target[name] for name in ('providerId', 'upstreamSource')
        }), binding_revision(key, target), self.snapshots[provider])
        if attempt.revision is None or not hmac.compare_digest(record[0], _context_id(attempt, self.private_key)):
            return unknown
        try:
            value = json.loads(record[1])
            stamp = datetime.fromisoformat(value['verifiedAt'])
            if (set(value) != {'state', 'verifiedAt', 'configVersion', 'evidenceHash'}
                    or value['state'] != 'verified' or stamp.utcoffset() is None
                    or value['configVersion'] != attempt.snapshot.config_version
                    or len(value['evidenceHash']) != 64
                    or any(c not in '0123456789abcdef' for c in value['evidenceHash'])):
                return unknown
            return value
        except (ValueError, TypeError, KeyError):
            return unknown
