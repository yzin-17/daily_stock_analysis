"""保护同步来源使用的MiniRacer原生操作，避免并发isolate初始化终止进程。"""

from functools import wraps
from importlib import import_module
from threading import RLock
from typing import Any

_NATIVE_LOCK = RLock()
_MARKER = "__thesis_ledger_native_serialized__"
_METHODS = ("__init__", "eval", "execute", "call", "close", "__enter__", "__exit__")


def _serialized(method):
    @wraps(method)
    def invoke(*args, **kwargs):
        with _NATIVE_LOCK:
            return method(*args, **kwargs)

    setattr(invoke, _MARKER, True)
    return invoke


def install_mini_racer_serialization(module: Any = None) -> bool:
    """在来源导入前安装；包装原类方法以保留已有导入引用和类型身份。"""
    if module is None:
        try:
            module = import_module("py_mini_racer")
        except ModuleNotFoundError as error:
            if error.name == "py_mini_racer":
                return False
            raise
    with _NATIVE_LOCK:
        racer = module.MiniRacer
        for name in _METHODS:
            method = getattr(racer, name, None)
            if callable(method) and not getattr(method, _MARKER, False):
                setattr(racer, name, _serialized(method))
    return True
