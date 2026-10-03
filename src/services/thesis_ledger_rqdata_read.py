"""RQData 已准入账号修订的读取接缝；来源范围准入由上层独立核验。"""

import hmac
import re

from data_provider.rqdata_client_factory import RqDataClientFactory
from data_provider.rqdata_fund_event_process import read_rqdata_fund_event_isolated
from src.services.provider_credential_revision import provider_credential_revision
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot


def read_rqdata_fund_event_with_credentials(
    kind, symbol, *, admitted_credential_revision, read_credentials, read_master_key,
    timeout_seconds, **options,
):
    """只把首次核验的不可变账号快照传给子进程；晚到数据不跨账号修订。"""
    if (not isinstance(admitted_credential_revision, str)
            or re.fullmatch(r'hmac-sha256-v1:[0-9a-f]{64}', admitted_credential_revision) is None):
        raise ValueError('rqdata_credential_not_admitted')

    def checked_snapshot():
        snapshot = read_credentials()
        if (not isinstance(snapshot, ProviderCredentialSnapshot) or snapshot.provider_id != 'rqdata'
                or snapshot.method != 'username_password'):
            raise ValueError('rqdata_credential_not_admitted')
        version, key = read_master_key()
        if not isinstance(version, str) or not version or not isinstance(key, bytes) or not key:
            raise ValueError('rqdata_credential_not_admitted')
        revision = provider_credential_revision(snapshot, version, key)
        if revision is None or not hmac.compare_digest(revision, admitted_credential_revision):
            raise ValueError('rqdata_credential_not_admitted')
        return snapshot

    snapshot = checked_snapshot()
    factory = RqDataClientFactory(snapshot.values['username'], snapshot.values['password'])
    result = read_rqdata_fund_event_isolated(factory, kind, symbol, timeout_seconds=timeout_seconds, **options)
    checked_snapshot()
    return result
