# -*- coding: utf-8 -*-
"""数据请求的内存诊断：记录 Provider 尝试、耗时和脱敏事件。"""
from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)


_CURRENT_CONTEXT: ContextVar[Optional["RunDiagnosticContext"]] = ContextVar(
    "run_diagnostic_context",
    default=None,
)


_SECRET_REDACTIONS = (
    (
        re.compile(r"(?i)\b(authorization)\s*[:=]\s*(?:(?:Bearer|Basic|Token)\s+)?[^\s,&;]+"),
        lambda match: f"{match.group(1)}=<redacted>",
    ),
    (
        re.compile(r"(https?://)([^/\s:@]+):([^@\s/]+)@"),
        r"\1<redacted>:<redacted>@",
    ),
    (
        re.compile(r"https?://[^\s]+?(?:token|key|secret|webhook)[^\s]*", re.IGNORECASE),
        "<redacted-url>",
    ),
    (
        re.compile(
            r"(?i)([\"']?)"
            r"([A-Z0-9_]*?(?:api[_-]?key|access[_-]?token|token|secret|password|passwd|cookie))"
            r"\1\s*:\s*([\"'])([^\"']+)\3"
        ),
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(1)}: {match.group(3)}<redacted>{match.group(3)}",
    ),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*?(?:api[_-]?key|access[_-]?token|token|secret|password|passwd|cookie))"
            r"\s*=\s*([^\s,&;]+)"
        ),
        lambda match: f"{match.group(1)}=<redacted>",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|cookie)"
            r"\s*:\s*([^\s,&;]+)"
        ),
        lambda match: f"{match.group(1)}=<redacted>",
    ),
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
        "Bearer <redacted>",
    ),
)


_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|(?:^|[_-])(?:auth|refresh|session|bearer)?[_-]?token$|secret|password|passwd|cookie|"
    r"webhook|sendkey|prompt|raw[_-]?prompt|raw[_-]?response|headers?|proxy)"
)


_WEBHOOK_URL_RE = re.compile(r"https?://[^\s]+?(?:webhook|token|key|secret|sendkey)[^\s]*", re.IGNORECASE)


_LOCAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w:/.-])(?:/(?:home|Users|root|var|tmp|opt|etc)/[^\s,;]+|[A-Za-z]:\\[^\s,;]+)"
)


_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|cookie|webhook|sendkey|"
    r"prompt|raw[_-]?prompt|raw[_-]?response)\s*[:=]\s*([^\s,&;]+)"
)


def build_trace_id() -> str:
    """Build a compact trace id suitable for logs, API responses, and SSE."""
    return uuid.uuid4().hex


def sanitize_diagnostic_text(value: Any, *, max_length: int = 300) -> Optional[str]:
    """Return a short diagnostic string with sensitive details redacted."""
    if value is None:
        return None

    text = " ".join(str(value).split())
    if not text:
        return None

    for pattern, replacement in _SECRET_REDACTIONS:
        text = pattern.sub(replacement, text)
    text = _WEBHOOK_URL_RE.sub("<redacted-url>", text)
    text = _LOCAL_ABSOLUTE_PATH_RE.sub("<redacted-path>", text)
    text = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)

    if len(text) > max_length:
        return f"{text[:max_length].rstrip()}..."
    return text


def safe_diagnostic_key(value: Any) -> str:
    """Normalize a diagnostic object key after applying text redaction."""
    text = sanitize_diagnostic_text(value, max_length=80) or ""
    return re.sub(r"[^A-Za-z0-9_]+", "_", text.strip().lower()).strip("_")[:80]


def sanitize_diagnostic_metadata(value: Any, *, depth: int = 0) -> Any:
    """Recursively redact diagnostic metadata before it reaches API/SSE payloads."""
    if depth > 3:
        return "<truncated>"
    if isinstance(value, Mapping):
        sanitized: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                sanitized["truncated"] = True
                break
            safe_key = safe_diagnostic_key(key)
            if not safe_key:
                continue
            if _SENSITIVE_KEY_RE.search(str(key)):
                sanitized[safe_key] = "<redacted>"
                continue
            safe_value = sanitize_diagnostic_metadata(item, depth=depth + 1)
            if safe_value not in (None, "", [], {}):
                sanitized[safe_key] = safe_value
        return sanitized
    if isinstance(value, list):
        items = [sanitize_diagnostic_metadata(item, depth=depth + 1) for item in value[:8]]
        return [item for item in items if item not in (None, "", [], {})]
    if isinstance(value, tuple):
        return sanitize_diagnostic_metadata(list(value), depth=depth)
    if isinstance(value, (int, float, bool)):
        return value
    return sanitize_diagnostic_text(value, max_length=160)


@dataclass
class ProviderRun:
    """One provider attempt in a trace."""

    trace_id: str
    data_type: str
    provider: str
    operation: str
    success: bool
    latency_ms: Optional[int] = None
    error_type: Optional[str] = None
    error_message_sanitized: Optional[str] = None
    fallback_from: Optional[str] = None
    fallback_to: Optional[str] = None
    cache_hit: Optional[bool] = None
    stale_seconds: Optional[int] = None
    record_count: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "trace_id": self.trace_id,
            "data_type": self.data_type,
            "provider": self.provider,
            "operation": self.operation,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "error_message_sanitized": self.error_message_sanitized,
            "fallback_from": self.fallback_from,
            "fallback_to": self.fallback_to,
            "cache_hit": self.cache_hit,
            "stale_seconds": self.stale_seconds,
            "record_count": self.record_count,
            "created_at": self.created_at,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass
class RunDiagnosticContext:
    """一次数据请求的 Provider 诊断上下文。"""

    trace_id: str
    task_id: Optional[str] = None
    query_id: Optional[str] = None
    stock_code: Optional[str] = None
    trigger_source: Optional[str] = None
    scope: Optional[str] = None
    provider_runs: List[ProviderRun] = field(default_factory=list)
    event_sink: Optional[Callable[[Dict[str, Any]], None]] = None
    flow_event_index: int = 0
    provider_attempt_index_by_type: Dict[str, int] = field(default_factory=dict)
    provider_pending_attempt_index_by_key: Dict[str, List[int]] = field(default_factory=dict)

    def record_provider_run(self, provider_run: ProviderRun) -> None:
        self.provider_runs.append(provider_run)
        data_type_key = _safe_event_key(provider_run.data_type) or "provider"
        pending_key = _provider_pending_key(
            provider_run.data_type,
            provider_run.provider,
            provider_run.operation,
        )
        pending_indexes = self.provider_pending_attempt_index_by_key.get(pending_key) or []
        if pending_indexes:
            attempt_index = pending_indexes.pop(0)
            if pending_indexes:
                self.provider_pending_attempt_index_by_key[pending_key] = pending_indexes
            else:
                self.provider_pending_attempt_index_by_key.pop(pending_key, None)
        else:
            attempt_index = self.provider_attempt_index_by_type.get(data_type_key, 0) + 1
            self.provider_attempt_index_by_type[data_type_key] = attempt_index
        self._emit_flow_event(_provider_flow_event(self, provider_run, attempt_index))

    def record_provider_run_started(
        self,
        *,
        data_type: str,
        provider: str,
        operation: str,
    ) -> None:
        data_type_key = _safe_event_key(data_type) or "provider"
        attempt_index = self.provider_attempt_index_by_type.get(data_type_key, 0) + 1
        self.provider_attempt_index_by_type[data_type_key] = attempt_index
        pending_key = _provider_pending_key(data_type, provider, operation)
        pending_indexes = self.provider_pending_attempt_index_by_key.get(pending_key) or []
        pending_indexes.append(attempt_index)
        self.provider_pending_attempt_index_by_key[pending_key] = pending_indexes
        self._emit_flow_event(
            _provider_started_flow_event(
                self,
                data_type=data_type,
                provider=provider,
                operation=operation,
                index=attempt_index,
            )
        )

    def _emit_flow_event(self, event: Dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.flow_event_index += 1
            event_payload = sanitize_diagnostic_metadata(event)
            event_payload = dict(event_payload) if isinstance(event_payload, Mapping) else {}
            event_payload["id"] = event_payload.get("id") or f"flow_{self.flow_event_index:04d}"
            self.event_sink(event_payload)
        except Exception as exc:  # pragma: no cover - defensive fail-open guard
            logger.warning("run-flow event sink failed: %s", exc)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "query_id": self.query_id,
            "stock_code": self.stock_code,
            "trigger_source": self.trigger_source,
            "scope": self.scope,
            "provider_runs": [run.to_dict() for run in self.provider_runs],
        }


def get_current_diagnostic_context() -> Optional[RunDiagnosticContext]:
    return _CURRENT_CONTEXT.get()


def activate_run_diagnostic_context(
    *,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    query_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    trigger_source: Optional[str] = None,
    scope: Optional[str] = None,
    event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Token:
    """Activate a diagnostic context and return its reset token."""
    context = RunDiagnosticContext(
        trace_id=trace_id or query_id or task_id or build_trace_id(),
        task_id=task_id,
        query_id=query_id,
        stock_code=stock_code,
        trigger_source=trigger_source,
        scope=scope,
        event_sink=event_sink,
    )
    return _CURRENT_CONTEXT.set(context)


def reset_run_diagnostic_context(token: Optional[Token]) -> None:
    if token is None:
        return
    try:
        _CURRENT_CONTEXT.reset(token)
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("run diagnostic context reset failed: %s", exc)


def current_diagnostic_snapshot() -> Optional[Dict[str, Any]]:
    context = get_current_diagnostic_context()
    if context is None:
        return None
    try:
        return context.snapshot()
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("run diagnostic snapshot failed: %s", exc)
        return None


_DATA_TYPE_LABELS = {
    "realtime_quote": "实时行情",
    "daily_data": "日线K线",
    "daily_bars": "日线K线",
    "technical": "技术指标",
    "news": "新闻舆情",
    "news_search": "新闻舆情",
    "fundamental": "基本面",
    "fundamentals": "基本面",
    "belong_boards": "所属板块",
    "chip": "筹码结构",
}


def _safe_event_key(value: Any) -> str:
    return safe_diagnostic_key(value)


def _clean_metadata(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        str(key): item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }


def _provider_pending_key(data_type: Any, provider: Any, operation: Any) -> str:
    return "|".join(
        (
            _safe_event_key(data_type) or "provider",
            _safe_event_key(provider) or "unknown",
            _safe_event_key(operation) or "operation",
        )
    )


def _flow_status_for_success(success: bool, *, fallback: bool = False, skipped: bool = False) -> str:
    if skipped:
        return "skipped"
    if success:
        return "fallback" if fallback else "success"
    return "failed"


def _started_at_from_end_and_duration(end: Any, duration_ms: Optional[int]) -> Optional[str]:
    if duration_ms is None or duration_ms < 0:
        return None
    if isinstance(end, datetime):
        parsed = end
    elif isinstance(end, str) and "T" in end:
        normalized = end[:-1] + "+00:00" if end.endswith("Z") else end
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None
    return (parsed - timedelta(milliseconds=duration_ms)).isoformat()


def _provider_started_flow_event(
    context: RunDiagnosticContext,
    *,
    data_type: str,
    provider: str,
    operation: str,
    index: int,
) -> Dict[str, Any]:
    data_type_key = _safe_event_key(data_type) or "provider"
    provider_key = _safe_event_key(provider) or "unknown"
    label = _DATA_TYPE_LABELS.get(data_type_key, data_type_key)
    node_id = f"provider_{data_type_key}_{provider_key}_{index}"
    timestamp = datetime.now().isoformat()
    message = f"{label} {provider} 调用中"
    return {
        "timestamp": timestamp,
        "severity": "info",
        "type": "provider_run_started",
        "node_id": node_id,
        "title": f"{label}开始",
        "message": sanitize_diagnostic_text(message, max_length=220),
        "metadata": _clean_metadata(
            {
                "trace_id": context.trace_id,
                "provider": provider,
                "data_type": data_type,
                "operation": operation,
                "node": {
                    "id": node_id,
                    "lane": "data_source",
                    "kind": "data_source",
                    "label": f"{label} · {provider}",
                    "status": "running",
                    "provider": provider,
                    "started_at": timestamp,
                    "attempts": 1,
                    "message": message,
                },
            }
        ),
    }


def _provider_flow_event(
    context: RunDiagnosticContext,
    run: ProviderRun,
    index: int,
) -> Dict[str, Any]:
    data_type = _safe_event_key(run.data_type) or "provider"
    provider_key = _safe_event_key(run.provider) or "unknown"
    label = _DATA_TYPE_LABELS.get(data_type, data_type)
    fallback = bool(run.fallback_from or run.fallback_to)
    status = _flow_status_for_success(run.success, fallback=fallback)
    node_id = f"provider_{data_type}_{provider_key}_{index}"
    started_at = _started_at_from_end_and_duration(run.created_at, run.latency_ms)
    message = (
        f"{label} {run.provider} 成功"
        if run.success
        else f"{label} {run.provider} 失败：{run.error_message_sanitized or run.error_type or '未知错误'}"
    )
    return {
        "timestamp": run.created_at,
        "severity": "success" if run.success else "warning",
        "type": "provider_run",
        "node_id": node_id,
        "title": f"{label}{'成功' if run.success else '失败'}",
        "message": sanitize_diagnostic_text(message, max_length=220),
        "metadata": _clean_metadata(
            {
                "trace_id": context.trace_id,
                "provider": run.provider,
                "data_type": run.data_type,
                "operation": run.operation,
                "duration_ms": run.latency_ms,
                "record_count": run.record_count,
                "fallback_from": run.fallback_from,
                "fallback_to": run.fallback_to,
                "error_type": run.error_type,
                "node": {
                    "id": node_id,
                    "lane": "data_source",
                    "kind": "data_source",
                    "label": f"{label} · {run.provider}",
                    "status": status,
                    "provider": run.provider,
                    "started_at": started_at,
                    "ended_at": run.created_at,
                    "duration_ms": run.latency_ms,
                    "record_count": run.record_count,
                    "message": message,
                },
            }
        ),
    }


def record_provider_run(
    *,
    data_type: str,
    provider: str,
    operation: str,
    success: bool,
    latency_ms: Optional[int] = None,
    error_type: Optional[str] = None,
    error_message: Optional[Any] = None,
    fallback_from: Optional[str] = None,
    fallback_to: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    stale_seconds: Optional[int] = None,
    record_count: Optional[int] = None,
) -> None:
    """Append a provider attempt to the active context without affecting callers."""
    context = get_current_diagnostic_context()
    if context is None:
        return

    try:
        context.record_provider_run(
            ProviderRun(
                trace_id=context.trace_id,
                data_type=data_type,
                provider=provider,
                operation=operation,
                success=success,
                latency_ms=latency_ms,
                error_type=error_type,
                error_message_sanitized=sanitize_diagnostic_text(error_message),
                fallback_from=fallback_from,
                fallback_to=fallback_to,
                cache_hit=cache_hit,
                stale_seconds=stale_seconds,
                record_count=record_count,
            )
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("provider diagnostic record failed: %s", exc)


def record_provider_run_started(
    *,
    data_type: str,
    provider: str,
    operation: str,
) -> None:
    """Emit a live provider-start event without changing persisted diagnostics."""
    context = get_current_diagnostic_context()
    if context is None:
        return

    try:
        context.record_provider_run_started(
            data_type=data_type,
            provider=provider,
            operation=operation,
        )
    except Exception as exc:  # pragma: no cover - defensive fail-open guard
        logger.warning("provider started diagnostic record failed: %s", exc)
