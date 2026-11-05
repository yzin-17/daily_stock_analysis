"""复用既有搜索 Adapter；指定单一来源，不读取产品配置或启用公共实例。"""

from dataclasses import dataclass, field
import json
from urllib.parse import urlsplit

from src.data_config import DataConfigurationError

PROVIDERS = {
    "tavily": ("TavilySearchProvider", "api.tavily.com", 20),
    "serpapi": ("SerpAPISearchProvider", "serpapi.com", 50),
    "bocha": ("BochaSearchProvider", "api.bocha.cn", 50),
    "anspire": ("AnspireSearchProvider", "plugin.anspire.cn", 50),
    "minimax": ("MiniMaxSearchProvider", "api.minimaxi.com", 50),
    "brave": ("BraveSearchProvider", "api.search.brave.com", 20),
    "searxng": ("SearXNGSearchProvider", None, 50),
}


@dataclass(frozen=True)
class NewsSearchSettings:
    provider_id: str
    api_keys: tuple[str, ...] = field(default=(), repr=False)
    base_urls: tuple[str, ...] = field(default=(), repr=False)

    @property
    def result_limit(self) -> int | None:
        definition = PROVIDERS.get(self.provider_id)
        return definition[2] if definition is not None else None

    def configured(self) -> bool:
        if self.provider_id == "searxng":
            return bool(self.base_urls)
        return any(key.strip() for key in self.api_keys)

    def permits_url(self, value: str) -> bool:
        target = urlsplit(value)
        if target.scheme not in {"http", "https"}:
            return False
        if self.provider_id == "searxng":
            origins = {(source.scheme, source.hostname, source.port or (443 if source.scheme == "https" else 80))
                       for source in map(urlsplit, self.base_urls)}
            return (target.scheme, target.hostname, target.port or (443 if target.scheme == "https" else 80)) in origins
        definition = PROVIDERS.get(self.provider_id)
        return bool(definition and target.scheme == "https" and target.hostname == definition[1])


def build_news_source(settings: NewsSearchSettings):
    from src import search_service
    cls = getattr(search_service, PROVIDERS[settings.provider_id][0])
    if settings.provider_id == "searxng":
        return cls(list(settings.base_urls), use_public_instances=False)
    return cls([key.strip() for key in settings.api_keys if key.strip()])


def news_settings(provider_id, *, config=None, values=None):
    """显式控制凭据不回退到环境；空值与配置加载失败分别处理。"""
    if provider_id not in PROVIDERS:
        raise ValueError("未声明的新闻来源")
    if values is not None:
        field_name = "baseUrls" if provider_id == "searxng" else "apiKeys"
        try:
            raw = json.loads(values[field_name]) if values.get(field_name) else []
            if not isinstance(raw, list) or any(not isinstance(value, str) or not value.strip() for value in raw):
                raise ValueError("新闻凭据字段必须是非空字符串列表")
            parsed = tuple(value.strip() for value in raw)
        except (ValueError, TypeError):
            raise DataConfigurationError("新闻来源凭据格式无效") from None
    else:
        if config is None:
            from src.data_config import get_data_config
            config = get_data_config()
        fields = {"serpapi": "serpapi_keys", "searxng": "searxng_base_urls"}
        key = fields.get(provider_id, f"{provider_id}_api_keys")
        parsed = tuple(getattr(config, key))
    if provider_id == "searxng":
        for value in parsed:
            try:
                url = urlsplit(value)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                    raise ValueError("实例地址无效")
                url.port
            except ValueError:
                raise DataConfigurationError("新闻实例地址无效") from None
        return NewsSearchSettings(provider_id, base_urls=parsed)
    return NewsSearchSettings(provider_id, api_keys=parsed)
