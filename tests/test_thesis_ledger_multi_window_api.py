"""受控运行时经真实 V3 HTTP 响应构造验证多窗口调度。"""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from api.thesis_ledger import router_v3
from api.thesis_ledger_multi_window_v3 import plan_hithink_windows, try_hithink_multi_window_v3
from src.services.thesis_ledger_market_v3_facts import market_pagination_contract_v3
from src.services.thesis_ledger_multi_window_fingerprint import multi_window_content_hash
from src.services.thesis_ledger_provider_runtime import ProviderCallError


SESSIONS = ['2000-01-03', '2004-12-31', '2005-01-03', '2009-12-31', '2010-01-04', '2011-01-03']


def test_planner_limits_each_window_and_requires_real_overlap_before_calls():
    windows = plan_hithink_windows(SESSIONS[0], SESSIONS[-1], SESSIONS, 3)
    assert windows == [('2000-01-03', '2005-01-03'), ('2005-01-03', '2010-01-03'), ('2009-12-31', '2011-01-03')]
    with pytest.raises(ValueError):
        plan_hithink_windows(SESSIONS[0], SESSIONS[-1], SESSIONS, 2)
    with pytest.raises(ValueError):
        plan_hithink_windows(SESSIONS[0], SESSIONS[-1], [], 3)


@pytest.mark.parametrize('mode', ['complete', 'conflict', 'failure', 'missing_bar', 'pending_page', 'late'])
def test_api_builds_verified_subresponses_and_stops_after_conflict(monkeypatch, mode):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-token')
    fixture = json.loads((Path(__file__).parent / 'fixtures/thesis-ledger-multi-window-hash.json').read_text())['response']
    key = fixture['routeKey']
    target = {'providerId': 'hithink', 'upstreamSource': 'fund-market-historical', 'routeIndex': 0}
    request = {'contractVersion': 3, 'requestId': 'synthetic-multi-window', 'symbol': '159516.SZ',
               'routeKey': key, 'routeTarget': target, 'start': SESSIONS[0], 'end': SESSIONS[-1]}

    def coverage(data):
        proof = deepcopy(fixture['coverageProof'])
        dates = [day for day in SESSIONS if data['start'] <= day <= data['end']]
        proof['calendar'].update(source='synthetic-calendar', revision='synthetic-v1',
                                  supportedRange={'start': SESSIONS[0], 'end': SESSIONS[-1]}, expectedSessionDates=dates)
        proof['listing']['firstTradingDate'] = SESSIONS[0]
        proof['window'].update(requestedStart=data['start'], requestedEnd=data['end'])
        return {**{k: proof[k] for k in ['calendar', 'listing', 'window']}, 'expectedPostListingSessionDates': dates}

    calls = []

    def execute(data, route_key, *, route_target):
        calls.append(data)
        assert route_target == target
        assert route_key == key
        assert 0 < data.parameters['target_timeout_seconds'] <= 4.5
        if mode == 'failure' and len(calls) == 2:
            raise ProviderCallError('permission_denied', '受控权限失败')
        days = [day for day in SESSIONS if data.start <= day <= data.end]
        frame = pd.DataFrame([{'date': day, 'open': 1., 'high': 1.2, 'low': .9, 'close': 1.,
                               'volume': 100., 'amount': 100.} for day in days])
        if mode == 'conflict' and len(calls) == 2:
            frame.loc[0, 'close'] = 1.1
        if mode == 'missing_bar' and len(calls) == 2:
            frame = frame.iloc[1:].copy()
        contract = market_pagination_contract_v3('ETF', 'hithink', target['upstreamSource'])
        frame.attrs['thesis_ledger_v3_pagination'] = {**contract, 'status': 'complete', 'pagesFetched': 1,
            'continuationPending': False, 'requestedStart': data.start, 'requestedEnd': data.end}
        if mode == 'pending_page' and len(calls) == 2:
            frame.attrs['thesis_ledger_v3_pagination']['continuationPending'] = True
        return SimpleNamespace(value=frame, provider='hithink', upstream_source=target['upstreamSource'],
                               route_index=0, effective_revision=17)

    monkeypatch.setattr('api.thesis_ledger._market_data_v3_coverage_context', coverage)
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime',
                        lambda: SimpleNamespace(execute_market_bars_v3=execute))
    if mode == 'late':
        clock = iter([0., 0., 5.])

        def timed_dispatch(*args, **kwargs):
            return try_hithink_multi_window_v3(*args, **kwargs, monotonic=lambda: next(clock))

        monkeypatch.setattr('api.thesis_ledger_multi_window_v3.try_hithink_multi_window_v3', timed_dispatch)
    app = FastAPI()
    app.include_router(router_v3, prefix='/api/v3')
    response = TestClient(app).post('/api/v3/thesis-ledger/market/bars', json=request,
                                    headers={'Authorization': 'Bearer synthetic-token'})
    if mode == 'complete':
        assert response.status_code == 200, response.json()
        value = response.json()
        assert len(value['windowObservations']) == 3
        assert len(value['bars']) == len(SESSIONS)
        assert value['inputFingerprint'] == multi_window_content_hash(value)
        assert value['coverageProof']['pagination']['pagesFetched'] == 3
        assert len(calls) == 3
    else:
        expected_status = {'conflict': 502, 'failure': 503, 'missing_bar': 422, 'pending_page': 502, 'late': 503}
        assert response.status_code == expected_status[mode], response.json()
        assert len(calls) == (1 if mode == 'late' else 2)
