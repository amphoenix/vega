"""
Tests for Secrets Manager — secure access to API keys and credentials.
"""

import pytest
from unittest.mock import MagicMock
from app.infrastructure.security.secrets_manager import (
    SecretsManager, SecretCategory, KNOWN_SECRETS, _mask,
)


class _FakeSettings:
    """Simple object that only has explicit attributes (no MagicMock surprises)."""
    pass


def _mock_settings(**overrides):
    defaults = dict(
        llm_api_key='sk-test-1234567890abcdef',
        llm_api_key_2='sk-test-second-key-999',
        llm_api_key_3='',
        aws_access_key_id='AKIA1234567890',
        aws_secret_access_key='aws-secret-key-1234',
        indmoney_access_token='',
        zep_api_key='',
    )
    defaults.update(overrides)
    s = _FakeSettings()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestAccess:
    def test_get_returns_value(self):
        mgr = SecretsManager(_mock_settings())
        assert mgr.get('llm_api_key') == 'sk-test-1234567890abcdef'

    def test_get_missing_returns_empty(self):
        mgr = SecretsManager(_mock_settings())
        assert mgr.get('nonexistent') == ''

    def test_get_empty_returns_empty(self):
        mgr = SecretsManager(_mock_settings())
        assert mgr.get('zep_api_key') == ''

    def test_is_set(self):
        mgr = SecretsManager(_mock_settings())
        assert mgr.is_set('llm_api_key')
        assert not mgr.is_set('zep_api_key')

    def test_get_masked(self):
        mgr = SecretsManager(_mock_settings())
        masked = mgr.get_masked('llm_api_key')
        assert '***' in masked
        assert 'sk-t' in masked  # first 4 chars

    def test_get_masked_unset(self):
        mgr = SecretsManager(_mock_settings())
        assert mgr.get_masked('zep_api_key') == '(not set)'


class TestValidation:
    def test_openai_valid(self):
        mgr = SecretsManager(_mock_settings())
        errors = mgr.validate(provider='openai')
        assert errors == []

    def test_openai_missing_key(self):
        mgr = SecretsManager(_mock_settings(llm_api_key=''))
        errors = mgr.validate(provider='openai')
        assert len(errors) == 1
        assert 'LLM_API_KEY' in errors[0]

    def test_bedrock_valid(self):
        mgr = SecretsManager(_mock_settings())
        errors = mgr.validate(provider='bedrock')
        assert errors == []

    def test_bedrock_missing_keys(self):
        mgr = SecretsManager(_mock_settings(
            aws_access_key_id='', aws_secret_access_key='',
        ))
        errors = mgr.validate(provider='bedrock')
        assert len(errors) == 2

    def test_no_provider_only_required(self):
        mgr = SecretsManager(_mock_settings())
        errors = mgr.validate()
        assert errors == []  # no secrets are marked required=True currently


class TestStatus:
    def test_status_returns_all_known(self):
        mgr = SecretsManager(_mock_settings())
        st = mgr.status()
        assert 'llm_api_key' in st
        assert st['llm_api_key']['set'] is True
        assert '***' in st['llm_api_key']['masked']
        assert st['llm_api_key']['category'] == 'llm'

    def test_status_unset_secret(self):
        mgr = SecretsManager(_mock_settings())
        st = mgr.status()
        assert st['zep_api_key']['set'] is False

    def test_summary(self):
        mgr = SecretsManager(_mock_settings())
        s = mgr.summary()
        assert s['llm'] == 2   # llm_api_key + llm_api_key_2 set
        assert s['aws'] == 2   # both AWS keys set
        assert s['broker'] == 0  # indmoney_access_token is empty


class TestMask:
    def test_empty(self):
        assert _mask('') == '(not set)'

    def test_short(self):
        assert _mask('abc') == '***'

    def test_exactly_8(self):
        assert _mask('12345678') == '***'

    def test_long(self):
        m = _mask('abcdefghijklmnop')
        assert m == 'abcd***mnop'

    def test_none_like(self):
        assert _mask('') == '(not set)'
