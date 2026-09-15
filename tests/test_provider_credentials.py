import sqlite3

import pytest

from src.services.provider_credentials import (
    CredentialPatch,
    configured_fields,
    decode_credential_plaintext,
    encode_credential_plaintext,
    merge_credential_patch,
    parse_credential_patch,
    validate_stored_credential,
)
from src.services.thesis_ledger_control import (
    ControlContractError,
    ThesisLedgerControlStore,
    _encrypt_secret,
)


def _store(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "0123456789abcdef-secret-key")
    return ThesisLedgerControlStore(str(tmp_path / "credentials.db"))


def test_structured_credential_schema_and_partial_merge():
    patch = parse_credential_patch("longbridge", {"method": "legacy", "values": {"appKey": "key"}})
    with pytest.raises(ValueError, match="首次配置或切换"):
        merge_credential_patch("longbridge", patch, None)

    current = merge_credential_patch(
        "longbridge",
        parse_credential_patch(
            "longbridge",
            {
                "method": "legacy",
                "values": {"appKey": "key", "appSecret": "secret", "accessToken": "token"},
            },
        ),
        None,
    )
    merged = merge_credential_patch(
        "longbridge",
        CredentialPatch("legacy", {"appSecret": "new-secret"}),
        current,
    )
    assert merged.values == {"appKey": "key", "appSecret": "new-secret", "accessToken": "token"}
    assert configured_fields("longbridge", merged) == {
        "accessToken": True,
        "appKey": True,
        "appSecret": True,
    }


def test_structured_credential_format_round_trips_and_legacy_is_compatible():
    patch = CredentialPatch("api_key", {"apiKey": "secret"})
    decoded = decode_credential_plaintext(encode_credential_plaintext(patch))
    assert decoded.method == "api_key"
    assert decoded.values == {"apiKey": "secret"}
    assert decode_credential_plaintext("old-single-secret").legacy is True
    with pytest.raises(ValueError, match="格式版本"):
        decode_credential_plaintext(
            '{"kind":"provider_credentials","version":99,"method":"token","values":{"token":"x"}}'
        )
    with pytest.raises(ValueError, match="未知字段"):
        decode_credential_plaintext(
            '{"kind":"provider_credentials","version":1,"method":"token","values":{"token":"x"},"extra":true}'
        )


def test_stored_structured_credentials_require_provider_method_and_complete_fields():
    with pytest.raises(ValueError, match="不支持凭证方式"):
        validate_stored_credential(
            "tushare",
            decode_credential_plaintext(
                '{"kind":"provider_credentials","version":1,"method":"legacy","values":{"token":"x"}}'
            ),
        )
    with pytest.raises(ValueError, match="未完整配置"):
        validate_stored_credential(
            "longbridge",
            decode_credential_plaintext(
                '{"kind":"provider_credentials","version":1,"method":"legacy","values":{"appKey":"x"}}'
            ),
        )


def test_invalid_stored_structured_credential_fails_closed(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_provider_config(
        "tushare",
        {"requestId": "valid", "credentials": {"method": "token", "values": {"token": "x"}}},
    )
    key_version, ciphertext = _encrypt_secret(
        '{"kind":"provider_credentials","version":99,"method":"token","values":{"token":"x"}}'
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE thesis_ledger_provider_config SET credential_ciphertext=?, secret_key_version=? "
            "WHERE provider_id='tushare'",
            (ciphertext, key_version),
        )
    tushare = next(item for item in store.provider_registry() if item["providerId"] == "tushare")
    assert tushare["credentialSource"] == "control"
    assert tushare["credentialConfigured"] is False
    assert tushare["credentialMethod"] is None
    assert tushare["credentialFieldsConfigured"] == {"token": False}


def test_provider_config_uses_page_credentials_and_versions(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    first = store.save_provider_config(
        "tushare",
        {
            "requestId": "first",
            "credentials": {"method": "token", "values": {"token": "token-1"}},
        },
    )
    assert first["credentialSource"] == "control"
    assert first["credentialFieldsConfigured"] == {"token": True}
    assert first["credentialMethod"] == "token"
    assert first["configVersion"] == 1
    assert first["credentialConfigured"] is True

    preserved = store.save_provider_config(
        "tushare",
        {
            "requestId": "second",
            "credentials": {"method": "token", "values": {"token": ""}},
        },
    )
    assert preserved["configVersion"] == 2
    assert preserved["credentialSource"] == "control"
    assert preserved["credentialMethod"] == "token"

    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT credential_ciphertext, config_version, credential_version "
            "FROM thesis_ledger_provider_config WHERE provider_id = 'tushare'"
        ).fetchone()
    assert "token-1" not in row[0]
    assert row[1:] == (2, 1)

    cleared = store.save_provider_config(
        "tushare", {"requestId": "third", "clearCredentials": True}
    )
    assert cleared["credentialSource"] == "none"
    assert cleared["credentialFieldsConfigured"] == {"token": False}
    assert cleared["credentialMethod"] is None
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT config_version, credential_version "
            "FROM thesis_ledger_provider_config WHERE provider_id = 'tushare'"
        ).fetchone() == (3, 2)


@pytest.mark.parametrize(
    ("provider_id", "method", "values", "fields"),
    [
        ("tushare", "token", {"token": "token"}, {"token": True}),
        ("tickflow", "api_key", {"apiKey": "tickflow"}, {"apiKey": True}),
        ("finnhub", "api_key", {"apiKey": "finnhub"}, {"apiKey": True}),
        ("alphavantage", "api_key", {"apiKey": "alpha"}, {"apiKey": True}),
        (
            "longbridge",
            "legacy",
            {"appKey": "key", "appSecret": "secret", "accessToken": "token"},
            {"accessToken": True, "appKey": True, "appSecret": True},
        ),
    ],
)
def test_all_manual_provider_credential_methods_are_storable(
    monkeypatch, tmp_path, provider_id, method, values, fields
):
    store = _store(monkeypatch, tmp_path / provider_id)
    result = store.save_provider_config(
        provider_id,
        {"requestId": provider_id, "credentials": {"method": method, "values": values}},
    )
    assert result["credentialSource"] == "control"
    assert result["credentialFieldsConfigured"] == fields
    assert result["credentialMethod"] == method
    assert result["credentialConfigured"] is True


def test_credentials_reject_oauth_unknown_fields_and_conflicting_clear(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    with pytest.raises(Exception, match="OAuth"):
        store.save_provider_config(
            "tushare",
            {"requestId": "oauth", "credentials": {"method": "oauth", "values": {}}},
        )
    with pytest.raises(Exception, match="credentials 包含未知字段"):
        store.save_provider_config(
            "tushare",
            {
                "requestId": "unknown-credential-key",
                "credentials": {"method": "token", "values": {}, "extra": "x"},
            },
        )
    with pytest.raises(Exception, match="未知字段"):
        store.save_provider_config(
            "finnhub",
            {
                "requestId": "unknown",
                "credentials": {"method": "api_key", "values": {"apiKey": "x", "extra": "y"}},
            },
        )
    with pytest.raises(ControlContractError, match="未知字段"):
        store.save_provider_config("tushare", {"requestId": "unknown-top-level", "unexpected": True})


def test_clearing_page_credential_restores_environment_source(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: type("EnvironmentConfig", (), {"tushare_token": "environment-token"})(),
    )
    store = _store(monkeypatch, tmp_path)
    store.save_provider_config(
        "tushare",
        {"requestId": "page", "credentials": {"method": "token", "values": {"token": "page"}}},
    )
    restored = store.save_provider_config("tushare", {"requestId": "clear", "clearCredentials": True})
    assert restored["credentialSource"] == "environment"
    assert restored["credentialFieldsConfigured"] == {"token": False}
    assert restored["credentialMethod"] is None
    with pytest.raises(Exception, match="不能与 credentials"):
        store.save_provider_config(
            "finnhub",
            {
                "requestId": "conflict",
                "clearCredentials": True,
                "credentials": {"method": "api_key", "values": {"apiKey": "x"}},
            },
        )


def test_historical_legacy_credential_does_not_take_over_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.config.get_config",
        lambda: type("EnvironmentConfig", (), {"tushare_token": "environment-token"})(),
    )
    store = _store(monkeypatch, tmp_path)
    result = store.save_provider_config("tushare", {"requestId": "legacy", "credential": "old"})
    assert result["credentialSource"] == "environment"
    assert result["credentialFieldsConfigured"] == {"token": False}
    assert result["credentialMethod"] is None


def test_longbridge_environment_oauth_does_not_fill_legacy_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "data_provider.longbridge_fetcher._longbridge_credentials",
        lambda _config: {
            "app_key": None,
            "app_secret": None,
            "access_token": None,
            "oauth_client_id": "oauth-client",
        },
    )
    monkeypatch.setattr(
        "data_provider.longbridge_fetcher.LongbridgeFetcher.has_configured_credentials",
        staticmethod(lambda _config=None: True),
    )
    store = _store(monkeypatch, tmp_path)
    longbridge = next(item for item in store.provider_registry() if item["providerId"] == "longbridge")
    assert longbridge["credentialSource"] == "environment"
    assert longbridge["credentialFieldsConfigured"] == {
        "accessToken": False,
        "appKey": False,
        "appSecret": False,
    }


def test_remove_provider_advances_both_versions_atomically(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_provider_config(
        "tushare",
        {"requestId": "save", "credentials": {"method": "token", "values": {"token": "secret"}}},
    )
    store.remove_provider("tushare", {"requestId": "remove"})
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT config_version, credential_version, credential_ciphertext "
            "FROM thesis_ledger_provider_config WHERE provider_id = 'tushare'"
        ).fetchone() == (2, 2, None)

    fresh = _store(monkeypatch, tmp_path / "fresh")
    fresh.remove_provider("finnhub", {"requestId": "remove-empty"})
    with sqlite3.connect(fresh.database_path) as connection:
        assert connection.execute(
            "SELECT config_version, credential_version FROM thesis_ledger_provider_config "
            "WHERE provider_id = 'finnhub'"
        ).fetchone() == (1, 1)


def test_explicit_clear_advances_credential_version_without_stored_value(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    for expected in (1, 2):
        store.save_provider_config("longbridge", {"clearCredentials": True})
        with sqlite3.connect(store.database_path) as connection:
            assert connection.execute(
                "SELECT credential_version FROM thesis_ledger_provider_config "
                "WHERE provider_id = 'longbridge'"
            ).fetchone() == (expected,)
