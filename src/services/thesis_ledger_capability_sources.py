"""生产静态注册入口；隔离测试能力不能加入此处。"""

from data_provider.thesis_ledger_sources.tencent import SOURCE as TENCENT
from data_provider.thesis_ledger_sources.hithink import SOURCE as HITHINK
from data_provider.thesis_ledger_sources.baostock import SOURCE as BAOSTOCK
from data_provider.thesis_ledger_sources.official_states import SOURCE as OFFICIAL_STATES
from data_provider.thesis_ledger_sources.efinance import SOURCE as EFINANCE
from data_provider.thesis_ledger_sources.akshare import SOURCE as AKSHARE
from data_provider.thesis_ledger_sources.pytdx import SOURCE as PYTDX
from data_provider.thesis_ledger_sources.tickflow import SOURCE as TICKFLOW
from data_provider.thesis_ledger_sources.alphavantage import SOURCE as ALPHAVANTAGE
from data_provider.thesis_ledger_sources.finnhub import SOURCE as FINNHUB
from data_provider.thesis_ledger_sources.tushare import SOURCE as TUSHARE
from data_provider.thesis_ledger_sources.yfinance import SOURCE as YFINANCE
from data_provider.thesis_ledger_sources.longbridge import SOURCE as LONGBRIDGE
from data_provider.thesis_ledger_sources.futu import SOURCE as FUTU
from data_provider.thesis_ledger_sources.official_rules import SOURCE as OFFICIAL_RULES
from data_provider.thesis_ledger_sources.official_hk_rules import SOURCE as OFFICIAL_HK_RULES
from data_provider.thesis_ledger_sources.official_us_rules import SOURCE as OFFICIAL_US_RULES
from data_provider.thesis_ledger_sources.official_dividends import SOURCE as OFFICIAL_DIVIDENDS
from data_provider.thesis_ledger_sources.rqdata import SOURCE as RQDATA
from data_provider.thesis_ledger_sources.news import SOURCES as NEWS_SOURCES
from data_provider.thesis_ledger_sources.market_flash_news import SOURCES as FLASH_NEWS
from .thesis_ledger_capability_registry import CapabilityRegistry
from data_provider.thesis_ledger_sources.local_materials import SOURCE as LOCAL_MATERIALS
from data_provider.thesis_ledger_sources.historical_execution import SOURCE as HISTORICAL_EXECUTION
from data_provider.thesis_ledger_sources.sse_event_coverage import SOURCE as SSE_EVENT_COVERAGE


CAPABILITY_SOURCES = CapabilityRegistry((TENCENT, HITHINK, BAOSTOCK, OFFICIAL_STATES, EFINANCE, AKSHARE, PYTDX, TICKFLOW, ALPHAVANTAGE, FINNHUB, TUSHARE, YFINANCE, LONGBRIDGE, FUTU, OFFICIAL_RULES, OFFICIAL_HK_RULES, OFFICIAL_US_RULES, OFFICIAL_DIVIDENDS, RQDATA, LOCAL_MATERIALS, HISTORICAL_EXECUTION, SSE_EVENT_COVERAGE, *NEWS_SOURCES, *FLASH_NEWS))
