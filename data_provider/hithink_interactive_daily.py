"""HiThink 普通图表读取；观测价格不产生独立上市或日历证明。"""

from datetime import datetime, time, timezone
from collections.abc import Mapping
import pandas as pd
import requests

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def read_interactive_hithink_daily(request, snapshot):

    if snapshot is None or not snapshot.values.get("apiKey"):
        raise CapabilityContractError("credential_missing", "HiThink API Key 未配置")
    if request.instrument_type == "STOCK":
        from src.services.thesis_ledger_hithink_stock import HiThinkStockHistoricalAdapter, HiThinkStockError
        try:
            return HiThinkStockHistoricalAdapter(snapshot.values["apiKey"], timeout_seconds=4.5).fetch_daily_bars(
                request.symbol, request.start, request.end, request.adjustment,
            )
        except HiThinkStockError as error:
            raise CapabilityContractError(error.code, "HiThink 股票日线读取未通过") from None
    if request.instrument_type != "ETF" or request.adjustment != "qfq":
        raise CapabilityContractError("unsupported_adjustment", "HiThink ETF 仅接入来源前复权")
    from src.services.thesis_ledger_hithink_etf import (
        HITHINK_ETF_HISTORICAL_URL, _validate_request, _parse_iso_date, _shanghai_midnight_epoch_ms,
        _raise_provider_error, _pagination_state, _payload_fingerprint, HiThinkETFAdapterError,
    )
    try:
        first, last = _parse_iso_date(request.start, "start"), _parse_iso_date(request.end, "end")
        _validate_request(request.symbol, first, last, "qfq", snapshot.values["apiKey"], 4.5)
        response = requests.get(HITHINK_ETF_HISTORICAL_URL, params={
            "thscode": request.symbol, "interval": "1d", "start": _shanghai_midnight_epoch_ms(first),
            "end": _shanghai_midnight_epoch_ms(last),
        }, headers={"X-api-key": snapshot.values["apiKey"]}, timeout=4.5, allow_redirects=False)
        status = response.status_code
        if status in {401, 403}:
            raise CapabilityContractError("permission_denied", "HiThink ETF 凭证或权限不足")
        if status == 429:
            raise CapabilityContractError("quota_unavailable", "HiThink ETF 调用额度不可用")
        if not 200 <= status < 300:
            raise CapabilityContractError("upstream_failure", "HiThink ETF 请求失败")
        payload = response.json()
        if not isinstance(payload, Mapping) or type(payload.get("code")) is not int:
            raise ValueError("invalid business state")
        if payload["code"] != 0:
            _raise_provider_error(payload["code"], _payload_fingerprint(payload))
        data = payload.get("data")
        if (not isinstance(data, Mapping) or data.get("thscode") != request.symbol or data.get("interval") != "1d"
                or "adjust" not in data or data["adjust"] is not None
                or _pagination_state(data) or _pagination_state(payload) or not isinstance(data.get("item"), list)):
            raise ValueError("invalid identity or transport")
        rows = []
        for item in data["item"]:
            if not isinstance(item, Mapping) or type(item.get("date_ms")) is not int:
                raise ValueError("invalid date")
            from zoneinfo import ZoneInfo
            local = datetime.fromtimestamp(item["date_ms"] / 1000, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
            if local.time() != time.min or not first <= local.date() <= last:
                raise ValueError("out of range")
            rows.append({"date": local.date().isoformat(), **{name: item.get(source) for name, source in {
                "open": "open_price", "high": "high_price", "low": "low_price", "close": "close_price",
                "volume": "volume", "amount": "turnover",
            }.items()}})
        dates = [row["date"] for row in rows]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("invalid ordering")
        if dates:
            if type(data.get("timestamp")) is not int:
                raise ValueError("missing response date")
            latest = datetime.fromtimestamp(data["timestamp"] / 1000, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
            if latest != dates[-1]:
                raise ValueError("response date mismatch")
        return normalize_exact_daily(pd.DataFrame(rows), start=request.start, end=request.end, source="fund-market-historical")
    except CapabilityContractError:
        raise
    except HiThinkETFAdapterError as error:
        raise CapabilityContractError(error.code, "HiThink ETF 响应校验未通过") from None
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None
    except requests.RequestException:
        raise CapabilityContractError("upstream_failure", "HiThink ETF 网络请求失败") from None
    except (TypeError, ValueError, KeyError, OverflowError, OSError):
        raise CapabilityContractError("invalid_response", "HiThink ETF 响应格式无效") from None
