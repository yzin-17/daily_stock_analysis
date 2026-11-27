"""同一 OpenD 端点的快照调用共享厂商公布的请求窗口。"""

from collections import deque
from threading import Lock
from time import monotonic

FUTU_SNAPSHOT_LIMIT = 60
FUTU_SNAPSHOT_WINDOW_SECONDS = 30

_lock = Lock()
_windows = {}


def reserve_snapshot(host, port):
    with _lock:
        now = monotonic()
        earliest = now - FUTU_SNAPSHOT_WINDOW_SECONDS
        for endpoint in tuple(_windows):
            attempts = _windows[endpoint]
            while attempts and attempts[0] <= earliest:
                attempts.popleft()
            if not attempts:
                del _windows[endpoint]
        attempts = _windows.setdefault((host, port), deque())
        if len(attempts) >= FUTU_SNAPSHOT_LIMIT:
            return False
        attempts.append(now)
        return True
