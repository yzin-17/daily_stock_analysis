"""标准分钟查询的凭证快照、强合同与返回前修订检查。"""

import pandas as pd
from pydantic import ValidationError
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_minute_contract_v1 import CONTRACT, MinuteRequest
from .thesis_ledger_price_route_access import basic_price_credential_ready
from .thesis_ledger_configured_read import ConfiguredReadError, check_configured_read_current
from .thesis_ledger_control import ControlContractError
from .thesis_ledger_control import PROVIDER_MANIFESTS


def execute_minute(runtime, request, key, policy, provider_id, upstream_source):
    from .thesis_ledger_provider_runtime import ProviderCallError
    target = {"providerId": provider_id, "upstreamSource": upstream_source}
    try:
        query = MinuteRequest.from_request(request)
        snapshot = runtime.store.provider_credential_snapshot(provider_id)
        if PROVIDER_MANIFESTS[provider_id].get('requiresCredential') and not basic_price_credential_ready(snapshot):
            raise ConfiguredReadError("credential_missing", "分钟来源缺少有效连接配置")
        adapter = runtime._adapter(provider_id, snapshot=snapshot, cache=False, market_v3_route_key=key)
        value = CAPABILITY_SOURCES.query(CONTRACT, query, key, target, ReadContext(adapter, snapshot, 4.5))
        check_configured_read_current(runtime.store, key, target, policy["revision"], snapshot)
    except CapabilityContractError as error:
        raise ProviderCallError(error.code, str(error), retryable=error.retryable) from None
    except ConfiguredReadError as error:
        raise ProviderCallError(error.code, str(error)) from None
    except ControlContractError:
        raise ProviderCallError("credential_missing", "分钟来源连接配置不可用") from None
    except (ValidationError, ValueError):
        raise ProviderCallError("invalid_request", "分钟请求与标准能力不匹配") from None
    frame = pd.DataFrame([row.model_dump() for row in value.rows])
    frame.attrs.update({"upstream_source": value.source, "timezone": value.timezone, "session": value.session,
                        "observedAt": value.observedAt, "responseSha256": value.responseSha256,
                        "has_more_before": False, "timeframe": value.timeframe,
                        "timestampConvention": value.timestampConvention, "intradayFieldUnits": value.fieldUnits})
    if value.pagination is not None:
        frame.attrs["pagination"] = value.pagination.model_dump()
    return frame
