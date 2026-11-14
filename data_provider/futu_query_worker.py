"""只读行情子进程入口，固定查询分支并收敛 SDK 异常。"""

import json
from contextlib import redirect_stdout
import sys
from .futu_lot_size import validate_opend_endpoint
from .security_lot_size import LotSizeError
from .futu_market import RESULT_PREFIX, FutuMarketError, read_futu_quote
from .futu_daily import read_futu_daily
from .futu_minute import read_futu_minute
from .futu_static_info import read_futu_static_info, find_futu_instrument
from .exact_daily_contract import ExactDailyError


def _read(request):
    host, port = validate_opend_endpoint(request.get("host"), request.get("port"))
    import futu
    if request.get("operation") == "quote":
        result = read_futu_quote(futu, host, port, request.get("symbol"), request.get("instrumentType"))
    elif request.get("operation") == "instrument-search":
        result = {"items": find_futu_instrument(futu, host, port, request.get("symbol"))}
    elif request.get("operation") == "static-info":
        result = read_futu_static_info(futu, host, port, request.get("symbol"), request.get("instrumentType"))
    elif request.get("operation") == "daily":
        frame = read_futu_daily(futu, host, port, request)
        attrs = {**frame.attrs, "fieldAvailability": {k: bool(v) for k, v in frame.attrs["fieldAvailability"].items()}}
        result = {"rows": frame[["date", "open", "high", "low", "close", "volume", "amount"]].to_dict(orient="records"),
                  "attrs": attrs}
    elif request.get("operation") == "minute":
        frame = read_futu_minute(futu, host, port, request)
        result = {"rows": frame.to_dict(orient="records"), "attrs": frame.attrs}
    else:
        raise FutuMarketError("invalid_request", "富途查询未匹配已接入的行情操作")
    return result


def main():
    try:
        # SDK 日志进入 stderr，stdout 仅承载唯一 JSON 响应。
        with redirect_stdout(sys.stderr):
            result = _read(json.load(sys.stdin))
    except ImportError:
        result = {"error": "dependency_missing", "message": "DSA 未安装富途 OpenAPI SDK"}
    except (FutuMarketError, ExactDailyError, LotSizeError) as error:
        result = {"error": error.code, "message": str(error)}
    except Exception:
        result = {"error": "upstream_failure", "message": "OpenD 未就绪或行情读取失败"}
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
