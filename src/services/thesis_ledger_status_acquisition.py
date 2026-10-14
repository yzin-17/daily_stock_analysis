"""独立状态事实获取的有界内存复用；控制和结果合同仍在原执行器处理。"""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import json
from threading import Event, RLock
from time import monotonic
from typing import Callable
from weakref import WeakKeyDictionary

from .thesis_ledger_capability_contracts import CapabilityContractError


@dataclass(frozen=True)
class StatusAcquisitionKey:
    provider: str
    source: str
    method: str
    symbol: str
    start: str | None
    end: str | None
    adapter_revision: str
    source_revision: str
    policy_revision: int
    provider_version: int


@dataclass
class AcquisitionFlight:
    signal: Event = field(default_factory=Event)
    value: dict | None = None
    error_code: str = "upstream_unavailable"


class StatusAcquisitionCache:
    def __init__(self, *, maximum_entries=64, maximum_bytes=524288, ttl_seconds=300,
                 wait_seconds=4.0, clock=monotonic):
        if (type(maximum_entries) is not int or not 0 < maximum_entries <= 64 or
                type(maximum_bytes) is not int or not 0 < maximum_bytes <= 524288 or
                not 0 < ttl_seconds <= 300 or not 0 < wait_seconds <= 4.0):
            raise ValueError("状态获取缓存边界无效")
        self.maximum_entries, self.maximum_bytes = maximum_entries, maximum_bytes
        self.ttl_seconds, self.wait_seconds, self.clock = ttl_seconds, wait_seconds, clock
        self._values, self._pending, self._lock = OrderedDict(), {}, RLock()

    def read(self, key: StatusAcquisitionKey, load: Callable[[], dict], cacheable: Callable[[dict], bool]) -> dict:
        with self._lock:
            expired = [item for item, (expires, _) in self._values.items() if expires <= self.clock()]
            for item in expired:
                del self._values[item]
            if key in self._values:
                self._values.move_to_end(key)
                return deepcopy(self._values[key][1])
            flight = self._pending.get(key)
            owner = flight is None
            if owner:
                if len(self._pending) >= self.maximum_entries:
                    raise CapabilityContractError("upstream_unavailable", "状态并发获取达到上限")
                flight = self._pending[key] = AcquisitionFlight()
        if not owner:
            if not flight.signal.wait(self.wait_seconds):
                raise CapabilityContractError("timeout", "共享状态获取等待超时")
            if flight.value is not None:
                return deepcopy(flight.value)
            raise CapabilityContractError(flight.error_code, "共享获取未产生可复用事实")
        try:
            value = load()
            payload = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
            if len(payload) <= self.maximum_bytes:
                flight.value = deepcopy(value)
                if cacheable(value):
                    with self._lock:
                        self._values[key] = (self.clock() + self.ttl_seconds, deepcopy(value))
                        self._values.move_to_end(key)
                        while len(self._values) > self.maximum_entries:
                            self._values.popitem(last=False)
            return value
        except CapabilityContractError as error:
            flight.error_code = error.code
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)
                flight.signal.set()


_CACHES = WeakKeyDictionary()
_LOCK = RLock()


def status_acquisition_cache(runtime):
    with _LOCK:
        cache = _CACHES.get(runtime)
        if cache is None:
            cache = _CACHES[runtime] = StatusAcquisitionCache()
        return cache
