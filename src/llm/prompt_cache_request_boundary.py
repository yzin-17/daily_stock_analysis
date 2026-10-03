"""Sanitize configured LiteLLM deployments at the Router request boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence


def sanitize_prompt_cache_router_deployments(
    model_list: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Copy deployments and remove configured cache keys before Router merges them."""
    deployments = deepcopy(list(model_list))
    for deployment in deployments:
        params = deployment.get("litellm_params")
        if isinstance(params, dict):
            params.pop("prompt_cache_key", None)
    return deployments
