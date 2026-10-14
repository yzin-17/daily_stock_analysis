"""公开报价单位仅消费来源声明及标准结果；不执行SDK或改变切源。"""

from __future__ import annotations

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_quote_units_contract import QuotePublicUnits, QuotePublicUnitsContext, QuotePublicUnitsError
from src.services.thesis_ledger_quote_result_contract import QuoteResult


def apply_quote_public_units(result, provider, source, quote, on_error):
    if isinstance(quote, QuoteResult):
        if quote.unit_error is not None:
            error = quote.unit_error
            on_error(error.code, error.message, error.status_code)
        if quote.units is not None:
            result['units'] = QuotePublicUnits.model_validate(quote.units.model_dump()).model_dump()
        return result
    definition = CAPABILITY_SOURCES.source(provider)
    if definition is None or definition.quote_public_units is None:
        return result
    try:
        units = definition.quote_public_units(QuotePublicUnitsContext(quote, source))
        if not isinstance(units, QuotePublicUnits):
            raise QuotePublicUnitsError()
        checked = QuotePublicUnits.model_validate(units.model_dump())
    except QuotePublicUnitsError as error:
        on_error(error.code, str(error), error.status_code)
        raise
    except ValueError:
        on_error(QuotePublicUnitsError.code, '报价单位合同无效', QuotePublicUnitsError.status_code)
        raise
    result['units'] = checked.model_dump()
    return result
