"""真实成功记录按精确能力及配置隔离，不以缺少记录阻止首次调用。"""

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from src.services.thesis_ledger_capability_verification import (
    TABLE, VerificationCatalog, begin_verification, publish_verified,
)
from src.services.thesis_ledger_control import ThesisLedgerControlStore

KEY = {'kind': 'bar', 'market': 'CN', 'assetType': 'STOCK',
       'capability': 'DAILY_BAR', 'timeframe': '1d', 'adjustment': 'qfq'}
TARGET = {'providerId': 'efinance', 'upstreamSource': 'eastmoney'}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('THESIS_LEDGER_DSA_SECRET_KEY', 'synthetic-master-key')
    return ThesisLedgerControlStore(str(tmp_path / 'control.db'))


def test_first_success_is_persisted_and_survives_restart(store):
    assert VerificationCatalog(store).read(KEY, TARGET) == {'state': 'unverified'}
    attempt = begin_verification(store, KEY, TARGET)
    assert publish_verified(store, attempt, evidence={'symbol': '600519.SH', 'fingerprint': 'synthetic'})
    result = VerificationCatalog(ThesisLedgerControlStore(store.database_path)).read(KEY, TARGET)
    assert result['state'] == 'verified' and result['verifiedAt'] and len(result['evidenceHash']) == 64
    assert VerificationCatalog(store).read({**KEY, 'adjustment': 'hfq'}, TARGET) == {'state': 'unverified'}


def test_fixture_or_cached_result_does_not_create_verification(store):
    attempt = begin_verification(store, KEY, TARGET)
    assert not publish_verified(store, attempt, evidence={}, real_read=False)
    assert VerificationCatalog(store).read(KEY, TARGET) == {'state': 'unverified'}
    with store._connect() as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()


def test_changed_configuration_rejects_late_success_and_requires_new_verification(store):
    old = begin_verification(store, KEY, TARGET)
    assert publish_verified(store, old, evidence={'request': 'first'})
    store.save_provider_config('efinance', {'requestId': 'new-config', 'enabled': True})
    assert VerificationCatalog(store).read(KEY, TARGET) == {'state': 'unverified'}
    assert not publish_verified(store, old, evidence={'request': 'late'})
    current = begin_verification(store, KEY, TARGET)
    assert publish_verified(store, current, evidence={'request': 'new'})
    assert VerificationCatalog(store).read(KEY, TARGET)['state'] == 'verified'


def test_concurrent_success_keeps_the_first_success_evidence(store):
    attempt = begin_verification(store, KEY, TARGET)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(lambda i: publish_verified(store, attempt, evidence={'request': i}), range(8)))
    first = VerificationCatalog(store).read(KEY, TARGET)
    assert publish_verified(store, attempt, evidence={'request': 'later'})
    assert VerificationCatalog(store).read(KEY, TARGET) == first


def test_corrupt_or_wrong_context_record_is_not_accepted(store):
    attempt = begin_verification(store, KEY, TARGET)
    assert publish_verified(store, attempt, evidence={})
    with store._connect() as connection:
        connection.execute(f'UPDATE {TABLE} SET evidence_json=? WHERE route_id=?',
                           (json.dumps({'state': 'verified'}), attempt.route_id))
    assert VerificationCatalog(store).read(KEY, TARGET) == {'state': 'unverified'}
