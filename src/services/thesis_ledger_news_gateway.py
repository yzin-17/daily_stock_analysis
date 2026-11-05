"""新闻正式请求入口与失败事实投影；复用现行 Runtime 选路和发布校验。"""

from datetime import datetime, timezone
import time

from src.data_config import DataConfigurationError
from src.services.thesis_ledger_current_data_route import CurrentDataRouteError
from src.services.thesis_ledger_control import ControlContractError
from src.services.thesis_ledger_news_contract_v1 import NewsError
from src.services.thesis_ledger_news_identity import news_time
from src.services.thesis_ledger_news_query import NewsTrace, build_news_operation
from src.services.thesis_ledger_news_records import RawNewsRead
from src.services.thesis_ledger_news_scope import news_response, news_response_model, news_scope
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest


def _failure_code(error, query, trace):
    code = getattr(error, "code", "configuration_failure")
    if code == "NO_ELIGIBLE_PROVIDER":
        effective = trace.effective or {}
        key, _symbol = news_scope(query)
        reasons = {target.get("reason") for route in effective.get("routes", []) if route.get("key") == key
                   for target in route.get("targets", [])}
        if "credential_missing" in reasons:
            return "missing_credentials"
        if "dependency_missing" in reasons:
            return "unsupported"
        return "not_admitted"
    if code == "upstream_unavailable" and trace.last_response is not None and trace.last_response.errors:
        return trace.last_response.errors[0].code
    if code in {"credential_missing", "INVALID_PROVIDER_CREDENTIALS"}:
        return "missing_credentials"
    if code in {"unsupported_capability", "unsupported_source", "dependency_missing"}:
        return "unsupported"
    if code not in {"not_admitted", "missing_credentials", "configuration_failure", "rate_limited",
                    "timeout", "upstream_failure", "ambiguous_instrument", "invalid_request", "not_covered", "unsupported"}:
        return "upstream_failure"
    return code


def _unavailable(query, trace, code):
    route = trace.last_response.route if trace.last_response is not None else None
    if code == "not_admitted" and trace.new_attempts:
        code = "upstream_failure"
    attempts = trace.attempts
    if code == "not_admitted":
        attempts = 0
    errors = list(trace.errors)
    if not errors or code not in {error.code for error in errors}:
        errors.append(NewsError(code=code, message="新闻请求未完成当前有效的数据读取"))
    return news_response(query, route, RawNewsRead(upstream_attempts=attempts, errors=tuple(errors)),
                         fetched_at=datetime.now(timezone.utc).isoformat())


def execute_news_request(runtime, query):
    trace = NewsTrace(time.monotonic() + query.requestBudgetMs / 1000)
    first = news_time(query.publishedStart).date().isoformat() if query.publishedStart else None
    last = news_time(query.publishedEnd).date().isoformat() if query.publishedEnd else None
    key, symbol = news_scope(query)
    response_type = news_response_model(query)
    request = ThesisLedgerDataRequest(
        query.capability, symbol, instrument_type=key["assetType"],
        start=first or last, end=last, limit=query.limit, request_id=query.requestId, parameters=query.model_dump(),
    )
    try:
        operation = build_news_operation(runtime, query, trace, ProviderCallError)
        execution = runtime._execute_request_with_boundary(request, query.capability, key["assetType"], operation)
        response = response_type.model_validate(execution.value)
    except (ProviderCallError, CurrentDataRouteError) as error:
        return _unavailable(query, trace, _failure_code(error, query, trace))
    except (DataConfigurationError, ControlContractError):
        return _unavailable(query, trace, "configuration_failure")
    if time.monotonic() >= trace.deadline:
        return _unavailable(query, trace, "timeout")
    if trace.errors:
        value = response.model_dump()
        value.update(status="partial", upstreamAttempts=trace.attempts, errors=[error.model_dump() for error in trace.errors] + value["errors"])
        value["coverage"]["complete"] = False
        value["coverage"]["missingReasons"] = sorted(set(value["coverage"]["missingReasons"] + ["fallback_after_failure"]))
        return response_type.model_validate(value)
    return response
