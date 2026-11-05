from pathlib import Path

from dotenv import dotenv_values

from src.data_config import DataServiceConfig

ROOT_DIR = Path(__file__).resolve().parents[1]


def test_env_example_documents_dedicated_searxng_configuration() -> None:
    path = ROOT_DIR / ".env.example"
    values = dotenv_values(path)
    config = DataServiceConfig.from_env(values)
    assert "专属实例地址" in path.read_text(encoding="utf-8")
    assert "SEARXNG_BASE_URLS" in values
    assert config.searxng_base_urls == []
    assert not config.searxng_public_instances_enabled


def test_daily_analysis_workflow_matches_documented_searxng_variable_mapping() -> None:
    workflow = (
        ROOT_DIR / ".github" / "workflows" / "00-daily-analysis.yml"
    ).read_text(encoding="utf-8")

    assert (
        "SEARXNG_BASE_URLS: ${{ vars.SEARXNG_BASE_URLS || secrets.SEARXNG_BASE_URLS }}"
        in workflow
    )
    assert "SEARXNG_BASE_URLS: ${{ secrets.SEARXNG_BASE_URLS }}" not in workflow


def test_changelog_mentions_searxng_actions_variable_mapping() -> None:
    changelog = (ROOT_DIR / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")

    assert (
        "- [修复] GitHub Actions 每日分析工作流读取 SearXNG 自建实例地址时"
        "支持 Variables 优先、Secrets 回退，修复仅配置 Variables 时 URL 不生效的问题。"
    ) in changelog
