"""使用真实基金披露和行情回放一日估值误差，输出可审计摘要。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import akshare as ak

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.thesis_ledger import _fund_holdings_payload
from data_provider.akshare_fetcher import AkshareFetcher


def main(fund_code: str = "000001", priced_holding_limit: int = 10) -> None:
    """以最近两个正式净值日为锚和目标，按披露持仓估算目标日净值。"""
    fetcher = AkshareFetcher()
    holdings_payload = _fund_holdings_payload(
        fund_code,
        fetcher.get_fund_holdings(fund_code),
        "akshare",
        False,
    )
    nav = fetcher.get_fund_nav_history(fund_code).sort_values("净值日期")
    anchor, target = nav.tail(2).to_dict("records")
    anchor_date = anchor["净值日期"].strftime("%Y%m%d")
    target_date = target["净值日期"].strftime("%Y%m%d")
    contribution = 0.0
    priced_coverage = 0.0
    priced_count = 0
    for holding in holdings_payload["holdings"][:priced_holding_limit]:
        exchange = "sh" if holding["symbol"].endswith(".SH") else "sz"
        try:
            prices = ak.stock_zh_a_daily(
                symbol=f"{exchange}{holding['symbol'][:6]}",
                start_date=anchor_date,
                end_date=target_date,
                adjust="qfq",
            )
        except Exception:  # noqa: BLE001 - 验收输出会通过覆盖率显式反映缺失行情。
            continue
        if prices is None or len(prices) < 2:
            continue
        anchor_close = float(prices.iloc[0]["close"])
        target_close = float(prices.iloc[-1]["close"])
        contribution += float(holding["weight"]) * (target_close / anchor_close - 1)
        priced_coverage += float(holding["weight"])
        priced_count += 1
    estimated_nav = float(anchor["单位净值"]) * (1 + contribution)
    official_nav = float(target["单位净值"])
    print(
        json.dumps(
            {
                "fundSymbol": holdings_payload["fundSymbol"],
                "reportPeriod": holdings_payload["reportPeriod"],
                "anchorDate": str(anchor["净值日期"]),
                "targetDate": str(target["净值日期"]),
                "disclosureCoverage": round(
                    sum(float(row["weight"]) for row in holdings_payload["holdings"]),
                    6,
                ),
                "pricedCoverage": round(priced_coverage, 6),
                "pricedHoldingCount": priced_count,
                "estimatedNav": round(estimated_nav, 6),
                "officialNav": official_nav,
                "absolutePercentageError": round(
                    abs(estimated_nav / official_nav - 1) * 100,
                    4,
                ),
                "evidenceVersion": holdings_payload["evidenceVersion"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "000001")
