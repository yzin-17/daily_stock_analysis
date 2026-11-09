"""已复核上交所原件的历史开盘重建；仅支持当日空仓买入回放，不认证实时成交。"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

RULE_HASH = "fc922c433438b2636cb631eab25cca405209712acbb6aaded768c45456ff8888"
DEFERRED_HASH = "f4a0d0976a01e3e95f82460170900993824b70d91183e42d09e13ba10a9958c5"
RULE_URL = "https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "证据时刻必须含时区")
    return result


def qualify(bundle, *, qualified_at):
    """读取已捕获文件，不联网、不调用模型、不写数据库；保留真正的后验可用时间。"""
    files = {key: Path(path).read_bytes() for key, path in bundle["files"].items()}
    hashes = {key: sha256(value).hexdigest() for key, value in files.items()}
    require(hashes["rules"] == RULE_HASH and hashes["deferred"] == DEFERRED_HASH,
            "规则原件不属于本次复核的版本")
    evidence = {key: json.loads(files[key]) for key in ["bars", "profile", "suspensions", "opening", "minutes"]}
    result = qualify_receipts(evidence, bundle, qualified_at=qualified_at)
    result["sourceHashes"] = hashes
    result["ruleSource"] = {"url": RULE_URL, "publishedDate": "2026-04-24", "effectiveDate": "2026-07-06",
                            "clauses": ["3.1.4", "3.3.8", "3.3.11", "3.3.13", "3.3.17"],
                            "deferredClausesApplicable": False}
    return result


def qualify_receipts(e, bundle, *, qualified_at):
    """标准合同和公开来源的交叉核对；调用方另校验规则原件摘要。"""
    symbol, day = bundle["symbol"], bundle["day"]
    local = ZoneInfo("Asia/Shanghai")
    now = instant(qualified_at)
    require(now.astimezone(local).date().isoformat() == day and day >= "2026-07-06",
            "当前上市概况只证明本次捕获当天；不扩大为历史状态")
    raw = e["bars"]
    request, response = raw["request"], raw["response"]
    require(request["symbol"] == response["symbol"] == symbol, "价格标的不符")
    require(request["start"] == request["end"] == day, "价格请求日期不符")
    require(response["requestId"] == request["requestId"], "价格请求身份不符")
    key = response["routeKey"]
    require(key == request["routeKey"] and key == {"kind": "bar", "market": "CN", "assetType": "STOCK",
            "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "none"}, "需要真实不复权股票日线")
    require(response["provenance"]["providerId"] == request["routeTarget"]["providerId"] == "hithink"
            and response["provenance"]["upstreamSource"] == request["routeTarget"]["upstreamSource"] == "hithink-financial-api",
            "价格来源不符")
    proof = response["coverageProof"]
    require(proof["calendar"]["expectedSessionDates"] == [day]
            and proof["window"] == {"status": "complete", "requestedStart": day, "requestedEnd": day}
            and proof["pagination"]["status"] == "complete" and not proof["pagination"]["continuationPending"],
            "日历、日期或传输覆盖不完整")
    require(len(response["bars"]) == 1 and response["sourcePriceBasis"]["adjustment"] == "none", "日线覆盖或价格口径无效")
    bar = response["bars"][0]
    require(bar["timestamp"][:10] == day and bar["completionStatus"] == "complete"
            and instant(bar["availableAt"]) <= now and Decimal(str(bar["volume"])) > 0, "缺少已完成的实际成交日线")
    profile = e["profile"]
    require(profile["sqlId"] == "COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GSGK_C" and len(profile["result"]) == 1,
            "上市概况来源或结果数量无效")
    company = profile["result"][0]
    require(company["A_STOCK_CODE"] == symbol.removesuffix(".SH") and company["COMPANY_CODE"] == company["A_STOCK_CODE"]
            and company["SEC_TYPE"] == "主板A" and company["LIST_BOARD"] == "1" and company["STATE_CODE_A_DESC"] == "上市",
            "仅支持已核实的上交所正常上市主板 A 股")
    listed = datetime.strptime(company["A_LIST_DATE"], "%Y%m%d").date()
    require((now.astimezone(local).date() - listed).days > 100
            and not any(prefix in company["SECURITY_ABBR_A_CN"] for prefix in ["退", "N", "C"]),
            "上市初期、重新上市及退市整理例外未核实")
    suspension = e["suspensions"]
    require(bundle["suspensionRequest"] == {"sqlId": "GW_PL_JYTS_TFPXX", "startStopDate": day.replace("-", ""),
                                          "endStopDate": day.replace("-", ""), "productCode": "", "keyWords": ""},
            "停复牌记录须为指定日期的全市场未过滤查询")
    page, rows = suspension["pageHelp"], suspension["result"]
    require(suspension["sqlId"] == "GW_PL_JYTS_TFPXX" and not suspension["actionErrors"]
            and page["pageNo"] == page["pageCount"] == 1 and page["total"] == len(rows)
            and page["data"] == rows, "官方停复牌记录未完整取得")
    require(not any(row["productCode"] == company["A_STOCK_CODE"] for row in rows), "标的存在官方停复牌安排，停止回放")
    opening = e["opening"]
    open_at = opening["openAt"]
    session = opening["quote"]["sessionContext"]["marketSession"]
    starts = sorted(s["startAt"] for s in session["sessions"] if s["tradingDate"] == day)
    require(starts and starts[0] == open_at and instant(open_at).astimezone(local).date().isoformat() == day,
            "开盘时点必须来自实际日历会话")
    quote = opening["quote"]
    require(instant(quote["marketTime"]).astimezone(local).date().isoformat() == day
            and instant(open_at) <= instant(quote["marketTime"]) <= instant(quote["fetchedAt"]) <= now
            and quote["stale"] is False, "冻结报价日期、时序或有效性不符")
    price, previous = Decimal(str(bar["open"])), Decimal(str(quote["previousClose"]))
    require(quote["symbol"] == symbol and quote["units"]["priceCurrency"] == "CNY"
            and price == Decimal(str(quote["open"])) and previous > 0, "日线和冻结开盘报价未相互印证")
    minute = e["minutes"]
    require(minute["identity"] == {"symbol": symbol, "assetType": "STOCK", "timeframe": "1m", "adjustment": "none"},
            "分钟旁证标的不符")
    first = minute["points"][0]
    require(0 < (instant(first["timestamp"]) - instant(open_at)).total_seconds() <= 60
            and Decimal(str(first["open"])) == price and Decimal(str(first["volume"])) > 0,
            "开盘第一分钟未取得同价的实际成交旁证")
    require(price > 0 and instant(open_at) < instant(bar["availableAt"]), "历史重建不得回填开盘可用时间")
    tick = Decimal("0.01")
    upper = (previous * Decimal("1.1")).quantize(tick, rounding=ROUND_HALF_UP)
    lower = (previous * Decimal("0.9")).quantize(tick, rounding=ROUND_HALF_UP)
    require(lower < price < upper, "开盘位于涨跌停边界，不能证明可成交")
    return {"version": "historical-opening-reconstruction-v1", "purpose": "initial-cash-buy-only-replay",
            "symbol": symbol, "tradingDate": day, "openAt": open_at, "qualifiedAt": qualified_at,
            "sourceAvailableAt": bar["availableAt"], "price": str(price), "previousClose": str(previous),
            "lot": "100", "tick": "0.01", "upperLimit": str(upper), "lowerLimit": str(lower),
            "statusBasis": "sse-complete-suspension-query+same-day-normal-listing+opening-trade-corroboration",
            "calendarRevision": opening["calendar"], "modelCalls": 0,
            "feeModel": "original-arena-fixed-v1", "sourceFeeStatus": raw["sourceFeeStatus"]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--output")
    target.add_argument("--verify")
    args = parser.parse_args()
    bundle = json.loads(Path(args.bundle).read_text())
    if args.verify:
        original = json.loads(Path(args.verify).read_text())
        require(qualify(bundle, qualified_at=original["qualifiedAt"]) == original, "冻结开盘证据与原件重新核验结果不符")
        print(json.dumps({"verified": True, "modelCalls": 0}))
    else:
        output = qualify(bundle, qualified_at=datetime.now(timezone.utc).isoformat())
        with open(args.output, "x", encoding="utf-8") as target:
            json.dump(output, target, ensure_ascii=False, indent=2)
        print(json.dumps({"output": args.output, "symbol": output["symbol"], "price": output["price"], "modelCalls": 0}))
