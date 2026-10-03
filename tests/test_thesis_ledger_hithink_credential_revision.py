from __future__ import annotations

import json
import re

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_control import (
    ThesisLedgerControlStore,
    _provider_credential_revision_from_snapshot,
)


_TEST_API_KEY = "synthetic-hithink-api-key"


def _snapshot(api_key: str, *, credential_version: int = 0) -> ProviderCredentialSnapshot:
    return ProviderCredentialSnapshot.create(
        "hithink",
        "environment",
        "api_key",
        {"apiKey": api_key} if api_key else {},
        config_version=0,
        credential_version=credential_version,
    )


def _set_synthetic_master(monkeypatch, value: str, version: str = "test-v1") -> None:
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", value)
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", version)
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)


def test_hithink_credential_revision_is_stable_and_tracks_key_and_master_rotation(
    monkeypatch,
):
    _set_synthetic_master(monkeypatch, "synthetic-master-one")
    snapshot = _snapshot(_TEST_API_KEY, credential_version=5)
    first_revision = _provider_credential_revision_from_snapshot(snapshot)

    assert first_revision == _provider_credential_revision_from_snapshot(snapshot)
    assert first_revision is not None
    assert re.fullmatch(r"hmac-sha256-v1:[0-9a-f]{64}", first_revision)
    assert _TEST_API_KEY not in first_revision
    assert "synthetic-master-one" not in first_revision

    rotated_api_key = _provider_credential_revision_from_snapshot(
        _snapshot("synthetic-hithink-api-key-rotated", credential_version=5)
    )
    assert rotated_api_key != first_revision

    _set_synthetic_master(monkeypatch, "synthetic-master-two")
    rotated_master = _provider_credential_revision_from_snapshot(snapshot)
    assert rotated_master != first_revision

    _set_synthetic_master(monkeypatch, "synthetic-master-one", version="test-v2")
    rotated_master_version = _provider_credential_revision_from_snapshot(snapshot)
    assert rotated_master_version != first_revision

    _set_synthetic_master(monkeypatch, "synthetic-master-one")
    same_key_with_unrelated_store_version = _provider_credential_revision_from_snapshot(
        _snapshot(_TEST_API_KEY, credential_version=99)
    )
    assert same_key_with_unrelated_store_version == first_revision


def test_hithink_credential_revision_fails_closed_for_missing_inputs(monkeypatch):
    _set_synthetic_master(monkeypatch, "synthetic-master")
    assert _provider_credential_revision_from_snapshot(_snapshot("")) is None

    monkeypatch.delenv("THESIS_LEDGER_DSA_SECRET_KEY", raising=False)
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)
    assert _provider_credential_revision_from_snapshot(_snapshot(_TEST_API_KEY)) is None


def test_hithink_credential_revision_requires_environment_api_key_snapshot(monkeypatch):
    _set_synthetic_master(monkeypatch, "synthetic-master")
    invalid_snapshots = (
        ProviderCredentialSnapshot.create(
            "tushare", "environment", "api_key", {"apiKey": _TEST_API_KEY}, 0, 0
        ),
        ProviderCredentialSnapshot.create(
            "hithink", "control", "api_key", {"apiKey": _TEST_API_KEY}, 0, 0
        ),
        ProviderCredentialSnapshot.create(
            "hithink", "environment", "token", {"apiKey": _TEST_API_KEY}, 0, 0
        ),
    )

    assert all(
        _provider_credential_revision_from_snapshot(snapshot) is None
        for snapshot in invalid_snapshots
    )


def test_credential_snapshot_repr_and_provider_registry_redact_revision_inputs(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HITHINK_API_KEY", _TEST_API_KEY)
    _set_synthetic_master(monkeypatch, "synthetic-master")
    store = ThesisLedgerControlStore(str(tmp_path / "credential-revision.sqlite"))
    snapshot = store.provider_credential_snapshot("hithink")
    revision = _provider_credential_revision_from_snapshot(snapshot)

    assert revision is not None
    assert _TEST_API_KEY not in repr(snapshot)
    assert revision not in repr(snapshot)

    registry_json = json.dumps(store.provider_registry())
    assert _TEST_API_KEY not in registry_json
    assert revision not in registry_json
    assert "credentialRevision" not in registry_json
