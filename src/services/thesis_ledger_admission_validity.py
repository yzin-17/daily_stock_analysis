"""准入的时间有效性、本地归档修订有效性与保留数据升级。"""

from datetime import datetime
import re


def revision_bound_route(key, target):
    return (isinstance(key, dict) and isinstance(target, dict)
            and key.get('kind') == 'data' and key.get('market') == 'CN'
            and key.get('assetType') in ('STOCK', 'ETF')
            and key.get('capability') == 'HISTORICAL_EXECUTION_CONTEXT'
            and target.get('providerId') == 'historical-execution-archive'
            and target.get('upstreamSource') == 'qualified-cn-execution')


def _instant(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def route_admission_state(admission, *, now):
    if admission is None:
        return 'pending'
    status = admission.get('status')
    if status != 'admitted':
        return str(status)
    current, start = _instant(now), _instant(admission.get('validFrom'))
    if current is None or start is None:
        return 'invalid'
    if current < start:
        return 'not_yet_valid'
    if 'validUntil' in admission and admission['validUntil'] is None:
        if not revision_bound_route(admission.get('routeKey'), admission.get('target')):
            return 'invalid'
        from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
        binding = CAPABILITY_SOURCES.resolve(admission['routeKey'], admission['target'])
        if (binding is None or admission.get('adapterRevision') != binding.adapter_revision
                or admission.get('sourceRevision') != binding.source_revision
                or admission.get('credentialRevision') != 'not-required'):
            return 'invalid'
        return 'admitted'
    end = _instant(admission.get('validUntil'))
    if end is None:
        return 'invalid'
    return 'expired' if current >= end else 'admitted'


def ensure_nullable_admission_expiry(connection):
    """事务内仅放宽有效期列，逐字保留旧记录及索引／触发器。"""
    table = 'thesis_ledger_route_admission_v3'
    columns = connection.execute('PRAGMA table_info(' + table + ')').fetchall()
    if not any(row['name'] == 'valid_until' and row['notnull'] for row in columns):
        return
    original = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,),
    ).fetchone()['sql']
    schema, changed = re.subn(r'(\bvalid_until\s+TEXT)\s+NOT\s+NULL\b', r'\1', original, flags=re.I)
    if changed != 1:
        raise ValueError('准入表有效期结构不匹配，保持原表')
    objects = connection.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name = ? AND type IN ('index', 'trigger') AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    temporary = table + '_nullable_expiry'
    schema = schema.replace(table, temporary, 1)
    connection.execute('SAVEPOINT admission_nullable_expiry')
    try:
        connection.execute(schema)
        connection.execute('INSERT INTO ' + temporary + ' SELECT * FROM ' + table)
        connection.execute('DROP TABLE ' + table)
        connection.execute('ALTER TABLE ' + temporary + ' RENAME TO ' + table)
        for item in objects:
            connection.execute(item['sql'])
        connection.execute('RELEASE admission_nullable_expiry')
    except Exception:
        connection.execute('ROLLBACK TO admission_nullable_expiry')
        connection.execute('RELEASE admission_nullable_expiry')
        raise
