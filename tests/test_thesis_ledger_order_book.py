"""盘口两类输入、标准合同和订阅边界；无真实网络。"""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from typing import Literal

import pytest

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_order_book_contract import OrderBook, OrderBookRequest, Level
from src.services.thesis_ledger_order_book_state import BookSession, BookSubscriptions, SubscriptionIdentity, BookDelta
from tests.order_book_adapter_fixtures import CONTRACT, SOURCES, RAW_A, RAW_B, Input

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "thesis-ledger/packages/schemas/test/fixtures/order-book-v1.json").read_text())
QUERY = OrderBookRequest.model_validate(FIXTURE["request"])
KEY = SOURCES[0].bindings[0].key.route_key()
REGISTRY = CapabilityRegistry(SOURCES)


def book(source=0, raw=None, query=QUERY, **options):
    target = {"providerId": SOURCES[source].provider_id, "upstreamSource": SOURCES[source].bindings[0].upstream_source}
    return REGISTRY.query(CONTRACT, query, KEY, target, ReadContext(Input(raw or deepcopy((RAW_A, RAW_B)[source]), **options)))


def identity(source=0, **changes):
    return replace(SubscriptionIdentity("account-a", 1, SOURCES[source].provider_id,
                   SOURCES[source].bindings[0].upstream_source, QUERY.symbol, QUERY.market, QUERY.depth, False), **changes)


def delta(session, *, previous=1, sequence=2, quantity="1100", generation=None, source=0):
    return BookDelta(book(source).target, QUERY.symbol, QUERY.market, generation or session.generation,
                     previous, sequence, "2026-10-09T08:00:02+00:00", None,
                     (("bids", Level(price="302.20", quantity=quantity, orderCount=None)),))


def test_two_structures_produce_one_shared_contract():
    a, b = book(), book(1)
    assert a.model_dump() == FIXTURE["response"]
    assert {key: value for key, value in a.model_dump().items() if key != "target"} == {
        key: value for key, value in b.model_dump().items() if key != "target"}
    assert "ORDER_BOOK" not in {binding.key.capability for source in CAPABILITY_SOURCES.sources for binding in source.bindings}


@pytest.mark.parametrize("changes", [
    {"symbol": "00701.HK"},
    {"symbol": "AAPL.US", "market": "US"},
    {"requestedDepth": 4},
    {"target": {"providerId": "other-source", "upstreamSource": "venue-a"}},
    {"target": {"providerId": "fixture-a", "upstreamSource": "other-venue"}},
])
def test_framework_rejects_valid_but_uncorrelated_adapter_result(changes):
    response = OrderBook.model_validate({**deepcopy(FIXTURE["response"]), **changes})
    source = replace(SOURCES[0], bindings=(replace(SOURCES[0].bindings[0], read=lambda query, context: response),))
    registry = CapabilityRegistry((source,))
    target = {"providerId": source.provider_id, "upstreamSource": source.bindings[0].upstream_source}
    with pytest.raises(CapabilityContractError) as error:
        registry.query(CONTRACT, QUERY, KEY, target, ReadContext(None))
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("query", [
    OrderBookRequest(symbol="AAPL.US", market="US", depth=5),
    OrderBookRequest.model_construct(symbol="00700.HK", market="HK", depth=0, allowDepthDegradation=False),
])
def test_framework_rejects_request_before_adapter(query):
    calls = []
    source = replace(SOURCES[0], bindings=(replace(SOURCES[0].bindings[0],
                     read=lambda request, context: calls.append(request) or OrderBook.model_validate(FIXTURE["response"])),))
    target = {"providerId": source.provider_id, "upstreamSource": source.bindings[0].upstream_source}
    with pytest.raises(CapabilityContractError) as error:
        CapabilityRegistry((source,)).query(CONTRACT, query, KEY, target, ReadContext(None))
    assert error.value.code == "invalid_request"
    assert calls == []


def test_query_handler_cannot_be_registered_as_subscription():
    contract = replace(CONTRACT, operations=("query", "subscribe"))
    binding = replace(SOURCES[0].bindings[0], contract=contract, operation="subscribe")
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCES[0], bindings=(binding,)),))


@pytest.mark.parametrize("changes", [
    {"validate_binding": None}, {"correlate_result": None}, {"validate_result": None},
    {"request_schema": object}, {"operations": ()}, {"operations": ("query", "query")},
    {"operations": ("execute",)}, {"version": 0}, {"version": True},
])
def test_new_capability_requires_valid_contract_and_correlation(changes):
    contract = replace(CONTRACT, **changes)
    binding = replace(SOURCES[0].bindings[0], contract=contract)
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCES[0], bindings=(binding,)),))


def test_contract_version_is_owned_by_the_capability():
    class RequestV2(OrderBookRequest):
        contractVersion: Literal[2] = 2

    class ResultV2(OrderBook):
        contractVersion: Literal[2]

    response = ResultV2.model_validate({**FIXTURE["response"], "contractVersion": 2})
    contract = replace(CONTRACT, version=2, request_schema=RequestV2,
                       validate_result=lambda value: ResultV2.model_validate(value.model_dump()))
    binding = replace(SOURCES[0].bindings[0], contract=contract, read=lambda query, context: response)
    registry = CapabilityRegistry((replace(SOURCES[0], bindings=(binding,)),))
    target = {"providerId": SOURCES[0].provider_id, "upstreamSource": binding.upstream_source}
    assert registry.resolve(KEY, target).contract_version == 2
    query = RequestV2.model_validate(FIXTURE["request"])
    assert registry.query(contract, query, KEY, target, ReadContext(None)).contractVersion == 2


@pytest.mark.parametrize('difference', ['request-schema', 'result-validator', 'request-correlation', 'result-correlation', 'operations'])
def test_second_source_cannot_fork_same_capability_version_contract(difference):
    calls = []
    class DifferentRequest(OrderBookRequest):
        extraParameter: bool = False
    changes = {
        'request-schema': {'request_schema': DifferentRequest},
        'result-validator': {'validate_result': lambda value: OrderBook.model_validate(value)},
        'request-correlation': {'validate_binding': lambda *args: None},
        'result-correlation': {'correlate_result': lambda request, result, target: result},
        'operations': {'operations': ('query', 'subscribe')},
    }
    fork = replace(CONTRACT, **changes[difference])
    first = replace(SOURCES[0], factory=lambda _: calls.append('factory-a'))
    second = replace(SOURCES[1], factory=lambda _: calls.append('factory-b'),
        bindings=(replace(SOURCES[1].bindings[0], contract=fork, read=lambda *_: calls.append('read')),))
    with pytest.raises(ValueError, match='能力版本合同不一致'):
        CapabilityRegistry((first, second))
    assert calls == []


def test_equivalent_contract_value_is_reusable_by_second_source():
    second = replace(SOURCES[1], bindings=(replace(SOURCES[1].bindings[0], contract=replace(CONTRACT)),))
    registry = CapabilityRegistry((SOURCES[0], second))
    target = {'providerId': second.provider_id, 'upstreamSource': second.bindings[0].upstream_source}
    assert registry.query(CONTRACT, QUERY, KEY, target, ReadContext(Input(RAW_B))).target.providerId == second.provider_id


def test_distinct_capability_versions_can_coexist_without_overwriting_first():
    class RequestV2(OrderBookRequest):
        contractVersion: Literal[2] = 2
    class ResultV2(OrderBook):
        contractVersion: Literal[2]
    response = ResultV2.model_validate({**FIXTURE['response'], 'contractVersion': 2,
                                      'target': {'providerId': 'fixture-b', 'upstreamSource': 'venue-b'}})
    contract = replace(CONTRACT, version=2, request_schema=RequestV2,
                       validate_result=lambda value: ResultV2.model_validate(value.model_dump()))
    second = replace(SOURCES[1], bindings=(replace(SOURCES[1].bindings[0], contract=contract,
                                      read=lambda query, context: response),))
    registry = CapabilityRegistry((SOURCES[0], second))
    target = {'providerId': second.provider_id, 'upstreamSource': second.bindings[0].upstream_source}
    result = registry.query(contract, RequestV2.model_validate(FIXTURE['request']), KEY, target, ReadContext(None))
    assert result.contractVersion == 2
    first_target = {'providerId': SOURCES[0].provider_id, 'upstreamSource': SOURCES[0].bindings[0].upstream_source}
    assert registry.resolve(KEY, first_target).contract is CONTRACT


def test_constructed_result_cannot_bypass_schema_validation():
    response = OrderBook.model_construct(**{**deepcopy(FIXTURE["response"]), "requestedDepth": 0})
    source = replace(SOURCES[0], bindings=(replace(SOURCES[0].bindings[0], read=lambda query, context: response),))
    target = {"providerId": source.provider_id, "upstreamSource": source.bindings[0].upstream_source}
    with pytest.raises(CapabilityContractError) as error:
        CapabilityRegistry((source,)).query(CONTRACT, QUERY, KEY, target, ReadContext(None))
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("fields", [
    {"venue": " "}, {"symbol": " 00700.HK"},
    {"symbol": "HK.00700"}, {"market": "US"},
    {"receivedAt": "2026-10-09 08:00:00+00:00"},
    {"bids": [{"price": "1", "quantity": "1", "orderCount": 9007199254740992}]},
])
def test_cross_language_identifier_clock_and_integer_boundaries(fields):
    with pytest.raises(ValueError):
        OrderBook.model_validate({**FIXTURE["response"], **fields})


@pytest.mark.parametrize("source", [0, 1])
def test_permission_failure_never_becomes_empty_success(source):
    with pytest.raises(CapabilityContractError) as error:
        book(source, permitted=False)
    assert error.value.code == "permission_denied"


def test_unknown_units_clocks_and_continuity_remain_unknown():
    raw = {**RAW_A, "lotSize": None, "timestamp_ms": None, "seq": None}
    a = book(raw=raw)
    assert a.quantityUnit == "unknown" and a.bids[0].quantity == "10"
    assert a.sourceEventTime is None and a.continuity == "unknown"
    b = book(1, raw={**RAW_B, "unit": "unspecified", "observed_at": None, "change_id": None})
    assert b.quantityUnit == "unknown" and b.sourceEventTime is None


def test_depth_degradation_requires_request_and_policy():
    raw = {**RAW_A, "maxLevels": 1, "buy": [["302.20", "10"]]}
    query = OrderBookRequest.model_validate({**QUERY.model_dump(), "allowDepthDegradation": True})
    for q, allowed in ((QUERY, True), (query, False)):
        with pytest.raises(CapabilityContractError):
            book(raw=raw, query=q, depth_policy_allowed=allowed)
    assert book(raw=raw, query=query, depth_policy_allowed=True).depthDegraded is True


@pytest.mark.parametrize("fields", [{"buy": [["302.20", "10"], ["302.20", "20"]]}, {"code": "HK.09999"}])
def test_bad_adapter_identity_or_duplicate_levels_rejected(fields):
    with pytest.raises(ValueError):
        book(raw={**RAW_A, **fields})


def test_initial_gap_resync_late_generation_and_source_switch():
    released = []
    session = BookSession(identity(), capacity=2, release=lambda: released.append("a"))
    assert session.state == "connecting"
    assert session.enqueue(delta(session)) is False
    assert session.state == "resyncing" and session.book is None
    assert session.snapshot(book(), 1)
    assert session.enqueue(delta(session, sequence=3))
    assert session.drain() is None and session.state == "resyncing"
    assert session.snapshot(book(), 1)
    assert session.enqueue(delta(session))
    assert session.drain().continuity == "verified"
    generation = session.switch(identity(1), lambda: released.append("b"))
    assert released == ["a"] and generation == 2 and session.book is None
    assert session.enqueue(delta(session, generation=1)) is False
    assert session.enqueue(delta(session, source=1)) is False
    assert session.snapshot(book(), generation) is False
    assert session.snapshot(book(1), generation)
    assert session.enqueue(delta(session, source=1))
    assert session.drain().target.providerId == "fixture-b"
    session.close()
    session.close()
    assert released == ["a", "b"] and session.state == "closed"
    assert session.snapshot(book(1), generation) is False


def test_buffer_overflow_invalidates_and_ui_coalescing_follows_reconstruction():
    session = BookSession(identity(), capacity=2, release=lambda: None)
    session.snapshot(book(), 1)
    assert session.enqueue(delta(session, quantity="1200"))
    assert session.enqueue(delta(session, previous=2, sequence=3, quantity="0"))
    latest = session.drain()
    assert len(latest.bids) == 1 and latest.bids[0].price == "302.10" and latest.sequence == 3
    session.snapshot(book(), 1)
    session.enqueue(delta(session))
    session.enqueue(delta(session, previous=2, sequence=3))
    assert session.enqueue(delta(session, previous=3, sequence=4)) is False
    assert session.book is None and session.state == "resyncing"


def test_no_sequence_cannot_claim_verified_continuity():
    session = BookSession(identity(), capacity=1, release=lambda: None)
    session.snapshot(book(raw={**RAW_A, "seq": None}), 1)
    session.enqueue(delta(session, previous=None, sequence=None))
    assert session.drain().continuity == "unknown"


def test_sharing_permission_epoch_parameters_revoke_and_disable():
    pool = BookSubscriptions()
    released = []
    def create(key):
        return BookSession(key, capacity=2, release=lambda: released.append(key))
    base = identity()
    session = pool.acquire(base, lambda: create(base))
    assert pool.acquire(base, lambda: create(base)) is session
    keys = [identity(account_scope="account-b"), identity(permission_epoch=2), identity(1),
            identity(depth=1), identity(allow_depth_degradation=True)]
    for key in keys:
        assert pool.acquire(key, lambda: create(key)) is not session
    pool.release(base)
    assert released == []
    pool.revoke("account-a", 1)
    assert session.state == "closed" and base in released
    pool.release(base)
    pool.disable("fixture-b", "venue-b")
    for key in keys:
        pool.release(key)
    assert len(released) == 6
