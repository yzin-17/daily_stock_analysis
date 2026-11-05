"""市场资料的适配器所有权、精确绑定及对应能力登记。"""

import json
from pathlib import Path
import sqlite3
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding
from src.services.thesis_ledger_reference_contract_v1 import CONTRACTS
from src.services.thesis_ledger_reference_catalog_v1 import CATALOG, capability
from src.services.thesis_ledger_reference_scope_v1 import asset_type
from .market_reference_projection import ADAPTER_REVISION
from .market_reference_financial import PROFILES as FINANCIAL, read_financial
from .market_reference_events import PROFILES as EVENTS, read_events
from .market_reference_market import POOLS, read_market
from .market_reference_news import read_news
from .market_reference_calendar import read_calendar
from .market_reference_index import read_index
from .market_reference_ticks import read_ticks
from .market_reference_valuation import read_valuation
from .market_reference_yields import read_yields
from .market_reference_public import PUBLIC_READERS
from .market_reference_public_sources import SOURCE_NAMES
from .market_reference_derivatives import DERIVATIVE_READERS
from .market_reference_derivatives_sources import SOURCE_NAMES as DERIVATIVE_SOURCE_NAMES

READERS = {
    **DERIVATIVE_READERS,
    **PUBLIC_READERS,
    **{key: ('akshare', 'sina', 'sina-original-finance-report-v1', read_financial) for key in FINANCIAL},
    **{key: ('akshare', 'eastmoney', 'eastmoney-reference-events-v1', read_events) for key in EVENTS},
    **{key: ('akshare', 'eastmoney', 'eastmoney-market-reference-v1', read_market)
       for key in ('concepts', 'fund-flow', 'stock-monitor', *POOLS)},
    'bond-yields': ('akshare', 'eastmoney', 'eastmoney-bond-yields-v1', read_yields),
    'global-news': ('akshare', 'eastmoney', 'eastmoney-global-news-reference-v1', read_news),
    'macro-calendar': ('wallstreetcn', 'wallstreetcn-macro-calendar', 'wallstreetcn-macro-calendar-v1', read_calendar),
    'index-members': ('akshare', 'sina', 'sina-current-index-members-v1', read_index),
    **{key: ('tencent', 'tencent', 'tencent-aggregated-ticks-v1', read_ticks) for key in ('ticks', 'etf-ticks')},
    'valuation-history': ('baostock', 'baostock', 'baostock-reference-valuation-v1', read_valuation),
}


def guarded(reader):
    def read(query, context):
        try:
            if context.database_path and context.database_path != ':memory:':
                provider = READERS[query.dataset][0]
                with sqlite3.connect(Path(context.database_path).resolve().as_uri() + '?mode=ro', uri=True) as connection:
                    row = connection.execute('SELECT settings_json FROM thesis_ledger_provider_config WHERE provider_id=?', (provider,)).fetchone()
                settings = json.loads(row[0]) if row else {}
                if any(key.lower() in {'headers', 'baseurl', 'base_url', 'proxy', 'timeout', 'timeoutms'} for key in settings):
                    raise CapabilityContractError('configuration_failure', '当前资料适配尚不支持保存的自定义传输设置，未忽略配置发起请求')
            return reader(query, context)
        except CapabilityContractError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            raise CapabilityContractError('invalid_response', '来源字段、身份、日期、单位或分页校验失败') from None
    return read


def reference_bindings(provider):
    return tuple(QueryBinding(CONTRACTS[capability(key)], ExactKey('CN',
                 asset_type(key), capability(key)), source,
                 ADAPTER_REVISION, revision, guarded(reader), admission_required=False)
                 for key, (owner, source, revision, reader) in READERS.items() if owner == provider)


def reference_manifest(value, bindings):
    for binding in bindings:
        name, asset, source_id = binding.key.capability, binding.key.asset_type, binding.upstream_source
        value['capabilities'][name] = [asset]
        source = next((s for s in value['upstreamSources'] if s['sourceId'] == source_id), None)
        if source is None:
            display_name = {**SOURCE_NAMES, **DERIVATIVE_SOURCE_NAMES}.get(source_id, '华尔街见闻宏观日历')
            source = {'sourceId': source_id, 'displayName': display_name, 'capabilities': {}}
            value['upstreamSources'].append(source)
        source.setdefault('capabilities', {})[name] = [asset]
    return value
