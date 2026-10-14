"""HiThink 股票读取编排；显式研究与完整覆盖入口分开。"""

from src.services.thesis_ledger_market_v3_facts import market_calendar_evidence_v3


def read_hithink_stock_bars(request, credential_snapshot, timeout_seconds):
    from src.services.thesis_ledger_provider_runtime import ProviderCallError

    if (
        request.capability != "DAILY_BAR"
        or request.instrument_type != "STOCK"
        or request.timeframe != "1d"
        or request.adjustment not in {"none", "qfq", "hfq"}
        or request.start is None
        or request.end is None
    ):
        raise ProviderCallError(
            "unsupported_source",
            "HiThink 股票 V3 仅登记 none/qfq/hfq 日线",
        )
    research = request.parameters.get("historical_tradability") is True and request.adjustment in {"none", "qfq"}
    calendar = market_calendar_evidence_v3("CN", request.start, request.end)
    if calendar is None and research:
        from datetime import datetime, timezone
        from src.services.thesis_ledger_research_calendar import research_calendar_evidence
        calendar = research_calendar_evidence(request.start, request.end, datetime.now(timezone.utc))
    if calendar is None or not isinstance(calendar.get("expectedSessionDates"), list):
        raise ProviderCallError("not_covered", "HiThink 股票缺少本地范围证据")

    if credential_snapshot is None:
        raise ProviderCallError("not_configured", "HiThink API 凭据未与当前 admission 复核")
    api_key = credential_snapshot.values.get("apiKey")
    if (
        credential_snapshot.provider_id != "hithink"
        or credential_snapshot.source not in {"environment", "control"}
        or credential_snapshot.method != "api_key"
        or not isinstance(api_key, str)
        or not api_key.strip()
    ):
        raise ProviderCallError("not_configured", "HiThink API 凭据未配置")

    from src.services.thesis_ledger_hithink_stock import (
        HiThinkStockError,
        HiThinkStockHistoricalAdapter,
    )

    adapter = HiThinkStockHistoricalAdapter(
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    try:
        return adapter.fetch_daily_bars(
            request.symbol,
            request.start,
            request.end,
            request.adjustment,
            expected_sessions=None if research else tuple(calendar["expectedSessionDates"]),
            calendar_revision=str(calendar["revision"]),
        )
    except HiThinkStockError as exc:
        raise ProviderCallError(
            exc.code,
            str(exc),
            retryable=exc.retryable,
        ) from exc

