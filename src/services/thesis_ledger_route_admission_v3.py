"""V3 RouteTarget admission evidence and request-scope predicates.

This module has no API or provider dependencies. It provides stable identity
and scope checks for the DSA-owned ControlStore and later Data consumers.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")


def canonical_route_key(key: dict[str, Any]) -> dict[str, str]:
    """Return the canonical exact V3 RouteKey shape or raise ``ValueError``."""
    if not isinstance(key, dict):
        raise ValueError("RouteKey must be an object")

    kind = key.get("kind")
    if kind == "bar":
        expected = {
            "kind", "market", "assetType", "capability", "timeframe", "adjustment"
        }
        if set(key) != expected:
            raise ValueError("bar RouteKey fields do not match V3")
        values = (
            key.get("market"), key.get("assetType"), key.get("capability"),
            key.get("timeframe"), key.get("adjustment"),
        )
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("bar RouteKey values must be non-empty strings")
        return {
            "kind": "bar",
            "market": key["market"].strip().upper(),
            "assetType": key["assetType"].strip().upper(),
            "capability": key["capability"].strip().upper(),
            "timeframe": key["timeframe"].strip().lower(),
            "adjustment": key["adjustment"].strip().lower(),
        }

    if kind == "data":
        expected = {"kind", "market", "assetType", "capability"}
        if set(key) != expected:
            raise ValueError("data RouteKey fields do not match V3")
        values = (key.get("market"), key.get("assetType"), key.get("capability"))
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("data RouteKey values must be non-empty strings")
        return {
            "kind": "data",
            "market": key["market"].strip().upper(),
            "assetType": key["assetType"].strip().upper(),
            "capability": key["capability"].strip(),
        }

    raise ValueError("RouteKey kind must be bar or data")


def canonical_route_key_json(key: dict[str, Any]) -> str:
    """Encode a RouteKey for exact, order-independent SQLite identity."""
    return json.dumps(
        canonical_route_key(key),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def normalize_scope_symbols(symbols: Any) -> list[str]:
    """Validate and canonicalize the explicit symbol set in an admission."""
    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise ValueError("scope_symbols must contain at least one symbol")
    normalized: set[str] = set()
    for value in symbols:
        if not isinstance(value, str):
            raise ValueError("scope symbols must be strings")
        symbol = value.strip().upper()
        if not symbol or not _SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("scope contains an invalid symbol")
        normalized.add(symbol)
    return sorted(normalized)


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if parsed.isoformat() != value:
        return None
    return parsed


def route_admission_scope_applies(
    admission: dict[str, Any],
    *,
    symbol: str,
    date_from: str,
    date_to: str,
) -> bool:
    """Return true only when the full request window fits an admitted scope."""
    if not isinstance(admission, dict) or not isinstance(symbol, str):
        return False
    try:
        allowed_symbols = normalize_scope_symbols(admission.get("scopeSymbols"))
    except ValueError:
        return False
    request_symbol = symbol.strip().upper()
    scope_start = _iso_date(admission.get("scopeDateFrom"))
    scope_end = _iso_date(admission.get("scopeDateTo"))
    request_start = _iso_date(date_from)
    request_end = _iso_date(date_to)
    if not all((scope_start, scope_end, request_start, request_end)):
        return False
    if scope_start > scope_end or request_start > request_end:
        return False
    return (
        request_symbol in allowed_symbols
        and scope_start <= request_start
        and request_end <= scope_end
    )
