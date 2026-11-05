"""在物理数据环境中启动真实 server:app；所有非本地连接立即失败。"""

import importlib.metadata
import importlib.util
import json
import multiprocessing
import os
import socket
import sys
from threading import Thread


def install_network_boundary():
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex
    resolve = socket.getaddrinfo

    def check(address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("数据验收拒绝非本地连接")

    def connect(sock, address):
        check(address)
        return original(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        check((host,))
        return resolve(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo


# spawn 子进程重新执行模块时同样安装边界。
install_network_boundary()


def emit(kind, **facts):
    print("DSA_RUNTIME:" + json.dumps({"kind": kind, **facts}, ensure_ascii=False), flush=True)


def visible(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def run():
    from tests.data_runtime_environment import FORBIDDEN_DISTRIBUTIONS, FORBIDDEN_MODULES

    models = {name: visible(name) for name in FORBIDDEN_MODULES}
    installed = sorted(item.metadata["Name"] for item in importlib.metadata.distributions())
    assert not any(models.values()), models
    assert not any(name.lower().replace("_", "-").startswith(FORBIDDEN_DISTRIBUTIONS) for name in installed)
    sdks = ("akshare", "baostock", "efinance", "futu", "longbridge", "pytdx", "requests",
            "serpapi", "tavily", "tickflow", "tushare", "yfinance")
    assert all(visible(name) for name in sdks)

    import uvicorn
    from server import app

    cases = []

    cleanup = None
    scenario = os.environ.get("DSA_DATA_RUNTIME_SCENARIO")
    if scenario:
        from tests.data_runtime_scenarios import configure
        cases, cleanup = configure(scenario)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    config = uvicorn.Config(app, log_level="critical", access_log=False, timeout_graceful_shutdown=5)
    service = uvicorn.Server(config)

    def stop():
        sys.stdin.readline()
        service.should_exit = True

    thread = Thread(target=stop, daemon=True, name="data-acceptance-stop")
    thread.start()
    emit("bound", port=listener.getsockname()[1], models=models, sdks=list(sdks),
         packages=len(installed), cases=cases)
    try:
        service.run(sockets=[listener])
    finally:
        listener.close()
        if cleanup is not None:
            cleanup()
    thread.join(2)
    forbidden = FORBIDDEN_MODULES + (
        "src.config", "src.auth", "src.agent", "src.llm", "src.core.pipeline", "src.notification",
        "src.storage", "src.stock_analyzer", "src.analyzer", "src.scheduler", "src.report_language",
        "src.services.system_config_service", "src.services.runtime_scheduler", "api.app", "api.v1",
    )
    loaded = sorted(
        name for name in sys.modules if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    )
    facts = {"forbiddenImports": loaded, "children": len(multiprocessing.active_children()),
             "lifecycleActive": hasattr(app.state, "data_lifecycle_active"),
             "oauthActive": hasattr(app.state, "provider_oauth_manager")}
    emit("stopped", **facts)
    assert facts == {"forbiddenImports": [], "children": 0, "lifecycleActive": False, "oauthActive": False}


if __name__ == "__main__":
    run()
