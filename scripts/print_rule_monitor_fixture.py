"""用当前来源目录与受控网络生成跨语言监测合同夹具，不读取外网。"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from src.services.rule_monitor_runtime import RuleMonitorRuntime, VERSION
from src.services.rule_monitor_store import RuleMonitorStore

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
clock = [datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp()]
generation = [1]


async def read_document(url, **kwargs):
    value = sha256((url + ":controlled:" + str(generation[0])).encode()).hexdigest()
    return {"rawHash": value, "contentHash": value}


with TemporaryDirectory(prefix="rule-monitor-contract-") as directory:
    runtime = RuleMonitorRuntime(RuleMonitorStore(Path(directory) / "monitor.sqlite", clock=lambda: clock[0]), fetch=read_document)
    selected = ["official-hk-rules/hk-stock-public-fees"]
    request = {"version": VERSION, "requestId": "00000000-0000-4000-8000-000000000015", "sourceIds": selected}
    queued = {"version": VERSION, "run": runtime.enqueue(request["requestId"], selected)}
    runtime.run_pending_once()
    first = runtime.status()
    generation[0] = 2
    clock[0] += 60
    runtime.enqueue("00000000-0000-4000-8000-000000000016", selected)
    runtime.run_pending_once()
    changed = runtime.status()
    document = next(item for item in changed["observations"] if item["kind"] == "document")
    versions = {"version": VERSION, "kind": "document", "identity": document["identity"],
        "versions": runtime.observations.versions("document", document["identity"])}
    result = {"fixtureKind": "受控网络与时钟，无外部读取", "request": request,
        "queued": queued, "status": first, "changedStatus": changed, "versions": versions}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
