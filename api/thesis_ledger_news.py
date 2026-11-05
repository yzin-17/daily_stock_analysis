"""固定新闻合同的认证 POST 入口。"""

from uuid import uuid4

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError

from api.thesis_ledger_auth import require_contract_token
from api.thesis_ledger_market_errors import market_data_error_v3
from src.services.thesis_ledger_news_contract_v1 import NewsRequest
from src.services.thesis_ledger_news_feed_contract_v1 import NewsFeedRequest
from src.services.thesis_ledger_news_gateway import execute_news_request


def build_news_router(get_runtime):
    router = APIRouter()

    def read(payload, request_type):
        request_id = payload.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            request_id = str(uuid4())
        try:
            query = request_type.model_validate(payload)
        except ValidationError:
            market_data_error_v3("invalid_request", 422, request_id)
        return execute_news_request(get_runtime(), query).model_dump()

    @router.post("/market/news", dependencies=[Depends(require_contract_token)])
    def read_news(payload: dict = Body(...)):
        return read(payload, NewsRequest)

    @router.post("/market/news-feed", dependencies=[Depends(require_contract_token)])
    def read_feed(payload: dict = Body(...)):
        return read(payload, NewsFeedRequest)

    return router
