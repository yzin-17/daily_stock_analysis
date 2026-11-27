# -*- coding: utf-8 -*-
"""
===================================
TushareFetcher - 备用数据源 1 (Priority 2)
===================================

数据来源：Tushare Pro API（挖地兔）
特点：需要 Token、有请求配额限制
优点：数据质量高、接口稳定

权限和访问频次以本次上游响应为准。
"""

import logging
import math
import re
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any

import pandas as pd
import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from .base import BaseFetcher, DataFetchError, RateLimitError, STANDARD_COLUMNS,is_bse_code, is_st_stock, is_kc_cy_stock, normalize_stock_code, _is_hk_market
from .realtime_types import UnifiedRealtimeQuote, ChipDistribution
from .sector_rankings_contract import normalize_sector_rankings
from .tushare_fund_daily import fetch_tushare_fund_daily
from .tushare_exact_daily import TushareExactDailyMixin
from .tushare_endpoint import _resolve_tushare_http_url
from .tushare_http_client import TushareApiError, _TushareHttpClient
from .tushare_fund_adj import TushareFundFactorsMixin
from .tushare_fund_dividend_reader import TushareFundDividendsMixin
from src.data_config import get_data_config as get_config
from src.services.thesis_ledger_quote_values import optional_quote_number
import os
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


# ETF code prefixes by exchange
# Shanghai: 51xxxx, 52xxxx, 56xxxx, 58xxxx
# Shenzhen: 15xxxx, 16xxxx, 18xxxx
_ETF_SH_PREFIXES = ('51', '52', '56', '58')
_ETF_SZ_PREFIXES = ('15', '16', '18')
_ETF_ALL_PREFIXES = _ETF_SH_PREFIXES + _ETF_SZ_PREFIXES


def _is_etf_code(stock_code: str) -> bool:
    """
    Check if the code is an ETF fund code.

    ETF code ranges:
    - Shanghai ETF: 51xxxx, 52xxxx, 56xxxx, 58xxxx
    - Shenzhen ETF: 15xxxx, 16xxxx, 18xxxx
    """
    code = normalize_stock_code(stock_code)
    return code.startswith(_ETF_ALL_PREFIXES) and len(code) == 6


def _is_us_code(stock_code: str) -> bool:
    """
    判断代码是否为美股
    
    美股代码规则：
    - 1-5个大写字母，如 'AAPL', 'TSLA'
    - 可能包含 '.'，如 'BRK.B'
    """
    code = stock_code.strip().upper()
    return bool(re.match(r'^[A-Z]{1,5}(\.[A-Z])?$', code))


class TushareFetcher(TushareExactDailyMixin, TushareFundFactorsMixin, TushareFundDividendsMixin, BaseFetcher):
    """
    Tushare Pro 数据源实现
    
    优先级：2
    数据来源：Tushare Pro API
    
    接口权限和访问频次由上游反馈。
    """
    
    name = "TushareFetcher"
    priority = int(os.getenv("TUSHARE_PRIORITY", "2"))  # 默认优先级，会在 __init__ 中根据配置动态调整

    def __init__(
        self,
        rate_limit_per_minute: int = 80,
        *,
        token: Optional[str] = None,
        request_timeout: int = 30,
        http_url: Optional[str] = None,
    ):
        """
        初始化 TushareFetcher

        Args:
            rate_limit_per_minute: 兼容参数，不再执行本地频次限制
        """
        self._api: Optional[object] = None  # Tushare API 实例
        self._explicit_token = token is not None
        self._token = (token or "").strip() if self._explicit_token else None
        self._request_timeout = int(request_timeout)
        self._http_url = http_url
        self.date_list: Optional[List[str]] = None  # 交易日列表缓存（倒序，最新日期在前）
        self._date_list_end: Optional[str] = None  # 缓存对应的截止日期，用于跨日刷新

        # 尝试初始化 API
        self._init_api()

        # 根据 API 初始化结果动态调整优先级
        self.priority = self._determine_priority()
    
    def _init_api(self) -> None:
        """
        初始化 Tushare API

        如果 Token 未配置，此数据源将不可用。
        这里直接使用内置 HTTP client，避免运行时强依赖 tushare SDK，
        从而减少 Docker / PyInstaller / 多虚拟环境场景下因缺包导致的初始化失败。
        """
        token = self._token
        if token is None:
            token = (get_config().tushare_token or "").strip()
        self._token = token
        if not token:
            logger.warning("Tushare Token 未配置，此数据源不可用")
            return

        try:
            self._api = self._build_api_client(token)
            logger.info("Tushare API 初始化成功")
        except Exception as e:
            logger.error(f"Tushare API 初始化失败: {e}")
            self._api = None

    def _build_api_client(self, token: str) -> _TushareHttpClient:
        """
        Build a lightweight Tushare Pro client over direct HTTP requests.

        The project already normalizes all Pro calls through the same request
        contract, so we do not need the official tushare SDK during runtime.

        支持通过 ``TUSHARE_HTTP_URL`` 环境变量将请求指向自建或第三方兼容
        端点，便于在网络无法直达 ``api.tushare.pro`` 时切换镜像/网关。
        留空或不设置则保持官方默认地址，行为与历史版本完全一致。
        """
        api_url = self._http_url if self._http_url is not None else _resolve_tushare_http_url()
        if api_url:
            logger.debug("Tushare 使用已配置接入地址")
            client = _TushareHttpClient(
                token=token,
                timeout=self._request_timeout,
                api_url=api_url,
            )
        else:
            client = _TushareHttpClient(token=token, timeout=self._request_timeout)
        logger.debug("Tushare API client configured for direct HTTP calls")
        return client

    def _determine_priority(self) -> int:
        """
        根据 Token 配置和 API 初始化状态确定优先级

        策略：
        - Token 配置且 API 初始化成功：优先级 -1（绝对最高，优于 efinance）
        - 其他情况：优先级 2（默认）

        Returns:
            优先级数字（0=最高，数字越大优先级越低）
        """
        if self._token and self._api is not None:
            # Token 配置且 API 初始化成功，提升为最高优先级
            logger.info("✅ 检测到 TUSHARE_TOKEN 且 API 初始化成功，Tushare 数据源优先级提升为最高 (Priority -1)")
            return -1

        # Token 未配置或 API 初始化失败，保持默认优先级
        return 2

    def is_available(self) -> bool:
        """
        检查数据源是否可用

        Returns:
            True 表示可用，False 表示不可用
        """
        return self._api is not None

    def _check_rate_limit(self) -> None:
        """兼容旧调用入口；频次由上游或 SDK 处理，不主动等待。"""

    def _call_api_with_rate_limit(self, method_name: str, **kwargs) -> pd.DataFrame:
        """统一调用 Tushare API，由上游返回频次及权限结果。"""
        if self._api is None:
            raise DataFetchError("Tushare API 未初始化，请检查 Token 配置")

        self._check_rate_limit()
        method = getattr(self._api, method_name)
        return method(**kwargs)

    def _get_china_now(self) -> datetime:
        """返回上海时区当前时间，方便测试覆盖跨日刷新逻辑。"""
        return datetime.now(ZoneInfo("Asia/Shanghai"))

    def _get_trade_dates(self, end_date: Optional[str] = None) -> List[str]:
        """按自然日刷新交易日历缓存，避免服务跨日后继续复用旧日历。"""
        if self._api is None:
            return []

        china_now = self._get_china_now()
        requested_end_date = end_date or china_now.strftime("%Y%m%d")

        if self.date_list is not None and self._date_list_end == requested_end_date:
            return self.date_list

        start_date = (china_now - timedelta(days=20)).strftime("%Y%m%d")
        df_cal = self._call_api_with_rate_limit(
            "trade_cal",
            exchange="SSE",
            start_date=start_date,
            end_date=requested_end_date,
        )

        if df_cal is None or df_cal.empty:
            logger.warning("[Tushare] trade_cal 返回为空，无法更新交易日历缓存")
            self.date_list = []
            self._date_list_end = requested_end_date
            return self.date_list
        if not {"cal_date", "is_open"}.issubset(df_cal.columns) or not df_cal["is_open"].isin([0, 1]).all():
            raise TushareApiError(code="invalid_response")

        trade_dates = sorted(
            df_cal[df_cal["is_open"] == 1]["cal_date"].astype(str).tolist(),
            reverse=True,
        )
        self.date_list = trade_dates
        self._date_list_end = requested_end_date
        return trade_dates

    @staticmethod
    def _pick_trade_date(trade_dates: List[str], use_today: bool) -> Optional[str]:
        """根据可用交易日列表选择当天或前一交易日。"""
        if not trade_dates:
            return None
        if use_today or len(trade_dates) == 1:
            return trade_dates[0]
        return trade_dates[1]

    @staticmethod
    def _detect_exchange_hint(stock_code: str) -> Optional[str]:
        """Return SH/SZ/BJ when the raw user input carries an explicit exchange hint."""
        upper = (stock_code or "").strip().upper()
        if upper.startswith(("SH", "SS")) or upper.endswith((".SH", ".SS")):
            return "SH"
        if upper.startswith("SZ") or upper.endswith(".SZ"):
            return "SZ"
        if upper.startswith("BJ") or upper.endswith(".BJ"):
            return "BJ"
        return None

    @classmethod
    def _get_legacy_realtime_symbol(cls, stock_code: str) -> str:
        """Build the legacy tushare symbol while preserving explicit SH/SZ hints."""
        code = normalize_stock_code(stock_code)
        exchange_hint = cls._detect_exchange_hint(stock_code)

        if code == '000001' and exchange_hint == 'SH':
            return 'sh000001'
        if code == '399001':
            return 'sz399001'
        if code == '399006':
            return 'sz399006'
        if code == '000300':
            return 'sh000300'
        if is_bse_code(code):
            return f"bj{code}"
        return code
    
    def _convert_stock_code(self, stock_code: str) -> str:
        """
        转换 A 股 / ETF / 北交所等为 Tushare ts_code（不含港股逻辑）。

        Tushare 要求的格式示例：
        - 沪市股票：600519.SH
        - 深市股票：000001.SZ
        - 沪市 ETF：510050.SH
        - 深市 ETF：159919.SZ

        Args:
            stock_code: 原始代码，如 '600519', '000001', '563230'

        Returns:
            Tushare 格式代码，如 '600519.SH', '000001.SZ'
        """
        raw_code = stock_code.strip()
        
        # Already has suffix.
        if '.' in raw_code:
            upper = raw_code.upper()
            code = normalize_stock_code(raw_code)
            exchange_hint = self._detect_exchange_hint(raw_code)
            if exchange_hint in ("SH", "SZ", "BJ") and code.isdigit():
                return f"{code}.{exchange_hint}"

            ts_code = upper
            if ts_code.endswith('.SS'):
                return f"{ts_code[:-3]}.SH"
            return ts_code

        if _is_us_code(raw_code):
            raise DataFetchError(f"TushareFetcher 不支持美股 {raw_code}，请使用 AkshareFetcher 或 YfinanceFetcher")

        if _is_hk_market(raw_code):
            #raise DataFetchError(f"TushareFetcher 不支持港股 {raw_code}，请使用 AkshareFetcher")
            return normalize_stock_code(raw_code)

        code = normalize_stock_code(raw_code)
        exchange_hint = self._detect_exchange_hint(raw_code)

        if exchange_hint == "SH":
            return f"{code}.SH"
        if exchange_hint == "SZ":
            return f"{code}.SZ"
        if exchange_hint == "BJ":
            return f"{code}.BJ"

        # ETF: determine exchange by prefix
        if code.startswith(_ETF_SH_PREFIXES) and len(code) == 6:
            return f"{code}.SH"
        if code.startswith(_ETF_SZ_PREFIXES) and len(code) == 6:
            return f"{code}.SZ"
        
        # BSE (Beijing Stock Exchange): 8xxxxx, 4xxxxx, 920xxx
        if is_bse_code(code):
            return f"{code}.BJ"
        
        # Regular stocks
        # Shanghai: 600xxx, 601xxx, 603xxx, 605xxx, 688xxx (STAR Market)
        # Shenzhen: 000xxx, 001xxx, 002xxx, 003xxx, 300xxx, 301xxx (ChiNext)
        if code.startswith(('600', '601', '603', '605', '688')):
            return f"{code}.SH"
        elif code.startswith(('000', '001', '002', '003', '300', '301')):
            return f"{code}.SZ"
        else:
            logger.warning(f"无法确定股票 {code} 的市场，默认使用深市")
            return f"{code}.SZ"

    def _convert_hk_stock_code_for_tushare(self, stock_code: str) -> str:
        """
        将用户输入转为 Tushare Pro 接口所需的 ts_code（含港股 nnnnn.HK）。

        - 非港股：委托 _convert_stock_code（A 股 / ETF / 北交所等）。
        - 港股：从 HK00700、00700、00700.HK 等形式归一为 5 位数字 + .HK。
        """
        raw_code = stock_code.strip()
        if _is_hk_market(raw_code):
            if "." in raw_code:
                ts_code = raw_code.upper()
                if ts_code.endswith(".SS"):
                    return f"{ts_code[:-3]}.SH"
                if ts_code.endswith(".HK"):
                    return ts_code
            digits = re.sub(r"\D", "", raw_code)
            if not digits:
                raise DataFetchError(f"无法识别港股代码 {raw_code}")
            code = digits[-5:].rjust(5, "0")
            return f"{code}.HK"
        return self._convert_stock_code(stock_code)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        从 Tushare 获取原始数据
        
        根据代码类型选择不同接口：
        - 普通股票：daily()
        - ETF 基金：fund_daily()
        
        流程：
        1. 检查 API 是否可用
        2. 检查是否为美股（不支持）
        3. 执行速率限制检查
        4. 转换股票代码格式
        5. 根据代码类型选择接口并调用
        """
        if self._api is None:
            raise DataFetchError("Tushare API 未初始化，请检查 Token 配置")
        
        # US stocks not supported
        if _is_us_code(stock_code):
            raise DataFetchError(f"TushareFetcher 不支持美股 {stock_code}，请使用 AkshareFetcher 或 YfinanceFetcher")
        
        is_hk = _is_hk_market(stock_code)
         # 判断是否为 ETF / 港股，以选择不同接口
        is_etf = _is_etf_code(stock_code)
        if is_hk:
            ts_code = self._convert_hk_stock_code_for_tushare(stock_code)
            api_name = "hk_daily"
        else:
            ts_code = self._convert_stock_code(stock_code)
            api_name = "fund_daily" if is_etf else "daily"
        
        # Convert date format (Tushare requires YYYYMMDD)
        ts_start = start_date.replace('-', '')
        ts_end = end_date.replace('-', '')
        
       

        logger.debug(f"调用 Tushare {api_name}({ts_code}, {ts_start}, {ts_end})")
        
        try:
            if is_hk:
                self._check_rate_limit()
                # 港股使用 hk_daily 接口
                df = self._api.hk_daily(
                    ts_code=ts_code,
                    start_date=ts_start,
                    end_date=ts_end,
                )
            elif is_etf:
                df = fetch_tushare_fund_daily(self._api, ts_code, ts_start, ts_end,
                                             before_call=self._check_rate_limit)
            else:
                self._check_rate_limit()
                # Regular A-share stocks use daily interface
                df = self._api.daily(
                    ts_code=ts_code,
                    start_date=ts_start,
                    end_date=ts_end,
                )
            
            return df
            
        except Exception as e:
            code = e.code if isinstance(e, TushareApiError) else TushareApiError(str(e)).code
            if code in {"rate_limited", "rate_limited_global"}:
                raise RateLimitError("Tushare 请求频率或额度受限") from e
            if isinstance(e, TushareApiError):
                raise
            raise DataFetchError(f"Tushare 获取数据失败: {e}") from e
    
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        标准化 Tushare 数据
        
        Tushare daily / fund_daily 返回的列名：
        ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount
        
        需要映射到标准列名：
        date, open, high, low, close, volume, amount, pct_chg

        单位缩放仅适用于 A 股（及 ETF 等使用同一套单位的接口）：
        - vol 按「手」计，乘以 100 转为「股」
        - amount 按「千元」计，乘以 1000 转为「元」

        港股 hk_daily 返回的 vol / amount 已是可直接使用的量级，不做上述缩放。
        """
        df = df.copy()
        is_hk = _is_hk_market(stock_code)

        # 列名映射
        column_mapping = {
            'trade_date': 'date',
            'vol': 'volume',
            # open, high, low, close, amount, pct_chg 列名相同
        }
        
        df = df.rename(columns=column_mapping)
        
        # 转换日期格式（YYYYMMDD -> YYYY-MM-DD）
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
        
        # 成交量 / 成交额：仅 A 股类接口做单位换算（港股 hk_daily 不换算）
        if 'volume' in df.columns and not is_hk:
            df['volume'] = df['volume'] * 100
        
        if 'amount' in df.columns and not is_hk:
            df['amount'] = df['amount'] * 1000
        
        # 添加股票代码列
        df['code'] = stock_code
        
        # 只保留需要的列
        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]
        
        return df

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """
        获取股票名称
        
        使用 Tushare 的 stock_basic 接口获取股票基本信息
        
        Args:
            stock_code: 股票代码
            
        Returns:
            股票名称，失败返回 None
        """
        if self._api is None:
            logger.warning("Tushare API 未初始化，无法获取股票名称")
            return None

        # 检查缓存
        if hasattr(self, '_stock_name_cache') and stock_code in self._stock_name_cache:
            return self._stock_name_cache[stock_code]
        
        # 初始化缓存
        if not hasattr(self, '_stock_name_cache'):
            self._stock_name_cache = {}
        
        try:
            # 速率限制检查
            self._check_rate_limit()
            

            # 根据市场/类型选择基础信息接口
            if _is_hk_market(stock_code):
                ts_code = self._convert_hk_stock_code_for_tushare(stock_code)
                # 港股：使用 hk_basic
                df = self._api.hk_basic(
                    ts_code=ts_code,
                    fields='ts_code,name'
                )
            elif _is_etf_code(stock_code):
                ts_code = self._convert_stock_code(stock_code)
                # ETF：使用 fund_basic
                df = self._api.fund_basic(
                    ts_code=ts_code,
                    fields='ts_code,name'
                )
            else:
                ts_code = self._convert_stock_code(stock_code)
                # A 股股票：使用 stock_basic
                df = self._api.stock_basic(
                    ts_code=ts_code,
                    fields='ts_code,name'
                )
            
            if df is not None and not df.empty:
                name = df.iloc[0]['name']
                self._stock_name_cache[stock_code] = name
                logger.debug(f"Tushare 获取股票名称成功: {stock_code} -> {name}")
                return name
            
        except Exception as e:
            logger.warning(f"Tushare 获取股票名称失败 {stock_code}: {e}")
        
        return None
    
    def get_stock_list(self) -> Optional[pd.DataFrame]:
        """
        获取股票列表
        
        使用 Tushare 的 stock_basic 接口获取 A 股列表（不含港股）。
        
        Returns:
            包含 code, name, industry, area, market 列的 DataFrame，失败返回 None
        """
        if self._api is None:
            logger.warning("Tushare API 未初始化，无法获取股票列表")
            return None
        
        try:
            self._check_rate_limit()

            df = self._api.stock_basic(
                exchange='',
                list_status='L',
                fields='ts_code,name,industry,area,market'
            )

            if df is None or df.empty:
                return None

            df = df.copy()
            df['code'] = df['ts_code'].astype(str).str.split('.').str[0]

            if not hasattr(self, '_stock_name_cache'):
                self._stock_name_cache = {}
            for _, row in df.iterrows():
                self._stock_name_cache[row['code']] = row['name']

            logger.info(f"Tushare 获取股票列表成功: {len(df)} 条")
            return df[['code', 'name', 'industry', 'area', 'market']]

        except Exception as e:
            logger.warning(f"Tushare 获取股票列表失败: {e}")

        return None
    
    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        获取实时行情

        策略：
        1. 优先尝试 Pro 接口（需要2000积分）：数据全，稳定性高
        2. 失败降级到旧版接口：门槛低，数据较少

        Args:
            stock_code: 股票代码

        Returns:
            UnifiedRealtimeQuote 对象，失败返回 None
        """
        if self._api is None:
            return None

        # HK stocks not supported by Tushare
        if _is_hk_market(stock_code):
            logger.debug(f"TushareFetcher 跳过港股实时行情 {stock_code}")
            return None

        normalized_code = normalize_stock_code(stock_code)

        from .realtime_types import RealtimeSource

        # 速率限制检查
        self._check_rate_limit()

        # 尝试 Pro 接口
        try:
            ts_code = self._convert_stock_code(stock_code)
            # 尝试调用 Pro 实时接口 (需要积分)
            df = self._api.quotation(ts_code=ts_code)

            if df is not None and len(df) == 1:
                row = df.iloc[0]
                price = optional_quote_number(row.get('price'))
                pre_close = optional_quote_number(row.get('pre_close'))
                if not price or not pre_close or ('ts_code' in row and row['ts_code'] != ts_code):
                    raise TushareApiError(code="invalid_response")
                volume = optional_quote_number(row.get('vol'))
                logger.debug(f"Tushare Pro 实时行情获取成功: {stock_code}")

                return UnifiedRealtimeQuote(
                    code=normalized_code,
                    name=str(row.get('name', '')),
                    source=RealtimeSource.TUSHARE,
                    price=price,
                    change_pct=optional_quote_number(row.get('pct_chg'), signed=True),
                    change_amount=optional_quote_number(row.get('change'), signed=True),
                    volume=int(volume) if volume is not None else None,
                    amount=optional_quote_number(row.get('amount')),
                    high=optional_quote_number(row.get('high')),
                    low=optional_quote_number(row.get('low')),
                    open_price=optional_quote_number(row.get('open')),
                    pre_close=pre_close,
                    turnover_rate=optional_quote_number(row.get('turnover_ratio')),
                    pe_ratio=optional_quote_number(row.get('pe'), signed=True),
                    pb_ratio=optional_quote_number(row.get('pb'), signed=True),
                    total_mv=optional_quote_number(row.get('total_mv')),
                )
        except Exception as e:
            # 仅记录调试日志，不报错，继续尝试降级
            logger.debug("Tushare Pro 实时行情读取失败 %s: %s", stock_code, e)

        # 降级：尝试旧版接口
        try:
            import tushare as ts

            symbol = self._get_legacy_realtime_symbol(stock_code)

            # 调用旧版实时接口 (ts.get_realtime_quotes)
            df = ts.get_realtime_quotes(symbol)

            if df is None or len(df) != 1:
                return None

            row = df.iloc[0]

            # 计算涨跌幅
            price = optional_quote_number(row.get('price'))
            pre_close = optional_quote_number(row.get('pre_close'))
            if not price or not pre_close:
                return None
            change_amount = price - pre_close
            change_pct = (change_amount / pre_close) * 100
            if not math.isfinite(change_pct):
                return None
            volume = optional_quote_number(row.get('volume'))
            name = row.get('name')

            # 构建统一对象
            return UnifiedRealtimeQuote(
                code=normalized_code,
                name=name if isinstance(name, str) else '',
                source=RealtimeSource.TUSHARE,
                price=price,
                change_pct=round(change_pct, 2),
                change_amount=round(change_amount, 2),
                volume=int(volume) // 100 if volume is not None else None,
                amount=optional_quote_number(row.get('amount')),
                high=optional_quote_number(row.get('high')),
                low=optional_quote_number(row.get('low')),
                open_price=optional_quote_number(row.get('open')),
                pre_close=pre_close,
            )

        except Exception as e:
            logger.warning(f"Tushare (旧版) 获取实时行情失败 {stock_code}: {e}")
            return None

    def get_main_indices(self, region: str = "cn") -> Optional[List[dict]]:
        """
        获取主要指数实时行情 (Tushare Pro)，仅支持 A 股
        """
        if region != "cn":
            return None
        if self._api is None:
            return None

        from .tushare_index_daily_rows import select_latest_index_daily_row

        # 指数映射：Tushare代码 -> 名称
        indices_map = {
            '000001.SH': '上证指数',
            '399001.SZ': '深证成指',
            '399006.SZ': '创业板指',
            '000688.SH': '科创50',
            '000016.SH': '上证50',
            '000300.SH': '沪深300',
        }

        try:
            self._check_rate_limit()

            # Tushare index_daily 获取历史数据，实时数据需用其他接口或估算
            # 由于 Tushare 免费用户可能无法获取指数实时行情，这里作为备选
            # 使用 index_daily 获取最近交易日数据

            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - pd.Timedelta(days=5)).strftime('%Y%m%d')

            results = []

            # 批量获取所有指数数据
            for ts_code, name in indices_map.items():
                try:
                    df = self._api.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
                    row = select_latest_index_daily_row(df, ts_code, start_date, end_date)
                    if row is not None:
                        current = optional_quote_number(row.get('close'))
                        change_pct = optional_quote_number(row.get('pct_chg'), signed=True)
                        if not current or change_pct is None:
                            continue
                        amount = optional_quote_number(row.get('amount'))
                        results.append({
                            'code': ts_code.split('.')[0], # 兼容 sh000001 格式需转换，这里保持纯数字
                            'name': name,
                            'current': current,
                            'change': optional_quote_number(row.get('change'), signed=True),
                            'change_pct': change_pct,
                            'open': optional_quote_number(row.get('open')),
                            'high': optional_quote_number(row.get('high')),
                            'low': optional_quote_number(row.get('low')),
                            'prev_close': optional_quote_number(row.get('pre_close')),
                            'volume': optional_quote_number(row.get('vol')),
                            'amount': optional_quote_number(amount * 1000) if amount is not None else None,
                            'amplitude': 0.0 # Tushare index_daily 不直接返回振幅
                        })
                except TushareApiError as error:
                    if error.code in {"permission_denied", "authentication_failed", "rate_limited", "rate_limited_global"}:
                        return None
                    logger.debug("Tushare 获取指数 %s 失败: %s", name, error)
                except Exception as e:
                    logger.debug(f"Tushare 获取指数 {name} 失败: {e}")
                    continue

            if results:
                return results
            else:
                logger.warning("[Tushare] 未获取到指数行情数据")

        except Exception as e:
            logger.error(f"[Tushare] 获取指数行情失败: {e}")

        return None

    def get_market_stats(self) -> Optional[dict]:
        """
        获取市场涨跌统计 (Tushare Pro)
        2000积分 每天访问该接口 ts.pro_api().rt_k 两次
        接口限制见：https://tushare.pro/document/1?doc_id=108
        """
        if self._api is None:
            return None

        try:
            logger.info("[Tushare] ts.pro_api() 获取市场统计...")
            
            # 获取当前中国时间，判断是否在交易时间内
            china_now = self._get_china_now()
            current_clock = china_now.strftime("%H:%M")
            current_date = china_now.strftime("%Y%m%d")

            trade_dates = self._get_trade_dates(current_date)
            if not trade_dates:
                return None

            if current_date in trade_dates:
                if current_clock < '09:30' or current_clock > '16:30':
                    use_realtime = False
                else:
                    use_realtime = True
            else:
                use_realtime = False

            # 若实盘的时候使用 则使用其他可以实盘获取的数据源 akshare、efinance
            if use_realtime:
                try:
                    df = self._call_api_with_rate_limit("rt_k", ts_code='3*.SZ,6*.SH,0*.SZ,92*.BJ')
                    if df is not None and not df.empty:
                        return self._calc_market_stats(df)
                    
                except Exception as e:
                    logger.error(f"[Tushare] ts.pro_api().rt_k 尝试获取实时数据失败: {e}")
                    return None
            else:

                if current_date not in trade_dates:
                    last_date = self._pick_trade_date(trade_dates, use_today=True)  # 拿最近的日期
                else:
                    if current_clock < '09:30': 
                        last_date = self._pick_trade_date(trade_dates, use_today=False)  # 拿取前一天的数据
                    else:  # 即 '> 16:30'                  
                        last_date = self._pick_trade_date(trade_dates, use_today=True)  # 拿取当天的数据

                if last_date is None:
                    return None

                try:
                    df = self._call_api_with_rate_limit(
                        "daily",
                        ts_code='3*.SZ,6*.SH,0*.SZ,92*.BJ',
                        start_date=last_date,
                        end_date=last_date,
                    )
                    # 为防止不同接口返回的列名大小写不一致（例如 rt_k 返回小写，daily 返回大写），统一将列名转为小写
                    df.columns = [col.lower() for col in df.columns]

                    # 获取股票基础信息（包含代码和名称）
                    df_basic = self._call_api_with_rate_limit("stock_basic", fields='ts_code,name')
                    df = pd.merge(df, df_basic, on='ts_code', how='left', validate='many_to_one')
                    if df is not None and not df.empty:
                        return self._calc_market_stats(df, amount_unit=1000)
                except Exception as e:
                    logger.error(f"[Tushare] ts.pro_api().daily 获取数据失败: {e}")
                    

            
        except Exception as e:
            logger.error(f"[Tushare] 获取市场统计失败: {e}")

        return None
    
    def _calc_market_stats(
        self, df: pd.DataFrame, *, amount_unit: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """按完整的有效行情行计算涨跌统计。"""
        aliases = (
            ('代码', '股票代码', 'ts_code', 'stock_code'),
            ('名称', '股票名称', 'name'),
            ('最新价', 'close', 'lastPrice'),
            ('昨收', '昨日收盘', 'pre_close', 'lastClose'),
            ('成交额', 'amount'),
        )
        columns = [next((field for field in names if field in df.columns), None) for names in aliases]
        if None in columns or not df.columns.is_unique:
            return None
        stats = dict(up_count=0, down_count=0, flat_count=0, limit_up_count=0, limit_down_count=0)
        total_amount = 0.0
        seen = set()
        for code, name, current, previous, turnover in df[columns].itertuples(index=False, name=None):
            amount = optional_quote_number(turnover)
            if amount == 0:
                continue
            current_price = optional_quote_number(current)
            pre_close = optional_quote_number(previous)
            if (amount is None or not current_price or not pre_close
                    or not isinstance(code, str) or not isinstance(name, str) or not name.strip()):
                return None
            pure_code = normalize_stock_code(code)
            if not re.fullmatch(r'[0-9]{6}', pure_code) or pure_code in seen:
                return None
            seen.add(pure_code)
            total_amount += amount
            if is_bse_code(pure_code):
                ratio = 0.30
            elif is_kc_cy_stock(pure_code):
                ratio = 0.20
            elif is_st_stock(name):
                ratio = 0.05
            else:
                ratio = 0.10

            for direction, change in (('up', ratio), ('down', -ratio)):
                theoretical = pre_close * (1 + change)
                cents = theoretical * 100 + 0.5
                if not math.isfinite(cents):
                    return None
                limit_price = math.floor(cents) / 100
                tolerance = round(abs(theoretical - limit_price), 10)
                if abs(current_price - limit_price) <= tolerance:
                    stats[f'limit_{direction}_count'] += 1
            if current_price > pre_close:
                stats['up_count'] += 1
            elif current_price < pre_close:
                stats['down_count'] += 1
            else:
                stats['flat_count'] += 1
        total_amount = total_amount / 1e8 * amount_unit
        if not seen or not math.isfinite(total_amount):
            return None
        return {**stats, 'total_amount': total_amount}

    def get_trade_time(self,early_time='09:30',late_time='16:30') -> Optional[str]:
        '''
        获取当前时间可以获得数据的开始时间日期

        Args:
                early_time: 默认 '09:30'
                late_time: 默认 '16:30'
                early_time-late_time 之间为使用上一个交易日数据的时间段，其他时间为使用当天数据的时间段
        Returns:
                start_date: 可以获得数据的开始日期
        '''
        china_now = self._get_china_now()
        china_date = china_now.strftime("%Y%m%d")
        china_clock = china_now.strftime("%H:%M")

        trade_dates = self._get_trade_dates(china_date)
        if not trade_dates:
            return None

        if china_date in trade_dates:
            if  early_time < china_clock < late_time: # 使用上一个交易日数据的时间段
                use_today = False
            else:
                use_today = True
        else:
            # 非交易日： today不在trade_dates中，trade_dates[0]就是最近交易日
            use_today = True

        start_date = self._pick_trade_date(trade_dates, use_today=use_today)
        if start_date is None:
            return None

        if not use_today:
            logger.info(f"[Tushare] 当前时间 {china_clock} 可能无法获取当天筹码分布，尝试获取前一个交易日的数据 {start_date}")

        return start_date
    
    def get_sector_rankings(self, n: int = 5) -> Optional[Tuple[list, list]]:
        """
        获取行业板块涨跌榜 (Tushare Pro)

        数据源优先级：
        1. 同花顺接口 (ts.pro_api().moneyflow_ind_ths)
        2. 东财接口 (ts.pro_api().moneyflow_ind_dc)
        注意：每个接口的行业分类和板块定义不同，会导致结果两者不一致
        """
        try:
            start_date = self.get_trade_time(early_time="00:00", late_time="15:30")
        except (TushareApiError, requests.exceptions.RequestException, TimeoutError) as error:
            logger.warning("Tushare 行业板块排行无法确认交易日: %s", error)
            return None
        if not start_date:
            return None

        logger.info("[Tushare] ts.pro_api().moneyflow_ind_ths 获取板块排行(同花顺)...")
        try:
            result = normalize_sector_rankings(
                self._call_api_with_rate_limit(
                    "moneyflow_ind_ths",
                    trade_date=start_date,
                ),
                name_column="industry",
                change_column="pct_change",
                source="tushare/ths:moneyflow_ind_ths",
                n=n,
            )
            if result is not None:
                return result
        except Exception as e:
            logger.warning(f"[Tushare] 获取同花顺行业板块涨跌榜失败: {e} 尝试东财接口")

        logger.info("[Tushare] ts.pro_api().moneyflow_ind_dc 获取板块排行(东财)...")
        try:
            frame = self._call_api_with_rate_limit(
                "moneyflow_ind_dc",
                trade_date=start_date,
            )
            if frame is None or frame.empty:
                return None
            if "content_type" not in frame.columns:
                return None
            frame = frame[frame["content_type"] == "行业"]
            return normalize_sector_rankings(
                frame,
                name_column="name",
                change_column="pct_change",
                source="tushare/eastmoney:moneyflow_ind_dc",
                n=n,
            )
        except Exception as e:
            logger.warning(f"[Tushare] 获取东财行业板块涨跌榜失败: {e}")
            return None
    
    

    
    def get_chip_distribution(self, stock_code: str, *, strict=False) -> Optional[ChipDistribution]:
        """
        获取筹码分布数据
        
        数据来源：ts.pro_api().cyq_chips()
        包含：获利比例、平均成本、筹码集中度
        
        注意：ETF/指数没有筹码分布数据，会直接返回 None；港股不支持，直接返回 None。
        5000积分以下每天访问15次,每小时访问5次
        
        Args:
            stock_code: 股票代码
            
        Returns:
            ChipDistribution 对象（最新交易日的数据），获取失败返回 None

        """
        if _is_us_code(stock_code):
            logger.warning(f"[Tushare] TushareFetcher 不支持美股 {stock_code} 的筹码分布")
            return None
        
        if _is_etf_code(stock_code):
            logger.warning(f"[Tushare] TushareFetcher 不支持 ETF {stock_code} 的筹码分布")
            return None

        if _is_hk_market(stock_code):
            logger.warning(f"[Tushare] TushareFetcher 不支持港股 {stock_code} 的筹码分布")
            return None
        
        try:
            # 19点之后才有当天数据
            start_date = self.get_trade_time(early_time='00:00', late_time='19:00') 
            if not start_date:
                return None

            ts_code = stock_code if strict else self._convert_stock_code(stock_code)

            df = self._call_api_with_rate_limit(
                "cyq_chips",
                ts_code=ts_code,
                start_date=start_date,
                end_date=start_date,
            )
            if df is not None and not df.empty:
                if strict:
                    self._require_chip_identity(df, ts_code, start_date)
                chips = self._normalize_chip_rows(df)
                daily_df = self._call_api_with_rate_limit(
                    "daily",
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=start_date,
                )
                if daily_df is None or daily_df.empty:
                    return None
                if len(daily_df) != 1:
                    raise TushareApiError(code="invalid_response")
                if strict:
                    self._require_chip_identity(daily_df, ts_code, start_date)
                metrics = self._compute_normalized_chip_metrics(chips, daily_df.iloc[0].get('close'))

                chip = ChipDistribution(
                    code=stock_code,
                    date=datetime.strptime(start_date, '%Y%m%d').strftime('%Y-%m-%d'),
                    profit_ratio=metrics['获利比例'],
                    avg_cost=metrics['平均成本'],
                    cost_90_low=metrics['90成本-低'],
                    cost_90_high=metrics['90成本-高'],
                    concentration_90=metrics['90集中度'],
                    cost_70_low=metrics['70成本-低'],
                    cost_70_high=metrics['70成本-高'],
                    concentration_70=metrics['70集中度'],
                )
                
                logger.info(f"[筹码分布] {stock_code} 日期={chip.date}: 获利比例={chip.profit_ratio:.1%}, "
                        f"平均成本={chip.avg_cost}, 90%集中度={chip.concentration_90:.2%}, "
                        f"70%集中度={chip.concentration_70:.2%}")
                return chip

        except Exception as e:
            if strict:
                raise
            logger.warning(f"[Tushare] 获取筹码分布失败 {stock_code}: {e}")
            return None

    @staticmethod
    def _require_chip_identity(frame, symbol, day):
        if (not {'ts_code', 'trade_date'}.issubset(frame.columns)
                or not frame['ts_code'].eq(symbol).fillna(False).all()
                or not frame['trade_date'].eq(day).fillna(False).all()):
            raise TushareApiError(code="invalid_response")

    @staticmethod
    def _normalize_chip_rows(df):
        if df.empty or not {'price', 'percent'}.issubset(df.columns):
            raise TushareApiError(code="invalid_response")
        chips = df[['price', 'percent']].apply(lambda column: column.map(optional_quote_number))
        if chips.isna().any().any() or (chips['price'] <= 0).any():
            raise TushareApiError(code="invalid_response")
        total_percent = float(chips['percent'].sum())
        if total_percent <= 0 or not math.isfinite(total_percent):
            raise TushareApiError(code="invalid_response")
        chips['norm_percent'] = chips['percent'] / total_percent * 100
        return chips.sort_values('price').reset_index(drop=True)

    def compute_cyq_metrics(self, df: pd.DataFrame, current_price: float) -> dict:
        """
        基于 Tushare 的筹码分布明细表 (cyq_chips) 计算常用筹码指标  
        :param df: 包含 'price' 和 'percent' 列的 DataFrame  
        :param current_price: 股票当天的当前价/收盘价 (用于计算获利比例)  
        :return: 包含各项筹码指标的字典  
        """
        return self._compute_normalized_chip_metrics(self._normalize_chip_rows(df), current_price)

    @staticmethod
    def _compute_normalized_chip_metrics(df_sorted, current_price):
        current_price = optional_quote_number(current_price)
        if not current_price:
            raise TushareApiError(code="invalid_response")
        # 3. 计算筹码的累积分布
        df_sorted['cumsum'] = df_sorted['norm_percent'].cumsum()

        # --- 获利比例 ---
        # 所有价格 <= 当前价的筹码之和
        winner_rate = df_sorted[df_sorted['price'] <= current_price]['norm_percent'].sum()

        # --- 平均成本 ---
        # 价格的加权平均值
        avg_cost = (df_sorted['price'] * (df_sorted['norm_percent'] / 100)).sum()
        if not math.isfinite(avg_cost):
            raise TushareApiError(code="invalid_response")

        # --- 辅助函数：求指定累积比例处的价格 ---
        def get_percentile_price(target_pct):
            # 寻找累积求和第一次大于等于目标百分比的行索引
            idx = df_sorted['cumsum'].searchsorted(target_pct)
            idx = min(idx, len(df_sorted) - 1) # 防止越界
            return df_sorted.loc[idx, 'price']

        # --- 90% 成本区与集中度 ---
        # 去头去尾各 5%
        cost_90_low = get_percentile_price(5)
        cost_90_high = get_percentile_price(95)
        if (cost_90_high + cost_90_low) != 0:
            concentration_90 = (cost_90_high - cost_90_low) / (cost_90_high + cost_90_low) * 100
        else:
            concentration_90 = 0.0
            
        # --- 70% 成本区与集中度 ---
        # 去头去尾各 15%
        cost_70_low = get_percentile_price(15)
        cost_70_high = get_percentile_price(85)
        if (cost_70_high + cost_70_low) != 0:
            concentration_70 = (cost_70_high - cost_70_low) / (cost_70_high + cost_70_low) * 100
        else:
            concentration_70 = 0.0

        # 返回格式化结果
        return {
            "获利比例": round(winner_rate/100, 4), # /100 与akshare保持一致，返回小数格式
            "平均成本": round(avg_cost, 4),
            "90成本-低": round(cost_90_low, 4),
            "90成本-高": round(cost_90_high, 4),
            "90集中度": round(concentration_90/100, 4),
            "70成本-低": round(cost_70_low, 4),
            "70成本-高": round(cost_70_high, 4),
            "70集中度": round(concentration_70/100, 4)
        }



if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    fetcher = TushareFetcher()
    
    try:
        # 测试历史数据
        df = fetcher.get_daily_data('600519')  # 茅台
        print(f"获取成功，共 {len(df)} 条数据")
        print(df.tail())
        
        # 测试股票名称
        name = fetcher.get_stock_name('600519')
        print(f"股票名称: {name}")
        
    except Exception as e:
        print(f"获取失败: {e}")

    # 测试市场统计
    print("\n" + "=" * 50)
    print("Testing get_market_stats (tushare)")
    print("=" * 50)
    try:
        stats = fetcher.get_market_stats()
        if stats:
            print(f"Market Stats successfully computed:")
            print(f"Up: {stats['up_count']} (Limit Up: {stats['limit_up_count']})")
            print(f"Down: {stats['down_count']} (Limit Down: {stats['limit_down_count']})")
            print(f"Flat: {stats['flat_count']}")
            print(f"Total Amount: {stats['total_amount']:.2f} 亿 (Yi)")
        else:
            print("Failed to compute market stats.")
    except Exception as e:
        print(f"Failed to compute market stats: {e}")


    # 测试筹码分布数据
    print("\n" + "=" * 50)
    print("测试筹码分布数据获取")
    print("=" * 50)
    try:
        chip = fetcher.get_chip_distribution('600519')  # 茅台
    except Exception as e:
        print(f"[筹码分布] 获取失败: {e}")

    # 测试行业板块排名
    print("\n" + "=" * 50)
    print("测试行业板块排名获取")
    print("=" * 50)
    try:
        rankings = fetcher.get_sector_rankings(n=5)
        if rankings:
            top, bottom = rankings
            print("涨幅榜 Top 5:")
            for sector in top:
                print(f"{sector['name']}: {sector['change_pct']}%")
            print("\n跌幅榜 Top 5:")
            for sector in bottom:
                print(f"{sector['name']}: {sector['change_pct']}%")
        else:
            print("未获取到行业板块排名数据")
    except Exception as e:
        print(f"[行业板块排名] 获取失败: {e}")
