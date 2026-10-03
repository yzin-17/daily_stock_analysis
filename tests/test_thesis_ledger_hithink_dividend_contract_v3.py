"""HiThink 分红候选合同只接受精确路由与当前凭据修订。"""

import pytest

from src.services.thesis_ledger_event_v3_adapters import event_adapter_matches, iter_event_adapters
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_ADAPTER_REVISION, HITHINK_DIVIDEND_KEY,
    HITHINK_DIVIDEND_SOURCE_REVISION, HITHINK_DIVIDEND_TARGET,
    hithink_dividend_route_matches, hithink_dividend_route_revisions,
)


VALID_CREDENTIAL = "hmac-sha256-v1:" + "a" * 64


def test_exact_contract_binds_route_hmac_and_runtime_inventory():
    assert hithink_dividend_route_matches(HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET)
    assert hithink_dividend_route_revisions(
        HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, VALID_CREDENTIAL,
    ) == {
        "adapterRevision": HITHINK_DIVIDEND_ADAPTER_REVISION,
        "sourceRevision": HITHINK_DIVIDEND_SOURCE_REVISION,
        "credentialRevision": VALID_CREDENTIAL,
    }
    assert event_adapter_matches(HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET)
    assert (HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, True) in list(iter_event_adapters())


@pytest.mark.parametrize("key,target", [
    ({**HITHINK_DIVIDEND_KEY, "capability": "SPLIT_EVENT"}, HITHINK_DIVIDEND_TARGET),
    ({**HITHINK_DIVIDEND_KEY, "assetType": "NAV_FUND"}, HITHINK_DIVIDEND_TARGET),
    ({**HITHINK_DIVIDEND_KEY, "adjustment": "none"}, HITHINK_DIVIDEND_TARGET),
    (HITHINK_DIVIDEND_KEY, {**HITHINK_DIVIDEND_TARGET, "upstreamSource": "fund-market-historical"}),
    (HITHINK_DIVIDEND_KEY, {**HITHINK_DIVIDEND_TARGET, "providerId": "akshare"}),
])
def test_crossed_or_broad_route_is_rejected(key, target):
    assert not hithink_dividend_route_matches(key, target)
    assert hithink_dividend_route_revisions(key, target, VALID_CREDENTIAL) is None


@pytest.mark.parametrize("credential", [
    None, "", "not-required", "1", "api-key", "hmac-sha256-v1:" + "A" * 64,
    "hmac-sha256-v1:" + "a" * 63,
])
def test_unknown_or_unbound_credential_revision_is_rejected(credential):
    assert hithink_dividend_route_revisions(
        HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, credential,
    ) is None
