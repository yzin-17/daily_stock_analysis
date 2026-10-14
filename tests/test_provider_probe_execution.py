"""诊断读取超时及并发预算，不接受晚到成功。"""

from threading import Event
import pytest
from src.services.thesis_ledger_probe_execution import run_probe
from src.services.thesis_ledger_provider_runtime import ProviderCallError


def test_timeout_rejects_late_result_and_releases_slot_when_reader_finishes():
    release, finished = Event(), Event()
    def read():
        release.wait(2)
        finished.set()
        return {"status": "healthy"}
    try:
        with pytest.raises(ProviderCallError) as caught:
            run_probe(read, timeout_seconds=0.01)
        assert caught.value.code == "network_failure"
    finally:
        release.set()
        assert finished.wait(2)
    assert run_probe(lambda: 1) == 1


def test_source_failure_is_preserved():
    def denied():
        raise ProviderCallError("permission_denied", "synthetic permission")
    with pytest.raises(ProviderCallError) as caught:
        run_probe(denied)
    assert caught.value.code == "permission_denied"
