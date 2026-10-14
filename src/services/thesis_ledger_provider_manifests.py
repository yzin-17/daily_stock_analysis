"""Provider 静态能力清单；凭据和路由状态仍由 Control 存储管理。"""

from typing import Any
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


PROVIDER_MANIFESTS: dict[str, dict[str, Any]] = CAPABILITY_SOURCES.manifests()

