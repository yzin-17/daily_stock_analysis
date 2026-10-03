#!/usr/bin/env python3
"""显式采集基金 NAV 来源证据，并可选择写入有界研究准入。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.services.thesis_ledger_control import ThesisLedgerControlStore, _database_path
from src.services.thesis_ledger_nav_research_admission import (
    NavResearchAdmissionError,
    prepare_nav_research_evidence,
    record_nav_research_admission,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="核验真实东方财富基金身份、净值原文和所需日期范围；默认仅保存证据，不写入准入。",
    )
    parser.add_argument(
        "--fund", required=True, action="append", metavar="SYMBOL:TYPE",
        help="显式基金类型映射，可重复传入，例如 161725.OF:domestic 或 118001.OF:qdii",
    )
    parser.add_argument("--research-mode", required=True, choices=("research-assumption",))
    parser.add_argument("--research-decision", required=True, help="调用方明确的研究假设说明")
    parser.add_argument("--start", required=True, help="执行窗口开始日 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="执行窗口结束日 YYYY-MM-DD")
    parser.add_argument("--warmup-periods", required=True, type=int, help="执行前所需真实 NAV 估值条数")
    parser.add_argument("--tail-trading-days", required=True, type=int, help="执行窗口后的真实处理日条数")
    parser.add_argument("--evidence-dir", required=True, type=Path, help="权限受限的证据输出目录")
    parser.add_argument("--recorded-by", required=True, help="登记责任人标识")
    parser.add_argument("--valid-hours", required=True, type=int, help="准入有效小时数，最多 24")
    parser.add_argument("--apply", action="store_true", help="显式写入当前 DATABASE_PATH 对应的 DSA Store")
    return parser


def _existing_store_path() -> Path:
    configured = _database_path()
    if configured == ":memory:":
        raise NavResearchAdmissionError("--apply requires the existing file-backed DATABASE_PATH")
    path = Path(configured).expanduser().resolve()
    if not path.is_file():
        raise NavResearchAdmissionError(
            "--apply refused because DATABASE_PATH does not point to an existing DSA database"
        )
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise NavResearchAdmissionError("--apply could not read the configured DSA database") from error
    required = {
        "thesis_ledger_policy_state",
        "thesis_ledger_route_admission_v3",
        "thesis_ledger_provider_config",
        "thesis_ledger_provider_health",
        "thesis_ledger_provider_tombstone",
    }
    if not required.issubset(tables):
        raise NavResearchAdmissionError(
            "--apply refused because the configured DSA database lacks the current Control Store tables"
        )
    return path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        funds = []
        for item in args.fund:
            symbol, separator, fund_type = item.rpartition(":")
            if not separator or not symbol or not fund_type:
                raise NavResearchAdmissionError("each --fund must use SYMBOL:domestic or SYMBOL:qdii")
            funds.append({"symbol": symbol, "fundType": fund_type})
        database_path = _existing_store_path() if args.apply else None
        evidence = prepare_nav_research_evidence(
            funds=funds,
            research_mode=args.research_mode,
            start=args.start,
            end=args.end,
            warmup_periods=args.warmup_periods,
            tail_trading_days=args.tail_trading_days,
            research_decision=args.research_decision,
            evidence_dir=args.evidence_dir,
            recorded_by=args.recorded_by,
            valid_hours=args.valid_hours,
        )
        valid_until = None
        status = "evidence-only"
        if args.apply:
            store = ThesisLedgerControlStore(database_path=str(database_path))
            admission = record_nav_research_admission(
                store,
                evidence,
                recorded_by=args.recorded_by,
                valid_hours=args.valid_hours,
            )
            valid_until = admission["validUntil"]
            status = "admitted"
        print(json.dumps({
            "status": status,
            "funds": evidence["funds"],
            "researchMode": evidence["researchMode"],
            "scopeDateFrom": evidence["scopeDateFrom"],
            "scopeDateTo": evidence["scopeDateTo"],
            "evidencePath": evidence["manifestPath"],
            "evidenceSha256": evidence["evidenceSha256"],
            "validUntil": valid_until,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (NavResearchAdmissionError, OSError, sqlite3.Error, ValueError) as error:
        print(f"NAV research admission refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
