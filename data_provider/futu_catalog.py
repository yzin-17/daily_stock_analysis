"""富途港美股票与 ETF 全市场目录；四个集合全部成功后才返回。"""

from contextlib import redirect_stdout
import logging
import re
import sys

from data_provider.futu_lot_size import validate_opend_endpoint
from data_provider.futu_market import FutuMarketError

COLLECTIONS = (("HK", "STOCK"), ("HK", "ETF"), ("US", "STOCK"), ("US", "ETF"))
logger = logging.getLogger(__name__)


def _invalid(market="", asset_type="", reason="重复市场身份"):
    return FutuMarketError("catalog_provider_invalid_response", f"富途目录集合身份或完整性无效：{market}/{asset_type}；{reason}")


def _collection(rows, market, asset_type):
    required = {"code", "name", "stock_type", "delisting"}
    if (not hasattr(rows, "iterrows") or not required.issubset(rows.columns)
            or not rows.columns.is_unique or not 0 < len(rows) <= 100_000):
        raise _invalid(market, asset_type, f"字段或集合行数无效；rows={len(rows) if hasattr(rows, '__len__') else 0}")
    items, seen = [], set()
    excluded_delisted, excluded_unsupported = 0, 0
    pattern = r"HK\.([0-9]{5})" if market == "HK" else r"US\.([A-Z][A-Z0-9.\-]{0,31})"
    for index, (_, row) in enumerate(rows.iterrows(), 1):
        code, name = row["code"], row["name"]
        # SDK 的 security.code 是不透明标识；产品格式在来源身份校验后单独筛选。
        native_valid = (isinstance(code, str) and code.startswith(market + ".")
                        and 0 < len(code[3:]) <= 64 and bool(code[3:].strip())
                        and all(char.isprintable() for char in code[3:]))
        delisted = row["delisting"]
        if hasattr(delisted, "item"):
            delisted = delisted.item()
        reason = None
        if not native_valid:
            reason = f"原生代码格式或市场；length={len(code) if isinstance(code, str) else 0}"
        elif not isinstance(row["stock_type"], str) or row["stock_type"] != asset_type:
            reason = f"证券类型；type={str(row['stock_type'])[:20]}"
        elif not isinstance(name, str) or not name.strip():
            reason = "名称为空"
        elif not isinstance(delisted, bool):
            reason = f"退市标记；valueType={type(delisted).__name__}"
        elif code in seen:
            reason = "代码重复"
        if reason:
            raise _invalid(market, asset_type, f"{reason}；rows={len(rows)}；row={index}")
        seen.add(code)
        if delisted:
            excluded_delisted += 1
            continue
        match = re.fullmatch(pattern, code)
        if not match or (market == "HK" and int(match[1]) == 0):
            excluded_unsupported += 1
            continue
        items.append({"canonicalCode": match[1], "market": market,
                      "instrumentType": asset_type, "displayName": name.strip()})
    logger.warning("FUTU_CATALOG_COLLECTION market=%s type=%s rows=%d included=%d delisted=%d unsupported=%d",
                   market, asset_type, len(rows), len(items), excluded_delisted, excluded_unsupported)
    if not items:
        raise _invalid(market, asset_type, f"可选集合为空；rows={len(rows)}；unsupported={excluded_unsupported}")
    return items


def read_catalog(sdk, host, port):
    """每个集合一次全量 SDK 请求，不分页、不重试、不创建交易上下文。"""
    host, port = validate_opend_endpoint(host, port)
    context = None
    try:
        context = sdk.OpenQuoteContext(host=host, port=port)
        items, seen = [], set()
        for market, asset_type in COLLECTIONS:
            status, rows = context.get_stock_basicinfo(
                getattr(sdk.Market, market), stock_type=getattr(sdk.SecurityType, asset_type),
            )
            if status != sdk.RET_OK:
                raise FutuMarketError("catalog_provider_unavailable", "富途目录读取失败，请检查 OpenD 登录与权限")
            for item in _collection(rows, market, asset_type):
                key = item["market"], item["canonicalCode"]
                if key in seen:
                    raise _invalid(market, asset_type)
                seen.add(key)
                items.append(item)
        return items
    finally:
        if context is not None:
            context.close()


def load_futu_catalog(host, port):
    """供 Catalog 可终止隔离进程调用，SDK 日志不污染标准输出。"""
    with redirect_stdout(sys.stderr):
        import futu
        return read_catalog(futu, host, port)
