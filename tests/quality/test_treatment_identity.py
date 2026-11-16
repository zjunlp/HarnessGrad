"""Declared wrapper/configuration commitments; authored files and controls only."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from harnessgrad.orchestration import treatment_identity as treatment
from harnessgrad.orchestration.improver_readiness import ImproverRequest, resolve_readiness


@pytest.fixture
def declared(tmp_path):
    wrapper, dependency = tmp_path / 'wrapper.py', tmp_path / 'dependency.py'
    wrapper.write_text('private-source-marker')
    dependency.write_text('exact dependency')
    path = tmp_path / 'manifest.json'
    value = {'schema': treatment.SCHEMA, 'files': {'wrapper': str(wrapper), 'helper': str(dependency)},
             'configuration': {'password': 'private-configuration-marker'}, 'environment': ['CUSTOM_PROFILE']}
    path.write_text(json.dumps(value))
    return path, wrapper, dependency, value


def test_record_contains_only_commitments_and_validates_before_projection(declared):
    path, _, _, _ = declared
    record = treatment.capture(path, runtime={'credential': 'private-runtime-marker'},
                               resolved={'model': 'private-model-marker'},
                               environment={'CUSTOM_PROFILE': 'private-profile-marker'})
    assert treatment.validate(record) == record
    text = json.dumps(record)
    for marker in ('private-source-marker', 'private-runtime-marker', 'private-configuration-marker',
                   'private-profile-marker', 'private-model-marker', 'password', str(path.parent)):
        assert marker not in text
    bad = deepcopy(record); bad['runtime_sha256'] = '0' * 64
    with pytest.raises(treatment.TreatmentRefused, match='digest mismatch'):
        treatment.require_same(bad, record, early=True)


@pytest.mark.parametrize('change', ['wrapper', 'dependency', 'executable', 'configuration', 'environment', 'runtime', 'resolved'])
def test_declared_treatment_drift_refuses(declared, change):
    path, wrapper, dependency, value = declared
    arguments = {'runtime': {'model': 'original'}, 'resolved': {'provider': 'original'}, 'environment': {'CUSTOM_PROFILE': 'original'}}
    before = treatment.capture(path, **arguments)
    if change == 'wrapper': wrapper.write_text('changed')
    if change == 'dependency': dependency.write_text('changed')
    if change == 'executable': wrapper.chmod(0o755)
    if change == 'configuration':
        value['configuration']['profile'] = 'changed'; path.write_text(json.dumps(value))
    if change == 'environment': arguments['environment'] = {'CUSTOM_PROFILE': 'changed'}
    if change == 'runtime': arguments['runtime'] = {'model': 'changed'}
    if change == 'resolved': arguments['resolved'] = {'provider': 'changed'}
    with pytest.raises(treatment.TreatmentRefused, match='treatment changed'):
        treatment.require_same(before, treatment.capture(path, **arguments))


@pytest.mark.parametrize('kind', ['file', 'directory', 'fifo', 'missing'])
def test_missing_and_non_regular_or_symlink_sources_refuse(declared, tmp_path, kind):
    path, wrapper, dependency, value = declared
    if kind == 'file':
        wrapper.unlink(); wrapper.symlink_to(dependency)
    if kind == 'directory':
        linked = tmp_path / 'linked'; linked.symlink_to(tmp_path, target_is_directory=True)
        value['files']['wrapper'] = str(linked / wrapper.name)
        path.write_text(json.dumps(value))
    if kind == 'fifo':
        wrapper.unlink(); os.mkfifo(wrapper)
    if kind == 'missing': wrapper.unlink()
    with pytest.raises(treatment.TreatmentRefused): treatment.capture(path)


def test_legacy_and_missing_record_boundaries(declared):
    record = treatment.capture(declared[0])
    treatment.require_same(None, None)
    for original, current, declared_flag in ((record, None, False), (None, record, False), (None, None, True)):
        with pytest.raises(treatment.TreatmentRefused, match='missing'):
            treatment.require_same(original, current, declared=declared_flag)


def test_duplicate_manifest_keys_refuse(declared):
    path = declared[0]
    path.write_text('{"schema":"a","schema":"b"}')
    with pytest.raises(treatment.TreatmentRefused, match='duplicate'):
        treatment.capture(path)


@pytest.mark.parametrize('own', [False, True])
def test_resolved_identity_guard_precedes_probe_even_without_improver(own):
    calls = []
    class Reporter:
        def note(self, *args, **kwargs): pass
    def guard(declaration, provider):
        calls.append(('guard', declaration, provider))
        raise treatment.TreatmentRefused('changed')
    def probe(*args):
        calls.append(('probe',)); raise AssertionError('probe must not run')
    with pytest.raises(treatment.TreatmentRefused):
        resolve_readiness(ImproverRequest('none' if own else 'authored', None, None), reporter=Reporter(),
            load_config=lambda: {}, resolve_config=lambda *a, **kw: {'ok': True, 'kind': 'cli',
                'name': 'authored', 'version': '1', 'sha256': 'a' * 64, 'model': 'authored'},
            provider_url=lambda declaration: 'http://authored.invalid', probe=probe, before_probe=guard)
    assert len(calls) == 1 and calls[0][0] == 'guard'


def test_driver_runtime_commitment_excludes_configured_credential_values(monkeypatch):
    from types import SimpleNamespace
    import driver
    from harnessgrad import config
    args = SimpleNamespace(method_entrypoint='', budget_profile=None, _namespace_args={})
    try:
        monkeypatch.setenv('HG_AGENT_API_KEY', 'first-authored-fixture-credential')
        config.reset()
        first = driver._treatment_runtime(args)
        monkeypatch.setenv('HG_AGENT_API_KEY', 'second-authored-fixture-credential')
        config.reset()
        second = driver._treatment_runtime(args)
        assert first == second
        raw = json.dumps(first)
        assert 'first-authored-fixture-credential' not in raw
        assert 'second-authored-fixture-credential' not in raw
        assert first['platform_configuration']['agent']['api_key'] == {'present': True}
    finally:
        config.reset()
