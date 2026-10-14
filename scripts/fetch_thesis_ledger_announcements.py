#!/usr/bin/env python3
"""显式获取中国内地公告，默认不解析、不改变策略或准入。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_provider.cninfo_announcements import acquire_announcements
from src.services.thesis_ledger_announcements import AnnouncementRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", required=True, help="逗号分隔，例如000881.SZ,600774.SH")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--parse", action="store_true", help="从原文解析明确的停复牌日期")
    args = parser.parse_args()
    result = acquire_announcements(args.symbols.split(","), args.start, args.end, max_pages=args.max_pages,
                                  repository=AnnouncementRepository(args.root), parse=args.parse)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["errors"] or result["truncated"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
