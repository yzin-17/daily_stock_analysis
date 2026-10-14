"""上市信息的已知／未知结构与日级投影，保留已知事实编码。"""

from typing import Any, Mapping


def canonical_listing_information(listing: Mapping[str, Any]) -> list[str]:
    """未知信息只携带标的；已知字段顺序沿用原覆盖证明。"""
    if not isinstance(listing, Mapping):
        raise ValueError("上市信息结构非法")
    if "status" in listing:
        if set(listing) != {"symbol", "status"} or listing["status"] != "unknown":
            raise ValueError("未知上市信息不能携带日期或来源")
        names = ("symbol", "status")
    else:
        names = ("symbol", "firstTradingDate", "source", "revision", "knownAt")
        if set(listing) != set(names):
            raise ValueError("已知上市信息字段不完整")
    if any(not isinstance(listing[name], str) or not listing[name].strip() for name in names):
        raise ValueError("上市信息字段必须为非空文本")
    return [listing[name] for name in names]


def daily_listing_information(listing: Mapping[str, Any]) -> dict[str, Any]:
    """日级记录的标的由父记录绑定，不复制未知日期或来源。"""
    canonical_listing_information(listing)
    if listing.get("status") == "unknown":
        return {"status": "unknown"}
    return {
        "listedOn": listing["firstTradingDate"],
        "source": {
            "provider": listing["source"], "revision": listing["revision"],
            "availableAt": listing["knownAt"],
        },
    }


def daily_listing_date(listing: Mapping[str, Any]) -> str | None:
    """缺上市日期保持未知；已知结构仍须携带完整来源字段。"""
    if not isinstance(listing, Mapping):
        raise ValueError("日级上市信息结构非法")
    if listing == {"status": "unknown"}:
        return None
    if set(listing) != {"listedOn", "source"}:
        raise ValueError("日级上市信息字段非法")
    source = listing["source"]
    if not isinstance(source, Mapping) or set(source) != {"provider", "revision", "availableAt"}:
        raise ValueError("日级上市来源字段不完整")
    if any(not isinstance(value, str) or not value.strip() for value in (listing["listedOn"], *source.values())):
        raise ValueError("日级上市信息字段必须为非空文本")
    return listing["listedOn"]
