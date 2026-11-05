"""正式汇率数据装配；缓存和时间合同独立于原生组合业务。"""

from contextlib import closing
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from src.data.fx_rate_repository import FxRateRepository

FX_MAX_AGE_DAYS = 7


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _fetch_yahoo_rate(**values: Any) -> float | None:
    from data_provider.fx_rates import fetch_fx_rate_from_yfinance
    return fetch_fx_rate_from_yfinance(**values)


def real_fx_rates(base_currency: str, currencies: list[str], as_of: date) -> dict[str, Any]:
    with closing(FxRateRepository()) as repo:
        return build_fx_rates(base_currency, currencies, as_of, repo=repo, fetch=_fetch_yahoo_rate)


def fx_row(
    *,
    from_currency: str,
    to_currency: str,
    rate: float,
    rate_date: date,
    provider: str,
    fetched_at: Optional[str],
    stale: bool,
    as_of: date,
) -> dict[str, Any]:
    age_days = max(0, (as_of - rate_date).days)
    effective_stale = stale or age_days > 0
    return {
        "fromCurrency": from_currency,
        "toCurrency": to_currency,
        "rate": rate,
        "rateDate": rate_date.isoformat(),
        "provider": provider,
        **({"fetchedAt": fetched_at} if fetched_at else {}),
        "freshness": "stale" if effective_stale else "delayed",
        "stale": effective_stale,
        "ageDays": age_days,
        "available": age_days <= FX_MAX_AGE_DAYS,
    }


def cached_fx_rate(
    *,
    repo: Any,
    from_currency: str,
    to_currency: str,
    as_of: date,
) -> Optional[dict[str, Any]]:
    direct = repo.get_latest_fx_rate(
        from_currency=from_currency,
        to_currency=to_currency,
        as_of=as_of,
    )
    if direct is not None and float(direct.rate or 0) > 0:
        return {
            "rate": float(direct.rate),
            "rate_date": direct.rate_date,
            "provider": direct.source or "cache",
            "fetched_at": _utc_iso(direct.updated_at) if direct.updated_at and direct.updated_at.tzinfo else None,
            "stale": bool(direct.is_stale),
        }
    inverse = repo.get_latest_fx_rate(
        from_currency=to_currency,
        to_currency=from_currency,
        as_of=as_of,
    )
    if inverse is not None and float(inverse.rate or 0) > 0:
        return {
            "rate": 1 / float(inverse.rate),
            "rate_date": inverse.rate_date,
            "provider": inverse.source or "cache",
            "fetched_at": _utc_iso(inverse.updated_at) if inverse.updated_at and inverse.updated_at.tzinfo else None,
            "stale": bool(inverse.is_stale),
        }
    return None


def build_fx_rates(
    base_currency: str, currencies: list[str], as_of: date, *, repo: Any, fetch: Callable[..., float | None],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for currency in currencies:
        if currency == base_currency:
            rows.append(
                fx_row(
                    from_currency=currency,
                    to_currency=base_currency,
                    rate=1.0,
                    rate_date=as_of,
                    provider="identity",
                    fetched_at=_now_iso(),
                    stale=False,
                    as_of=as_of,
                )
            )
            continue
        cached = cached_fx_rate(
            repo=repo,
            from_currency=currency,
            to_currency=base_currency,
            as_of=as_of,
        )
        if cached is None or (as_of - cached["rate_date"]).days > FX_MAX_AGE_DAYS:
            try:
                fetched = fetch(
                    from_currency=currency,
                    to_currency=base_currency,
                    as_of_date=as_of,
                )
            except Exception:  # noqa: BLE001 - contract returns unavailable status.
                fetched = None
            if fetched is not None and fetched > 0:
                repo.save_fx_rate(
                    from_currency=currency,
                    to_currency=base_currency,
                    rate_date=as_of,
                    rate=fetched,
                    source="yfinance",
                    is_stale=False,
                )
                cached = {
                    "rate": float(fetched),
                    "rate_date": as_of,
                    "provider": "yfinance",
                    "fetched_at": _now_iso(),
                    "stale": False,
                }
        if cached is None or (as_of - cached["rate_date"]).days > FX_MAX_AGE_DAYS:
            rows.append(
                {
                    "fromCurrency": currency,
                    "toCurrency": base_currency,
                    "freshness": "unavailable",
                    "stale": False,
                    "ageDays": None if cached is None else (as_of - cached["rate_date"]).days,
                    "available": False,
                }
            )
            continue
        rows.append(
            fx_row(
                from_currency=currency,
                to_currency=base_currency,
                rate=cached["rate"],
                rate_date=cached["rate_date"],
                provider=cached["provider"],
                fetched_at=cached["fetched_at"],
                stale=cached["stale"],
                as_of=as_of,
            )
        )
    return {
        "version": 3,
        "baseCurrency": base_currency,
        "asOf": as_of.isoformat(),
        "fetchedAt": _now_iso(),
        "maxAgeDays": FX_MAX_AGE_DAYS,
        "rates": rows,
    }
