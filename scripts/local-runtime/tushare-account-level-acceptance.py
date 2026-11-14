"""目标镜像中的账号权限验收，仅在临时目录写入合成账号。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


CHILD = r'''
import json
import logging
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

logging.disable(logging.CRITICAL)
network_attempts = []
def blocked(*args, **kwargs):
    network_attempts.append(True)
    raise AssertionError("验收禁止出站请求")
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked

assert Path(os.environ["DATABASE_PATH"]).resolve().parent.name.startswith("tl-account-level-")
assert Path(os.environ["DATABASE_PATH"]).resolve().parent.parent == Path("/tmp")
import src.services.thesis_ledger_control as control
import src.services.thesis_ledger_provider_runtime as runtime_module
from src.services.thesis_ledger_account_access_configuration import configure_provider
import data_provider.tushare_fetcher as fetcher

control._environment_credential_values = lambda _: {}
store = control.ThesisLedgerControlStore()
runtime = runtime_module.ThesisLedgerProviderRuntime(store)
runtime_module.get_thesis_ledger_runtime = lambda: runtime
calls = []
def synthetic_post(url, **options):
    assert sys.argv[1] == "save", "重启恢复不能触发接口检查"
    assert url == "https://synthetic.example/api"
    assert options["json"]["token"] == "synthetic-target-token"
    calls.append(options["json"]["api_name"])
    return SimpleNamespace(status_code=200, text=json.dumps({"code": 0, "msg": None,
        "data": {"fields": ["ts_code"], "items": []}}))
fetcher.requests.post = synthetic_post

if sys.argv[1] == "save":
    configure_provider(store, "tushare", {"contractVersion": 3, "consumer": "thesis-ledger",
        "requestId": "synthetic-target-check", "enabled": False,
        "credentials": {"method": "token", "values": {"token": "synthetic-target-token"}},
        "settings": {"accountLevel": {"points": 120, "independentApis": []}}})

provider = next(p for p in store.provider_registry() if p["providerId"] == "tushare")
assert provider["settings"]["accountLevel"] == {"points": 120, "independentApis": []}
assert provider["enabled"] is False
entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["target"]["providerId"] == "tushare"]
assert len(entries) == 6
states = [e["accountAccess"]["state"] for e in entries]
assert states.count("allowed") == 1 and states.count("not_included") == 5
assert all(e["accountAccess"]["attempted"] is False for e in entries
           if e["accountAccess"]["state"] == "not_included")
assert all(e["accountAccess"]["persisted"] is True for e in entries)
assert calls == (["daily"] if sys.argv[1] == "save" else [])
assert not network_attempts
print(json.dumps({"phase": sys.argv[1], "calls": calls, "states": states,
    "points": 120, "configVersion": provider["configVersion"],
    "checkedAt": entries[0]["accountAccess"]["checkedAt"], "persisted": True,
    "networkRequests": len(network_attempts)}))
'''


def main():
    with tempfile.TemporaryDirectory(prefix="tl-account-level-", dir="/tmp") as directory:
        env = {**os.environ, "DATABASE_PATH": str(Path(directory) / "isolated.sqlite"),
               "THESIS_LEDGER_DSA_SECRET_KEY": "synthetic-target-master-key",
               "THESIS_LEDGER_DSA_SECRET_KEY_VERSION": "v1",
               "THESIS_LEDGER_DSA_SECRET_KEY_PREVIOUS": "",
               "TUSHARE_HTTP_URL": "https://synthetic.example/api", "TUSHARE_API_KEY": "",
               "THESIS_LEDGER_FIXTURE_MODE": "false", "PYTHONDONTWRITEBYTECODE": "1"}
        phases = []
        for phase in ("save", "recover"):
            result = subprocess.run([sys.executable, "-B", "-c", CHILD, phase], env=env,
                                    text=True, capture_output=True, timeout=45)
            assert result.returncode == 0, "目标隔离阶段失败：" + phase
            phases.append(json.loads(result.stdout))
        assert phases[0]["checkedAt"] == phases[1]["checkedAt"], "重启后检查时间变化"
        assert phases[0]["configVersion"] == phases[1]["configVersion"], "重启后配置变化"
    assert not Path(directory).exists(), "临时目录未清理"
    return {"passed": True, "phases": phases, "cleaned": True,
            "databaseScope": "temporary-synthetic", "realProviderRequests": 0}


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except Exception as error:
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "reason": str(error) if isinstance(error, AssertionError) else "目标隔离验收失败"}))
        sys.exit(1)
