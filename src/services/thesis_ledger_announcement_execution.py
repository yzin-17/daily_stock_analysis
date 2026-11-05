"""公告获取的停止上下文；保留未完成任务供租约恢复。"""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event

_STOPPING: ContextVar[Event | None] = ContextVar("announcement_acquisition_stopping", default=None)


class AnnouncementAcquisitionStopped(BaseException):
    """生命周期停止不进入下载失败或业务终态提交。"""


@contextmanager
def announcement_acquisition(stopping: Event):
    token = _STOPPING.set(stopping)
    try:
        announcement_checkpoint()
        yield
    finally:
        _STOPPING.reset(token)


def announcement_checkpoint() -> None:
    stopping = _STOPPING.get()
    if stopping is not None and stopping.is_set():
        raise AnnouncementAcquisitionStopped()
