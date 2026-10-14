"""原 SDK 的每手参考兼容入口；标准值与执行由能力拥有。"""

from src.services.thesis_ledger_security_lot_values import LotSizeError, hk_symbol, positive_lot, reference_digest
from data_provider.thesis_ledger_sources.futu import LOT_SOURCE_URL as FUTU_URL
from data_provider.thesis_ledger_sources.longbridge import LOT_SOURCE_URL as LONGBRIDGE_URL

CAPABILITY = "SECURITY_LOT_SIZE"
SOURCE_URLS = {"futu": FUTU_URL, "longbridge": LONGBRIDGE_URL}


def lot_reference(symbol, shares, provider, observed_at):
    canonical = hk_symbol(symbol)
    if provider not in SOURCE_URLS:
        raise LotSizeError("unsupported_source", "每手股数来源尚未接入")
    shares = positive_lot(shares)
    return {"symbol": canonical, "sharesPerLot": shares, "historicalChanges": "unverified",
        "source": {"provider": provider, "sourceUrl": SOURCE_URLS[provider], "observedAt": observed_at,
                   "sha256": reference_digest(canonical, shares, provider, observed_at)}}


def read_security_lot_size(provider, adapter, symbol):
    from src.services.thesis_ledger_capability_contracts import ReadContext
    from src.services.thesis_ledger_security_lot_contract_v1 import LotRequest
    from src.services.thesis_ledger_security_lot_query import read_lot
    query = LotRequest(symbol=symbol)
    return read_lot(query, query.route_key(), {"providerId": provider, "upstreamSource": provider},
                    ReadContext(adapter), LotSizeError)
