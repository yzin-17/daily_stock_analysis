"""获取复用有界，消费参数隔离，旧控制代次不能复用。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_status_acquisition import StatusAcquisitionCache, StatusAcquisitionKey


def key(symbol='600000.SH', policy=1, provider=0):
    return StatusAcquisitionKey('baostock', 'baostock', 'flags', symbol, '2024-07-15', '2024-07-15', 'adapter-v1', 'source-v1', policy, provider)


def test_bounded_lru_ttl_and_copy_isolation():
    now, calls = [0], []
    cache = StatusAcquisitionCache(maximum_entries=2, ttl_seconds=5, clock=lambda: now[0])
    def read(k):
        return cache.read(k, lambda: calls.append(k) or {'state': ['suspended']}, lambda _: True)
    value = read(key())
    value['state'][0] = 'trading'
    assert read(key())['state'] == ['suspended'] and len(calls) == 1
    read(key('600001.SH'))
    read(key('600002.SH'))
    read(key())
    assert len(calls) == 4 and len(cache._values) == 2
    now[0] = 6
    read(key())
    assert len(calls) == 5


def test_control_epochs_never_share_acquisition():
    calls, cache = [], StatusAcquisitionCache()
    for k in (key(), key(policy=2), key(provider=1)):
        cache.read(k, lambda: calls.append(k) or {'facts': 'known'}, lambda _: True)
    assert len(calls) == 3


def test_same_key_concurrency_uses_one_source_call():
    started, release = Event(), Event()
    calls, cache = [], StatusAcquisitionCache()
    def load():
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {'facts': 'known'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.read, key(), load, lambda _: True)
        assert started.wait(2)
        second = pool.submit(cache.read, key(), load, lambda _: True)
        release.set()
        assert first.result(2) == second.result(2) == {'facts': 'known'}
    assert calls == [1]


def test_unknown_is_delivered_to_concurrent_waiters_without_persistent_reuse(monkeypatch):
    started, release, joined = Event(), Event(), Event()
    cache, calls = StatusAcquisitionCache(), []
    def load():
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {'facts': 'unknown'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.read, key(), load, lambda _: False)
        assert started.wait(2)
        signal = cache._pending[key()].signal
        original_wait = signal.wait
        def waiting(timeout):
            joined.set()
            return original_wait(timeout)
        monkeypatch.setattr(signal, 'wait', waiting)
        second = pool.submit(cache.read, key(), load, lambda _: False)
        assert joined.wait(2)
        release.set()
        assert first.result(2) == second.result(2) == {'facts': 'unknown'}
    assert not cache._values
    assert calls == [1]


def test_wait_is_bounded_and_failure_does_not_poison_future_read():
    started, release = Event(), Event()
    cache = StatusAcquisitionCache(wait_seconds=0.02)
    def fail():
        started.set()
        assert release.wait(2)
        raise RuntimeError('来源失败')
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(cache.read, key(), fail, lambda _: True)
        assert started.wait(2)
        with pytest.raises(CapabilityContractError) as error:
            cache.read(key(), lambda: {}, lambda _: True)
        assert error.value.code == 'timeout'
        release.set()
        with pytest.raises(RuntimeError):
            first.result(2)
    assert cache.read(key(), lambda: {'valid': True}, lambda _: True) == {'valid': True}


@pytest.mark.parametrize('cacheable,maximum_bytes', [(False, 100), (True, 1)])
def test_unknown_and_oversized_results_not_cached(cacheable, maximum_bytes):
    calls, cache = [], StatusAcquisitionCache(maximum_bytes=maximum_bytes)
    for _ in range(2):
        cache.read(key(), lambda: calls.append(1) or {'facts': 'unknown'}, lambda _: cacheable)
    assert len(calls) == 2 and not cache._values
