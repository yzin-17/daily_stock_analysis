"""ThesisLedger 已声明来源的环境凭证解析。"""

from collections.abc import Mapping
from src.data_config import DataConfigurationError
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


def environment_credential_values(provider_id: str) -> dict[str, str]:
    source = CAPABILITY_SOURCES.source(provider_id)
    if source is not None and source.environment_credential_policy is not None:
        return source.environment_credential_policy.read()
    return read_config_fields(())


def read_config_fields(fields: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """读取数据配置；正常空值与内部加载失败分别处理。"""
    if not fields:
        return {}
    try:
        from src.data_config import get_data_config
        runtime_config = get_data_config()
        return {
            field: str(getattr(runtime_config, env_name, "") or "").strip()
            for field, env_name in fields
            if str(getattr(runtime_config, env_name, "") or "").strip()
        }
    except DataConfigurationError:
        raise
    except Exception:
        raise DataConfigurationError("数据凭据配置读取失败") from None


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
