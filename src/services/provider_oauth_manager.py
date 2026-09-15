"""Longbridge 授权任务执行面；公开状态始终来自 SQLite。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from src.services.provider_oauth_store import OAuthStateError, PENDING, ProviderOAuthStore


def sdk_oauth_builder():
    try:
        from longbridge.openapi import OAuthBuilder
    except ImportError:
        raise OAuthStateError("OAUTH_SDK_UNAVAILABLE") from None
    if getattr(OAuthBuilder, "THESIS_LEDGER_STORAGE_VERSION", None) != 1:
        raise OAuthStateError("OAUTH_SDK_UNAVAILABLE")
    return OAuthBuilder


def oauth_error_code(error: BaseException) -> str:
    if isinstance(error, OAuthStateError):
        return error.code
    text = str(error).lower()
    if "address already in use" in text or "eaddrinuse" in text:
        return "OAUTH_PORT_IN_USE"
    if "access_denied" in text:
        return "OAUTH_DENIED"
    if "timeout" in text or "timed out" in text:
        return "OAUTH_EXPIRED"
    if "connect" in text or "network" in text:
        return "OAUTH_NETWORK_ERROR"
    return "OAUTH_FAILED"


class ProviderOAuthManager:
    def __init__(
        self,
        repository: ProviderOAuthStore,
        *,
        callback_host: str = "127.0.0.1",
        builder_factory: Callable[[], Any] = sdk_oauth_builder,
    ):
        if callback_host not in {"127.0.0.1", "0.0.0.0"}:
            raise OAuthStateError("OAUTH_LISTENER_INVALID")
        self.repository = repository
        self.callback_host = callback_host
        self.builder_factory = builder_factory
        self.tasks: dict[str, asyncio.Task] = {}

    def recover(self) -> int:
        return self.repository.recover_pending()

    async def create(self, client_id: str) -> dict:
        builder = self.builder_factory()
        session, created = self.repository.create(client_id)
        if created:
            session_id = session["sessionId"]
            task = asyncio.create_task(self._authorize(session, client_id, builder))
            self.tasks[session_id] = task
            task.add_done_callback(lambda finished: self._finished(session_id, finished))
        return session

    def _finished(self, session_id: str, task: asyncio.Task) -> None:
        self.tasks.pop(session_id, None)
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger(__name__).warning("OAuth 会话执行未正常收敛，需检查持久化服务")

    def get(self, session_id: str) -> dict:
        session = self.repository.get(session_id)
        if session["status"] not in PENDING:
            task = self.tasks.get(session_id)
            if task and not task.done():
                task.cancel()
        return session

    def current(self) -> dict | None:
        return self.repository.current()

    async def cancel(self, session_id: str) -> dict:
        session = self.repository.finish(session_id, "cancelled", "OAUTH_CANCELLED")
        task = self.tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return session

    async def shutdown(self) -> None:
        tasks = list(self.tasks.items())
        for session_id, task in tasks:
            try:
                self.repository.finish(session_id, "failed", "OAUTH_RESTARTED")
            except Exception:
                logging.getLogger(__name__).warning("OAuth 关闭状态保存失败，将在启动时恢复")
            finally:
                task.cancel()
        await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)

    async def _authorize(self, session: dict, client_id: str, builder_class: Any) -> None:
        session_id = session["sessionId"]
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        callback_error: list[str] = []

        def open_url(url: str) -> None:
            try:
                if not self.repository.set_authorization_url(session_id, url):
                    raise OAuthStateError("OAUTH_SESSION_INACTIVE")
            except Exception as error:
                callback_error.append(oauth_error_code(error))
                if task:
                    loop.call_soon_threadsafe(task.cancel)

        def save_token(token_json: str) -> None:
            try:
                self.repository.save_authorized_token(session_id, token_json)
            except Exception as error:
                callback_error.append(oauth_error_code(error))
                raise

        try:
            builder = builder_class(
                client_id,
                callback_port=60355,
                on_token_save=save_token,
                callback_host=self.callback_host,
                allow_authorization=True,
                authorization_timeout_secs=600,
            )
            expires = datetime.fromisoformat(session["expiresAt"]).timestamp()
            remaining = max(0.001, expires - self.repository.clock())
            await asyncio.wait_for(builder.build_async(open_url), timeout=remaining)
            if self.repository.get(session_id)["status"] in PENDING:
                self.repository.finish(session_id, "failed", "OAUTH_TOKEN_NOT_SAVED")
        except asyncio.CancelledError:
            if callback_error:
                self.repository.finish(session_id, "failed", callback_error[-1])
            else:
                self.repository.finish(session_id, "cancelled", "OAUTH_CANCELLED")
        except (asyncio.TimeoutError, TimeoutError):
            self.repository.finish(session_id, "expired", "OAUTH_EXPIRED")
        except Exception as error:
            code = callback_error[-1] if callback_error else oauth_error_code(error)
            status = "expired" if code == "OAUTH_EXPIRED" else "failed"
            self.repository.finish(session_id, status, code)
