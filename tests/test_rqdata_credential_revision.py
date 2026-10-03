"""RQData 内部账号修订复用既有用途隔离 HMAC，不暴露账号值。"""

import pytest

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_credential_revision import provider_credential_revision


def snapshot(**changes):
    values = {'username': 'synthetic-account', 'password': 'synthetic-password'}
    values.update(changes)
    return ProviderCredentialSnapshot.create('rqdata', 'environment', 'username_password', values, 1, 1)


def revision(value, version='v1', key=b'synthetic-master-key'):
    return provider_credential_revision(value, version, key)


def test_same_immutable_account_has_same_private_revision():
    first = revision(snapshot())
    assert first == revision(snapshot())
    assert first.startswith('hmac-sha256-v1:')
    assert 'synthetic' not in first
    assert 'synthetic' not in repr(snapshot())


@pytest.mark.parametrize('field', ['username', 'password'])
def test_each_account_component_rotates_revision(field):
    assert revision(snapshot(**{field: 'changed'})) != revision(snapshot())


def test_master_rotation_changes_revision():
    original = revision(snapshot())
    assert revision(snapshot(), version='v2') != original
    assert revision(snapshot(), key=b'another-master-key') != original


@pytest.mark.parametrize('changes', [{'username': ''}, {'username': ' account'}, {'password': ''}])
def test_invalid_account_has_no_admission_revision(changes):
    assert revision(snapshot(**changes)) is None


def test_password_whitespace_is_not_silently_normalized():
    assert revision(snapshot(password=' secret ')) != revision(snapshot(password='secret'))
