"""Offline custody and launch checks for user-authored RSI methods."""
import hashlib
from pathlib import Path

import pytest

from console import custom_methods, server


def test_create_only_method_is_validated_and_listed(tmp_path):
    root = tmp_path / 'methods'
    text = 'print("{}")\n'
    digest = custom_methods.create(root, 'first', text)
    assert digest == hashlib.sha256(text.encode()).hexdigest()
    assert custom_methods.read(root, 'first') == (text.encode(), digest)
    assert custom_methods.list_methods(root) == [{'name': 'first', 'title': 'first', 'kind': 'custom'}]
    with pytest.raises(FileExistsError):
        custom_methods.create(root, 'first', 'print(123)')
    with pytest.raises(ValueError):
        custom_methods.create(root, '../escape', text)
    with pytest.raises(ValueError):
        custom_methods.create(root, 'broken', 'def !')
    assert not (root / 'broken.py').exists()


def test_read_refuses_symlink(tmp_path):
    root = tmp_path / 'methods'; root.mkdir()
    secret = tmp_path / 'secret'; secret.write_text('sensitive')
    (root / 'evil.py').symlink_to(secret)
    with pytest.raises(OSError):
        custom_methods.read(root, 'evil')
    assert custom_methods.list_methods(root) == []


def test_server_create_conflicts_without_replacing_method(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'STATE_ROOT', tmp_path)
    monkeypatch.setattr(server, 'EXTERNAL_STATE', True)
    assert server.create_custom_method('sample', 'print(1)')[0] == 201
    assert server.create_custom_method('sample', 'print(2)')[0] == 409
    assert (tmp_path / 'methods/sample.py').read_text() == 'print(1)'
