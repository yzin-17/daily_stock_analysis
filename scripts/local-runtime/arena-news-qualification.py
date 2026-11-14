"""只读核验本机交易竞技场的单页新闻来源。实读需要另行确认，结果不登记为正式准入。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import time

PROJECT = "thesis-ledger-dev"
CONTAINER = PROJECT + "-dsa-1"
SYMBOL = "600036.SH"
LIMIT = 10


def load_news_source():
    from data_provider.hithink_news import HithinkNewsSettings
    from src.services.thesis_ledger_control import ThesisLedgerControlStore, _database_path
    from src.services.thesis_ledger_news_contract_v1 import NewsInstrument
    from src.services.thesis_ledger_news_instruments import resolve_public_news_instrument

    db = Path(_database_path()).resolve()
    if db != Path("/app/data/stock_analysis.db") or not db.is_file():
        raise ValueError("目标数据库不符")

    class ReadOnlyStore(ThesisLedgerControlStore):
        def __init__(self):
            self.database_path = str(db)

        @contextmanager
        def _connect(self):
            con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                yield con
            finally:
                con.close()

    store = ReadOnlyStore()
    identity = resolve_public_news_instrument(
        NewsInstrument(symbol=SYMBOL, market="CN", assetType="STOCK"), str(db))
    snap = store.provider_credential_snapshot("hithink")
    settings = HithinkNewsSettings(snap.values.get("apiKey", ""))
    enabled = any(p["providerId"] == "hithink" and p["enabled"] for p in store.provider_registry())
    if not enabled or not settings.configured() or not identity or identity.symbol != SYMBOL:
        raise ValueError("身份或新闻接入尚未就绪")
    return db, store, snap, settings


def worker(probe):
    from data_provider.hithink_news import fetch_hithink_news
    _, _, _, settings = load_news_source()
    result = {"symbol": SYMBOL, "source": "hithink-news-stock-timeline", "maxItems": LIMIT,
              "upstreamAttempts": 0, "mode": "probe" if probe else "plan"}
    if probe:
        class Observer:
            def send(self, event):
                if event[0] == "attempt":
                    result["upstreamAttempts"] += 1
                elif event[0] == "http_status":
                    result["httpStatus"] = event[1]
        try:
            page = fetch_hithink_news(settings, SYMBOL, Observer(), time.monotonic() + 6,
                                      max_results=LIMIT)
            if len(page.records) > LIMIT:
                raise ValueError("返回条数超过本次范围")
            result.update(status="available", truncated=page.has_more, sha256=page.response_sha256,
                          items=[{"publishedAt": r.published_at, "title": r.title,
                                  "publisher": r.publisher} for r in page.records])
        except Exception as error:
            result.update(status="failed", reason=getattr(error, "code", type(error).__name__))
    return result


def run_host(probe):
    args = ["docker", "inspect", "--format",
            '{{.Name}}|{{index .Config.Labels "com.docker.compose.project"}}|'
            '{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Health.Status}}', CONTAINER]
    if subprocess.check_output(args, text=True).strip() != f"/{CONTAINER}|{PROJECT}|dsa|healthy":
        raise ValueError("目标容器不符")
    if probe:
        root = Path(__file__).resolve().parents[3] / "thesis-ledger/.recovery-artifacts/arena-news"
        root.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).date()
        stem = f"{SYMBOL}-{day}"
        with (root / f"{stem}.reserved.json").open("x") as claim:
            json.dump({"symbol": SYMBOL, "limit": LIMIT, "when": datetime.now(timezone.utc).isoformat()}, claim)
    cmd = ["docker", "exec", "-i", CONTAINER, "python", "-", "--worker"]
    if probe:
        cmd.append("--probe")
    proc = subprocess.run(cmd, input=Path(__file__).read_text(), capture_output=True, text=True, timeout=20)
    if proc.returncode:
        raise RuntimeError("资格核验进程未完成，不自动重发")
    result = json.loads(proc.stdout)
    if probe:
        with (root / f"{stem}.result.json").open("x") as receipt:
            json.dump(result, receipt, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--probe", action="store_true", help="执行已获授权的一次实读")
    options = parser.parse_args()
    print(json.dumps((worker if options.worker else run_host)(options.probe), ensure_ascii=False))
