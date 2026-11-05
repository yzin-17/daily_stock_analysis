"""新闻请求的显式证券或市场范围；市场标识不冒充证券代码。"""

from .thesis_ledger_news_contract_v1 import NewsResponse
from .thesis_ledger_news_feed_contract_v1 import (
    FEED_SCOPE_SYMBOL, NewsFeedResponse, build_news_feed_response,
)
from .thesis_ledger_news_results import build_news_response


def news_scope(query):
    if query.capability == "NEWS_FEED":
        return ({"kind": "data", "market": query.market, "assetType": "STOCK", "capability": "NEWS_FEED"},
                FEED_SCOPE_SYMBOL)
    return ({"kind": "data", "market": query.instrument.market,
             "assetType": query.instrument.assetType, "capability": "NEWS_SEARCH"}, query.instrument.symbol)


def news_response_model(query):
    return NewsFeedResponse if query.capability == "NEWS_FEED" else NewsResponse


def news_response(query, route, read, *, fetched_at):
    builder = build_news_feed_response if query.capability == "NEWS_FEED" else build_news_response
    return builder(query, route, read, fetched_at=fetched_at)
