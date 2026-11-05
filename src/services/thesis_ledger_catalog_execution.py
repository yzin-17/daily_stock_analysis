"""目录执行的协作停止边界；让隔离 Provider 和重试等待及时退出。"""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
import time

_STOPPING: ContextVar[Event | None] = ContextVar("catalog_execution_stopping", default=None)


class CatalogExecutionStopped(BaseException):
    """停止是控制流，不进入 Provider 故障降级或任务失败提交。"""


@contextmanager
def catalog_execution(stopping: Event):
    token = _STOPPING.set(stopping)
    try:
        catalog_checkpoint()
        yield
    finally:
        _STOPPING.reset(token)


def catalog_checkpoint() -> None:
    stopping = _STOPPING.get()
    if stopping is not None and stopping.is_set():
        raise CatalogExecutionStopped()


def poll_catalog_result(connection, timeout_seconds: float) -> bool:
    if _STOPPING.get() is None:
        return connection.poll(timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    while True:
        catalog_checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if connection.poll(min(0.1, remaining)):
            catalog_checkpoint()
            return True


def catalog_backoff(seconds: float) -> None:
    stopping = _STOPPING.get()
    if stopping is None:
        time.sleep(seconds)
    else:
        stopping.wait(seconds)
        catalog_checkpoint()
