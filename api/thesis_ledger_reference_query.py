"""市场资料的独立、严格、只读 HTTP 入口。"""

from uuid import uuid4

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError

from api.thesis_ledger_auth import require_contract_token
from api.thesis_ledger_market_errors import market_data_error_v3
from src.services.thesis_ledger_market_errors_v3 import market_runtime_error
from src.services.thesis_ledger_reference_contract_v1 import ReferenceWireRequest
from src.services.thesis_ledger_reference_scope_v1 import runtime_symbol
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest


def build_reference_query_router(get_runtime):
    router = APIRouter()

    @router.post('/market/reference', dependencies=[Depends(require_contract_token)])
    def read_reference(payload: dict = Body(...)):
        request_id = payload.get('requestId')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            request_id = str(uuid4())
        try:
            wire = ReferenceWireRequest.model_validate(payload)
        except ValidationError:
            market_data_error_v3('invalid_request', 422, request_id)
        query = wire.model_dump(exclude={'contractVersion', 'consumer'})
        parameters = {key: query[key] for key in ('page', 'pageSize', 'cursor', 'indexCode', 'requestBudgetMs')}
        try:
            route_key = wire.route_key()
            request = ThesisLedgerDataRequest(route_key['capability'], runtime_symbol(wire),
                start=wire.start, end=wire.end, instrument_type=route_key['assetType'],
                request_id=wire.requestId, parameters=parameters)
            result = get_runtime().execute_request(request)
            return {**result.value, 'contractVersion': 3, 'consumer': 'thesis-ledger',
                    'policyRevision': result.effective_revision,
                    'routeTarget': {'providerId': result.provider, 'upstreamSource': result.upstream_source,
                                    'routeIndex': result.route_index}}
        except ProviderCallError as error:
            code, status = market_runtime_error(error)
            market_data_error_v3(code, status, wire.requestId)

    return router
