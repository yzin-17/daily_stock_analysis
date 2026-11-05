"""公告获取停止、资源释放及未完成租约恢复。"""

from datetime import datetime, timezone
from functools import partial
from threading import Event, Thread

import pytest

from data_provider import cninfo_announcements as source
from src.services.thesis_ledger_announcement_execution import (
    AnnouncementAcquisitionStopped, announcement_acquisition, announcement_checkpoint,
)
from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs
from src.services.thesis_ledger_announcement_runtime import AnnouncementRuntime
from src.services.thesis_ledger_announcements import AnnouncementRepository


def jobs_at(root):
    now = [datetime(2025, 2, 1, 1, tzinfo=timezone.utc).timestamp()]
    return AnnouncementJobs(root, clock=lambda: now[0]), now


def test_repeated_start_and_stop_only_owns_two_threads(tmp_path):
    jobs, _ = jobs_at(tmp_path)
    runtime = AnnouncementRuntime(jobs, lambda *args, **kwargs: pytest.fail("disabled source called"), lambda: False)
    try:
        runtime.start()
        original = list(runtime.threads)
        runtime.start()
        assert runtime.threads == original and len(original) == 2
        runtime.stop()
        runtime.stop()
        assert not any(thread.is_alive() for thread in original)
        runtime.start()
        assert len(runtime.threads) == 2 and all(thread not in original for thread in runtime.threads)
    finally:
        runtime.stop()


def test_partial_thread_start_failure_releases_already_started_worker(monkeypatch, tmp_path):
    from src.services import thesis_ledger_announcement_runtime as module
    jobs, _ = jobs_at(tmp_path)
    count = [0]

    class FailingThread(Thread):
        def start(self):
            count[0] += 1
            if count[0] == 2:
                raise RuntimeError("controlled-thread-start-failure")
            super().start()

    monkeypatch.setattr(module, "Thread", FailingThread)
    runtime = AnnouncementRuntime(jobs, lambda *args, **kwargs: {}, lambda: False)
    with pytest.raises(RuntimeError, match="controlled-thread-start-failure"):
        runtime.start()
    assert not any(thread.is_alive() for thread in runtime.threads)
    runtime.stop()


def test_cancelling_stream_closes_owned_session_and_preserves_job_for_lease_recovery(monkeypatch, tmp_path):
    repo = AnnouncementRepository(tmp_path)
    jobs, now = jobs_at(tmp_path)
    summary = jobs.ensure(["000881.SZ"], "2025-01-01", "2025-01-31")
    entered = Event()
    sessions = []
    runtime = AnnouncementRuntime(jobs, partial(source.acquire_announcements, repository=repo), lambda: True)
    item = {"secCode": "000881", "announcementId": "123", "announcementTitle": "停牌公告",
            "adjunctUrl": "finalpage/2025-01-01/123.PDF", "announcementTime": 1735689600000}

    class Response:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def raise_for_status(self):
            pass

        def iter_content(self, _size):
            entered.set()
            yield b"%PDF-1.4 controlled"
            assert runtime.stopping.wait(5)
            yield b"controlled-second-chunk"

    response = Response()

    class Session:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def get(self, *_args, **_kwargs):
            return response

    def create_session():
        session = Session()
        sessions.append(session)
        return session

    monkeypatch.setattr(source.requests, "Session", create_session)
    monkeypatch.setattr(source, "query_announcements", lambda *args, **kwargs: ([("000881.SZ", item)], [], False))
    try:
        runtime.start()
        assert entered.wait(5)
        runtime.stop()
        assert len(sessions) == 1 and sessions[0].closed and response.closed
        previous = jobs.read(summary["jobIds"][0])
        assert previous["state"] == "running" and "result" not in previous
        now[0] += jobs.LEASE_SECONDS + 1
        recovered = AnnouncementJobs(repo.root, clock=lambda: now[0]).claim()
        assert recovered["attempt"] == 2 and recovered["owner"] != previous["owner"]
        assert jobs.finish(previous, {"errors": []}) is False
    finally:
        runtime.stop()


def test_caller_session_is_not_closed_and_stop_context_does_not_leak(monkeypatch, tmp_path):
    class CallerSession:
        def __enter__(self):
            pytest.fail("caller-owned session entered")

        def __exit__(self, *_args):
            pytest.fail("caller-owned session closed")

    monkeypatch.setattr(source, "query_announcements", lambda *args, **kwargs: ([], [], False))
    stopping = Event()
    stopping.set()
    with pytest.raises(AnnouncementAcquisitionStopped):
        with announcement_acquisition(stopping):
            pass
    announcement_checkpoint()
    result = source.acquire_announcements(
        ["000881.SZ"], "2025-01-01", "2025-01-31",
        session=CallerSession(), repository=AnnouncementRepository(tmp_path),
    )
    assert result["matched"] == 0 and result["errors"] == []


def test_stopped_runtime_rejects_prepare_and_unresponsive_stop_is_not_success(tmp_path):
    jobs, _ = jobs_at(tmp_path)
    entered, release = Event(), Event()
    jobs.ensure(["000881.SZ"], "2025-01-01", "2025-01-31")

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(5)
        return {"errors": []}

    runtime = AnnouncementRuntime(jobs, blocked, lambda: True)
    try:
        runtime.start()
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match="退出期限"):
            runtime.stop(timeout=0.01)
        with pytest.raises(RuntimeError, match="已停止"):
            runtime.prepare(["000881.SZ"], "2025-01-01", "2025-01-31")
    finally:
        release.set()
        runtime.stop()


def test_cancellation_after_company_lookup_stops_pagination_without_writing_cache(tmp_path):
    from data_provider.cninfo_announcement_query import query_announcements
    stopping = Event()
    calls = []

    class Client:
        def post(self, url, **kwargs):
            calls.append(url)
            stopping.set()
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return [{"code": "000881", "category": "A股", "orgId": "controlled"}]

    with pytest.raises(AnnouncementAcquisitionStopped):
        with announcement_acquisition(stopping):
            query_announcements(Client(), ["000881.SZ"], "2025-01-01", "2025-01-31", 5, root=tmp_path)
    assert len(calls) == 1 and "topSearch" in calls[0]
    assert not (tmp_path / ".queries").exists()
