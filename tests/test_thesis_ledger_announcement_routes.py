from hashlib import sha256
from src.services.thesis_ledger_announcements import AnnouncementRepository, observation_clock
from src.services import thesis_ledger_trading_status as states
from tests.test_thesis_ledger_trading_status_routes import configured_client, DATA_HEADERS, CONTROL_HEADERS, DATA_URL, TARGET, policy, wire_request
from tests.test_thesis_ledger_announcements import record
from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs
from src.services.thesis_ledger_announcement_runtime import AnnouncementRuntime


def test_new_symbol_reads_without_admission_and_missing_days_remain_unknown(configured_client):
    client, runtime, store = configured_client
    repo = AnnouncementRepository()
    raw = b"%PDF-fixture"
    item = {**record(), "downloadState": "downloaded", "parseState": "parsed", "sha256": sha256(raw).hexdigest(),
            "observedAt": observation_clock(), "days": [{"date": "2023-08-21", "state": "suspended"}]}
    repo.save(item)
    (repo.root / "123.pdf").write_bytes(raw)
    store.apply_policy_v3(policy())
    assert store.get_route_admission_v3(key=wire_request()["routeKey"], target=TARGET) is None
    request = {**wire_request(), "symbol": "000881.SZ", "range": {"start": "2023-08-20", "end": "2023-08-23"}}
    response = client.post(DATA_URL, json=request, headers=DATA_HEADERS)
    assert response.status_code == 200
    assert [d["state"] for d in response.json()["days"]] == ["unknown", "suspended", "unknown", "unknown"]
    assert response.json()["sourceDocuments"][0]["kind"] == "issuerDisclosure"
    listed = client.get("/api/v3/thesis-ledger/market/announcements?q=000881", headers=DATA_HEADERS)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert client.get("/api/v3/thesis-ledger/market/announcements", headers=CONTROL_HEADERS).status_code == 401
    assert client.get("/api/v3/thesis-ledger/market/announcements?pageSize=101", headers=DATA_HEADERS).status_code == 422
    (repo.root / "123.pdf").write_bytes(b"corrupt")
    assert client.post(DATA_URL, json=request, headers=DATA_HEADERS).json()["detail"]["code"] == "invalid_response"


def test_partial_seed_window_returns_known_state_and_unknown_dates(configured_client):
    client, _, store = configured_client
    store.apply_policy_v3(policy())
    request = {**wire_request(), "range": {"start": "2023-08-15", "end": "2023-08-21"}}
    response = client.post(DATA_URL, json=request, headers=DATA_HEADERS)
    assert response.status_code == 200
    assert response.json()["days"][0]["state"] == "unknown"
    assert response.json()["days"][2]["state"] == "suspended"


def test_preparation_is_authenticated_nonblocking_and_persistent(configured_client):
    client, _, _ = configured_client
    jobs = AnnouncementJobs(AnnouncementRepository().root)
    runtime = AnnouncementRuntime(jobs, lambda *a, **kw: {"errors": [], "matched": 0}, lambda: True)
    client.app.state.announcement_runtime = runtime
    url = '/api/v3/thesis-ledger/control/announcements/prepare'
    payload = {"symbols": ["000881.SZ"], "start": "2023-08-21", "end": "2023-08-23"}
    assert client.post(url, json=payload, headers=DATA_HEADERS).status_code == 401
    response = client.post(url, json=payload, headers=CONTROL_HEADERS)
    assert response.status_code == 200 and response.json()['status'] == 'preparing'
    assert runtime.execute_one()
    complete = client.post(url, json=payload, headers=CONTROL_HEADERS).json()
    assert complete['status'] == 'ready' and complete['matched'] == 0
    assert complete['jobIds'] == response.json()['jobIds']


def test_downloaded_library_does_not_depend_on_company_seed_file(configured_client, monkeypatch, tmp_path):
    client, _, store = configured_client
    repo = AnnouncementRepository()
    raw = b"%PDF-fixture"
    item = {**record(), "downloadState": "downloaded", "parseState": "parsed", "sha256": sha256(raw).hexdigest(),
            "observedAt": observation_clock(), "days": [{"date": "2023-08-21", "state": "suspended"}]}
    repo.save(item)
    (repo.root / "123.pdf").write_bytes(raw)
    monkeypatch.setattr(states, "DATA_PATH", tmp_path / "absent-seed.json")
    store.apply_policy_v3(policy())
    request = {**wire_request(), "symbol": "000881.SZ"}
    response = client.post(DATA_URL, json=request, headers=DATA_HEADERS)
    assert response.status_code == 200
    assert response.json()["days"][-1]["state"] == "suspended"
