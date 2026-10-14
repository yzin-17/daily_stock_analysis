"""来源研究帧投影沿唯一绑定执行，不透传原请求或私有字段。"""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.thesis_ledger_sources.yfinance import SOURCE
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services import thesis_ledger_zero_volume_observations as projection


def sample():
    return ({'requestId': 'projection', 'symbol': '00147.HK', 'tradabilityMode': 'assume-untradable-no-bar',
             'routeKey': {'kind': 'bar', 'market': 'HK', 'assetType': 'STOCK', 'capability': 'DAILY_BAR',
                          'timeframe': '1d', 'adjustment': 'qfq'},
             'routeTarget': {'providerId': 'yfinance', 'upstreamSource': 'yfinance', 'routeIndex': 0}},
            pd.DataFrame([{'date': '2025-08-07', 'open': .2, 'high': .3, 'low': .1, 'close': .25, 'volume': 0, 'amount': None}]))


def test_new_source_projection_uses_original_entry_once(monkeypatch):
    from src.services.thesis_ledger_capability_contracts import ResearchFrameProjection
    request, frame = sample()
    calls = []

    def project(context):
        assert context.symbol == request['symbol'] and context.frame is frame
        assert not hasattr(context, 'request') and not hasattr(context, 'adapter')
        calls.append(context)
        return ResearchFrameProjection(context.frame)

    original = next(binding for key, target, binding in CapabilityRegistry((SOURCE,)).routes()
                    if key == request['routeKey'] and target['upstreamSource'] == 'yfinance')
    registry = CapabilityRegistry((replace(SOURCE, provider_id='new-projection-source',
        bindings=(replace(original, research_frame_projection=project),)),))
    monkeypatch.setattr(projection, 'CAPABILITY_SOURCES', registry)
    request['routeTarget']['providerId'] = 'new-projection-source'
    assert projection.executable_research_frame(request, frame) is frame
    assert len(calls) == 1
    request['routeKey']['adjustment'] = 'none'
    assert projection.executable_research_frame(request, frame) is frame and len(calls) == 1


@pytest.mark.parametrize('mode', [None, 'require-known-status'])
def test_other_mode_does_not_even_select_source(monkeypatch, mode):
    request, frame = sample()
    request['tradabilityMode'] = mode
    def forbidden(*args):
        raise AssertionError('非原模式不得投影')
    monkeypatch.setattr(projection, 'CAPABILITY_SOURCES', SimpleNamespace(resolve=forbidden))
    assert projection.executable_research_frame(request, frame) is frame


@pytest.mark.parametrize('result', [None, {}, 1])
def test_untyped_projection_rejected_with_original_error_callback(monkeypatch, result):
    request, frame = sample()
    monkeypatch.setattr(projection, 'CAPABILITY_SOURCES', SimpleNamespace(
        resolve=lambda *args: replace(SOURCE.bindings[0], research_frame_projection=lambda context: result)))
    errors = []
    with pytest.raises(ValueError):
        projection.executable_research_frame(request, frame, lambda *args: errors.append(args))
    assert errors == [('invalid_response', 502, 'projection')]


def test_invalid_frame_cannot_be_wrapped_as_standard_projection():
    from src.services.thesis_ledger_capability_contracts import ResearchFrameProjection
    with pytest.raises(ValueError):
        ResearchFrameProjection({'rows': []})


def test_registration_rejects_non_callable_projection():
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, bindings=(replace(SOURCE.bindings[0], research_frame_projection=1),)),))


def test_original_yahoo_projection_preserves_frame_and_repeat_identity():
    request, frame = sample()
    frame.attrs['responseSha256'] = 'a' * 64
    result = projection.executable_research_frame(request, frame)
    assert len(result) == 0 and len(frame) == 1
    assert 'excluded_zero_volume_observations' not in frame.attrs
    assert projection.executable_research_frame(request, result) is result
    assert result.attrs['excluded_zero_volume_observations']['2025-08-07']['sourceResponseSha256'] == 'a' * 64


def test_hk_none_only_original_symbol_and_us_none_keeps_frame():
    request, frame = sample()
    frame.attrs['responseSha256'] = 'a' * 64
    request['routeKey']['adjustment'] = 'none'
    request['routeTarget']['upstreamSource'] = 'yahoo-hk-share-window'
    assert len(projection.executable_research_frame(request, frame)) == 0
    request['symbol'] = '00700.HK'
    assert projection.executable_research_frame(request, frame) is frame
    request['symbol'] = 'GME.US'
    request['routeKey']['market'] = 'US'
    request['routeTarget']['upstreamSource'] = 'yahoo-gme-share-window'
    assert projection.executable_research_frame(request, frame) is frame
