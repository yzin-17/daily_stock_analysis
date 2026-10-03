"""V3 原生价格事实的 HTTP 投影；单位不改变数值或授予转换资格。"""

from typing import Any, Mapping

from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE,
)
from src.services.thesis_ledger_hithink_etf_units import hithink_etf_field_contract_is_valid


def native_field_units(attrs: Mapping[str, Any], key: Mapping[str, Any],
                       provider: str, source: str,
                       request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    hithink_contract = attrs.get("hithink_etf_field_units")
    native_contract = attrs.get("native_daily_contract")
    if hithink_contract is not None:
        if (
            native_contract is not None
            or provider != "hithink"
            or source != HITHINK_ETF_HISTORY_SOURCE
            or key.get("assetType") != "ETF"
            or key.get("timeframe") != "1d"
            or key.get("market") != "CN"
            or key.get("adjustment") != "qfq"
            or not isinstance(hithink_contract, Mapping)
            or not hithink_etf_field_contract_is_valid(
                dict(hithink_contract), dict(request) if isinstance(request, Mapping) else None,
            )
        ):
            raise ValueError("invalid HiThink ETF field unit contract")
        return {"fieldUnits": {
            "volume": hithink_contract["volumeUnit"],
            "amount": hithink_contract["amountUnit"],
        }}

    if native_contract is None:
        return {}
    contract = native_contract
    stock = key["assetType"] == "STOCK"
    expected = {
        "contractVersion": "akshare-eastmoney-native-fields-v1",
        "providerId": "akshare", "upstreamSource": "eastmoney",
        "independentSourceId": "eastmoney",
        "assetType": key["assetType"], "adjustment": key["adjustment"],
        "nativeAdjust": "" if key["adjustment"] == "none" else key["adjustment"],
        "endpoint": "stock_zh_a_hist" if stock else "fund_etf_hist_em",
        "volumeUnit": "hand" if stock else "unknown",
        "amountUnit": "CNY" if stock else "unknown",
        "volumeAdjustment": "unknown",
    }
    if (
        provider != "akshare" or source != "eastmoney"
        or key["assetType"] not in {"STOCK", "ETF"}
        or key["timeframe"] != "1d" or key["market"] != "CN"
        or not isinstance(contract, Mapping)
        or any(contract.get(name) != value for name, value in expected.items())
        or contract.get("valuesConverted") is not False
    ):
        raise ValueError("invalid native field contract")
    return {"fieldUnits": {"volume": contract["volumeUnit"], "amount": contract["amountUnit"]}}


def native_source_basis(adjustment: str, method_version: str, fingerprint: str,
                        fetched_at: str, units: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "adjustment": adjustment, "method": "provider-native",
        "methodVersion": method_version, "basisScope": "provider-defined",
        "anchor": None, "revision": {"origin": "local-observation", "contentHash": fingerprint},
        "observedAt": fetched_at, "volumeBasis": "unknown", **units,
        "dividendMeaning": "provider-defined", "dividendEvidenceRef": None,
        "conversionAvailable": False, "conversionEvidenceRef": None, "derivation": None,
    }
