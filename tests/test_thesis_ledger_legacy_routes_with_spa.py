"""完整 DSA 应用含前端回退时，旧 ThesisLedger 合同仍逐方法返回 404。"""

from fastapi.testclient import TestClient

from api.app import create_app


def test_removed_thesis_ledger_post_does_not_fall_into_spa_method_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "false")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-token")
    (tmp_path / "index.html").write_text("<html><body>fixture</body></html>", encoding="utf-8")
    client = TestClient(create_app(static_dir=tmp_path))
    headers = {"authorization": "Bearer control-token"}

    for prefix in ("/api/v1/thesis-ledger", "/api/v2/thesis-ledger"):
        response = client.post(
            f"{prefix}/control/handshake",
            headers=headers,
            json={"contractVersion": 3},
        )
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
        assert client.get(f"{prefix}/control/handshake", headers=headers).status_code == 404
        assert client.post(prefix, headers=headers).status_code == 404


def test_all_removed_data_families_reject_before_producer_with_spa(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "false")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    (tmp_path / "index.html").write_text("<html>fixture</html>", encoding="utf-8")
    client = TestClient(create_app(static_dir=tmp_path))
    headers = {"authorization": "Bearer data-token"}
    paths = [
        "/capabilities", "/market/quote", "/market/chip", "/market/fund-nav",
        "/market/fund-nav/history", "/market/fund-holdings", "/market/fx-rates",
        "/market/bars", "/market/chart-bars", "/market/events",
        "/market/indicators/calculate", "/backtest/calendar", "/backtest/instrument-facts",
    ]
    for prefix in ("/api/v1/thesis-ledger", "/api/v2/thesis-ledger"):
        for path in paths:
            for method in ("GET", "POST"):
                response = client.request(method, prefix + path, headers=headers)
                assert response.status_code == 404, (prefix, path, method)
                assert response.json()["error"] == "not_found"
