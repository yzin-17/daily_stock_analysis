"""窗口规划器的关联、执行前拒绝与未声明边界。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from api import thesis_ledger_multi_window_v3 as api
from data_provider.thesis_ledger_sources.hithink import SOURCE
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, SourceDefinition
from src.services.thesis_ledger_daily_window_contract import DailyWindow, DailyWindowPlan, DailyWindowPlanningContext, validate_window_plan
from src.services.thesis_ledger_provider_runtime import ProviderCallError


CONTEXT = DailyWindowPlanningContext('2024-01-01', '2024-01-05', ('2024-01-01', '2024-01-03', '2024-01-05'), 2)


def plan(*pairs):
    return DailyWindowPlan(windows=tuple(DailyWindow(start=start, end=end) for start, end in pairs))


def test_valid_plan_revalidates_and_is_immutable():
    value = plan(('2024-01-01', '2024-01-03'), ('2024-01-03', '2024-01-05'))
    assert validate_window_plan(value, CONTEXT) == value
    with pytest.raises(ValueError):
        value.windows[0].start = '2024-01-02'


@pytest.mark.parametrize('value', [None, {}, plan(), plan(('2024-01-02', '2024-01-05')),
    plan(('2024-01-01', '2024-01-04')),
    plan(('2024-01-01', '2024-01-02'), ('2024-01-04', '2024-01-05')),
    plan(('2024-01-01', '2024-01-03'), ('2024-01-01', '2024-01-05')),
    plan(('2024-01-01', '2024-01-03'), ('2024-01-03', '2024-01-04'), ('2024-01-04', '2024-01-05')),
    DailyWindowPlan.model_construct(windows=(DailyWindow.model_construct(start='bad-date', end='2024-01-05'),))])
def test_bad_plan_is_rejected_before_any_child_request(monkeypatch, value):
    binding = next(binding for binding in SOURCE.bindings if binding.window_planner is not None)
    registry = CapabilityRegistry((SourceDefinition('test-plan', lambda: {}, lambda _: None,
                                    (replace(binding, window_planner=lambda _: value),)),))
    monkeypatch.setattr(api, 'CAPABILITY_SOURCES', registry)
    calls = []
    request = {'routeKey': binding.key.route_key(), 'routeTarget': {'providerId': 'test-plan', 'upstreamSource': binding.upstream_source},
               'start': CONTEXT.start, 'end': CONTEXT.end}
    coverage = {'calendar': {'expectedSessionDates': list(CONTEXT.sessions)}, 'listing': {'firstTradingDate': CONTEXT.start}}
    with pytest.raises(ProviderCallError) as failure:
        api.try_daily_multi_window_v3(request, coverage, SimpleNamespace(), lambda *_: calls.append('render'),
                                     lambda *_: calls.append('coverage'), max_requests=2, timeout_seconds=4.5)
    assert failure.value.code == 'invalid_response' and calls == []


def test_unbound_target_does_not_plan_or_inspect_coverage():
    assert api.try_daily_multi_window_v3({'routeKey': {}, 'routeTarget': {}}, {}, SimpleNamespace(),
        lambda *_: None, lambda *_: None, max_requests=8, timeout_seconds=4.5) is None


def test_non_callable_planner_rejected_at_registration():
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, bindings=(replace(SOURCE.bindings[0], window_planner=1),), quote_source_aliases=()),))
