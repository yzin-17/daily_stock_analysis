"""news-feed-v1：市场快讯与个股检索分别建模，不推断证券关联。"""

from typing import Literal
from pydantic import Field

from .thesis_ledger_news_contract_v1 import (
    NewsCoverage, NewsInstrument, NewsItem, NewsResponseContent, NewsWindowRequest,
)

FEED_CAPABILITY = "NEWS_FEED"
FEED_CONTRACT_VERSION = "news-feed-v1"
FEED_SCOPE_SYMBOL = "CN.STOCK"


class NewsFeedRequest(NewsWindowRequest):
    contractVersion: Literal["news-feed-v1"] = FEED_CONTRACT_VERSION
    capability: Literal["NEWS_FEED"] = FEED_CAPABILITY
    market: Literal["CN"]
    providerId: str | None = Field(default=None, min_length=1, max_length=128)


class NewsFeedItem(NewsItem):
    relatedInstruments: list[NewsInstrument] = Field(default_factory=list, max_length=0)


class NewsFeedCoverage(NewsCoverage):
    basis: Literal["current-feed"] = "current-feed"


class NewsFeedResponse(NewsResponseContent):
    version: Literal[1] = 1
    contractVersion: Literal["news-feed-v1"] = FEED_CONTRACT_VERSION
    capability: Literal["NEWS_FEED"] = FEED_CAPABILITY
    market: Literal["CN"]
    items: list[NewsFeedItem] = Field(max_length=50)
    coverage: NewsFeedCoverage


def build_news_feed_response(request, route, read, *, fetched_at):
    from .thesis_ledger_news_results import build_news_content
    return NewsFeedResponse(
        requestId=request.requestId, market=request.market,
        **build_news_content(request, route, read, fetched_at=fetched_at, related_instruments=[],
                             item_type=NewsFeedItem, coverage_type=NewsFeedCoverage),
    )
