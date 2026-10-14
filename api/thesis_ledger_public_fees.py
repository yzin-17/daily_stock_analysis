"""公共收费正式入口；沿用本地历史规则网关的认证和错误契约。"""

from typing import Any, Callable
from fastapi import APIRouter

from api.thesis_ledger_local_rules import build_rule_data_router
from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime
from src.services.thesis_ledger_public_fees import CAPABILITY


def build_public_fee_router(get_runtime: Callable[..., Any] = get_thesis_ledger_runtime) -> APIRouter:
    return build_rule_data_router(CAPABILITY, "/market/public-fees", "公共收费", get_runtime)
