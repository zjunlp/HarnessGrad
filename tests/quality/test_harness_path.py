"""An external source enters the same driver, with a reproducible input identity."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import validate_harness as validator

ROOT = Path(__file__).resolve().parents[2]
AGENT = '''import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--task')
p.add_argument('--workdir')
a = p.parse_args()
Path(a.workdir, 'answer.txt').write_text('alpha')
'''


def _harness(parent, **fields):
    root = parent / 'external'
    root.mkdir(parents=True)
    man = {'name': 'external-test', 'version': '1.0', 'entrypoint': 'agent.py',
           'backend': 'cli', 'env_kinds': ['files']}
    man.update(fields)
    (root / 'harness.json').write_text(json.dumps(man), encoding='utf-8')
    (root / 'agent.py').write_text(AGENT, encoding='utf-8')
    return root


def _argv(tmp_path, *source):
    return ['driver.py', *source, '--dataset', 'demo', '--sampling', 'all',
            '--rounds', '0', '--jobs', '1', '--trials', '1', '--run-id', 'a1-test',
            '--runs-root', str(tmp_path/'runs'), '--work-root', str(tmp_path/'work')]


def _run(tmp_path, *source):
    return subprocess.run([sys.executable, *_argv(tmp_path, *source)], cwd=ROOT,
                          env={**os.environ, 'HG_AGENT_BACKEND': 'mock'},
                          capture_output=True, text=True, timeout=120)


def _refused(proc, detail):
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert detail.lower() in proc.stderr.lower(), proc.stdout + proc.stderr
    assert 'Traceback' not in proc.stderr


def test_absolute_harness_path_runs(tmp_path):
    source = _harness(tmp_path)
    proc = _run(tmp_path, '--harness-path', str(source))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    meta = json.loads((tmp_path/'runs/a1-test/run_meta.json').read_text())
    assert meta['harness'] == 'external-test'
    points = [json.loads(line) for line in (tmp_path/'runs/a1-test/curve.jsonl').read_text().splitlines()]
    assert len(points) == 1 and len(points[0]['per_task']) == 4
    assert points[0]['measured_by_platform'] is True


def test_owned_absolute_symlink_runs_after_staging(tmp_path):
    source = _harness(tmp_path, entrypoint='linked.py')
    (source/'linked.py').symlink_to(source/'agent.py')
    proc = _run(tmp_path, '--harness-path', str(source))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (source/'linked.py').readlink() == source/'agent.py'


@pytest.mark.parametrize('value', ['base_harness/loop', ''])
def test_relative_harness_path_is_refused(tmp_path, value):
    _refused(_run(tmp_path, '--harness-path', value), 'absolute')


def test_external_source_is_not_silently_ignored_on_eval(tmp_path):
    source = _harness(tmp_path)
    _refused(_run(tmp_path, '--harness-path', str(source), '--side', 'eval'), 'harness-path')


def test_external_default_run_id_does_not_use_manifest_path_segments(tmp_path):
    source = _harness(tmp_path, name='../display-only')
    argv = _argv(tmp_path, '--harness-path', str(source))
    index = argv.index('--run-id')
    del argv[index:index+2]
    proc = subprocess.run([sys.executable, *argv], cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, 'HG_AGENT_BACKEND':'mock'}, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    metas = list((tmp_path/'runs').glob('*/run_meta.json'))
    assert len(metas) == 1 and metas[0].parent.name.startswith('external-')
    assert json.loads(metas[0].read_text())['harness'] == '../display-only'


@pytest.mark.parametrize('raw', [b'{broken', b'[]', b'\xff', b'{}'])
def test_bad_manifest_is_refused(tmp_path, raw):
    source = _harness(tmp_path)
    (source/'harness.json').write_bytes(raw)
    _refused(_run(tmp_path, '--harness-path', str(source)), 'harness.json')
    assert not (tmp_path/'runs/a1-test/verdicts').exists()


def test_missing_entrypoint_is_refused(tmp_path):
    source = _harness(tmp_path, entrypoint='missing.py')
    _refused(_run(tmp_path, '--harness-path', str(source)), 'entrypoint')


@pytest.mark.parametrize('kind', ['entrypoint', 'install', 'path', 'extra', 'manifest'])
def test_symlink_escape_is_refused(tmp_path, kind, monkeypatch):
    source = _harness(tmp_path)
    outside = tmp_path/'private'
    outside.mkdir()
    (outside/'secret.py').write_text('private data')
    (outside/'manifest.json').write_text('{}')
    if kind == 'manifest':
        (source/'harness.json').unlink()
        (source/'harness.json').symlink_to(outside/'manifest.json')
    elif kind == 'extra':
        (source/'extra.txt').symlink_to(outside/'secret.py')
    else:
        man = json.loads((source/'harness.json').read_text())
        man[kind] = 'escape'
        (source/'harness.json').write_text(json.dumps(man))
        (source/'escape').symlink_to(outside if kind == 'path' else outside/'secret.py')
    real_open = Path.open

    def guarded(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(outside), 'read outside harness'
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded)
    problems = validator.check(source)
    assert problems and any(p['code'] == 'unsafe-path' for p in problems)


@pytest.mark.parametrize('fields', [
    {'name': 1}, {'version': []}, {'entrypoint': ['agent.py']}, {'name': ' '},
    {'install': ['x']}, {'path': 7}, {'env_kinds': 'files'}, {'env_kinds': [1]},
])
def test_bad_field_types_are_named_problems(tmp_path, fields):
    source = _harness(tmp_path, **fields)
    problems = validator.check(source)
    assert problems and any(p.get('field') in fields for p in problems)
    assert all(isinstance(p['detail'], str) and p['detail'] for p in problems)


@pytest.mark.parametrize('field', ['path', 'entrypoint', 'install'])
@pytest.mark.parametrize('value', ['/etc/passwd', '../outside', 'C:\\private\\data'])
def test_declared_paths_cannot_escape(tmp_path, field, value):
    problems = validator.check(_harness(tmp_path, **{field: value}))
    assert any(p['code'] == 'unsafe-path' and p.get('field') == field for p in problems)


@pytest.mark.parametrize('kind', ['broken', 'cycle', 'directory'])
def test_bad_files_are_refused_without_exception(tmp_path, kind):
    source = _harness(tmp_path)
    entry = source/'agent.py'
    entry.unlink()
    if kind == 'broken':
        entry.symlink_to('missing.py')
    elif kind == 'cycle':
        entry.symlink_to('agent.py')
    else:
        entry.mkdir()
    assert validator.check(source)


def test_unreadable_manifest_is_a_problem(tmp_path, monkeypatch):
    source = _harness(tmp_path)
    real_open = Path.open

    def deny(path, *args, **kwargs):
        if path.name == 'harness.json':
            raise PermissionError('manifest read denied')
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', deny)
    assert any('denied' in p['detail'] for p in validator.check(source))


def test_source_parameters_are_mutually_exclusive(tmp_path):
    source = _harness(tmp_path)
    _refused(_run(tmp_path, '--harness', 'loop', '--harness-path', str(source)), 'not allowed')


@pytest.mark.parametrize('source', [(), ('--harness', 'loop'), ('--harness', 'absolute')])
def test_legacy_harness_calls_still_run(tmp_path, source):
    if source == ('--harness', 'absolute'):
        source = ('--harness', str(_harness(tmp_path)))
    proc = _run(tmp_path, *source)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_is_quiet_and_legacy_validate_returns_strings(tmp_path, capsys):
    source = _harness(tmp_path, entrypoint='absent.py', extra='configuration')
    problems = validator.check(source)
    assert capsys.readouterr().out == ''
    assert validator.validate(source, smoke=False) == [p['detail'] for p in problems]


def test_check_never_executes_the_entrypoint(tmp_path):
    source = _harness(tmp_path)
    (source/'agent.py').write_text('raise RuntimeError("must never execute")')
    assert validator.check(source) == []


@pytest.mark.parametrize('replacement', ['unreadable', '[]'])
def test_legacy_adapter_survives_manifest_reread_failure(tmp_path, monkeypatch, replacement):
    source = _harness(tmp_path)
    real_read = Path.read_text
    reads = 0

    def changing(path, *args, **kwargs):
        nonlocal reads
        if path.name == 'harness.json':
            reads += 1
            if reads >= 3:
                if replacement == 'unreadable':
                    raise PermissionError('manifest changed while validating')
                return replacement
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', changing)
    problems = validator.validate(source, smoke=False)
    assert problems and all(isinstance(p, str) for p in problems)


def test_external_bun_generated_state_is_not_source(tmp_path):
    source = _harness(tmp_path, runtime={'kind':'bun','source':'owned','entrypoint':'agent.py','package':'worker'})
    (source/'owned').mkdir()
    generated = source/'.opencode-state'
    generated.mkdir()
    (generated/'broken.py').write_text('def broken(')
    assert validator.check(source) == []
    (source/'real.py').write_text('def broken(')
    assert any('real.py' in p['detail'] for p in validator.check(source))


def _digest(root):
    paths = sorted((p for p in root.rglob('*') if p.is_file()),
                   key=lambda p: p.relative_to(root).as_posix().encode('utf-8'))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        digest.update(path.read_bytes())
    return {'sha256': digest.hexdigest(), 'entries': len(paths)}


def test_source_identity_is_location_and_order_independent(tmp_path):
    source = _harness(tmp_path)
    (source/'z.txt').write_bytes(b'z\0binary')
    (source/'a.txt').write_bytes(b'a')
    clone = tmp_path/'clone'
    clone.mkdir()
    for p in reversed(list(source.iterdir())):
        shutil.copyfile(p, clone/p.name)
    assert validator.source_identity(source) == validator.source_identity(clone) == _digest(source)


def test_source_identity_changes_with_bytes_or_relative_name(tmp_path):
    source = _harness(tmp_path)
    before = validator.source_identity(source)
    (source/'agent.py').write_text(AGENT + '\n# changed\n')
    changed = validator.source_identity(source)
    (source/'agent.py').rename(source/'renamed.py')
    assert before != changed != validator.source_identity(source)


def test_source_identity_counts_entries(tmp_path):
    source = _harness(tmp_path)
    (source/'empty-directory').mkdir()
    assert validator.source_identity(source) == _digest(source)
    assert validator.source_identity(source)['entries'] == 2


def test_safe_links_and_directory_aliases_are_preserved_in_identity(tmp_path):
    source = _harness(tmp_path)
    (source/'assets').mkdir()
    (source/'assets/data.txt').write_bytes(b'payload')
    (source/'alias').symlink_to('assets', target_is_directory=True)
    (source/'linked.txt').symlink_to('assets/data.txt')
    assert validator.check(source) == []
    expected = [(p, (source/p).read_bytes()) for p in
                ['agent.py','alias/data.txt','assets/data.txt','harness.json','linked.txt']]
    digest = hashlib.sha256()
    for name, data in expected:
        digest.update(name.encode() + b'\0' + data)
    assert validator.source_identity(source) == {'sha256':digest.hexdigest(),'entries':5}


def test_identity_rejects_unsafe_links_even_when_called_directly(tmp_path):
    source = _harness(tmp_path)
    (source/'private').symlink_to(tmp_path/'outside')
    with pytest.raises((OSError, ValueError)):
        validator.source_identity(source)


def test_subdirectory_entrypoint_can_link_to_owned_parent_source(tmp_path):
    source = _harness(tmp_path, path='src')
    (source/'src').mkdir()
    (source/'src/agent.py').symlink_to('../agent.py')
    assert validator.check(source) == []


@pytest.mark.parametrize('git_kind', ['directory', 'pointer'])
def test_external_git_metadata_is_not_passed_to_staging(tmp_path, monkeypatch, git_kind):
    import driver
    source = _harness(tmp_path)
    if git_kind == 'directory':
        (source/'.git').mkdir()
        (source/'.git/config').write_text('[core]\n')
    else:
        (source/'.git').write_text('gitdir: /outside/another-checkout\n')
    before = _digest(source)

    class StagingInspected(Exception):
        pass

    def inspect(src, work):
        assert not (src/'.git').exists(), 'external Git metadata reached platform staging'
        assert validator.check(src) == []
        raise StagingInspected

    monkeypatch.setattr(driver, 'stage_workspace', inspect)
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setattr(sys, 'argv', _argv(tmp_path, '--harness-path', str(source)))
    with pytest.raises(StagingInspected):
        driver._main({})
    assert _digest(source) == before


def test_external_run_meta_records_source_identity(tmp_path):
    source = _harness(tmp_path)
    expected = _digest(source)
    proc = _run(tmp_path, '--harness-path', str(source))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    meta = json.loads((tmp_path/'runs/a1-test/run_meta.json').read_text())
    assert meta['harness_source'] == expected
    assert _digest(source) == expected, 'the driver changed the submitted source'


def test_source_change_during_staging_is_refused(tmp_path, monkeypatch, capsys):
    import driver
    source = _harness(tmp_path)
    original = driver.stage_workspace

    def changing(src, work):
        original(src, work)
        (src/'changed.txt').write_text('changed while staging')

    monkeypatch.setattr(driver, 'stage_workspace', changing)
    monkeypatch.setenv('HG_AGENT_BACKEND', 'mock')
    monkeypatch.setattr(sys, 'argv', _argv(tmp_path, '--harness-path', str(source)))
    assert driver._main({}) == 2
    assert 'changed' in capsys.readouterr().err.lower()
    assert not (tmp_path/'runs/a1-test/verdicts').exists()
