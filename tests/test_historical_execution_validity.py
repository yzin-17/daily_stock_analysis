"""本地归档修订有效性及真实临时控制库的保留升级。"""

from datetime import datetime, timedelta, timezone
import socket
import sqlite3

import pytest

from data_provider.thesis_ledger_sources.historical_execution import ExecutionArchive
from src.services.thesis_ledger_admission_validity import ensure_nullable_admission_expiry
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_historical_execution import ExecutionRequest
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, ThesisLedgerGatewayError
from tests.test_configured_market_routes import store  # noqa: F401
from tests.test_historical_execution import admit, configure, outer, sample, write_archive


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('隔离检查禁止网络')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


def test_revision_bound_archive_reads_after_clock_advances(store, tmp_path, monkeypatch):
    query, _ = write_archive(tmp_path / 'archive', sample())
    admit(store, query, valid_until=None)
    configure(store, query)
    runtime = ThesisLedgerProviderRuntime(store, adapters={
        'historical-execution-archive': ExecutionArchive(tmp_path / 'archive'),
    })
    future = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    monkeypatch.setattr('src.services.thesis_ledger_control._utc_now', lambda: future)
    record = store.get_route_admission_v3(key=query.routeKey.model_dump(), target=query.routeTarget.model_dump(exclude={'routeIndex'}))
    assert record['validUntil'] is None and record['admissionState'] == 'admitted'
    assert store.effective_policy_v3()['routes'][0]['targets'][0]['eligible']
    assert runtime.execute_request(outer(query)).value['context'] == sample()['context']


@pytest.mark.parametrize('change', ['source', 'adapter', 'symbol', 'day', 'revoked', 'disabled'])
def test_revision_bound_archive_still_enforces_actual_constraints(store, tmp_path, change):
    query, _ = write_archive(tmp_path / 'archive', sample())
    changes = {'source': {'source_revision': 'changed'}, 'adapter': {'adapter_revision': 'changed'},
               'symbol': {'scope_symbols': ['510300.SH']},
               'day': {'scope_date_from': '2026-11-09', 'scope_date_to': '2026-11-09'}}
    admit(store, query, valid_until=None, **changes.get(change, {}))
    configure(store, query)
    if change == 'revoked':
        store.revoke_route_admission_v3(key=query.routeKey.model_dump(), target=query.routeTarget.model_dump(exclude={'routeIndex'}),
                                       reason='隔离撤销')
    if change == 'disabled':
        store.save_provider_config('historical-execution-archive', {'requestId': 'offline-disable', 'enabled': False})
    runtime = ThesisLedgerProviderRuntime(store, adapters={
        'historical-execution-archive': ExecutionArchive(tmp_path / 'archive'),
    })
    with pytest.raises(ThesisLedgerGatewayError):
        runtime.execute_request(outer(query))
    if change in ('source', 'adapter', 'revoked', 'disabled'):
        assert not store.effective_policy_v3()['routes'][0]['targets'][0]['eligible']


def test_existing_expired_admission_is_not_implicitly_promoted(store):
    query = ExecutionRequest.model_validate(sample()['request'])
    admit(store, query, valid_until=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    record = store.get_route_admission_v3(key=query.routeKey.model_dump(), target=query.routeTarget.model_dump(exclude={'routeIndex'}))
    assert record['admissionState'] == 'expired' and record['recordVersion'] == 1
    assert record['validUntil'] is not None


def test_other_provider_cannot_record_null_expiry(store):
    query = ExecutionRequest.model_validate(sample()['request'])
    with pytest.raises(ValueError, match='只有本地'):
        store.record_route_admission_v3(key=query.routeKey.model_dump(),
            target={'providerId': 'futu', 'upstreamSource': 'futu'},
            evidence_ref='fixture://evidence', evidence_sha256='a' * 64,
            scope_symbols=[query.symbol], scope_date_from='2026-11-10', scope_date_to='2026-11-10',
            adapter_revision='v1', source_revision='v1', credential_revision='1',
            valid_from='2026-11-01T00:00:00+00:00', valid_until=None, recorded_by='offline')


def legacy_database(store, tmp_path):
    query = ExecutionRequest.model_validate(sample()['request'])
    admit(store, query)
    store.revoke_route_admission_v3(key=query.routeKey.model_dump(), target=query.routeTarget.model_dump(exclude={'routeIndex'}),
                                   reason='保留原撤销')
    with store._connect() as connection:
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='thesis_ledger_route_admission_v3'").fetchone()[0]
        before = [tuple(row) for row in connection.execute('SELECT * FROM thesis_ledger_route_admission_v3')]
    schema = schema.replace('valid_until TEXT,', 'valid_until TEXT NOT NULL,')
    path = tmp_path / 'legacy.db'
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(schema)
    connection.executemany('INSERT INTO thesis_ledger_route_admission_v3 VALUES (' + ','.join('?' for _ in before[0]) + ')', before)
    connection.execute('CREATE INDEX preserved_admission_index ON thesis_ledger_route_admission_v3(record_version)')
    connection.execute('CREATE TABLE preserved_audit(value TEXT)')
    connection.execute("CREATE TRIGGER preserved_admission_trigger AFTER UPDATE ON thesis_ledger_route_admission_v3 BEGIN INSERT INTO preserved_audit VALUES ('changed'); END")
    connection.commit()
    return path, connection, before


def test_real_store_upgrade_preserves_rows_indexes_triggers_and_is_idempotent(store, tmp_path):
    path, connection, before = legacy_database(store, tmp_path)
    connection.close()
    reopened = ThesisLedgerControlStore(str(path))
    reopened._ensure_schema()
    with reopened._connect() as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM thesis_ledger_route_admission_v3')] == before
        columns = connection.execute('PRAGMA table_info(thesis_ledger_route_admission_v3)').fetchall()
        assert next(row for row in columns if row['name'] == 'valid_until')['notnull'] == 0
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('index','trigger')")}
        assert {'preserved_admission_index', 'preserved_admission_trigger'} <= names
        assert connection.execute('SELECT COUNT(*) FROM preserved_audit').fetchone()[0] == 0


def test_upgrade_failure_rolls_back_original_table_and_rows(store, tmp_path):
    _, connection, before = legacy_database(store, tmp_path)
    connection.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE else sqlite3.SQLITE_OK)
    with pytest.raises(sqlite3.DatabaseError):
        ensure_nullable_admission_expiry(connection)
    connection.set_authorizer(None)
    assert [tuple(row) for row in connection.execute('SELECT * FROM thesis_ledger_route_admission_v3')] == before
    columns = connection.execute('PRAGMA table_info(thesis_ledger_route_admission_v3)').fetchall()
    assert next(row for row in columns if row['name'] == 'valid_until')['notnull'] == 1
    connection.close()
