from datetime import datetime, timezone

from src.services.thesis_ledger_dependency_facts import instrument_facts_response


def test_identity_only_does_not_fetch_bars_or_assert_tradability(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("身份读取不得重新请求行情")
    monkeypatch.setattr("src.services.thesis_ledger_dependency_facts.real_cn_tradability", forbidden)
    response = instrument_facts_response(
        "159516.SZ", datetime(2026, 9, 30, tzinfo=timezone.utc),
        "2026-05-18", "2026-05-20", "2026-05-18", "2026-05-20", [],
        instrument_type="ETF", identity_only=True,
    )
    assert response["status"] == "supported"
    assert response["coverage"]["complete"] is True
    assert response["facts"][0]["tradable"] is False
    assert "historicalTradability" not in response
