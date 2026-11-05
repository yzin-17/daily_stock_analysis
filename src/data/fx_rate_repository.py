"""汇率数据仓储：原位兼容既有缓存表，只初始化自身结构。"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import sqlite3
from threading import RLock
import time

from src.data_config import DataServiceConfig, get_data_config


@dataclass(frozen=True)
class FxRate:
    id: int
    from_currency: str
    to_currency: str
    rate_date: date
    rate: float
    source: str
    is_stale: bool
    updated_at: datetime | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_fx_rates (
    id INTEGER NOT NULL PRIMARY KEY,
    from_currency VARCHAR(8) NOT NULL,
    to_currency VARCHAR(8) NOT NULL,
    rate_date DATE NOT NULL,
    rate FLOAT NOT NULL,
    source VARCHAR(32) NOT NULL,
    is_stale BOOLEAN NOT NULL,
    updated_at DATETIME,
    CONSTRAINT uix_portfolio_fx_pair_date UNIQUE (from_currency, to_currency, rate_date)
);
CREATE INDEX IF NOT EXISTS ix_portfolio_fx_rates_from_currency ON portfolio_fx_rates (from_currency);
CREATE INDEX IF NOT EXISTS ix_portfolio_fx_rates_to_currency ON portfolio_fx_rates (to_currency);
CREATE INDEX IF NOT EXISTS ix_portfolio_fx_rates_rate_date ON portfolio_fx_rates (rate_date);
"""


class FxRateRepository:
    def __init__(self, database_path: str | None = None, *, config: DataServiceConfig | None = None):
        self.config = config or get_data_config()
        self.database_path = self.config.database_path if database_path is None else database_path
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        if self.database_path != ":memory:":
            Path(self.database_path).absolute().parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.database_path, timeout=self.config.sqlite_busy_timeout_ms / 1000,
            check_same_thread=False, isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={int(self.config.sqlite_busy_timeout_ms)}")
            if self.database_path != ":memory:" and self.config.sqlite_wal_enabled:
                connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            self._connection = connection
        except Exception:
            connection.close()
            raise
        return connection

    def get_latest_fx_rate(self, *, from_currency: str, to_currency: str, as_of: date) -> FxRate | None:
        with self._lock:
            row = self._connect().execute(
                "SELECT * FROM portfolio_fx_rates WHERE from_currency=? AND to_currency=? "
                "AND rate_date<=? ORDER BY rate_date DESC LIMIT 1",
                (from_currency, to_currency, as_of.isoformat()),
            ).fetchone()
            if row is None:
                return None
            return FxRate(
                id=row["id"], from_currency=row["from_currency"], to_currency=row["to_currency"],
                rate_date=date.fromisoformat(row["rate_date"]), rate=float(row["rate"]),
                source=row["source"], is_stale=bool(row["is_stale"]),
                updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            )

    def save_fx_rate(
        self, *, from_currency: str, to_currency: str, rate_date: date, rate: float,
        source: str = "manual", is_stale: bool = False,
    ) -> None:
        values = (from_currency, to_currency, rate_date.isoformat(), rate, source, int(is_stale),
                  datetime.now().isoformat(sep=" ", timespec="microseconds"))
        with self._lock:
            for attempt in range(self.config.sqlite_write_retry_max + 1):
                try:
                    self._connect().execute(
                        "INSERT INTO portfolio_fx_rates "
                        "(from_currency,to_currency,rate_date,rate,source,is_stale,updated_at) VALUES (?,?,?,?,?,?,?) "
                        "ON CONFLICT(from_currency,to_currency,rate_date) DO UPDATE SET "
                        "rate=excluded.rate,source=excluded.source,is_stale=excluded.is_stale,updated_at=excluded.updated_at",
                        values,
                    )
                    return
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt >= self.config.sqlite_write_retry_max:
                        raise
                    time.sleep(self.config.sqlite_write_retry_base_delay * (2 ** attempt))

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
