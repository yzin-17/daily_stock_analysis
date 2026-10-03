"""读取前后账号/密钥修订变化必须拒绝，凭据不进入返回结果。"""

from unittest.mock import Mock

import pytest

from src.services.provider_credential_revision import provider_credential_revision
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_rqdata_read import read_rqdata_fund_event_with_credentials


def snapshot(password='synthetic-password'):
    return ProviderCredentialSnapshot.create('rqdata', 'environment', 'username_password',
                                             {'username': 'synthetic-user', 'password': password}, 1, 1)


@pytest.mark.parametrize('revision', ['', '未知修订', 'hmac-sha256-v1:bad', None])
def test_malformed_revision_rejected_before_reading_secrets(revision):
    credentials, master = Mock(), Mock()
    with pytest.raises(ValueError, match='^rqdata_credential_not_admitted$'):
        read_rqdata_fund_event_with_credentials(
            'split', '000246.OF', admitted_credential_revision=revision,
            read_credentials=credentials, read_master_key=master, timeout_seconds=1,
        )
    credentials.assert_not_called()
    master.assert_not_called()


@pytest.mark.parametrize('change', ['none', 'before', 'after', 'master', 'revoked'])
def test_credential_gate_surrounds_exact_isolated_read(monkeypatch, change):
    original = snapshot()
    changed = snapshot('rotated-password')
    credentials = Mock(side_effect=[original, original])
    master = Mock(side_effect=[('v1', b'synthetic-key'), ('v1', b'synthetic-key')])
    if change == 'before':
        credentials.side_effect = [changed]
    elif change == 'after':
        credentials.side_effect = [original, changed]
    elif change == 'master':
        master.side_effect = [('v1', b'synthetic-key'), ('v2', b'rotated-key')]
    elif change == 'revoked':
        credentials.side_effect = [original, None]
    execution = Mock(return_value={'coverage': {'complete': False}, 'facts': []})
    monkeypatch.setattr('src.services.thesis_ledger_rqdata_read.read_rqdata_fund_event_isolated', execution)
    args = dict(admitted_credential_revision=provider_credential_revision(original, 'v1', b'synthetic-key'),
                read_credentials=credentials, read_master_key=master, timeout_seconds=4,
                query_fund_code='000246', instrument_type='NAV_FUND', start='2025-01-01', end='2025-12-31')
    if change == 'none':
        assert read_rqdata_fund_event_with_credentials('split', '000246.OF', **args) == execution.return_value
    else:
        with pytest.raises(ValueError, match='^rqdata_credential_not_admitted$'):
            read_rqdata_fund_event_with_credentials('split', '000246.OF', **args)
    assert execution.call_count == (0 if change == 'before' else 1)
    if execution.called:
        factory, kind, symbol = execution.call_args.args
        assert factory.password == 'synthetic-password'
        assert (kind, symbol) == ('split', '000246.OF')
        assert execution.call_args.kwargs['timeout_seconds'] == 4
