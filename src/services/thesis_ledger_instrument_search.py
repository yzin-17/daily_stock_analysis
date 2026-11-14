"""目录未命中后的单次证券检索，按来源能力读取当前身份。"""

from contextlib import contextmanager
import re

from data_provider.futu_market import FutuMarketAdapter, FutuMarketError
from data_provider.hithink_instrument_search import InstrumentSearchError, search_instruments
from data_provider.security_lot_size import LotSizeError
from .thesis_ledger_feature_policy import feature_provider_epoch
from .thesis_ledger_provider_runtime import ProviderCallError


@contextmanager
def search_credentials(store, provider):
    def enabled_epoch():
        epoch = feature_provider_epoch(store, [provider])[provider]
        if not epoch[1] or not (store.effective_policy_v3() or {}).get("enabled"):
            raise ProviderCallError("disabled", "证券检索来源未启用")
        return epoch

    epoch = enabled_epoch()
    credentials = store.provider_credential_snapshot(provider)
    fields = ("apiKey",) if provider == "hithink" else ("host", "port")
    if not all(credentials.values.get(field) for field in fields):
        raise ProviderCallError("credential_missing", "证券检索来源缺少有效凭证")
    yield credentials.values, epoch[0]
    if enabled_epoch() != epoch or store.provider_credential_snapshot(provider) != credentials:
        raise ProviderCallError("not_admitted", "证券检索期间来源配置已变化")


def search_current_instruments(store, query, market=None, instrument_type=None):
    query = query.strip()
    if not query or len(query) > 80:
        raise ProviderCallError("invalid_request", "检索词须为 1 至 80 个字符")
    canonical = query.upper()
    suffix = canonical.rpartition(".")[2] if "." in canonical else None
    if suffix in {"SH", "SZ", "BJ", "OF", "HK", "US"}:
        if market and market != suffix:
            raise ProviderCallError("invalid_request", "检索代码与市场筛选不一致")
        market = suffix
        query = canonical

    if market in {"HK", "US"}:
        code = canonical.rsplit(".", 1)[0] if suffix == market else canonical
        pattern = r"[0-9]{1,5}" if market == "HK" else r"[A-Z][A-Z0-9.\-]{0,31}"
        if not re.fullmatch(pattern, code) or instrument_type == "MUTUAL_FUND":
            raise ProviderCallError("unsupported_capability", "港美股来源仅支持按股票或 ETF 代码精确检索")
        symbol = (code.zfill(5) if market == "HK" else code) + "." + market
        provider = "futu"
        with search_credentials(store, provider) as (values, _):
            try:
                items = FutuMarketAdapter(**values).find_instrument(symbol)
            except (FutuMarketError, LotSizeError) as error:
                raise ProviderCallError(error.code, "富途证券检索不可用") from None
        if instrument_type:
            items = [item for item in items if item["instrumentType"] == instrument_type]
    else:
        provider = "hithink"
        with search_credentials(store, provider) as (values, _):
            try:
                rows = search_instruments(values["apiKey"], query, current=True, market=market,
                                          instrument_type=instrument_type)["items"]
            except InstrumentSearchError as error:
                raise ProviderCallError(error.code, "境内证券检索不可用") from None
        items = []
        for row in rows:
            code, exchange = row["symbol"].split(".")
            items.append({"canonicalCode": code, "market": exchange, "instrumentType": row["assetType"],
                          "displayName": row["name"]})
    return {"contractVersion": 3, "providerId": provider, "items": items}
