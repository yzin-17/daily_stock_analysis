"""ThesisLedger 已声明来源的环境凭证解析。"""

from collections.abc import Mapping
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES

def environment_credential_values(provider_id: str) -> dict[str, str]:
    source = CAPABILITY_SOURCES.source(provider_id)
    if source is not None and source.environment_credential_policy is not None:
        return source.environment_credential_policy.read()
    return read_config_fields(())


def read_config_fields(fields: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """按来源声明读取既有Config字段；保持原空值和异常边界。"""
    try:
        from src.config import get_config
        runtime_config = get_config()
        return {
            field: str(getattr(runtime_config, env_name, "") or "").strip()
            for field, env_name in fields
            if str(getattr(runtime_config, env_name, "") or "").strip()
        }
    except Exception:
        return {}


def environment_credential_method(provider_id: str, values: Mapping[str, str]) -> str | None:
    source = CAPABILITY_SOURCES.source(provider_id)
    if not values or source is None or not source.credentials:
        return None
    policy = source.environment_credential_policy
    if policy is not None and policy.select_method is not None:
        return policy.select_method(values)
    return source.credentials[0][0]


def environment_snapshot_values(provider_id: str, values: dict[str, str]) -> dict[str, str]:
    source = CAPABILITY_SOURCES.source(provider_id)
    policy = source.environment_credential_policy if source is not None else None
    if policy is not None and policy.snapshot_values is not None:
        return policy.snapshot_values(values)
    return values

