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

from harnessgrad.orchestration import source_lease

from tools import validate_harness as validator
from test_scenario import offline_loop_dependencies  # noqa: F401 - explicit fixture reuse

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
    proc = _run(tmp_path, '--harness-path', str(source), '--side', 'eval')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    meta = json.loads((tmp_path/'runs/a1-test/run_meta.json').read_text())
    assert meta['harness'] == 'external-test'
    receipt = validator.source_identity(source)
    assert {k: meta['harness_source'][k] for k in ('submission_sha256', 'entries')} == receipt
    assert meta['harness_source']['path'] == str(source)
    point = json.loads((tmp_path/'runs/a1-test/curve.jsonl').read_text().splitlines()[0])
    assert point['side'] == 'eval'
    assert set(point['per_task']) == {'t05', 't06', 't07', 't08'}
    # The staged tree's content address, under the same name and function the point uses:
    # `harness_sha` is always the Git tree hash of the tree that ran, and
    # `submission_sha256` is always the digest of the directory that was handed in.
    assert meta['harness_source']['harness_sha'] == point['identity']['harness_sha']


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


@pytest.mark.parametrize('root_cycle', [False, True])
def test_old_python_symlink_loop_errors_are_named_refusals(tmp_path, monkeypatch, root_cycle):
    source = _harness(tmp_path)
    cycle = (tmp_path/'root-cycle') if root_cycle else (source/'cycle')
    cycle.symlink_to(cycle.name)
    original = Path.resolve

    def old_python(path, *args, **kwargs):
        if path == cycle:
            raise RuntimeError(f"Symlink loop from {path}")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', old_python)
    target = cycle if root_cycle else source
    problems = validator.check(target)
    assert problems and 'loop' in problems[0]['detail'].lower()
    with pytest.raises((OSError, ValueError)):
        validator.source_identity(target)


def test_unreadable_manifest_is_a_problem(tmp_path, monkeypatch):
    source = _harness(tmp_path)
    real_open = Path.open

    def deny(path, *args, **kwargs):
        if path.name == 'harness.json':
            raise PermissionError('manifest read denied')
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', deny)
    assert any('denied' in p['detail'] for p in validator.check(source))


@pytest.mark.parametrize('kind', ['directory', 'fifo'])
def test_manifest_must_be_a_regular_file_before_it_is_read(tmp_path, monkeypatch, kind):
    source = _harness(tmp_path)
    path = source/'harness.json'
    path.unlink()
    if kind == 'directory':
        path.mkdir()
    else:
        os.mkfifo(path)
    original = Path.read_text

    def guarded(target, *args, **kwargs):
        assert target != path, 'attempted to read a non-regular manifest'
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', guarded)
    assert any(p['code'] == 'manifest-invalid' for p in validator.check(source))


@pytest.mark.parametrize('overlap', ['source-is-workspace', 'work-in-source', 'runs-in-source'])
def test_external_input_is_not_changed_by_overlapping_outputs(tmp_path, overlap):
    if overlap == 'source-is-workspace':
        source = _harness(tmp_path/'work/a1-test')
        source = source.rename(source.with_name('workspace'))
    else:
        source = _harness(tmp_path)
    expected = _digest(source)
    argv = _argv(tmp_path, '--harness-path', str(source))
    if overlap != 'source-is-workspace':
        flag = '--work-root' if overlap == 'work-in-source' else '--runs-root'
        argv[argv.index(flag)+1] = str(source/'output')
    proc = subprocess.run([sys.executable, *argv], cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ,'HG_AGENT_BACKEND':'mock'}, timeout=120)
    # A source occupying the reserved work namespace is refused before staging.
    # Nested outputs otherwise reach the distinct source/output overlap guard.
    _refused(proc, 'namespace' if overlap == 'source-is-workspace' else 'overlap')
    assert source.is_dir() and _digest(source) == expected
    assert not (source/'output').exists()


def test_source_parameters_are_mutually_exclusive(tmp_path):
    source = _harness(tmp_path)
    _refused(_run(tmp_path, '--harness', 'loop', '--harness-path', str(source)), 'not allowed')


@pytest.mark.parametrize('source', [(), ('--harness', 'loop'), ('--harness', 'absolute')])
def test_legacy_harness_calls_still_run(tmp_path, source, offline_loop_dependencies):
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
    return {'submission_sha256': digest.hexdigest(), 'entries': len(paths)}


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
    assert validator.source_identity(source) == {'submission_sha256':digest.hexdigest(),
                                                 'entries':5}


def test_bytecode_is_not_part_of_a_source_identity(tmp_path):
    """A tree that has been imported is the same submission as one that has not.

    Bytecode is derived from sources the hash already covers, and it appears or disappears
    on its own -- measured 2026-11-01: copying `base_harness/loop` after the test suite had
    loaded its `agent.py` produced a different receipt for identical source.
    """
    source = _harness(tmp_path)
    fresh = validator.source_identity(source)
    (source/'__pycache__').mkdir()
    (source/'__pycache__'/'agent.cpython-312.pyc').write_bytes(b'bytes')
    (source/'stray.pyc').write_bytes(b'bytes')
    assert validator.source_identity(source) == fresh, 'bytecode changed the identity'
    (source/'__pycache__/agent.cpython-312.pyc').unlink()
    (source/'stray.pyc').unlink()
    (source/'__pycache__').rmdir()
    (source/'notes.txt').write_bytes(b'real input')
    assert validator.source_identity(source)['entries'] == fresh['entries'] + 1, \
        'a real input file still counts'


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
    receipt = meta['harness_source']
    # Three facts: what was handed in, and the content address of what ran beside it.
    assert {k: receipt[k] for k in ('submission_sha256', 'entries')} == expected
    assert set(receipt) == {'path', 'submission_sha256', 'entries', 'harness_sha'}
    assert receipt['path'] == str(source)
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


@pytest.mark.parametrize('value', ['', 'a'*63, 'A'*64, 'g'*64])
def test_expected_source_hash_rejects_invalid_digest_before_model(tmp_path, monkeypatch, capsys, value):
    import driver
    source = _harness(tmp_path)
    monkeypatch.setattr(driver, 'describe_model', lambda: pytest.fail('invalid hash reached model'))
    monkeypatch.setattr(sys, 'argv', _argv(tmp_path, '--harness-path', str(source),
                                        '--expected-harness-sha256', value))
    assert driver._main({}) == 2
    assert 'lowercase SHA256' in capsys.readouterr().err


def test_expected_source_hash_requires_explicit_path(tmp_path, monkeypatch, capsys):
    import driver
    monkeypatch.setattr(driver, 'describe_model', lambda: pytest.fail('invalid source reached model'))
    monkeypatch.setattr(sys, 'argv', _argv(tmp_path, '--harness', 'loop',
                                        '--expected-harness-sha256', 'a'*64))
    assert driver._main({}) == 2
    assert 'requires --harness-path' in capsys.readouterr().err


def test_reviewed_source_change_before_driver_refuses_before_model(tmp_path, monkeypatch, capsys):
    import driver
    source = _harness(tmp_path)
    expected = _digest(source)['submission_sha256']
    (source/'agent.py').write_text(AGENT+'\n# changed after launcher review\n')
    monkeypatch.setattr(driver, 'describe_model', lambda: pytest.fail('changed source reached model'))
    monkeypatch.setattr(sys, 'argv', _argv(tmp_path, '--harness-path', str(source),
                                        '--expected-harness-sha256', expected))
    assert driver._main({}) == 2
    assert 'expected reviewed SHA256' in capsys.readouterr().err
    assert not (tmp_path/'runs').exists()


def test_reviewed_source_transient_change_during_snapshot_refuses_before_model(tmp_path, monkeypatch, capsys):
    import driver
    source = _harness(tmp_path)
    expected = _digest(source)['submission_sha256']
    original = source_lease.external_source_snapshot
    original_bytes = (source/'agent.py').read_bytes()
    snapshots = []
    def change_and_restore(src, snapshot):
        snapshots.append(snapshot)
        (src/'agent.py').write_bytes(original_bytes+b'\n# transient tamper\n')
        try:
            original(src, snapshot)
        finally:
            (src/'agent.py').write_bytes(original_bytes)
    monkeypatch.setattr(source_lease, 'external_source_snapshot', change_and_restore)
    monkeypatch.setattr(driver, 'describe_model', lambda: pytest.fail('tampered snapshot reached model'))
    monkeypatch.setattr(sys, 'argv', _argv(tmp_path, '--harness-path', str(source),
                                        '--expected-harness-sha256', expected))
    assert driver._main({}) == 2
    assert 'owned harness snapshot differs' in capsys.readouterr().err
    assert _digest(source)['submission_sha256'] == expected
    assert snapshots and all(not path.exists() for path in snapshots)
    assert not (tmp_path/'runs').exists()


def test_reviewed_source_runs_from_owned_copy_and_preserves_original_identity(tmp_path):
    source = _harness(tmp_path)
    expected = _digest(source)
    proc = _run(tmp_path, '--harness-path', str(source),
                '--expected-harness-sha256', expected['submission_sha256'])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    meta = json.loads((tmp_path/'runs/a1-test/run_meta.json').read_text())
    assert meta['harness_source']['path'] == str(source)
    assert meta['harness_source']['submission_sha256'] == expected['submission_sha256']
    assert _digest(source) == expected


@pytest.mark.parametrize('checked', [False, True])
def test_reviewed_pep552_bytecode_cannot_execute_outside_submission_hash(tmp_path, checked):
    import importlib.util
    import py_compile
    source = _harness(tmp_path)
    (source/'agent.py').write_text(AGENT.replace('import argparse', 'import argparse\nimport payload\nassert payload.VALUE == "alpha"'))
    payload = source/'payload.py';payload.write_text('VALUE = "alpha"\n')
    expected = validator.source_identity(source)
    cache = Path(importlib.util.cache_from_source(str(payload)));cache.parent.mkdir()
    malicious = tmp_path/'unreviewed.py';malicious.write_text('raise RuntimeError("UNREVIEWED_BYTECODE_EXECUTED")\n')
    py_compile.compile(str(malicious),cfile=str(cache),doraise=True,
        invalidation_mode=(py_compile.PycInvalidationMode.CHECKED_HASH if checked
                           else py_compile.PycInvalidationMode.UNCHECKED_HASH))
    if checked:
        raw=cache.read_bytes();cache.write_bytes(raw[:8]+importlib.util.source_hash(payload.read_bytes())+raw[16:])
    assert validator.source_identity(source)==expected, 'cache unexpectedly bound by reviewed SHA'
    # Prove the threat locally: -B still consumes both valid PEP552 cache modes.
    control=subprocess.run([sys.executable,'-B','-c','import payload'],cwd=source,
        capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},timeout=20)
    assert control.returncode != 0 and 'UNREVIEWED_BYTECODE_EXECUTED' in control.stderr
    cached_before=cache.read_bytes()
    proc=_run(tmp_path,'--harness-path',str(source),
        '--expected-harness-sha256',expected['submission_sha256'])
    assert proc.returncode==0, proc.stdout+proc.stderr
    rows=[json.loads(p.read_text()) for p in (tmp_path/'runs/a1-test/progress/round-0').glob('*.json')]
    assert len(rows)==4 and all(row['exit_code']==0 for row in rows), rows
    assert cache.read_bytes()==cached_before and validator.source_identity(source)==expected


def test_reviewed_bytecode_strip_covers_root_nested_files_and_owned_aliases(tmp_path):
    import driver
    source=_harness(tmp_path)
    (source/'root.pyc').write_bytes(b'ignored bytecode')
    (source/'pkg').mkdir();(source/'pkg/deep.pyc').write_bytes(b'ignored bytecode')
    (source/'pkg/__pycache__').mkdir();(source/'pkg/__pycache__/payload.pyc').write_bytes(b'ignored bytecode')
    (source/'alias.pyc').symlink_to('agent.py')
    (source/'cache_target').mkdir();(source/'cache_target/owned.py').write_text('VALUE=1\n')
    (source/'__pycache__').symlink_to('cache_target',target_is_directory=True)
    # A directory ending in .pyc has hashed children and must not be dropped.
    (source/'code.pyc').mkdir();(source/'code.pyc/owned.py').write_text('VALUE=2\n')
    (source/'directory-alias.pyc').symlink_to('code.pyc',target_is_directory=True)
    before=validator.source_identity(source)
    snapshot=tmp_path/'snapshot';driver._external_source_snapshot(source,snapshot)
    driver._strip_reviewed_bytecode(snapshot)
    assert validator.source_identity(snapshot)==before
    assert not (snapshot/'root.pyc').exists() and not (snapshot/'alias.pyc').is_symlink()
    assert not (snapshot/'pkg/deep.pyc').exists() and not (snapshot/'pkg/__pycache__').exists()
    assert not (snapshot/'__pycache__').is_symlink()
    assert (snapshot/'cache_target/owned.py').read_text()=='VALUE=1\n'
    assert (snapshot/'code.pyc/owned.py').is_file() and (snapshot/'directory-alias.pyc').is_symlink()
    assert validator.source_identity(source)==before and (source/'root.pyc').is_file()


@pytest.mark.parametrize('link_name', ['__pycache__','escape.pyc'])
def test_reviewed_bytecode_escaping_links_refuse_before_model(tmp_path, monkeypatch, capsys, link_name):
    import driver
    source=_harness(tmp_path)
    outside=tmp_path/'outside';outside.mkdir();private=outside/'private.pyc';private.write_bytes(b'do not touch')
    (source/link_name).symlink_to(outside if link_name=='__pycache__' else private,
                                 target_is_directory=link_name=='__pycache__')
    # __pycache__ is ignored by identity; .pyc escaping links refuse even earlier.
    expected=(validator.source_identity(source)['submission_sha256'] if link_name=='__pycache__' else 'a'*64)
    monkeypatch.setattr(driver,'describe_model',lambda:pytest.fail('unsafe cache reached model'))
    monkeypatch.setattr(sys,'argv',_argv(tmp_path,'--harness-path',str(source),
        '--expected-harness-sha256',expected))
    assert driver._main({})==2
    assert 'outside the harness' in capsys.readouterr().err
    assert private.read_bytes()==b'do not touch' and not (tmp_path/'runs').exists()


def test_reviewed_alias_into_excluded_cache_refuses_before_model(tmp_path, monkeypatch, capsys):
    import driver
    source=_harness(tmp_path)
    (source/'__pycache__').mkdir();(source/'__pycache__/owned.py').write_text('VALUE=1\n')
    (source/'reviewed.py').symlink_to('__pycache__/owned.py')
    expected=validator.source_identity(source)['submission_sha256']
    monkeypatch.setattr(driver,'describe_model',lambda:pytest.fail('ambiguous cache alias reached model'))
    monkeypatch.setattr(sys,'argv',_argv(tmp_path,'--harness-path',str(source),
        '--expected-harness-sha256',expected))
    assert driver._main({})==2
    assert 'refusing --harness-path' in capsys.readouterr().err
    assert (source/'reviewed.py').read_text()=='VALUE=1\n'
    assert not (tmp_path/'runs').exists()


@pytest.mark.parametrize('target', ['__pycache__', 'missing-cache'])
def test_reviewed_broken_or_cyclic_cache_alias_refuses_cleanly(tmp_path, monkeypatch, capsys, target):
    import driver
    source=_harness(tmp_path)
    (source/'__pycache__').symlink_to(target,target_is_directory=True)
    expected=validator.source_identity(source)['submission_sha256']
    monkeypatch.setattr(driver,'describe_model',lambda:pytest.fail('ambiguous cache reached model'))
    monkeypatch.setattr(sys,'argv',_argv(tmp_path,'--harness-path',str(source),
        '--expected-harness-sha256',expected))
    assert driver._main({})==2
    assert 'refusing --harness-path' in capsys.readouterr().err
    assert (source/'__pycache__').is_symlink() and not (tmp_path/'runs').exists()
