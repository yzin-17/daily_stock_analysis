"""事件生命周期的请求绑定回调；来源不持有公共Runtime或选源权。"""

from dataclasses import dataclass
from typing import Callable, Literal, Mapping, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from .provider_credentials_runtime import ProviderCredentialSnapshot


@dataclass(frozen=True)
class EventCredentialContext:
    database_path: str
    read_admission: Callable[[], Mapping[str, object] | None]
    read_credentials: Callable[[], 'ProviderCredentialSnapshot']
    read_master_key: Callable[[], tuple[str, bytes]]
    build_adapter: Callable[['ProviderCredentialSnapshot'], object]
    read_local_admission: Callable[[], Mapping[str, object] | None] | None = None


@dataclass(frozen=True)
class EventExecutionContext:
    adapter_factory: Callable[[], object]
    admitted_state: Callable[[], dict[str,object]]
    check_security: Callable[[],None]|None=None
    reader_override: Callable[[str,str,str,str],Mapping[str,object]]|None=None
    credentials: EventCredentialContext | None = None
    database_path: str | None = None


class MappedProbeOptions(TypedDict):
    database_path: str
    start: str
    end: str
    data_as_of: str
    read_admission: Callable[[], Mapping[str, object] | None]
    read_credentials: Callable[[], 'ProviderCredentialSnapshot']
    read_master_key: Callable[[], tuple[str, bytes]]
    timeout_seconds: float


@dataclass(frozen=True)
class EventProbeReadContext:
    capability: Literal['CASH_DISTRIBUTION', 'SPLIT_EVENT']
    symbol: str
    start: str
    end: str
    data_as_of: str
    admission: Mapping[str, object]
    credentials: EventCredentialContext
    timeout_seconds: float = 4.5

    def mapped_read_options(self) -> MappedProbeOptions:
        return {'database_path': self.credentials.database_path, 'start': self.start, 'end': self.end,
                'data_as_of': self.data_as_of, 'read_admission': self.credentials.read_admission,
                'read_credentials': self.credentials.read_credentials, 'read_master_key': self.credentials.read_master_key,
                'timeout_seconds': self.timeout_seconds}
