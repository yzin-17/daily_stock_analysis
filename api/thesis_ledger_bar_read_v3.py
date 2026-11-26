"""完整回测日线窗口读取与成功验证发布。"""

import logging
import uuid

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.services.thesis_ledger_verification_request import VerificationRequest

logger = logging.getLogger(__name__)


def read_bar_window(request, payload):
    from api.thesis_ledger import (
        _market_data_v3_request, _market_data_v3_coverage_context,
        _market_data_v3_response, _market_data_v3_error,
    )
    from src.services.thesis_ledger_market_errors_v3 import market_runtime_error
    from src.services.thesis_ledger_provider_runtime import DAILY_BAR_TARGET_TIMEOUT_SECONDS, get_thesis_ledger_runtime
    from api.thesis_ledger_multi_window_v3 import execute_market_window_v3, try_daily_multi_window_v3

    request_id = request.headers.get('x-request-id') or str(uuid.uuid4())
    try:
        parsed = _market_data_v3_request(payload, request_id)
        covered = _market_data_v3_coverage_context(parsed)
        if covered is None:
            _market_data_v3_error('insufficient_coverage', 422, parsed['requestId'])
        runtime = get_thesis_ledger_runtime()
        verification = VerificationRequest(runtime, parsed['routeKey'])
        try:
            response = try_daily_multi_window_v3(
                parsed, covered, runtime, _market_data_v3_response, _market_data_v3_coverage_context,
                max_requests=8, timeout_seconds=DAILY_BAR_TARGET_TIMEOUT_SECONDS,
            )
            if response is None:
                execution = execute_market_window_v3(parsed, runtime)
                response = _market_data_v3_response(parsed, execution, covered)
        except HTTPException:
            raise
        except Exception as error:
            logger.warning('ThesisLedger dsa-data-v1 failed requestId=%s code=%s',
                           parsed['requestId'], getattr(error, 'code', None) or 'upstream_failure')
            mapped, status = market_runtime_error(error)
            _market_data_v3_error(mapped, status, parsed['requestId'])
        return verification.accept(response)
    except HTTPException as error:
        return JSONResponse(status_code=error.status_code, content=error.detail)
