"""富途 OpenD 只读静态资料；不创建交易上下文。"""

from datetime import datetime, timezone
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .security_lot_size import LotSizeError, hk_symbol, lot_reference, positive_lot

RESULT_PREFIX = "THESIS_LOT_SIZE="


def validate_opend_endpoint(host, port):
    host = str(host or "").strip()
    port_text = str(port or "").strip()
    # 接受 IP、主机名和 Docker 的 host.docker.internal，不接受 URL、路径或凭证。
    if (not host or len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9_.:\-]+", host)
            or not re.fullmatch(r"[0-9]{1,5}", port_text) or not 1 <= int(port_text) <= 65535):
        raise LotSizeError("invalid_request", "请填写有效的 OpenD 主机地址和 1–65535 端口")
    return host, int(port_text)


def read_futu_static(sdk, host, port, symbol):
    canonical = hk_symbol(symbol)
    code = "HK." + canonical[:-3]
    context = None
    try:
        context = sdk.OpenQuoteContext(host=host, port=port)
        status, rows = context.get_stock_basicinfo(
            sdk.Market.HK, stock_type=sdk.SecurityType.STOCK, code_list=[code],
        )
        if status != sdk.RET_OK:
            raise LotSizeError("upstream_failure", "富途静态资料读取失败，请检查 OpenD 登录和行情权限")
        if not hasattr(rows, "iterrows") or len(rows) != 1:
            raise LotSizeError("not_covered", "富途未返回唯一的证券静态资料")
        row = next(rows.iterrows())[1]
        if str(row.get("code")) != code or str(row.get("stock_type")) != "STOCK":
            raise LotSizeError("invalid_response", "富途静态资料标的或证券类型不匹配")
        return positive_lot(row.get("lot_size"))
    finally:
        if context is not None:
            context.close()


class FutuLotSizeAdapter:
    """按连接配置缓存当前股数；隔离 SDK 的长连接，超时可真正终止。"""

    def __init__(self, *, host, port):
        self.host, self.port = validate_opend_endpoint(host, port)
        self._cache = {}

    def get_security_lot_size(self, symbol):
        canonical = hk_symbol(symbol)
        cached = self._cache.get(canonical)
        if cached and time.monotonic() - cached[0] < 86400:
            return {**cached[1], "source": dict(cached[1]["source"])}
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "data_provider.futu_lot_size"],
                input=json.dumps({"host": self.host, "port": self.port, "symbol": canonical}),
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                timeout=4.5, check=False,
            )
            results = [line[len(RESULT_PREFIX):] for line in completed.stdout.splitlines()
                       if line.startswith(RESULT_PREFIX)]
            if completed.returncode != 0 or len(results) != 1:
                raise LotSizeError("upstream_failure", "富途静态资料进程未返回有效结果")
            value = json.loads(results[0])
        except subprocess.TimeoutExpired:
            raise LotSizeError("timeout", "连接 OpenD 或读取静态资料超时，请检查连接或备用源") from None
        except (OSError, ValueError) as error:
            if isinstance(error, LotSizeError):
                raise
            raise LotSizeError("upstream_failure", "富途静态资料读取失败") from None
        if value.get("error"):
            raise LotSizeError(value["error"], value["message"])
        reference = lot_reference(canonical, value.get("sharesPerLot"), "futu", value["observedAt"])
        self._cache[canonical] = (time.monotonic(), reference)
        return {**reference, "source": dict(reference["source"])}


def main():
    try:
        request = json.load(sys.stdin)
        host, port = validate_opend_endpoint(request.get("host"), request.get("port"))
        import futu
        shares = read_futu_static(futu, host, port, request.get("symbol"))
        result = {"sharesPerLot": shares, "observedAt": datetime.now(timezone.utc).isoformat()}
    except ImportError:
        result = {"error": "dependency_missing", "message": "DSA 未安装富途 OpenAPI SDK"}
    except LotSizeError as error:
        result = {"error": error.code, "message": str(error)}
    except Exception:
        result = {"error": "upstream_failure", "message": "OpenD 未就绪或静态资料读取失败"}
    print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
