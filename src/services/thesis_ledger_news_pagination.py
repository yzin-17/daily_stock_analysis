"""新闻继续点：绑定固定查询及控制上下文，不向客户端暴露来源 offset。"""

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import secrets
from threading import RLock
from time import monotonic

from src.services.thesis_ledger_capability_contracts import CapabilityContractError


@dataclass(frozen=True)
class NewsPageState:
    scope: str
    provider: str
    source: str
    offset: str
    seen_offsets: tuple[str, ...] = ()
    missing_reasons: tuple[str, ...] = ()
    last_published: str | None = None


class NewsCursorCache:
    def __init__(self, *, clock=monotonic, capacity=512, ttl=1800, max_bytes=8 * 1024 * 1024):
        self.clock, self.capacity, self.ttl = clock, capacity, ttl
        self.max_bytes, self._bytes = max_bytes, 0
        self._entries = OrderedDict()
        self._lock = RLock()

    def get(self, token, scope=None):
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry[0] <= self.clock():
                old = self._entries.pop(token, None)
                if old:
                    self._bytes -= old[2]
                raise CapabilityContractError("invalid_request", "新闻分页已失效，请重新查询")
            state = entry[1]
            if scope is not None and state.scope != scope:
                raise CapabilityContractError("invalid_request", "新闻分页与当前查询条件或控制版本不一致")
            self._entries.move_to_end(token)
            return state

    def put(self, state):
        if len(state.seen_offsets) > 10000:
            raise CapabilityContractError("unsupported", "新闻分页超过单次查询容量，请缩小范围")
        size = len(repr(state).encode())
        if size > min(self.max_bytes, 256 * 1024):
            raise CapabilityContractError("unsupported", "新闻分页超过单次查询容量，请缩小范围")
        token = "news-page-v1:" + secrets.token_urlsafe(32)
        with self._lock:
            now = self.clock()
            for old in [key for key, value in self._entries.items() if value[0] <= now]:
                self._bytes -= self._entries.pop(old)[2]
            self._entries[token] = (now + self.ttl, state, size)
            self._bytes += size
            while len(self._entries) > self.capacity or self._bytes > self.max_bytes:
                self._bytes -= self._entries.popitem(last=False)[1][2]
        return token


NEWS_CURSORS = NewsCursorCache()


def news_page_scope(request, context, revisions):
    from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
    value = {
        "query": request.model_dump(exclude={"requestId", "cursor", "requestBudgetMs"}),
        "database": context.database_path, "epoch": context.control_epoch,
        "credential": _provider_credential_revision_from_snapshot(context.snapshot),
        "revisions": revisions,
    }
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
