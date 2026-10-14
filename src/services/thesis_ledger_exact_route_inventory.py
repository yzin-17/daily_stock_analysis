"""精确能力的唯一组合库存；声明本身不授予配置或证据资格。"""

from src.services.thesis_ledger_market_v3_adapters import iter_market_v3_bar_adapters, iter_market_v3_gated_bar_adapters
from src.services.thesis_ledger_event_v3_adapters import iter_event_adapters
from src.services.thesis_ledger_current_data_route import iter_current_data_adapters
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


def exact_route_inventory():
    return [
        *((key, target, False) for key, target in iter_market_v3_bar_adapters()),
        *((key, target, True) for key, target in iter_market_v3_gated_bar_adapters()),
        *iter_event_adapters(),
        *((key, target, binding.gated if binding is not None else True)
          for key, target in iter_current_data_adapters()
          for binding in (CAPABILITY_SOURCES.resolve(key, target),)),
    ]
