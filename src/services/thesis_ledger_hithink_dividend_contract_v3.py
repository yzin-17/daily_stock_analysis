"""HiThink 基金分红的精确来源合同。"""

import re
from typing import Any, Mapping


HITHINK_DIVIDEND_SOURCE = "fund-corporate-actions-dividends"
HITHINK_DIVIDEND_TARGET = {
    "providerId": "hithink",
    "upstreamSource": HITHINK_DIVIDEND_SOURCE,
}
HITHINK_DIVIDEND_KEY = {
    "kind": "data",
    "market": "CN",
    "assetType": "ETF",
    "capability": "CASH_DISTRIBUTION",
}
HITHINK_DIVIDEND_ADAPTER_REVISION = "dsa-hithink-fund-dividend-v1"
HITHINK_DIVIDEND_SOURCE_REVISION = "hithink-fund-dividends-single-response-v1"
_CREDENTIAL_REVISION = re.compile(r"hmac-sha256-v1:[a-f0-9]{64}\Z")


def hithink_dividend_route_matches(key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    """仅确认精确候选身份；不授予生产路由或历史覆盖。"""
    return (isinstance(key, Mapping) and dict(key) == HITHINK_DIVIDEND_KEY
            and isinstance(target, Mapping)
            and target.get("providerId") == HITHINK_DIVIDEND_TARGET["providerId"]
            and target.get("upstreamSource") == HITHINK_DIVIDEND_TARGET["upstreamSource"])


def hithink_dividend_route_revisions(
    key: Mapping[str, Any], target: Mapping[str, Any], credential_revision: str | None,
) -> dict[str, str] | None:
    """凭据修订必须来自当前 HMAC 快照，不能接受配置版本号或原密钥。"""
    if (not hithink_dividend_route_matches(key, target)
            or not isinstance(credential_revision, str)
            or _CREDENTIAL_REVISION.fullmatch(credential_revision) is None):
        return None
    return {
        "adapterRevision": HITHINK_DIVIDEND_ADAPTER_REVISION,
        "sourceRevision": HITHINK_DIVIDEND_SOURCE_REVISION,
        "credentialRevision": credential_revision,
    }
