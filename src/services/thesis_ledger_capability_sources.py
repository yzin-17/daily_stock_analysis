"""生产静态注册入口；隔离测试能力不能加入此处。"""

from data_provider.thesis_ledger_sources.tencent import SOURCE as TENCENT
from data_provider.thesis_ledger_sources.hithink import SOURCE as HITHINK
from data_provider.thesis_ledger_sources.baostock import SOURCE as BAOSTOCK
from data_provider.thesis_ledger_sources.official_states import SOURCE as OFFICIAL_STATES
from data_provider.thesis_ledger_sources.efinance import SOURCE as EFINANCE
from data_provider.thesis_ledger_sources.akshare import SOURCE as AKSHARE
from data_provider.thesis_ledger_sources.pytdx import SOURCE as PYTDX
from data_provider.thesis_ledger_sources.tickflow import SOURCE as TICKFLOW
from data_provider.thesis_ledger_sources.tickflow_free_history import SOURCE as TICKFLOW_FREE
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
from .thesis_ledger_capability_registry import CapabilityRegistry


CAPABILITY_SOURCES = CapabilityRegistry((TENCENT, HITHINK, BAOSTOCK, OFFICIAL_STATES, EFINANCE, AKSHARE, PYTDX, TICKFLOW, TICKFLOW_FREE, ALPHAVANTAGE, FINNHUB, TUSHARE, YFINANCE, LONGBRIDGE, FUTU, OFFICIAL_RULES, OFFICIAL_HK_RULES, OFFICIAL_US_RULES, OFFICIAL_DIVIDENDS, RQDATA))
