"""只读核对现有富途/HiThink 凭据与筹码入口；不保存或输出凭据。"""

import contextlib
import io
import json
import logging
from pathlib import Path
import sqlite3
import sys

import requests


def read_snapshot(provider_id):
    from src.services.thesis_ledger_control import ThesisLedgerControlStore, _database_path

    class ReadOnlyStore(ThesisLedgerControlStore):
        def __init__(self):
            self.database_path = _database_path()

        @contextlib.contextmanager
        def _connect(self):
            path = Path(self.database_path).resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(path, uri=True)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA query_only = ON")
                yield connection
            finally:
                connection.close()

    return ReadOnlyStore().provider_credential_snapshot(provider_id)


def request_summary(url, *, headers=None, params=None):
    try:
        response = requests.get(url, headers=headers, params=params, timeout=(3, 6), allow_redirects=False)
    except requests.RequestException:
        return {"result": "network_failure"}
    summary = {"httpStatus": response.status_code}
    try:
        payload = response.json()
    except ValueError:
        return {**summary, "result": "non_json"}
    if not isinstance(payload, dict):
        return {**summary, "result": "invalid_envelope"}
    # 禁止输出错误文本、原始响应及请求头，仅返回数值状态和公开字段名。
    code = payload.get("code", payload.get("ret_code"))
    summary["businessCode"] = code if isinstance(code, int) and not isinstance(code, bool) else None
    data = payload.get("data")
    if isinstance(data, dict):
        allowed = {"latest_date", "dates", "date", "shape", "item", "items", "pagination",
                   "avg_cost", "profit_ratio", "seventy_begin", "seventy_end", "ninety_begin", "ninety_end"}
        summary["dataFields"] = sorted(set(data) & allowed)
        for name in ("dates", "items", "item"):
            if isinstance(data.get(name), list):
                summary[name + "Count"] = len(data[name])
    summary["result"] = "success" if response.status_code == 200 and code == 0 else "rejected"
    return summary


def main():
    result = {"readOnly": True}
    for provider in ("hithink", "futu"):
        try:
            snapshot = read_snapshot(provider)
        except Exception:
            result[provider] = {"credentialState": "unavailable"}
            continue
        row = {"credentialSource": snapshot.source, "credentialMethod": snapshot.method,
               "configVersion": snapshot.config_version, "configured": bool(snapshot.values)}
        result[provider] = row
        if provider == "hithink":
            key = snapshot.values.get("apiKey")
            if not key:
                row["result"] = "not_configured"
                continue
            headers = {"X-api-key": key, "Accept": "application/json"}
            row["quoteControl"] = request_summary(
                "https://fuyao.aicubes.cn/api/a-share/prices/snapshot",
                headers=headers, params={"thscodes": "600036.SH"},
            )
            row["chipShapeDates"] = request_summary(
                "https://fuyao.aicubes.cn/api/a-share/special-data/chip-shape-dates", headers=headers,
            )
        else:
            logging.disable(logging.CRITICAL)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    import futu
                row["sdkChipMethods"] = sorted(
                    name for name in dir(futu.OpenQuoteContext) if "chip" in name.lower() or "cyq" in name.lower()
                )
                row["sdkVersion"] = getattr(futu, "__version__", None)
            except ImportError:
                row["sdkState"] = "not_installed"
            row["restCredentialConfigured"] = snapshot.method in {"oauth", "api_key"}
            if not row["restCredentialConfigured"]:
                row["restAnonymousControl"] = request_summary(
                    "https://webapi.futunn.com/api/v1.0/quote/SH.600036/chip-distribution")
                row["result"] = "rest_authentication_missing"
    print("CHIP_PROVIDER_PROBE=" + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    # 项目根目录是运行入口；容器内以 python -B - 从 stdin 执行时不依赖 __file__。
    sys.path.insert(0, str(Path.cwd()))
    main()
