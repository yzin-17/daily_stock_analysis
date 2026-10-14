"""日线公开单位合同与来源纯投影策略，不授予单位转换资格。"""

from dataclasses import dataclass
from typing import Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict


class DailyFieldUnits(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    volume: Literal['hand', 'share', 'fund-unit', 'unknown']
    amount: Literal['CNY', 'unknown']


@dataclass(frozen=True)
class DailyUnitsContext:
    metadata: Mapping[str, object]
    key: Mapping[str, object]
    request: Mapping[str, object] | None = None


@dataclass(frozen=True)
class DailyUnitsPolicy:
    metadata_names: tuple[str, ...]
    project: Callable[[DailyUnitsContext], DailyFieldUnits | None]

    def __post_init__(self):
        if (not isinstance(self.metadata_names, tuple) or not self.metadata_names
                or any(not isinstance(name, str) or not name.strip() for name in self.metadata_names)
                or len(set(self.metadata_names)) != len(self.metadata_names) or not callable(self.project)):
            raise ValueError('日线单位策略必须声明唯一私有合同名称及纯投影')
