"""只读诊断的执行预算；超时结果不会晚到成为成功，限制后台读取数量。"""

from contextvars import copy_context
from threading import BoundedSemaphore, Event, Thread
from src.services.thesis_ledger_provider_runtime import ProviderCallError

_slots = BoundedSemaphore(4)


def run_probe(operation, *, timeout_seconds=8):
    if not _slots.acquire(blocking=False):
        raise ProviderCallError("probe_busy", "其他来源检查仍在执行，请稍后重试")
    done, result = Event(), []
    context = copy_context()

    def read():
        try:
            result.append((True, context.run(operation)))
        except Exception as error:
            result.append((False, error))
        finally:
            _slots.release()
            done.set()

    try:
        Thread(target=read, daemon=True).start()
    except Exception:
        _slots.release()
        raise
    if not done.wait(timeout_seconds):
        raise ProviderCallError("network_failure", "来源检查超过读取预算")
    success, value = result[0]
    if not success:
        raise value
    return value
