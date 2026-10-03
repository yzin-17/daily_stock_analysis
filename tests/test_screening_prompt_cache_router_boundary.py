# -*- coding: utf-8 -*-
"""Screening Router deployment prompt-cache boundary tests."""

import sys
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from src.services.screening.ranker import _call_llm


def test_screening_router_drops_configured_key_before_dispatch(tmp_path):
    config_path = tmp_path / "litellm.yaml"
    config_path.write_text(
        """model_list:
  - model_name: openai/gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: synthetic-key
      api_base: http://127.0.0.1:8765/v1
      prompt_cache_key: synthetic-cache-key
    model_info:
      id: synthetic-route
""",
        encoding="utf-8",
    )
    captured = []
    routers = []

    class CapturingRouter:
        def __init__(self, *, model_list):
            self.model_list = model_list
            routers.append(self)

        def completion(self, **kwargs):
            captured.append({**self.model_list[0]["litellm_params"], **kwargs})
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            )

    fake_litellm = SimpleNamespace(Router=CapturingRouter)
    with patch.dict(sys.modules, {"litellm": fake_litellm}, clear=False):
        result = _call_llm(
            "synthetic prompt",
            api_key="synthetic-key",
            model="openai/gpt-4o-mini",
            base_url="",
            config_path=str(config_path),
            json_mode=False,
        )

    assert result == "ok"
    assert len(captured) == 1
    assert "prompt_cache_key" not in captured[0]
    assert captured[0]["api_base"] == "http://127.0.0.1:8765/v1"
    assert routers[0].model_list[0]["model_info"] == {"id": "synthetic-route"}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["model_list"][0][
        "litellm_params"
    ]["prompt_cache_key"] == "synthetic-cache-key"
