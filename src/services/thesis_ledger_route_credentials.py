"""精确能力的凭证要求，由来源契约定义，供目录与执行共用。"""

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


def route_requires_credential(key, target, manifest):
    source = CAPABILITY_SOURCES.source(target["providerId"])
    if source is not None and source.route_requires_credential is not None:
        required = source.route_requires_credential(key, target)
        if type(required) is not bool:
            raise ValueError("来源精确能力的凭证要求必须为布尔值")
        return required
    return manifest.get("requiresCredential")
