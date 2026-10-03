"""多窗口行情的跨语言内容编码；输入必须先通过多窗口响应校验。"""

from copy import deepcopy
import hashlib
import json
import math
import struct
from typing import Any

MULTI_WINDOW_PROTOCOL = "market-multi-window-content-v1"


def _encode(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("多窗口指纹不接受非有限数值")
        return ["number", struct.pack(">d", 0.0 if number == 0 else number).hex()]
    if isinstance(value, list):
        return ["array", [_encode(child) for child in value]]
    if isinstance(value, dict):
        return ["object", [[key, _encode(value[key])] for key in sorted(value)]]
    raise ValueError("多窗口指纹只接受 JSON 值")


def canonical_multi_window_encoding(response: dict) -> str:
    value = deepcopy(response)
    value["requestId"] = "multi-window-transport"
    value["inputFingerprint"] = "multi-window-content-v1"
    value["sourcePriceBasis"]["revision"] = {
        "origin": "local-observation", "contentHash": "0" * 64,
    }
    for observation in value["windowObservations"]:
        observation["response"]["requestId"] = "multi-window-transport"
    return json.dumps([MULTI_WINDOW_PROTOCOL, _encode(value)],
                      ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def multi_window_content_hash(response: dict) -> str:
    return hashlib.sha256(canonical_multi_window_encoding(response).encode("utf-8")).hexdigest()
