"""隔离 HTTP 验收入口：受控抓取生成两版本，重开 SQLite 后提供真实 Control API。"""

import socket
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
import httpx
import uvicorn

from api.thesis_ledger_rule_monitor import router
from src.services.rule_monitor_fetch import fetch_document
from src.services.rule_monitor_registry import Source
from src.services.rule_monitor_runtime import RuleMonitorRuntime
from src.services.rule_monitor_snapshot import text_snapshot
from src.services.rule_monitor_store import RuleMonitorStore

directory = tempfile.TemporaryDirectory(prefix="rule-monitor-http-qa-")
path = Path(directory.name) / "monitor.sqlite"
identity = "official-rules/cn-stock-auction-fees"
url = "https://www.sse.com.cn/rule"
current = ["0.1%"]


def serve(request):
    return httpx.Response(200, text='<main><p>' + "受控收费规则正文用于跨进程验收。" * 10
        + f'</p><p>收费比例{current[0]}</p></main>', headers={"Content-Type": "text/html"})


async def fetch(url, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        return await fetch_document(url, client=client, **kwargs)


source = Source(identity, "CN", "受控来源", "isolated-snapshot-v1", "a" * 64,
    ({"id": "rule", "title": "受控收费资料", "url": url},), text_snapshot("json", '{"fee": 1}'))
monitor = RuleMonitorRuntime(RuleMonitorStore(path), registry=lambda: {identity: source}, fetch=fetch)
for rate in ["0.1%", "0.2%"]:
    current[0] = rate
    monitor.enqueue(str(uuid4()), [identity])
    assert monitor.run_pending_once()

app = FastAPI()
app.state.rule_monitor = RuleMonitorRuntime(RuleMonitorStore(path), registry=lambda: {identity: source})
app.include_router(router, prefix="/api/v3")

if __name__ == "__main__":
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    print(f"snapshot-listen:{listener.getsockname()[1]}", flush=True)
    uvicorn.run(app, fd=listener.fileno(), log_level="warning")
