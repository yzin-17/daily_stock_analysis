"""公开网页搜索的服务间认证入口。"""
from fastapi import APIRouter, Depends
from api.thesis_ledger_auth import require_contract_token
from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime
from src.services.thesis_ledger_public_search import SearchRequest, SearchResult, execute_public_search
router = APIRouter()


@router.post("/thesis-ledger/research/search", dependencies=[Depends(require_contract_token)], response_model=SearchResult)
def search(query: SearchRequest):
    return execute_public_search(get_thesis_ledger_runtime(), query)
