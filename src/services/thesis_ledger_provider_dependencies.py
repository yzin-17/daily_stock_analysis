"""运行环境的来源依赖检查；只检查安装能力，不调用外部服务。"""

from importlib.util import find_spec
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES

def provider_dependency_ready(provider, key=None, target=None):
    source = CAPABILITY_SOURCES.source(provider)
    modules = source.dependency_modules if source is not None else ()
    binding = CAPABILITY_SOURCES.resolve(key, target) if key and target else None
    exact_modules = getattr(binding, 'dependency_modules', None)
    if exact_modules is not None:
        modules = exact_modules
    try:
        return all(find_spec(module) is not None for module in modules)
    except (ImportError, ValueError, AttributeError):
        return False
