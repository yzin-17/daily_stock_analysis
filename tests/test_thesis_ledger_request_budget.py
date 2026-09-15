"""ThesisLedger ETF 请求资格的跨连接并发回归。"""

import threading
from concurrent.futures import ThreadPoolExecutor

from src.services.thesis_ledger_control import ThesisLedgerControlStore


class _NoopLock:
    """让测试依赖 SQLite writer lock，而不是进程内互斥。"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _IndependentStore(ThesisLedgerControlStore):
    """模拟两个 worker，各自使用独立连接且不共享 Python 锁。"""

    _schema_lock = _NoopLock()


def test_request_budget_concurrent_claim_has_exactly_one_winner(tmp_path):
    """同一请求键并发领取时只能有一个 upstream 资格。"""
    database_path = str(tmp_path / "request-budget.db")
    first = _IndependentStore(database_path)
    second = _IndependentStore(database_path)
    barrier = threading.Barrier(2)

    def claim(store):
        barrier.wait(timeout=2)
        return store.claim_provider_request_budget(
            "efinance",
            "REALTIME_QUOTE",
            "ETF",
            "510300",
            request_id="concurrent-test",
            now=1_700_000_000,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, (first, second)))

    assert sorted(result["allowed"] for result in results) == [False, True]
    assert sum(result["allowed"] for result in results) == 1
