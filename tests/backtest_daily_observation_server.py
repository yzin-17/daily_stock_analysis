"""供Server跨进程合同验收使用；真实V3路由消费冻结样本，禁止外部网络。"""
import argparse
import json
import os
import socket
import sys
from hashlib import sha256
from threading import Thread
from types import SimpleNamespace

from tests.data_runtime_process import install_network_boundary

install_network_boundary()
os.environ["THESIS_LEDGER_DSA_TOKEN"] = "isolated-price-only-contract"

import uvicorn
import pandas as pd
from fastapi import FastAPI
from api.middlewares.error_handler import add_error_handlers
from api.thesis_ledger import router_v3
import api.thesis_ledger as routes
import src.services.thesis_ledger_provider_runtime as runtime
from tests.test_thesis_ledger_market_tradability_common import sample

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=0)
parser.add_argument("--rollout-sample", choices=("positive-volume", "unknown-volume"))
args = parser.parse_args()


def include_warmup(execution, query):
    if (query.start, query.end) != ("2026-06-23", "2026-07-10"):
        raise AssertionError("分阶段验收只读取固定日期窗口")
    dates = pd.bdate_range(query.start, query.end)
    frame = execution.value.iloc[[0] * len(dates)].reset_index(drop=True)
    frame["date"] = dates
    if args.rollout_sample == "unknown-volume":
        frame.loc[len(frame) - 1, "volume"] = None
    retrieval = frame.attrs["tencentDailyRetrieval"]
    parts = [{**retrieval["partitions"][0], "start": query.start, "end": query.end, "rows": len(frame)}]
    retrieval.update(partitions=parts, revision=sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest())
    frame.attrs["thesis_ledger_v3_pagination"].update(requestedStart=query.start, requestedEnd=query.end)
    execution.value = frame


def execute(query, *_args, **_kwargs):
    _, execution, _, _ = sample()
    if args.rollout_sample:
        include_warmup(execution, query)
        return execution
    case = query.request_id
    if "unknown" in case:
        execution.value.loc[0, "volume"] = None
    elif "zero" in case:
        execution.value.loc[0, "volume"] = 0
    elif "negative" in case:
        execution.value.loc[0, "volume"] = -1
    elif "bad-ohlc" in case:
        execution.value.loc[0, "high"] = 0.5
    return execution


runtime.get_thesis_ledger_runtime = lambda: SimpleNamespace(execute_market_bars_v3=execute)
routes._now_iso = lambda: "2026-11-20T00:00:00+00:00"
app = FastAPI()
app.include_router(router_v3, prefix="/api/v1")
add_error_handlers(app)
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", args.port))
listener.listen(128)
server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))


def stop():
    sys.stdin.readline()
    server.should_exit = True


Thread(target=stop, daemon=True).start()
print(json.dumps({"port": listener.getsockname()[1], "pid": os.getpid()}), flush=True)
try:
    server.run(sockets=[listener])
finally:
    listener.close()
