"""自主研究按检索词定位当前证券身份，不认证历史资格。"""

from pydantic import Field
from .thesis_ledger_arena_history import HistoryRequest, archive, digest
from .thesis_ledger_instrument_search import search_credentials
from .thesis_ledger_provider_runtime import ProviderCallError
from data_provider.hithink_instrument_search import InstrumentSearchError, search_instruments


class InstrumentDiscoveryRequest(HistoryRequest):
    query: str = Field(min_length=1, max_length=80)


def read_instruments(runtime, request):
    day = request.day()
    if not request.query.strip():
        raise ValueError("检索词为空")
    with search_credentials(runtime.store, "hithink") as (values, version):
        try:
            result = search_instruments(values["apiKey"], request.query.strip())
        except InstrumentSearchError as error:
            raise ProviderCallError(error.code, "证券身份检索不可用") from None
    ref = archive(runtime, {"kind": "current-instrument-discovery", "query": request.query,
                           "providerVersion": version, **result})
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": request.requestId,
            "kind": "current-instrument-discovery", "historicalEligibility": "unverified", "market": "CN", "date": day,
            "query": request.query, "providerId": "hithink", "providerVersion": version, "archiveRef": ref,
            "observedAt": result["observedAt"], "sourceTimestamp": result["sourceTimestamp"],
            "responseSha256": digest(result["raw"]), "items": result["items"]}
