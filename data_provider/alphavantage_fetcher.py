# -*- coding: utf-8 -*-
"""
AlphaVantageFetcher — US market data source (Priority 3)

Data source: AlphaVantage REST API
Rate limit: 25 calls/day, 5 calls/min (free tier)
Markets: US only
"""

import logging
import os
from contextvars import ContextVar
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, ProviderHTTPError, STANDARD_COLUMNS
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource
from .us_index_mapping import is_us_stock_code

logger = logging.getLogger(__name__)

_AV_BASE_URL = "https://www.alphavantage.co/query"
_STRICT_PROBE: ContextVar[bool] = ContextVar("alphavantage_strict_probe", default=False)
_STRICT_PROBE_TIMEOUT_SECONDS = 5


def _strict_request_error(error: Exception) -> ProviderHTTPError:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return ProviderHTTPError("AlphaVantage", int(status_code))
    reason = "timeout" if isinstance(error, requests.Timeout) else "network_failure"
    return ProviderHTTPError("AlphaVantage", reason=reason)


def _strict_payload_error(data: dict) -> ProviderHTTPError | None:
    if "Note" in data or "Information" in data:
        message = str(data.get("Note") or data.get("Information") or "").lower()
        if any(word in message for word in ("premium", "subscription", "permission")):
            reason = "permission_denied"
        elif any(word in message for word in ("api key", "apikey")) and any(
            word in message for word in ("invalid", "missing", "not valid")
        ):
            reason = "authentication_failed"
        elif any(word in message for word in ("limit", "frequency", "quota")):
            reason = "rate_limited"
        else:
            reason = "invalid_response"
        return ProviderHTTPError("AlphaVantage", reason=reason)
    if "Error Message" in data:
        message = str(data["Error Message"]).lower()
        reason = "authentication_failed" if "key" in message else "permission_denied"
        return ProviderHTTPError("AlphaVantage", reason=reason)
    return None


class AlphaVantageFetcher(BaseFetcher):
    name = "AlphaVantageFetcher"
    priority = 3

    def __init__(self, api_key: Optional[str] = None, *, strict_errors: bool = False):
        if api_key is None:
            from src.config import get_config

            config = get_config()
            api_key = getattr(config, "alphavantage_api_key", None) or os.getenv(
                "ALPHAVANTAGE_API_KEY"
            )
        self._api_key = (api_key or "").strip()
        self._strict_errors = bool(strict_errors)
        if not self._api_key:
            logger.debug("[AlphaVantage] API key not configured, fetcher disabled")

    def _is_us_stock(self, stock_code: str) -> bool:
        return is_us_stock_code(stock_code)

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self._api_key:
            raise DataFetchError("[AlphaVantage] API key not configured")
        if not self._is_us_stock(stock_code):
            raise DataFetchError(f"[AlphaVantage] {stock_code} is not a US stock")

        symbol = stock_code.strip().upper()
        params = {
            'function': 'TIME_SERIES_DAILY',
            'symbol': symbol,
            'outputsize': 'compact',
            'apikey': self._api_key,
        }

        try:
            self.random_sleep(0.5, 1.5)
            timeout = _STRICT_PROBE_TIMEOUT_SECONDS if _STRICT_PROBE.get() else 30
            resp = requests.get(_AV_BASE_URL, params=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            if _STRICT_PROBE.get() or self._strict_errors:
                raise _strict_request_error(e) from None
            raise DataFetchError(f"[AlphaVantage] HTTP request failed for {symbol}: {e}") from e
        except Exception as e:
            if _STRICT_PROBE.get() or self._strict_errors:
                raise ProviderHTTPError("AlphaVantage", reason="invalid_response") from None
            raise DataFetchError(f"[AlphaVantage] HTTP request failed for {symbol}: {e}") from e

        payload_error = _strict_payload_error(data)
        if payload_error is not None and (_STRICT_PROBE.get() or self._strict_errors):
            raise payload_error
        if 'Note' in data:
            raise DataFetchError(f"[AlphaVantage] Rate limited: {data['Note']}")
        if 'Error Message' in data:
            raise DataFetchError(f"[AlphaVantage] API error for {symbol}: {data['Error Message']}")

        ts_key = 'Time Series (Daily)'
        if ts_key not in data or not data[ts_key]:
            raise DataFetchError(f"[AlphaVantage] No time series data for {symbol}")

        rows = []
        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        for date_str, values in data[ts_key].items():
            row_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            if start <= row_date <= end:
                rows.append({
                    'date': date_str,
                    '1. open': float(values.get('1. open', 0)),
                    '2. high': float(values.get('2. high', 0)),
                    '3. low': float(values.get('3. low', 0)),
                    '4. close': float(values.get('4. close', 0)),
                    '5. volume': float(values.get('5. volume', 0)),
                })

        if not rows:
            raise DataFetchError(f"[AlphaVantage] No data in date range for {symbol}")

        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df['date'])
        df.index.name = None  # 避免与 _normalize_data 重新添加的 'date' 列冲突
        return df.drop(columns=['date'])

    def get_daily_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: int = 30,
        *,
        strict: bool = False,
    ) -> pd.DataFrame:
        token = _STRICT_PROBE.set(strict or self._strict_errors)
        try:
            return super().get_daily_data(stock_code, start_date, end_date, days)
        finally:
            _STRICT_PROBE.reset(token)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.copy()
        df['date'] = pd.to_datetime(df.index).date
        df = df.rename(columns={
            '1. open': 'open', '2. high': 'high', '3. low': 'low',
            '4. close': 'close', '5. volume': 'volume',
        })
        # AlphaVantage returns newest-first; sort ascending before computing pct_chg
        df = df.sort_values('date', ascending=True).reset_index(drop=True)
        df['pct_chg'] = df['close'].pct_change() * 100
        df['pct_chg'] = df['pct_chg'].fillna(0).round(2)
        df['amount'] = df['volume'] * df['close']
        df['code'] = stock_code

        keep = ['code'] + STANDARD_COLUMNS
        df = df[[col for col in keep if col in df.columns]]
        return df

    def get_realtime_quote(
        self,
        stock_code: str,
        *,
        strict: bool = False,
    ) -> Optional[UnifiedRealtimeQuote]:
        if not self._api_key or not self._is_us_stock(stock_code):
            return None

        symbol = stock_code.strip().upper()
        try:
            self.random_sleep(0.5, 1.5)
            resp = requests.get(_AV_BASE_URL, params={
                'function': 'GLOBAL_QUOTE',
                'symbol': symbol,
                'apikey': self._api_key,
            }, timeout=5 if strict or self._strict_errors else 15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            if strict or self._strict_errors:
                raise _strict_request_error(e) from None
            logger.warning(f"[AlphaVantage] Realtime quote failed for {symbol}: {e}")
            return None

        payload_error = _strict_payload_error(data)
        if payload_error is not None and (strict or self._strict_errors):
            raise payload_error
        gq = data.get('Global Quote', {})
        price_str = gq.get('05. price')
        if not price_str:
            return None

        price = float(price_str)
        prev_close = float(gq.get('08. previous close', 0))
        change_pct_str = gq.get('10. change percent', '0%').replace('%', '')
        change_pct = float(change_pct_str) if change_pct_str else None

        return UnifiedRealtimeQuote(
            code=symbol,
            source=RealtimeSource.FALLBACK,
            price=price,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            change_amount=round(float(gq.get('09. change', 0)), 4),
            volume=int(float(gq.get('06. volume', 0))),
            amount=None,
            volume_ratio=None,
            turnover_rate=None,
            amplitude=None,
            open_price=float(gq.get('02. open', 0)),
            high=float(gq.get('03. high', 0)),
            low=float(gq.get('04. low', 0)),
            pre_close=prev_close,
        )

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        if not self._api_key or not self._is_us_stock(stock_code):
            return None

        symbol = stock_code.strip().upper()
        try:
            resp = requests.get(_AV_BASE_URL, params={
                'function': 'SYMBOL_SEARCH',
                'keywords': symbol,
                'apikey': self._api_key,
            }, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug(f"[AlphaVantage] Symbol search failed for {symbol}: {e}")
            return None

        for match in data.get('bestMatches', []):
            if match.get('1. symbol') == symbol and match.get('2. name'):
                return match['2. name']
        return None
