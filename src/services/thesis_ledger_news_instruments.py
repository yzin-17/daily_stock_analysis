"""新闻查询的公开身份核验；只读已发布目录与现有数据索引。"""

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from src.data_config import DataConfigurationError


@dataclass(frozen=True)
class PublicNewsInstrument:
    symbol: str
    market: str
    asset_type: str
    name: str

    def query(self):
        market = {"CN": "中国市场", "HK": "香港市场", "US": "美国市场"}[self.market]
        asset = {"STOCK": "股票", "ETF": "ETF", "INDEX": "指数", "MUTUAL_FUND": "场外基金"}[self.asset_type]
        return f"{self.symbol} {self.name} {market} {asset} 新闻"


def _catalog_rows(database_path):
    path = Path(database_path).resolve()
    if not path.is_file():
        return []
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='thesis_ledger_catalog_generation'"
        ).fetchone()
        if not exists:
            return []
        row = connection.execute(
            "SELECT * FROM thesis_ledger_catalog_generation ORDER BY generation DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return []
        from src.services.thesis_ledger_catalog_contract import current_catalog_row
        return current_catalog_row(row)["items"]
    finally:
        connection.close()


def _index_rows():
    from src.data.stock_index_loader import find_existing_stock_index_path, _load_stock_index_payload
    path = find_existing_stock_index_path()
    return _load_stock_index_payload(path) if path is not None else []


def _catalog_identities(rows):
    result = []
    for row in rows:
        exchange = row["market"]
        if exchange not in {"SH", "SZ", "BJ", "OF"}:
            continue
        result.append(PublicNewsInstrument(
            f'{row["canonicalCode"]}.{exchange}', "CN", row["instrumentType"], row["displayName"],
        ))
    return result


def _index_identities(rows):
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 9 or row[8] is not True:
            continue
        symbol, name, market, kind = row[0], row[2], row[6], row[7]
        if (market not in {"CN", "BSE", "HK", "US"} or kind != "stock"
                or not isinstance(symbol, str) or not isinstance(name, str) or not name.strip()):
            continue
        if market == "US":
            symbol = symbol + ".US"
        result.append(PublicNewsInstrument(symbol, "CN" if market == "BSE" else market, "STOCK", name.strip()))
    return result


def resolve_public_news_instrument(instrument, database_path):
    """目录中的同代码身份优先；无法唯一核验时不向上游搜索。"""
    try:
        catalog = _catalog_identities(_catalog_rows(database_path))
        same_code = [item for item in catalog if item.symbol == instrument.symbol]
        candidates = same_code or [item for item in _index_identities(_index_rows())
                                   if item.symbol == instrument.symbol]
    except Exception:
        raise DataConfigurationError("公开新闻标的目录读取失败") from None
    identities = {(item.symbol, item.market, item.asset_type) for item in candidates}
    expected = (instrument.symbol, instrument.market, instrument.assetType)
    if identities != {expected}:
        return None
    return next(item for item in candidates
                if (item.symbol, item.market, item.asset_type) == expected)
