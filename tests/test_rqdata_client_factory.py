"""显式账号初始化、序列化与诊断隐藏边界，不访问真实 SDK。"""

import pickle
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data_provider.rqdata_client_factory import RqDataClientFactory


def test_pickled_factory_initializes_exact_credentials(monkeypatch):
    init = Mock()
    sdk = SimpleNamespace(init=init, fund=object())
    monkeypatch.setitem(sys.modules, 'rqdatac', sdk)
    factory = RqDataClientFactory('synthetic-account', 'synthetic-password')
    assert 'synthetic' not in repr(factory)
    restored = pickle.loads(pickle.dumps(factory))
    assert restored() is sdk
    init.assert_called_once_with(username='synthetic-account', password='synthetic-password')


@pytest.mark.parametrize('username,password', [('', 'secret'), (' account', 'secret'),
                                             ('account', ''), ('account', None), (None, 'secret')])
def test_missing_credentials_do_not_fall_back_to_environment(username, password, monkeypatch):
    monkeypatch.setenv('RQDATAC_CONF', 'synthetic-environment-account')
    with pytest.raises(ValueError, match='^rqdata_explicit_credentials_required$'):
        RqDataClientFactory(username, password)


@pytest.mark.parametrize('failure', ['init', 'extension'])
def test_initialization_errors_are_safe(monkeypatch, failure):
    sdk = SimpleNamespace(init=Mock(side_effect=RuntimeError('synthetic-sensitive') if failure == 'init' else None))
    monkeypatch.setitem(sys.modules, 'rqdatac', sdk)
    with pytest.raises(RuntimeError, match='^rqdata_client_unavailable$'):
        RqDataClientFactory('account', 'secret')()
