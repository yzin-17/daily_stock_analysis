"""已实际读取的港美股身份观察；缺市场单位保持null，不推断上市日。"""

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from src.services.thesis_ledger_dependency_facts import DependencyFactError, validate_cn_stock
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import DailyBinding

DATA_PATH = Path(__file__).resolve().parents[2] / "data_provider/market_reference/p6-identities.json"


def reference_identity(symbol: str, market: str, data_as_of: datetime | None = None):
    try:
        raw = DATA_PATH.read_bytes()
        pack = json.loads(raw)
        matches = [item for item in pack["identities"] if item["symbol"] == symbol and item["market"] == market]
        if len(matches) != 1:
            return None
        item = matches[0]
        expected = {"HK": ("HKD", "Asia/Hong_Kong", "HKG"), "US": ("USD", "America/New_York", "NYQ")}.get(market)
        if (expected is None or (item["currency"], item["timezone"], item["sourceExchange"]) != expected
                or item["sourceInstrumentType"] != "EQUITY" or item["instrumentType"] != "STOCK"):
            return None
        observed = datetime.fromisoformat(item["observedAt"])
        if observed.tzinfo is None or (data_as_of is not None and observed > data_as_of):
            return None
        return {**item, "revision": "yfinance-identity-sha256:" + sha256(raw).hexdigest()}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def validate_instrument_request(symbol, market, instrument_type, canonicalize, identity_only=False, adjustment=None,
                                bar_provider_id=None, bar_upstream_source=None):
    if market == "CN":
        return validate_cn_stock(symbol, market, instrument_type, canonicalize)
    if instrument_type == 'STOCK' and identity_only and adjustment == 'none':
        binding = CAPABILITY_SOURCES.resolve(
            {'kind': 'bar', 'market': market, 'assetType': instrument_type, 'capability': 'DAILY_BAR', 'timeframe': '1d', 'adjustment': adjustment},
            {'providerId': bar_provider_id, 'upstreamSource': bar_upstream_source},
        )
        if (isinstance(binding, DailyBinding) and binding.research_instrument_policy is not None
                and binding.research_instrument_policy.supports_symbol(symbol) is True):
            return symbol
    if not identity_only or instrument_type != "STOCK" or adjustment != "qfq" or reference_identity(symbol, market) is None:
        raise DependencyFactError("unsupported_capability", "港美股首批仅支持已核验身份的归一化观察；原价或市场单位未核实")
    return symbol


def reference_instrument_fact(symbol: str, market: str, data_as_of: datetime):
    item = reference_identity(symbol, market, data_as_of)
    if item is None:
        return None
    return {"symbol": symbol, "market": market, "instrumentType": "STOCK", "currency": item["currency"],
            "lotSize": None, "tickSize": None, "tradable": False,
            "executionRules": {"status": "unavailable", "reason": "仅证券身份观察，市场整手/价位与历史制度未核实"},
            "provider": "yfinance-identity-observation", "providerRevision": item["revision"],
            "occurredAt": item["observedAt"], "availableAt": item["observedAt"]}
