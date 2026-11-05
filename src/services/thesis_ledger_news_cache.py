"""同一业务新闻请求的有界响应重放；保留原始时钟和尝试事实。"""

from collections import OrderedDict
from hashlib import sha256
import json
from threading import RLock
from time import monotonic
from weakref import WeakKeyDictionary


class NewsReplayCache:
    def __init__(self, *, clock=monotonic):
        self.clock = clock
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    def get(self, key):
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            expires, response, size = item
            if expires <= self.clock():
                del self._entries[key]
                self._bytes -= size
                return None
            self._entries.move_to_end(key)
            return response.model_copy(deep=True)

    def put(self, key, response):
        if response.status != "available":
            return
        size = len(response.model_dump_json().encode())
        if size > 1_048_576:
            return
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= old[2]
            self._entries[key] = (self.clock() + 60, response.model_copy(deep=True), size)
            self._bytes += size
            while len(self._entries) > 64 or self._bytes > 1_048_576:
                _, (_, _, removed_size) = self._entries.popitem(last=False)
                self._bytes -= removed_size


def news_cache_key(request, *, policy_revision, target, binding, credential_revision, epochs, identity):
    facts = {"request": request.model_dump(), "policyRevision": policy_revision, "target": target,
             "adapterRevision": binding.adapter_revision, "sourceRevision": binding.source_revision,
             "credentialRevision": credential_revision, "providerEpochs": epochs,
             "publicIdentity": None if identity is None else
             [identity.symbol, identity.market, identity.asset_type, identity.name]}
    return sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_CACHES = WeakKeyDictionary()
_LOCK = RLock()


def news_replay_cache(runtime):
    with _LOCK:
        if runtime not in _CACHES:
            _CACHES[runtime] = NewsReplayCache()
        return _CACHES[runtime]
