"""同步JS原生调用的进程锁、重入和安装边界。"""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from threading import Lock
import time

import pytest

from src.mini_racer_runtime import install_mini_racer_serialization


def test_native_operations_are_serial_and_class_identity_and_results_survive():
    active, maximum = 0, 0
    statistics = Lock()

    def native(value):
        nonlocal active, maximum
        with statistics:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.001)
        with statistics:
            active -= 1
        return value

    class MiniRacer:
        def __init__(self, value=1):
            self.value = native(value)

        def eval(self, code, *, timeout=None):
            return native((self.value, code, timeout))

        def execute(self, code):
            return self.eval(code, timeout=7)

        def close(self):
            native(None)

    module = SimpleNamespace(MiniRacer=MiniRacer)
    assert install_mini_racer_serialization(module)
    wrapped = MiniRacer.__init__
    assert install_mini_racer_serialization(module)
    assert MiniRacer.__init__ is wrapped

    def work(number):
        racer = module.MiniRacer(number)
        assert isinstance(racer, MiniRacer)
        value = racer.execute("1+1")
        racer.close()
        return value

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(20)))
    assert results == [(number, "1+1", 7) for number in range(20)]
    assert maximum == 1


def test_errors_are_preserved_and_optional_dependency_is_not_required(monkeypatch):
    class MiniRacer:
        def eval(self, code):
            raise ValueError(code)

    install_mini_racer_serialization(SimpleNamespace(MiniRacer=MiniRacer))
    with pytest.raises(ValueError, match="原异常"):
        MiniRacer().eval("原异常")

    def missing(name):
        raise ModuleNotFoundError("未安装可选依赖", name=name)

    monkeypatch.setattr("src.mini_racer_runtime.import_module", missing)
    assert install_mini_racer_serialization() is False


def test_transitive_import_failures_are_not_hidden(monkeypatch):
    def broken(_):
        raise ModuleNotFoundError("原生依赖损坏", name="other_dependency")

    monkeypatch.setattr("src.mini_racer_runtime.import_module", broken)
    with pytest.raises(ModuleNotFoundError, match="原生依赖损坏"):
        install_mini_racer_serialization()
