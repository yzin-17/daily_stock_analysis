"""从当前本地官方资料生成共享ETF收费夹具；使用声明的受控时钟，不联网。"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from src.services.thesis_ledger_etf_fee_sources import SOURCES, BASE
from src.services.thesis_ledger_multimarket_rules import MultimarketRuleProvider
from src.services.thesis_ledger_public_fees import OfficialPublicFeeProvider
from src.services.thesis_ledger_public_fees_contract_v1 import FeeResult

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, required=True, help="共享合同夹具目录")
args = parser.parse_args()
clock = datetime(2026, 11, 6, tzinfo=timezone.utc)
for provider, (market, source, _, filename) in SOURCES.items():
    pack = json.loads((BASE / filename).read_text())
    request = {"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "etf-public-fees-fixture",
        "routeKey": {"kind": "data", "market": market, "assetType": "ETF", "capability": "HISTORICAL_PUBLIC_FEES"},
        "routeTarget": {"providerId": provider, "upstreamSource": source, "routeIndex": 0},
        "symbol": pack["symbol"], "range": pack["coverage"]}
    kind = OfficialPublicFeeProvider if market == "CN" else MultimarketRuleProvider
    response = FeeResult.model_validate(kind(clock=lambda: clock).read(request)).model_dump(mode="json", exclude_unset=True)
    value = {"fixtureClock": clock.isoformat(), "request": request, "response": response}
    (args.output_dir / f"market-{market.lower()}-etf-public-fees-v3.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
