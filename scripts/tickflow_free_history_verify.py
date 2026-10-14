"""有界免费历史验收：保存原业务数据，独立核对规范化，参考请求可按需关闭。"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_provider.thesis_ledger_sources.tickflow_free_history import FreeHistoryConnection, daily, BASE_URL
from src.services.thesis_ledger_capability_contracts import DailyQuery, ReadContext

SYMBOLS = ("600519.SH", "000001.SZ")
START, END = "2025-06-23", "2025-07-04"


def independent_rows(payload):
    rows = []
    for index, timestamp in enumerate(payload["timestamp"]):
        day = datetime.fromtimestamp(timestamp / 1000, ZoneInfo("Asia/Shanghai")).date().isoformat()
        if START <= day <= END:
            rows.append({"date": day, **{field: payload[field][index] if field in payload else None
                         for field in ("open", "high", "low", "close", "volume", "amount")}})
    assert len({row["date"] for row in rows}) == len(rows)
    return sorted(rows, key=lambda row: row["date"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--references", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"endpoint": BASE_URL, "symbols": SYMBOLS, "window": [START, END],
                          "maximumRequests": 14, "retries": 0}, ensure_ascii=False))
        return
    capture = {"version": "tickflow-free-history-verification-v1", "endpoint": BASE_URL,
               "startedAt": datetime.now(timezone.utc).isoformat(), "requests": 0,
               "instruments": {}, "observations": [], "references": {}, "errors": []}
    def save():
        Path(args.output).write_text(json.dumps(capture, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for symbol in SYMBOLS:
            for adjustment in ("none", "qfq", "hfq"):
                connection = FreeHistoryConnection(2.25)
                query = DailyQuery.model_validate({"symbol": symbol, "instrument_type": "STOCK",
                    "adjustment": adjustment, "start": START, "end": END, "request_id": "finite-validation"})
                raw = {}
                original = connection.client.klines.get
                original_instrument = connection.client.instruments.get
                def observe_instrument(ticker):
                    capture["requests"] += 1
                    assert capture["requests"] <= 14
                    instrument = original_instrument(ticker)
                    capture["instruments"][ticker] = instrument
                    save()
                    return instrument
                def observe(*positional, **options):
                    capture["requests"] += 1
                    assert capture["requests"] <= 14
                    result = original(*positional, **options)
                    raw.update(result)
                    return result
                connection.client.klines.get = observe
                connection.client.instruments.get = observe_instrument
                frame = daily(query, ReadContext(connection)).frame
                expected = independent_rows(raw)
                actual = frame.to_dict(orient="records")
                assert [row["date"] for row in actual] == [row["date"] for row in expected]
                for left, right in zip(actual, expected):
                    for field in ("open", "high", "low", "close", "volume", "amount"):
                        if right[field] is None:
                            assert left[field] is None
                        else:
                            assert Decimal(str(left[field])) == Decimal(str(right[field]))
                observation = {"symbol": symbol, "adjustment": adjustment, "observedAt": datetime.now(timezone.utc).isoformat(),
                    "queryIdentity": "request-bound; instrument metadata separately verified", "raw": raw,
                    "normalized": actual, "fingerprint": frame.attrs["responseSha256"],
                    "independentNormalizationMatch": True, "publicVolumeUnit": "unknown", "publicAmountUnit": "unknown"}
                capture["observations"].append(observation)
                save()
                print(json.dumps({"symbol": symbol, "adjustment": adjustment, "rows": len(actual),
                                  "normalizationMatch": True}, ensure_ascii=False))
            if args.references:
                from data_provider.thesis_ledger_sources.tencent import factory, daily as reference_daily
                capture["requests"] += 1
                assert capture["requests"] <= 14
                value = reference_daily(query.model_copy(update={"adjustment": "none"}), ReadContext(factory(ReadContext(None))))
                capture["references"][symbol] = {"source": "tencent", "rows": json.loads(value.frame.to_json(orient="records", date_format="iso")),
                                                   "metadata": value.frame.attrs}
                save()
        capture["completedAt"] = datetime.now(timezone.utc).isoformat()
        save()
    except Exception as error:
        capture["errors"].append({"type": type(error).__name__, "message": str(error)[:400]})
        save()
        raise


if __name__ == "__main__":
    main()
