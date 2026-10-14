from datetime import datetime, timezone
from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs, month_windows
from src.services.thesis_ledger_announcement_runtime import AnnouncementRuntime
from api.thesis_ledger_announcement_runtime import announcements_selected
from concurrent.futures import ThreadPoolExecutor


def clock():
    return datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc).timestamp()


def test_months_and_exact_cache_with_no_announcements(tmp_path):
    assert list(month_windows("2023-08-21", "2023-09-05")) == [("2023-08-01", "2023-08-31"), ("2023-09-01", "2023-09-30")]
    jobs = AnnouncementJobs(tmp_path, clock)
    first = jobs.ensure(["000881.SZ"], "2023-08-01", "2023-08-21")
    second = jobs.ensure(["000881.SZ"], "2023-08-15", "2023-08-31")
    assert first["jobIds"] == second["jobIds"]
    row = jobs.claim()
    assert jobs.claim() is None
    assert jobs.finish(row, {"matched": 0, "errors": [], "truncated": False})
    assert jobs.ensure(["000881.SZ"], "2023-08-01", "2023-08-31")["status"] == "ready"
    assert jobs.claim() is None


def test_parser_upgrade_requeues_old_success_once_without_changing_month_identity(monkeypatch, tmp_path):
    jobs = AnnouncementJobs(tmp_path, clock)
    monkeypatch.setattr(jobs, "REVISION", "cninfo-company-v1-status-risk-v2")
    old = jobs.ensure(["000881.SZ"], "2023-08-01", "2023-08-31")
    assert jobs.finish(jobs.claim(), {"matched": 1, "downloaded": 1, "parsed": 0, "errors": []})
    monkeypatch.delattr(jobs, "REVISION")
    upgraded = jobs.ensure(["000881.SZ"], "2023-08-01", "2023-08-31")
    assert upgraded["jobIds"] == old["jobIds"]
    assert upgraded["status"] == "preparing"
    row = jobs.claim()
    assert row["attempt"] == 1
    assert jobs.finish(row, {"matched": 1, "downloaded": 1, "parsed": 1, "errors": []})
    current = jobs.ensure(["000881.SZ"], "2023-08-01", "2023-08-31")
    assert current["status"] == "ready" and current["parsed"] == 1
    assert jobs.claim() is None


def test_lease_recovery_and_late_owner_cannot_commit(tmp_path):
    now = [clock()]
    jobs = AnnouncementJobs(tmp_path, lambda: now[0])
    result = jobs.ensure(["000881.SZ"], "2023-08-01", "2023-08-31")
    old = jobs.claim()
    now[0] += 301
    recovered = AnnouncementJobs(tmp_path, lambda: now[0]).claim()
    assert recovered["attempt"] == 2 and recovered["owner"] != old["owner"]
    assert not jobs.finish(old, {"errors": []})
    assert jobs.finish(recovered, {"errors": []})
    assert jobs.summary(result["jobIds"])["status"] == "ready"


def test_retry_is_bounded_and_failure_has_terminal_progress(tmp_path):
    now = [clock()]
    jobs = AnnouncementJobs(tmp_path, lambda: now[0])
    result = jobs.ensure(["000881.SZ"], "2023-08-01", "2023-08-31")
    for i in range(3):
        row = jobs.claim()
        assert row["attempt"] == i + 1
        jobs.finish(row, {"errors": [{"stage": "query", "reason": "503"}]})
        now[0] += 90
    summary = jobs.summary(result["jobIds"])
    assert summary["status"] == "ready" and summary["failed"] == 1
    assert jobs.claim() is None


def test_daily_increment_only_uses_recent_symbols_and_does_not_renew_interest(tmp_path):
    now = [clock()]
    jobs = AnnouncementJobs(tmp_path, lambda: now[0])
    jobs.ensure(["000881.SZ"], "2023-08-01", "2023-08-31")
    runtime = AnnouncementRuntime(jobs, lambda *a, **k: {"errors": []}, lambda: True)
    assert runtime.schedule_once()
    assert not runtime.schedule_once()
    now[0] += 31 * 86400
    assert jobs.recent_symbols() == []
    assert runtime.schedule_once()


def test_disabled_runtime_does_not_enqueue_or_acquire(tmp_path):
    jobs = AnnouncementJobs(tmp_path, clock)
    runtime = AnnouncementRuntime(jobs, lambda *a: (_ for _ in ()).throw(AssertionError()), lambda: False)
    assert runtime.prepare(["000881.SZ"], "2023-08-01", "2023-08-31")["total"] == 0
    assert not runtime.execute_one() and not runtime.schedule_once()


def test_selection_requires_enabled_provider_and_relevant_local_route():
    class Store:
        enabled = True
        source = "cn-stock-risk-price-limits"
        def provider_registry(self): return [{"providerId": "official-states", "enabled": self.enabled}]
        def effective_policy_v3(self): return {"enabled": True, "routes": [{"key": {
            "market": "CN", "assetType": "STOCK", "capability": "HISTORICAL_TRADING_STATUS"},
            "targets": [{"providerId": "official-states", "eligible": True, "upstreamSource": self.source}]}]}
    store = Store()
    assert announcements_selected(store)
    store.enabled = False
    assert not announcements_selected(store)
    store.enabled = True
    store.source = "cn-stock-initial-listing"
    assert not announcements_selected(store)


def test_concurrent_preparations_share_one_durable_job_and_one_claim(tmp_path):
    jobs = AnnouncementJobs(tmp_path, clock)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: jobs.ensure(['000881.SZ'], '2023-08-01', '2023-08-31'), range(4)))
        claims = list(pool.map(lambda _: jobs.claim(), range(4)))
    assert len({result['jobIds'][0] for result in results}) == 1
    assert len([row for row in claims if row]) == 1
