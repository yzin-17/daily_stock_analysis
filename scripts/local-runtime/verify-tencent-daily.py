"""腾讯当前精确日线单次只读验收载荷；保留原适配器及资格边界。"""
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import data_provider.tencent_native_daily as native
from data_provider.thesis_ledger_sources.tencent import verify_transport
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_intraday_observation import candidates, read_entry
from src.services.thesis_ledger_probe_store import ReadOnlyProbeStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, classify_provider_exception


plan = json.load(sys.stdin)
report = {"readOnly": True, "requestCount": 0, "observedAt": datetime.now(timezone.utc).isoformat()}
logging.disable(logging.CRITICAL)
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        now = datetime.now(timezone.utc)
        assert now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() == plan["day"]
        report["nativeSha256"] = hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest()
        assert report["nativeSha256"] == plan["nativeSha256"]
        runtime = ThesisLedgerProviderRuntime(store=ReadOnlyProbeStore())
        catalog = runtime.market_route_catalog_v3()
        assert catalog.get("integrity") == "complete"
        entries = [e for e in candidates(catalog, ["CN"], now)
                   if e["target"] == {"providerId": "tencent", "upstreamSource": "tencent"}
                   and e["key"]["timeframe"] == "1d"]
        assert len(entries) == 1
        entry = entries[0]
        report.update(key=entry["key"], target=entry["target"], symbol=plan["symbol"],
                      bindingRevision=CAPABILITY_SOURCES.resolve(entry["key"], entry["target"]).adapter_revision,
                      requestRowLimit=native.REQUEST_ROW_LIMIT, responseRowLimit=native.MAX_ROWS_PER_RESPONSE)
        original_get = native.requests.get

        def observed_get(url, **kwargs):
            assert url == native.TENCENT_NATIVE_DAILY_URL
            report["requestCount"] += 1
            response = original_get(url, **kwargs)
            text = response.text
            record = {"httpStatus": response.status_code, "params": kwargs["params"],
                      "rawSHA256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                      "rawBytes": len(text.encode("utf-8")), "redirectCount": len(response.history),
                      "rawResponse": text}
            prefix = kwargs["params"]["_var"] + "="
            record["wrapperMatches"] = text.startswith(prefix)
            try:
                body = json.loads(text[len(prefix):] if record["wrapperMatches"] else text)
                rows = body.get("data", {}).get("sh600519", {}).get("day", [])
                record.update(businessCode=body.get("code"), rawRows=len(rows),
                              rawFirstDate=rows[0][0] if rows else None,
                              rawLastDate=rows[-1][0] if rows else None)
            except (ValueError, AttributeError, TypeError, IndexError):
                record["rawShape"] = "unrecognized"
            report.setdefault("responses", []).append(record)
            return response

        native.requests.get = observed_get
        original_adapter = runtime._adapter

        def inspected_adapter(*args, **kwargs):
            adapter = original_adapter(*args, **kwargs)
            original_daily = adapter.get_daily_data_for_source

            def inspected_daily(*args, **kwargs):
                frame = original_daily(*args, **kwargs)
                report["verifiedPartitions"] = verify_transport(frame, plan["day"], plan["day"], "none")
                report["retrieval"] = frame.attrs["tencentDailyRetrieval"]
                return frame

            adapter.get_daily_data_for_source = inspected_daily
            return adapter

        runtime._adapter = inspected_adapter
        points = read_entry(runtime, entry, plan["symbol"], now)
        assert len(points) == 1 and points[0]["timestamp"][:10] == plan["day"]
        report.update(status="success", points=points)
except Exception as error:
    report.update(status="failed", errorType=type(error).__name__,
                  errorCode=getattr(error, "code", None) or classify_provider_exception(error))
report["completedAt"] = datetime.now(timezone.utc).isoformat()
print(json.dumps(report, ensure_ascii=False))
