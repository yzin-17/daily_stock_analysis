"""Longbridge 页面授权的持久状态与凭证条件提交。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import time
from typing import Callable
import uuid

from src.services.provider_credentials import CredentialPatch, encode_credential_plaintext

from src.services.provider_oauth_contract import (
    OAuthStateError,
    PENDING,
    PROVIDER,
    validate_oauth_token,
)


class ProviderOAuthStore:
    def __init__(
        self,
        database_path: str,
        *,
        encrypt: Callable[[str], tuple[str, str]],
        clock: Callable[[], float] = time.time,
    ):
        self.database_path = str(database_path)
        self.encrypt = encrypt
        self.clock = clock
        with self._transaction() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS thesis_ledger_provider_oauth_session (
                    session_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('starting','authorizing','succeeded','failed','cancelled','expired')),
                    credential_version INTEGER NOT NULL,
                    authorization_url TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    error_code TEXT
                )
            """)
            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS thesis_ledger_oauth_pending_provider
                ON thesis_ledger_provider_oauth_session(provider_id)
                WHERE status IN ('starting','authorizing')
            """)

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        url = row["authorization_url"] if row["status"] in PENDING else None
        return {
            "sessionId": row["session_id"],
            "providerId": row["provider_id"],
            "status": row["status"],
            "authorizationUrl": url,
            "expiresAt": datetime.fromtimestamp(row["expires_at"], timezone.utc).isoformat(),
            "errorCode": row["error_code"],
        }

    def _expire(self, connection):
        connection.execute(
            """
            UPDATE thesis_ledger_provider_oauth_session
            SET status='expired', error_code='OAUTH_EXPIRED', authorization_url=NULL
            WHERE status IN ('starting','authorizing') AND expires_at <= ?
        """,
            (int(self.clock()),),
        )

    def recover_pending(self) -> int:
        """仅在进程启动时调用；持久记录不能替代已经丢失的监听进程。"""
        with self._transaction() as connection:
            return connection.execute("""
                UPDATE thesis_ledger_provider_oauth_session
                SET status='failed', error_code='OAUTH_RESTARTED', authorization_url=NULL
                WHERE status IN ('starting','authorizing')
            """).rowcount

    def create(self, client_id: str) -> tuple[dict, bool]:
        if (
            not isinstance(client_id, str)
            or not client_id.strip()
            or len(client_id) > 200
            or any(ord(c) < 33 for c in client_id)
        ):
            raise OAuthStateError("OAUTH_CLIENT_ID_INVALID")
        # 在打开授权前确认当前密钥可用；不持久化探测密文。
        self.encrypt("oauth-storage-readiness")
        with self._transaction() as connection:
            self._expire(connection)
            existing = connection.execute(
                """
                SELECT * FROM thesis_ledger_provider_oauth_session
                WHERE provider_id=? AND status IN ('starting','authorizing')
            """,
                (PROVIDER,),
            ).fetchone()
            if existing:
                if existing["client_id"] != client_id:
                    raise OAuthStateError("OAUTH_ALREADY_PENDING")
                return self._public(existing), False
            tombstone = connection.execute(
                "SELECT 1 FROM thesis_ledger_provider_tombstone WHERE provider_id=?", (PROVIDER,)
            ).fetchone()
            if tombstone:
                raise OAuthStateError("OAUTH_PROVIDER_REMOVED")
            config = connection.execute(
                "SELECT credential_version FROM thesis_ledger_provider_config WHERE provider_id=?",
                (PROVIDER,),
            ).fetchone()
            version = int(config[0]) if config else 0
            now = int(self.clock())
            session_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO thesis_ledger_provider_oauth_session
                (session_id, provider_id, client_id, status, credential_version, created_at, expires_at)
                VALUES (?, ?, ?, 'starting', ?, ?, ?)
            """,
                (session_id, PROVIDER, client_id, version, now, now + 600),
            )
            row = connection.execute(
                "SELECT * FROM thesis_ledger_provider_oauth_session WHERE session_id=?",
                (session_id,),
            ).fetchone()
            return self._public(row), True

    def get(self, session_id: str) -> dict:
        with self._transaction() as connection:
            self._expire(connection)
            row = connection.execute(
                "SELECT * FROM thesis_ledger_provider_oauth_session WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise OAuthStateError("OAUTH_SESSION_NOT_FOUND")
            return self._public(row)

    def current(self) -> dict | None:
        with self._transaction() as connection:
            self._expire(connection)
            row = connection.execute(
                """
                SELECT * FROM thesis_ledger_provider_oauth_session
                WHERE provider_id=? AND status IN ('starting','authorizing')
            """,
                (PROVIDER,),
            ).fetchone()
            return self._public(row) if row else None

    def set_authorization_url(self, session_id: str, url: str) -> bool:
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname
                in {
                    "openapi.longbridge.com",
                    "openapi.longbridge.cn",
                    "openapi-global.longbridge.xyz",
                }
                and parsed.path == "/oauth2/authorize"
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise OAuthStateError("OAUTH_AUTHORIZATION_URL_INVALID")
        with self._transaction() as connection:
            self._expire(connection)
            result = connection.execute(
                """
                UPDATE thesis_ledger_provider_oauth_session SET status='authorizing', authorization_url=?
                WHERE session_id=? AND status='starting'
            """,
                (url, session_id),
            )
            return result.rowcount == 1

    def finish(self, session_id: str, status: str, error_code: str | None = None) -> dict:
        if status not in {"failed", "cancelled", "expired"}:
            raise ValueError("会话终态必须使用明确失败或取消状态")
        with self._transaction() as connection:
            self._expire(connection)
            connection.execute(
                """
                UPDATE thesis_ledger_provider_oauth_session SET status=?, error_code=?, authorization_url=NULL
                WHERE session_id=? AND status IN ('starting','authorizing')
            """,
                (status, error_code, session_id),
            )
            row = connection.execute(
                "SELECT * FROM thesis_ledger_provider_oauth_session WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise OAuthStateError("OAUTH_SESSION_NOT_FOUND")
            return self._public(row)

    def save_authorized_token(self, session_id: str, token_json: str) -> int:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM thesis_ledger_provider_oauth_session WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] not in PENDING
                or row["expires_at"] <= int(self.clock())
            ):
                raise OAuthStateError("OAUTH_SESSION_INACTIVE")
            version = self._write_token(
                connection, row["client_id"], token_json, row["credential_version"]
            )
            connection.execute(
                """
                UPDATE thesis_ledger_provider_oauth_session SET status='succeeded', authorization_url=NULL, error_code=NULL
                WHERE session_id=?
            """,
                (session_id,),
            )
            return version

    def save_refreshed_token(self, client_id: str, token_json: str, expected_version: int) -> int:
        with self._transaction() as connection:
            return self._write_token(connection, client_id, token_json, expected_version)

    def _write_token(
        self, connection, client_id: str, token_json: str, expected_version: int
    ) -> int:
        validate_oauth_token(client_id, token_json)
        existing = connection.execute(
            "SELECT * FROM thesis_ledger_provider_config WHERE provider_id=?", (PROVIDER,)
        ).fetchone()
        current_version = int(existing["credential_version"]) if existing else 0
        removed = connection.execute(
            "SELECT 1 FROM thesis_ledger_provider_tombstone WHERE provider_id=?", (PROVIDER,)
        ).fetchone()
        if current_version != expected_version or removed:
            raise OAuthStateError("OAUTH_CONFIG_CHANGED")
        key_version, ciphertext = self.encrypt(
            encode_credential_plaintext(
                CredentialPatch("oauth", {"clientId": client_id, "tokenJson": token_json})
            )
        )
        updated_at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
        if existing:
            connection.execute(
                """
                UPDATE thesis_ledger_provider_config SET credential_ciphertext=?, secret_key_version=?,
                credential_version=credential_version+1, config_version=config_version+1, updated_at=?
                WHERE provider_id=? AND credential_version=?
            """,
                (ciphertext, key_version, updated_at, PROVIDER, expected_version),
            )
        else:
            connection.execute(
                """
                INSERT INTO thesis_ledger_provider_config
                (provider_id, enabled, settings_json, credential_ciphertext, secret_key_version, config_version, credential_version, updated_at)
                VALUES (?, 1, '{}', ?, ?, 1, 1, ?)
            """,
                (PROVIDER, ciphertext, key_version, updated_at),
            )
        return current_version + 1
