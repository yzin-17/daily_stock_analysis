"""单标研报最小元数据的独立 Data Contract HTTP 入口。"""

from uuid import uuid4

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError

from api.thesis_ledger_auth import require_contract_token
from api.thesis_ledger_market_errors import market_data_error_v3
from src.services.thesis_ledger_market_errors_v3 import market_runtime_error
from src.services.thesis_ledger_report_metadata_contract import CAPABILITY, ReportMetadataWireRequest
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest


def build_report_metadata_router(get_runtime):
    router = APIRouter()

    @router.post('/market/research-report-metadata', dependencies=[Depends(require_contract_token)])
    def read_metadata(payload: dict = Body(...)):
        request_id = payload.get('requestId')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            request_id = str(uuid4())
        try:
            query = ReportMetadataWireRequest.model_validate(payload)
        except ValidationError:
            market_data_error_v3('invalid_request', 422, request_id)
        try:
            request = ThesisLedgerDataRequest(CAPABILITY, query.symbol, start=query.start, end=query.end,
                instrument_type='STOCK', request_id=query.requestId,
                parameters={'page': query.page, 'pageSize': query.pageSize, 'requestBudgetMs': query.requestBudgetMs})
            result = get_runtime().execute_request(request)
            return {**result.value, 'contractVersion': 1, 'consumer': 'thesis-ledger',
                    'policyRevision': result.effective_revision,
                    'routeTarget': {'providerId': result.provider, 'upstreamSource': result.upstream_source,
                                    'routeIndex': result.route_index}}
        except ProviderCallError as error:
            code, status = market_runtime_error(error)
            market_data_error_v3(code, status, query.requestId)

    return router
