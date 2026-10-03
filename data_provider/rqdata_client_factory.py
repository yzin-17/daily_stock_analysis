"""隔离进程使用的显式 RQData 账号快照，不读取隐式环境账号。"""

from dataclasses import dataclass, field
import importlib


@dataclass(frozen=True)
class RqDataClientFactory:
    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.username, str) or not self.username.strip()
                or self.username != self.username.strip()
                or not isinstance(self.password, str) or not self.password.strip()):
            raise ValueError('rqdata_explicit_credentials_required')

    def __call__(self):
        try:
            client = importlib.import_module('rqdatac')
            client.init(username=self.username, password=self.password)
            if not hasattr(client, 'fund'):
                raise RuntimeError('missing fund extension')
            return client
        except Exception:
            raise RuntimeError('rqdata_client_unavailable') from None
