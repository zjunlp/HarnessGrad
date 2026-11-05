"""Staged receipts bind endpoint identities, not server-specific availability."""
import pytest
from eval.policy.diagnostic_policy import _endpoint


@pytest.mark.parametrize('url', ['http://127.0.0.1:8001/v1', 'http://127.0.0.1:8002/v1',
                                 'https://models.example.invalid/v1'])
def test_valid_explicit_endpoints_are_supported(url):
    assert _endpoint(url)


@pytest.mark.parametrize('url', ['', 'file:///etc/passwd', 'https://user:password@example.invalid/v1',
                                 'https://example.invalid/v1?key=secret', 'http://host:99999/v1',
                                 'http://host/v1\nInjected: x'])
def test_unsafe_or_credential_bearing_endpoints_are_rejected(url):
    assert not _endpoint(url)
