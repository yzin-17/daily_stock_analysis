"""精确口径的图表交互读取；不产生回测完整窗口证明。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from api.thesis_ledger_source_basis import native_field_units, native_source_basis

from api.thesis_ledger import (
    MARKET_DATA_V3_METHOD_VERSION,
    MARKET_DATA_V3_SUPPORTED_PROVIDER_IDS,
    _bar_series_fingerprint,
    _bar_series_point,
    _iso_timestamp,
    _market_data_v3_error,
    _market_data_v3_request,
    _now_iso,
    _number,
    require_contract_token,
)

router = APIRouter(prefix="/thesis-ledger", tags=["ThesisLedger Market Contract V3"])


def chart_request(payload: Any, request_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("purpose") != "interactive-chart":
        _market_data_v3_error("invalid_response", 422, request_id)
    base = {key: value for key, value in payload.items() if key != "purpose"}
    parsed = _market_data_v3_request(base, request_id)
    if "routeTarget" not in parsed:
        _market_data_v3_error("invalid_response", 422, parsed["requestId"])
    # Current native adapters and completion clock only cover CN daily bars.
    if parsed["routeKey"]["timeframe"] != "1d" or parsed["routeKey"]["market"] != "CN":
        _market_data_v3_error("unsupported_price_basis", 422, parsed["requestId"])
    return parsed


def chart_response(request: Mapping[str, Any], execution: Any) -> dict[str, Any]:
    request_id = request["requestId"]
    target = request["routeTarget"]
    provenance = {
        "providerId": getattr(execution, "provider", None),
        "upstreamSource": getattr(execution, "upstream_source", None),
        "routeIndex": getattr(execution, "route_index", None),
        "effectivePolicyRevision": getattr(execution, "effective_revision", None),
    }
    revision = provenance["effectivePolicyRevision"]
    if (
        any(provenance[key] != value for key, value in target.items())
        or provenance["providerId"] not in MARKET_DATA_V3_SUPPORTED_PROVIDER_IDS
        or isinstance(provenance["routeIndex"], bool)
        or not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0
    ):
        _market_data_v3_error("invalid_response", 502, request_id)
    frame = getattr(execution, "value", None)
    fetched_at = _now_iso()
    points = []
    dates: set[str] = set()
    try:
        for _, row in frame.iterrows():
            timestamp = _iso_timestamp(row.get("date"))
            trading_date = datetime.fromisoformat(timestamp).astimezone(
                ZoneInfo("Asia/Shanghai")
            ).date().isoformat()
            if trading_date in dates or not request["start"] <= trading_date <= request["end"]:
                raise ValueError("invalid date")
            dates.add(trading_date)
            prices = {key: _number(row.get(key), key) for key in (
                "open", "high", "low", "close", "volume", "amount"
            )}
            if (
                any(value < 0 for value in prices.values())
                or prices["high"] < max(prices["open"], prices["close"], prices["low"])
                or prices["low"] > min(prices["open"], prices["close"], prices["high"])
            ):
                raise ValueError("invalid prices")
            points.append(_bar_series_point(
                {"timestamp": timestamp, **prices},
                fetched_at=fetched_at, observed_availability=True,
            ))
        attrs = frame.attrs
        units = native_field_units(attrs, request["routeKey"],
                                   provenance["providerId"], provenance["upstreamSource"], request)
        has_more_before = attrs.get("has_more_before", False)
        if not isinstance(has_more_before, bool) or len(points) > 100_000:
            raise ValueError("invalid coverage")
    except Exception:
        _market_data_v3_error("invalid_response", 502, request_id)
    points.sort(key=lambda point: point["timestamp"])
    key = request["routeKey"]
    fingerprint = _bar_series_fingerprint(points, {
        "symbol": request["symbol"], "assetType": key["assetType"],
        "timeframe": key["timeframe"], "adjustment": key["adjustment"],
        **units,
    })
    complete_dates = [
        datetime.fromisoformat(point["timestamp"]).astimezone(ZoneInfo("Asia/Shanghai"))
        .date().isoformat()
        for point in points if point["completionStatus"] == "complete"
    ]
    return {
        "contractVersion": 3, "purpose": "interactive-chart",
        "requestId": request_id, "symbol": request["symbol"], "routeKey": dict(key),
        "bars": points,
        "coverage": {
            "requestedStart": request["start"], "requestedEnd": request["end"],
            "actualStart": points[0]["timestamp"] if points else None,
            "actualEnd": points[-1]["timestamp"] if points else None,
            "hasMoreBefore": has_more_before,
            "latestCompleteTradingDate": max(complete_dates) if complete_dates else None,
        },
        "sourcePriceBasis": native_source_basis(
            key["adjustment"], MARKET_DATA_V3_METHOD_VERSION, fingerprint, fetched_at, units,
        ),
        "provenance": provenance, "inputFingerprint": fingerprint,
    }


@router.post("/market/chart-bars", dependencies=[Depends(require_contract_token)], response_model=None)
def market_chart_bars(request: Request, payload: Any = Body(...)) -> dict[str, Any] | JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    try:
        parsed = chart_request(payload, request_id)
        from src.services.thesis_ledger_provider_runtime import (
            ThesisLedgerDataRequest, get_thesis_ledger_runtime,
        )
        key = parsed["routeKey"]
        try:
            execution = get_thesis_ledger_runtime().execute_market_bars_v3(
                ThesisLedgerDataRequest(
                    capability=key["capability"], symbol=parsed["symbol"],
                    timeframe=key["timeframe"], start=parsed["start"], end=parsed["end"],
                    instrument_type=key["assetType"], adjustment=key["adjustment"],
                    request_id=parsed["requestId"],
                ), key, route_target=parsed["routeTarget"],
            )
        except Exception as error:
            code = str(getattr(error, "code", ""))
            mapped, status = {
                "unsupported_adjustment": ("unsupported_price_basis", 422),
                "not_covered": ("insufficient_coverage", 422),
                "insufficient_coverage": ("insufficient_coverage", 422),
                "invalid_response": ("invalid_response", 502),
                "upstream_invalid_response": ("invalid_response", 502),
            }.get(code, ("upstream_failure", 503))
            _market_data_v3_error(mapped, status, parsed["requestId"])
        return chart_response(parsed, execution)
    except HTTPException as error:
        return JSONResponse(status_code=error.status_code, content=error.detail)
