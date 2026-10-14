"""既有研究帧桥接按精确绑定投影；来源私有元信息由来源解析。"""
from collections.abc import Mapping

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import DailyBinding
from src.services.thesis_ledger_capability_contracts import ResearchFrameContext, ResearchFrameProjection


def executable_research_frame(request, frame, on_error=None):
    try:
        return _executable_research_frame(request, frame)
    except (ValueError, KeyError, TypeError) as exc:
        if on_error is not None:
            on_error("invalid_response", 502, str(request["requestId"]))
        raise exc


def _executable_research_frame(request, frame):
    key = request.get("routeKey", {})
    target = request.get("routeTarget", {})
    if (request.get('tradabilityMode') != 'assume-untradable-no-bar'
            or not isinstance(key, Mapping) or not isinstance(target, Mapping)):
        return frame
    try:
        binding = CAPABILITY_SOURCES.resolve(key, target)
    except TypeError:
        return frame
    if not isinstance(binding, DailyBinding) or binding.research_frame_projection is None:
        return frame
    result = binding.research_frame_projection(ResearchFrameContext(str(request.get('symbol', '')), frame))
    if not isinstance(result, ResearchFrameProjection):
        raise ValueError('来源研究投影未返回标准桥接结果')
    return result.frame
