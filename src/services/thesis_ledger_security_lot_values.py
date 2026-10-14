"""港股每手当前参考的规范证券、整数与观察摘要。"""

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re


class LotSizeError(ValueError):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def hk_symbol(value):
    text = str(value or "").strip().upper()
    if text.startswith("HK."):
        text = text[3:]
    elif text.endswith(".HK"):
        text = text[:-3]
    else:
        raise LotSizeError("invalid_request", "每手股数查询需要港股代码，例如 00700.HK")
    if not re.fullmatch(r"[0-9]{1,5}", text) or int(text) <= 0:
        raise LotSizeError("invalid_request", "港股代码无效")
    return text.zfill(5) + ".HK"


def positive_lot(value):
    try:
        if isinstance(value, bool):
            raise ValueError("boolean")
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise ValueError("not a positive integer")
        result = str(int(number))
        if len(result) > 15:
            raise ValueError("out of range")
        return result
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise LotSizeError("invalid_response", "来源每手股数不是有效正整数") from None


def reference_digest(symbol, shares, provider, observed_at):
    return hashlib.sha256(json.dumps({"symbol": symbol, "sharesPerLot": shares,
        "historicalChanges": "unverified", "provider": provider, "observedAt": observed_at},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
