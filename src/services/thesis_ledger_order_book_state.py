"""盘口订阅纯状态验证面；不持有生产连接、账号或推送服务。"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Literal

from .thesis_ledger_order_book_contract import BookTarget, Level, OrderBook


@dataclass(frozen=True)
class SubscriptionIdentity:
    account_scope: str
    permission_epoch: int
    provider_id: str
    upstream_source: str
    symbol: str
    market: str
    depth: int
    allow_depth_degradation: bool


@dataclass(frozen=True)
class BookDelta:
    target: BookTarget
    symbol: str
    market: str
    generation: int
    previous_sequence: int | None
    sequence: int | None
    received_at: str
    source_event_time: str | None
    changes: tuple[tuple[Literal["bids", "asks"], Level], ...]


class BookSession:
    def __init__(self, identity: SubscriptionIdentity, *, capacity: int, release: Callable[[], None]):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("缓冲必须有明确正数上限")
        self.identity = identity
        self.capacity = capacity
        self.generation = 1
        self.state = "connecting"
        self.book: OrderBook | None = None
        self._buffer = deque()
        self._release = release
        self._closed = False

    def _matches(self, target, symbol, market, generation):
        return (not self._closed and generation == self.generation
                and (target.providerId, target.upstreamSource, symbol, market) == (
                    self.identity.provider_id, self.identity.upstream_source, self.identity.symbol, self.identity.market))

    def snapshot(self, book: OrderBook, generation: int):
        if not self._matches(book.target, book.symbol, book.market, generation):
            return False
        if book.state != "valid" or book.requestedDepth != self.identity.depth:
            self.invalidate()
            return False
        if book.depthDegraded and not self.identity.allow_depth_degradation:
            self.invalidate()
            return False
        self.book = OrderBook.model_validate({**book.model_dump(), "continuity": "unknown"})
        self._buffer.clear()
        self.state = "live"
        return True

    def enqueue(self, delta: BookDelta):
        if not self._matches(delta.target, delta.symbol, delta.market, delta.generation):
            return False
        if self.book is None or self.state != "live" or len(self._buffer) >= self.capacity:
            self.invalidate()
            return False
        self._buffer.append(delta)
        return True

    def drain(self):
        """先按序重建所有增量，再供 UI 合并完整状态。"""
        while self._buffer and self.book is not None:
            delta = self._buffer.popleft()
            book = self.book
            if any(value is not None and (type(value) is not int or value < 0)
                   for value in (delta.previous_sequence, delta.sequence)):
                self.invalidate()
                break
            verifiable = book.sequence is not None
            if verifiable and (delta.previous_sequence != book.sequence or delta.sequence != book.sequence + 1):
                self.invalidate()
                break
            if not verifiable and (delta.previous_sequence is not None or delta.sequence is not None):
                self.invalidate()
                break
            value = book.model_dump()
            for side in ("bids", "asks"):
                levels = {Decimal(level.price): level for level in getattr(book, side)}
                for changed_side, level in delta.changes:
                    if changed_side not in {"bids", "asks"}:
                        self.invalidate()
                        return None
                    if changed_side != side:
                        continue
                    price = Decimal(level.price)
                    if Decimal(level.quantity) == 0:
                        levels.pop(price, None)
                    else:
                        levels[price] = level
                # 保留重建深度内全部档位，不在增量入队前做 UI 抽样。
                limit = min(book.requestedDepth, book.sourceDepthLimit or book.requestedDepth)
                ordered = sorted(levels, reverse=side == "bids")
                if len(ordered) > limit:
                    # 超深档位会影响后续删除后的重建，不能静默截断。
                    self.invalidate()
                    return None
                value[side] = [levels[price].model_dump() for price in ordered]
            value.update(sequence=delta.sequence, receivedAt=delta.received_at,
                         sourceEventTime=delta.source_event_time, continuity="verified" if verifiable else "unknown")
            try:
                self.book = OrderBook.model_validate(value)
            except ValueError:
                self.invalidate()
                return None
        return self.book

    def invalidate(self):
        if self._closed:
            return
        self.book = None
        self._buffer.clear()
        self.state = "resyncing"

    def switch(self, identity: SubscriptionIdentity, release: Callable[[], None]):
        if self._closed:
            raise ValueError("已关闭订阅不能切源")
        self._release()
        self._release = release
        self.identity = identity
        self.generation += 1
        self.invalidate()
        return self.generation

    def close(self):
        if not self._closed:
            self._closed = True
            self.book = None
            self._buffer.clear()
            self.state = "closed"
            self._release()


class BookSubscriptions:
    """隔离共享键和最后一个消费者释放；无真实连接管理。"""

    def __init__(self):
        self._sessions: dict[SubscriptionIdentity, tuple[BookSession, int]] = {}

    def acquire(self, identity: SubscriptionIdentity, create: Callable[[], BookSession]):
        session, count = self._sessions.get(identity, (None, 0))
        if session is None:
            session = create()
        if session.identity != identity or session.state == "closed":
            raise ValueError("共享订阅身份已变化")
        self._sessions[identity] = session, count + 1
        return session

    def release(self, identity: SubscriptionIdentity):
        if identity not in self._sessions:
            return
        session, count = self._sessions[identity]
        if count == 1:
            del self._sessions[identity]
            session.close()
        else:
            self._sessions[identity] = session, count - 1

    def revoke(self, account_scope: str, permission_epoch: int):
        for identity in list(self._sessions):
            if identity.account_scope == account_scope and identity.permission_epoch == permission_epoch:
                session, _ = self._sessions.pop(identity)
                session.close()

    def disable(self, provider_id: str, upstream_source: str):
        for identity in list(self._sessions):
            if (identity.provider_id, identity.upstream_source) == (provider_id, upstream_source):
                session, _ = self._sessions.pop(identity)
                session.close()
