"""宿主复测入口的只读载荷；复用目标容器已部署的精确读取模块。"""
import contextlib
from datetime import datetime, timezone
import io
import json
import logging
import sys

from src.services.thesis_ledger_intraday_observation import candidates, read_entry, sample_batch
from src.services.thesis_ledger_probe_store import ReadOnlyProbeStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime

entry = json.loads(sys.argv[1])
diagnostics = []


def reader(runtime, current, symbol, now):
    try:
        return read_entry(runtime, current, symbol, now)
    except Exception as error:
        # 不记录供应商异常文本、请求URL或凭证，只保留异常类型链。
        seen = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            diagnostics.append({"type": type(error).__name__, "module": type(error).__module__})
            error = error.__cause__ or error.__context__
        raise


logging.disable(logging.CRITICAL)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    runtime = ThesisLedgerProviderRuntime(store=ReadOnlyProbeStore())
    original = runtime.market_route_catalog_v3

    def selected_catalog():
        catalog = original()
        return {**catalog, "entries": [v for v in catalog["entries"]
                                      if v["key"] == entry["key"] and v["target"] == entry["target"]]}

    runtime.market_route_catalog_v3 = selected_catalog
    eligible = list(candidates(selected_catalog(), [entry["key"]["market"]], datetime.now(timezone.utc)))
    report = sample_batch(runtime, [entry["key"]["market"]], {}, reader=reader)
    report.update(ineligible=not bool(eligible), diagnostics=diagnostics)
print(json.dumps(report, ensure_ascii=False))
