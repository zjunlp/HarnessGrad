"""The public UI describes configured services; it does not manage hosts."""
from tools import serve_ui


def test_provider_catalog_is_offline_and_does_not_scan_private_directories(monkeypatch):
    monkeypatch.setattr(serve_ui.urllib.request, 'urlopen', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('network')))
    catalog = serve_ui.provider_catalog()
    assert '_local_model_dirs' not in catalog
    assert catalog['本地 OpenAI 兼容服务']['base_url'] == ''


def test_model_lifecycle_no_longer_runs_processes(monkeypatch):
    monkeypatch.setattr(serve_ui.subprocess, 'Popen', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('process')))
    for action in ('start', 'stop', 'status'):
        assert 'not provided' in serve_ui.model_service(action)['error']


def test_unconfigured_presets_default_to_mock(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_ui, 'ROOT', tmp_path)
    assert list(serve_ui.model_presets()) == ['mock(零成本冒烟,不调模型)']
