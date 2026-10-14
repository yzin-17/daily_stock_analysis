"""精确行情读取的传输完整性校验；独立交易日覆盖由调用方核验。"""

from typing import Mapping

from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_market_v3_facts import market_pagination_contract_v3


class MarketPaginationError(ValueError):
    def __init__(self, code="invalid_response"):
        self.code = code
        super().__init__(code)


def market_pagination_proof_v3(
    frame, *, asset_type, provider, upstream_source, expected_session_count,
    requested_start, requested_end, requested_adjustment=None,
):
    contract = market_pagination_contract_v3(asset_type, provider, upstream_source)
    attrs = getattr(frame, "attrs", None)
    evidence = attrs.get("thesis_ledger_v3_pagination") if isinstance(attrs, Mapping) else None
    if contract is None or not isinstance(evidence, Mapping) or set(evidence) != {
        "status", "pagesFetched", "continuationPending", "protocol", "maximumRows", "requestedStart", "requestedEnd",
    }:
        raise MarketPaginationError()
    expected_pages = 1
    candidates = [binding for key, target, binding in CAPABILITY_SOURCES.routes("DAILY_BAR")
                  if (key["assetType"], target["providerId"], target["upstreamSource"]) ==
                  (asset_type, provider, upstream_source)]
    binding = next((item for item in candidates if item.key.adjustment == requested_adjustment), None)
    if binding is None and requested_adjustment is None and len(candidates) == 1:
        binding = candidates[0]
    if binding is None and any(item.verify_transport is not None for item in candidates):
        raise MarketPaginationError()
    if binding is not None and binding.verify_transport is not None:
        try:
            expected_pages = binding.verify_transport(frame, requested_start, requested_end, requested_adjustment)
        except CapabilityContractError as error:
            raise MarketPaginationError(error.code) from None
    if (
        evidence.get("status") != "complete" or evidence.get("protocol") != contract["protocol"]
        or type(evidence.get("pagesFetched")) is not int or evidence["pagesFetched"] != expected_pages
        or evidence.get("continuationPending") is not False
        or evidence.get("maximumRows") != contract["maximumRows"]
        or evidence.get("requestedStart") != requested_start or evidence.get("requestedEnd") != requested_end
    ):
        raise MarketPaginationError()
    if contract["maximumRows"] is not None and expected_session_count > contract["maximumRows"]:
        raise MarketPaginationError("insufficient_coverage")
    return {"status": "complete", "pagesFetched": expected_pages, "continuationPending": False}
