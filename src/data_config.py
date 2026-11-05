"""独立的数据配置与环境解析；不导入产品、Provider SDK 或应用装配。"""

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
from threading import RLock
from typing import Dict, List, Mapping, MutableMapping, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_FALSEY_ENV_VALUES = {"0", "false", "no", "off"}

TICKFLOW_KLINE_ADJUST_VALUES = {"none", "forward", "backward", "forward_additive", "backward_additive"}


def normalize_tickflow_kline_adjust(value: Optional[str]) -> str:
    """保留 TickFlow 已有复权值与缺省语义。"""
    normalized = (value or "none").strip().lower()
    if normalized in TICKFLOW_KLINE_ADJUST_VALUES:
        return normalized
    logger.warning(
        "TICKFLOW_KLINE_ADJUST 无效，回退为 none",
    )
    return "none"


FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT = 8.0

NEWS_STRATEGY_WINDOWS: Dict[str, int] = {
    "ultra_short": 1,
    "short": 3,
    "medium": 7,
    "long": 30,
}


def parse_env_bool(value: Optional[str], default: bool = False) -> bool:
    """保留既有环境布尔值的真值与缺省规则。"""
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized not in _FALSEY_ENV_VALUES


def parse_env_int(
    value: Optional[str],
    default: int,
    *,
    field_name: str,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """按既有下界、上界与回退值解析整数。"""
    raw_value = value
    if raw_value is None or not str(raw_value).strip():
        parsed = int(default)
    else:
        try:
            parsed = int(str(raw_value).strip())
        except (TypeError, ValueError):
            logger.warning(
                "%s 参数无效，回退为 %s",
                field_name,
                default,
            )
            parsed = int(default)

    if minimum is not None and parsed < minimum:
        logger.warning(
            "%s=%r is below minimum %s; clamping to %s",
            field_name,
            parsed,
            minimum,
            minimum,
        )
        parsed = minimum
    if maximum is not None and parsed > maximum:
        logger.warning(
            "%s=%r is above maximum %s; clamping to %s",
            field_name,
            parsed,
            maximum,
            maximum,
        )
        parsed = maximum
    return parsed


def parse_env_float(
    value: Optional[str],
    default: float,
    *,
    field_name: str,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """按既有下界、上界与回退值解析浮点参数。"""
    raw_value = value
    if raw_value is None or not str(raw_value).strip():
        parsed = float(default)
    else:
        try:
            parsed = float(str(raw_value).strip())
        except (TypeError, ValueError):
            logger.warning(
                "%s 参数无效，回退为 %s",
                field_name,
                default,
            )
            parsed = float(default)

    if minimum is not None and parsed < minimum:
        logger.warning(
            "%s=%r is below minimum %s; clamping to %s",
            field_name,
            parsed,
            minimum,
            minimum,
        )
        parsed = minimum
    if maximum is not None and parsed > maximum:
        logger.warning(
            "%s=%r is above maximum %s; clamping to %s",
            field_name,
            parsed,
            maximum,
            maximum,
        )
        parsed = maximum
    return parsed


def normalize_news_strategy_profile(value: Optional[str]) -> str:
    """将新闻窗口策略限制在既有集合内。"""
    candidate = (value or "short").strip().lower()
    return candidate if candidate in NEWS_STRATEGY_WINDOWS else "short"


def resolve_news_window_days(news_max_age_days: int, news_strategy_profile: Optional[str]) -> int:
    """计算新闻策略与最大时效共同允许的天数。"""
    profile = normalize_news_strategy_profile(news_strategy_profile)
    profile_days = NEWS_STRATEGY_WINDOWS.get(profile, NEWS_STRATEGY_WINDOWS["short"])
    return max(1, min(max(1, int(news_max_age_days)), profile_days))


class DataConfigurationError(RuntimeError):
    """配置加载失败与正常缺少来源凭据分别处理。"""


def _split_values(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_search_urls(value: str) -> List[str]:
    valid = []
    invalid_count = 0
    for url in _split_values(value):
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            valid.append(url)
        else:
            invalid_count += 1
    if invalid_count:
        logger.warning("SEARXNG_BASE_URLS 中有 %s 个无效地址，已忽略", invalid_count)
    return valid


def parse_news_strategy_profile(value: Optional[str]) -> str:
    normalized = normalize_news_strategy_profile(value)
    if (value or "short").strip().lower() != normalized:
        logger.warning("NEWS_STRATEGY_PROFILE 无效，回退为 short")
    return normalized


def resolve_realtime_source_priority(values: Mapping[str, str]) -> str:
    explicit = values.get("REALTIME_SOURCE_PRIORITY")
    default = "tencent,akshare_sina,efinance,akshare_em"
    if explicit:
        return explicit
    if values.get("TUSHARE_TOKEN", "").strip():
        return "tushare," + default
    return default


def configure_data_proxy_environment(values: MutableMapping[str, str]) -> None:
    """保留既有代理别名与国内来源绕过设置，不发出请求。"""
    http_proxy = values.get('HTTP_PROXY') or values.get('http_proxy')
    if http_proxy:
        # 国内金融数据源域名列表
        domestic_domains = [
            'eastmoney.com',   # 东方财富 (Efinance/Akshare)
            'sina.com.cn',     # 新浪财经 (Akshare)
            '163.com',         # 网易财经 (Akshare)
            'tushare.pro',     # Tushare
            'baostock.com',    # Baostock
            'sse.com.cn',      # 上交所
            'szse.cn',         # 深交所
            'csindex.com.cn',  # 中证指数
            'cninfo.com.cn',   # 巨潮资讯
            'localhost',
            '127.0.0.1'
        ]

        # 获取现有的 no_proxy
        current_no_proxy = values.get('NO_PROXY') or values.get('no_proxy') or ''
        existing_domains = current_no_proxy.split(',') if current_no_proxy else []

        # 合并去重
        final_domains = list(set(existing_domains + domestic_domains))
        final_no_proxy = ','.join(filter(None, final_domains))

        # 设置环境变量 (requests/urllib3/aiohttp 都会遵守此设置)
        values['NO_PROXY'] = final_no_proxy
        values['no_proxy'] = final_no_proxy

        # 确保 HTTP_PROXY 也被正确设置（以防仅在 .env 中定义但未导出）
        values['HTTP_PROXY'] = http_proxy
        values['http_proxy'] = http_proxy

        # HTTPS_PROXY 同理
        https_proxy = values.get('HTTPS_PROXY') or values.get('https_proxy')
        if https_proxy:
            values['HTTPS_PROXY'] = https_proxy
            values['https_proxy'] = https_proxy


@dataclass
class DataServiceConfig:
    """只保存已确认的数据、缓存、搜索、路径和 SQLite 配置。"""

    tushare_token: Optional[str] = field(default=None, repr=False)
    tickflow_api_key: Optional[str] = field(default=None, repr=False)
    tickflow_kline_adjust: str = "none"
    tickflow_priority: int = 2
    tickflow_batch_daily_enabled: bool = True
    tickflow_batch_size: int = 100
    finnhub_api_key: Optional[str] = field(default=None, repr=False)
    alphavantage_api_key: Optional[str] = field(default=None, repr=False)
    longbridge_app_key: Optional[str] = field(default=None, repr=False)
    longbridge_app_secret: Optional[str] = field(default=None, repr=False)
    longbridge_access_token: Optional[str] = field(default=None, repr=False)
    longbridge_oauth_client_id: Optional[str] = field(default=None, repr=False)
    stock_index_remote_update_enabled: bool = True
    anspire_api_keys: List[str] = field(default_factory=list, repr=False)
    bocha_api_keys: List[str] = field(default_factory=list, repr=False)
    minimax_api_keys: List[str] = field(default_factory=list, repr=False)
    tavily_api_keys: List[str] = field(default_factory=list, repr=False)
    brave_api_keys: List[str] = field(default_factory=list, repr=False)
    serpapi_keys: List[str] = field(default_factory=list, repr=False)
    searxng_base_urls: List[str] = field(default_factory=list, repr=False)
    searxng_public_instances_enabled: bool = True
    news_max_age_days: int = 3
    news_strategy_profile: str = "short"
    prefetch_realtime_quotes: bool = True
    database_path: str = "./data/stock_analysis.db"
    sqlite_wal_enabled: bool = True
    sqlite_busy_timeout_ms: int = 5000
    sqlite_write_retry_max: int = 3
    sqlite_write_retry_base_delay: float = 0.1
    log_dir: str = "./logs"
    log_level: str = "INFO"
    enable_realtime_quote: bool = True
    enable_chip_distribution: bool = True
    enable_eastmoney_patch: bool = False
    realtime_source_priority: str = "tencent,akshare_sina,efinance,akshare_em"
    realtime_cache_ttl: int = 600
    enable_fundamental_pipeline: bool = True
    fundamental_stage_timeout_seconds: float = FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT
    fundamental_fetch_timeout_seconds: float = 8.0
    fundamental_retry_max: int = 1
    fundamental_cache_ttl_seconds: int = 120
    fundamental_cache_max_entries: int = 256

    @classmethod
    def from_env(cls, values: Mapping[str, str]) -> "DataServiceConfig":
        """显式输入解析；不读取文件、修改环境或初始化来源。"""
        return cls(
            tushare_token=values.get('TUSHARE_TOKEN'),
            tickflow_api_key=values.get('TICKFLOW_API_KEY'),
            tickflow_kline_adjust=normalize_tickflow_kline_adjust(values.get('TICKFLOW_KLINE_ADJUST')),
            tickflow_priority=parse_env_int(values.get('TICKFLOW_PRIORITY'), 2, field_name='TICKFLOW_PRIORITY', minimum=0),
            tickflow_batch_daily_enabled=parse_env_bool(values.get('TICKFLOW_BATCH_DAILY_ENABLED'), default=True),
            tickflow_batch_size=parse_env_int(values.get('TICKFLOW_BATCH_SIZE'), 100, field_name='TICKFLOW_BATCH_SIZE', minimum=1),
            finnhub_api_key=values.get('FINNHUB_API_KEY') or None,
            alphavantage_api_key=values.get('ALPHAVANTAGE_API_KEY') or None,
            longbridge_app_key=values.get('LONGBRIDGE_APP_KEY') or None,
            longbridge_app_secret=values.get('LONGBRIDGE_APP_SECRET') or None,
            longbridge_access_token=values.get('LONGBRIDGE_ACCESS_TOKEN') or None,
            longbridge_oauth_client_id=values.get('LONGBRIDGE_OAUTH_CLIENT_ID') or None,
            stock_index_remote_update_enabled=parse_env_bool(values.get('STOCK_INDEX_REMOTE_UPDATE_ENABLED'), default=True),
            anspire_api_keys=_split_values(values.get('ANSPIRE_API_KEYS', '')),
            bocha_api_keys=_split_values(values.get('BOCHA_API_KEYS', '')),
            minimax_api_keys=_split_values(values.get('MINIMAX_API_KEYS', '')),
            tavily_api_keys=_split_values(values.get('TAVILY_API_KEYS', '')),
            brave_api_keys=_split_values(values.get('BRAVE_API_KEYS', '')),
            serpapi_keys=_split_values(values.get('SERPAPI_API_KEYS', '')),
            searxng_base_urls=_parse_search_urls(values.get('SEARXNG_BASE_URLS', '')),
            searxng_public_instances_enabled=parse_env_bool(values.get('SEARXNG_PUBLIC_INSTANCES_ENABLED'), default=True),
            news_max_age_days=parse_env_int(values.get('NEWS_MAX_AGE_DAYS'), 3, field_name='NEWS_MAX_AGE_DAYS', minimum=1),
            news_strategy_profile=parse_news_strategy_profile(values.get('NEWS_STRATEGY_PROFILE', 'short')),
            prefetch_realtime_quotes=values.get('PREFETCH_REALTIME_QUOTES', 'true').lower() == 'true',
            database_path=values.get('DATABASE_PATH', './data/stock_analysis.db'),
            sqlite_wal_enabled=values.get('SQLITE_WAL_ENABLED', 'true').lower() == 'true',
            sqlite_busy_timeout_ms=parse_env_int(values.get('SQLITE_BUSY_TIMEOUT_MS'), 5000, field_name='SQLITE_BUSY_TIMEOUT_MS', minimum=0),
            sqlite_write_retry_max=parse_env_int(values.get('SQLITE_WRITE_RETRY_MAX'), 3, field_name='SQLITE_WRITE_RETRY_MAX', minimum=0),
            sqlite_write_retry_base_delay=parse_env_float(values.get('SQLITE_WRITE_RETRY_BASE_DELAY'), 0.1, field_name='SQLITE_WRITE_RETRY_BASE_DELAY', minimum=0.0),
            log_dir=values.get('LOG_DIR', './logs'),
            log_level=values.get('LOG_LEVEL', 'INFO'),
            enable_realtime_quote=values.get('ENABLE_REALTIME_QUOTE', 'true').lower() == 'true',
            enable_chip_distribution=values.get('ENABLE_CHIP_DISTRIBUTION', 'true').lower() == 'true',
            enable_eastmoney_patch=values.get('ENABLE_EASTMONEY_PATCH', 'false').lower() == 'true',
            realtime_source_priority=resolve_realtime_source_priority(values),
            realtime_cache_ttl=parse_env_int(values.get('REALTIME_CACHE_TTL'), 600, field_name='REALTIME_CACHE_TTL', minimum=0),
            enable_fundamental_pipeline=values.get('ENABLE_FUNDAMENTAL_PIPELINE', 'true').lower() == 'true',
            fundamental_stage_timeout_seconds=parse_env_float(values.get('FUNDAMENTAL_STAGE_TIMEOUT_SECONDS'), FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT, field_name='FUNDAMENTAL_STAGE_TIMEOUT_SECONDS', minimum=0.0),
            fundamental_fetch_timeout_seconds=parse_env_float(values.get('FUNDAMENTAL_FETCH_TIMEOUT_SECONDS'), 8.0, field_name='FUNDAMENTAL_FETCH_TIMEOUT_SECONDS', minimum=0.0),
            fundamental_retry_max=parse_env_int(values.get('FUNDAMENTAL_RETRY_MAX'), 1, field_name='FUNDAMENTAL_RETRY_MAX', minimum=0),
            fundamental_cache_ttl_seconds=parse_env_int(values.get('FUNDAMENTAL_CACHE_TTL_SECONDS'), 120, field_name='FUNDAMENTAL_CACHE_TTL_SECONDS', minimum=0),
            fundamental_cache_max_entries=parse_env_int(values.get('FUNDAMENTAL_CACHE_MAX_ENTRIES'), 256, field_name='FUNDAMENTAL_CACHE_MAX_ENTRIES', minimum=1),
        )

    def get_effective_news_window_days(self) -> int:
        return resolve_news_window_days(self.news_max_age_days, self.news_strategy_profile)


_instance: Optional[DataServiceConfig] = None
_lock = RLock()


def load_data_env(override: bool = False) -> None:
    """沿用 ENV_FILE 与缺省位置；文件加载失败不能被视为缺少凭据。"""
    path = Path(os.environ.get("ENV_FILE") or Path(__file__).parent.parent / ".env")
    try:
        if path.exists() and not path.is_file():
            raise DataConfigurationError("数据配置路径不是文件")
        load_dotenv(dotenv_path=path, override=override)
    except DataConfigurationError:
        raise
    except Exception as exc:
        raise DataConfigurationError("数据配置文件加载失败") from exc
    configure_data_proxy_environment(os.environ)


def get_data_config() -> DataServiceConfig:
    """按进程缓存解析结果；不实例化 SDK、任务或数据库。"""
    global _instance
    with _lock:
        if _instance is None:
            try:
                load_data_env()
                _instance = DataServiceConfig.from_env(os.environ)
            except DataConfigurationError:
                raise
            except Exception as exc:
                raise DataConfigurationError("数据配置解析失败") from exc
        return _instance


def reset_data_config() -> None:
    """显式清除本模块缓存，不重置其他应用或环境变量。"""
    global _instance
    with _lock:
        _instance = None
