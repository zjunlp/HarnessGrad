"""Submission admission rejects hostile archives before it executes any code."""

import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import zipfile
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('submission_gate', ROOT / 'console/submissions.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
MIB = 1024 * 1024
MANIFEST = {'name': 'admission-probe', 'version': '1', 'entrypoint': 'agent.py',
            'env_kinds': ['files']}
ACTOR = '''import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--task')
p.add_argument('--workdir')
a = p.parse_args()
Path(a.workdir, 'answer.txt').write_text('ok')
'''


def files(**changes):
    man = dict(MANIFEST)
    man.update(changes)
    return {'harness.json': json.dumps(man).encode(), 'agent.py': ACTOR.encode()}


def archive(tmp_path, members=None, *, kind='tar.gz', wrapper='', name=None, modes=None):
    members = files() if members is None else members
    path = tmp_path / (name or ('upload.' + kind))
    if kind == 'zip':
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as out:
            for key, value in members.items():
                info = zipfile.ZipInfo(wrapper + key)
                info.create_system = 3
                info.external_attr = ((stat.S_IFDIR | 0o755) if value is None else
                                      (stat.S_IFREG | (modes or {}).get(key,0o644))) << 16
                out.writestr(info, b'' if value is None else value,
                             compress_type=zipfile.ZIP_DEFLATED)
    else:
        with tarfile.open(path, 'w:gz' if kind == 'tar.gz' else 'w') as out:
            for key, value in members.items():
                info = tarfile.TarInfo(wrapper + key)
                info.type = tarfile.DIRTYPE if value is None else tarfile.REGTYPE
                info.mode = 0o755 if value is None else (modes or {}).get(key,0o644)
                info.size = 0 if value is None else len(value)
                out.addfile(info, None if value is None else io.BytesIO(value))
    return path


def codes(path):
    assert callable(getattr(gate, 'inspect_archive', None)), 'A4 archive admission is missing'
    problems = gate.inspect_archive(path)
    assert all(isinstance(p.get('detail'), str) and p['detail'] for p in problems)
    return {p['code'] for p in problems}


def test_archive_rejects_bad_format(tmp_path):
    path = tmp_path / 'upload.tar.gz'
    path.write_bytes(b'not an archive')
    assert codes(path) == {'bad-archive'}


def test_archive_compressed_boundary_is_inclusive(tmp_path):
    path = archive(tmp_path, kind='tar')
    with path.open('ab') as out:
        out.truncate(20 * MIB)
    assert codes(path) == set()
    with path.open('ab') as out:
        out.write(b'\0')
    assert codes(path) == {'payload-too-large'}


def test_archive_entry_boundary_counts_directories(tmp_path):
    members = {'harness.json': b'{}', **{f'd{i}/': None for i in range(1999)}}
    path = archive(tmp_path, members)
    assert codes(path) == set()
    members['one-too-many/'] = None
    assert codes(archive(tmp_path, members)) == {'too-many-entries'}


@pytest.mark.parametrize('extra', [0, 1])
@pytest.mark.parametrize('kind', ['tar.gz', 'zip'])
def test_archive_real_decompressed_boundary(tmp_path, extra, kind):
    # Real 200 MiB data, streamed from a sparse file, not a lowered test threshold.
    data = tmp_path / 'data'
    with data.open('wb') as out:
        out.truncate(200 * MIB - 2 + extra)
    path = tmp_path / ('large.' + kind)
    if kind == 'zip':
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as out:
            out.writestr('harness.json', b'{}')
            out.write(data, 'data')
    else:
        with tarfile.open(path, 'w:gz') as out:
            info = tarfile.TarInfo('harness.json')
            info.size = 2
            out.addfile(info, io.BytesIO(b'{}'))
            info = tarfile.TarInfo('data')
            info.size = data.stat().st_size
            with data.open('rb') as source:
                out.addfile(info, source)
    assert codes(path) == ({'archive-too-large'} if extra else set())


@pytest.mark.parametrize('member', ['/escape', '../escape', 'a/../escape', 'C:/escape',
                                     'C:escape', '\\escape', '\\\\host\\share\\x',
                                     'a\\..\\escape', 'a\\b', 'x:stream'])
def test_archive_refuses_cross_platform_paths(tmp_path, member):
    assert 'unsafe-path' in codes(archive(tmp_path, {**files(), member: b'x'}))


@pytest.mark.parametrize('entry_type', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE,
                                       tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_archive_refuses_tar_special_entries(tmp_path, entry_type):
    path = tmp_path / 'special.tar'
    with tarfile.open(path, 'w') as out:
        info = tarfile.TarInfo('harness.json')
        info.type = entry_type
        info.linkname = '../private'
        out.addfile(info)
    assert 'unsafe-entry' in codes(path)


def test_archive_refuses_ambiguous_root(tmp_path):
    assert 'ambiguous-root' in codes(archive(tmp_path, {'a/agent.py': b'', 'b/x': b''}))


def test_archive_bounds_extended_metadata_before_parsing(tmp_path):
    path = tmp_path / 'pax-bomb.tar.gz'
    info = tarfile.TarInfo('././@PaxHeader')
    info.type = tarfile.XHDTYPE
    info.size = 250 * MIB
    with gzip.open(path, 'wb') as out:
        out.write(info.tobuf())
        # Declared bomb, deliberately no data: bounded scan refuses before allocation.
        out.write(b'\0' * 1024)
    assert codes(path) & {'bad-archive', 'archive-too-large'}


def harness(tmp_path, members=None):
    root = tmp_path / 'harness'
    root.mkdir()
    for name, value in (files() if members is None else members).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if value is None:
            target.mkdir(exist_ok=True)
        else:
            target.write_bytes(value)
    return root


@pytest.mark.parametrize('change,want,field', [
    ({'name': ''}, 'manifest-invalid', 'name'),
    ({'version': 1}, 'manifest-invalid', 'version'),
    ({'entrypoint': []}, 'manifest-invalid', 'entrypoint'),
    ({'entrypoint': 'missing.py'}, 'entrypoint-missing', 'entrypoint'),
    ({'entrypoint': '../private.py'}, 'entrypoint-missing', 'entrypoint'),
    ({'entrypoint': 'C:\\private.py'}, 'entrypoint-missing', 'entrypoint'),
    ({'backend': 'other'}, 'backend-unsupported', 'backend'),
    ({'install': 'missing.sh'}, 'install-missing', 'install'),
    ({'install': '../private'}, 'install-missing', 'install'),
    ({'install': 12}, 'manifest-invalid', 'install'),
    ({'env_kinds': ['terminal']}, 'env-kind-unsupported', 'env_kinds'),
    ({'env_kinds': None}, 'env-kind-unsupported', 'env_kinds'),
])
def test_harness_rejects_manifest_fields(tmp_path, change, want, field):
    root = harness(tmp_path, files(**change))
    assert callable(getattr(gate, 'inspect_harness', None)), 'A4 manifest admission is missing'
    with pytest.raises(gate.SubmissionRejected) as caught:
        gate.inspect_harness(root, scenario={'env_kinds': ['files']}, work_root=tmp_path/'work')
    assert any(p['code'] == want and p.get('field') == field for p in caught.value.problems)


@pytest.mark.parametrize('raw,want', [(None, 'manifest-missing'), (b'{', 'manifest-invalid'),
                                    (b'[]', 'manifest-invalid'), (b'\xff', 'manifest-invalid')])
def test_harness_rejects_unreadable_manifest_before_execution(tmp_path, raw, want):
    members = {'agent.py': b"raise AssertionError('must not execute')"}
    if raw is not None:
        members['harness.json'] = raw
    root = harness(tmp_path, members)
    assert callable(getattr(gate, 'inspect_harness', None)), 'A4 manifest admission is missing'
    with pytest.raises(gate.SubmissionRejected) as caught:
        gate.inspect_harness(root, work_root=tmp_path/'work')
    assert caught.value.problems[0]['code'] == want


@pytest.mark.parametrize('optional', ['omitted-kinds', 'null-install', 'both'])
def test_harness_accepts_documented_optional_manifest_defaults(tmp_path, optional):
    man = dict(MANIFEST)
    if optional in ('omitted-kinds', 'both'):
        del man['env_kinds']
    if optional in ('null-install', 'both'):
        man['install'] = None
    root = harness(tmp_path, {'harness.json': json.dumps(man).encode(),
                              'agent.py': ACTOR.encode()})
    assert gate.inspect_harness(root, scenario={'env_kinds': ['files']},
                                work_root=tmp_path/'work') == man


@pytest.mark.parametrize('actor,detail', [
    ('pass\n', 'answer.txt'),
    ("import sys\nprint('smoke-out')\nprint('smoke-err', file=sys.stderr)\nsys.exit(7)\n", '7'),
])
def test_smoke_rejects_real_bad_actor(tmp_path, actor, detail):
    root = harness(tmp_path, {**files(), 'agent.py': actor.encode()})
    with pytest.raises(gate.SubmissionRejected) as caught:
        gate.inspect_harness(root, work_root=tmp_path/'work')
    problem = caught.value.problems[0]
    assert problem['code'] == 'smoke-failed' and detail in problem['detail']
    if detail == '7':
        assert 'smoke-out' in problem['detail'] and 'smoke-err' in problem['detail']


def test_smoke_real_isolation_and_no_install(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'not-for-the-subject')
    monkeypatch.setenv('HG_METHOD_TOKEN', 'not-for-the-subject')
    marker = tmp_path/'private-marker'
    marker.write_text('private')
    install_marker = tmp_path/'install-ran'
    platform = ROOT/'driver.py'
    root = harness(tmp_path, {**files(install='install.py'),
                              'install.py': f"from pathlib import Path\nPath({str(install_marker)!r}).touch()".encode()})
    actor = f'''import os, socket
from pathlib import Path
assert not any('KEY' in k or 'TOKEN' in k for k in os.environ)
assert not Path({str(marker)!r}).exists()
assert not Path({str(platform)!r}).exists()
assert socket.if_nameindex() == [(1, 'lo')]
try:
    Path(__file__).write_text('overwritten')
except OSError:
    pass
else:
    raise AssertionError('source was writable')
''' + ACTOR
    (root/'agent.py').write_text(actor)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    assert gate.inspect_harness(root, work_root=tmp_path/'work')['version'] == '1'
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert not install_marker.exists()
    assert not list((tmp_path/'work').iterdir())


def test_smoke_unavailable_never_runs_actor_on_host(tmp_path, monkeypatch):
    from eval.containers import sandbox
    marker = tmp_path/'host-executed'
    root = harness(tmp_path, {**files(), 'agent.py':
                              f"from pathlib import Path\nPath({str(marker)!r}).touch()\n".encode()})
    def unavailable(*args, **kwargs):
        raise sandbox.SandboxUnavailable('fixture: bwrap missing')
    monkeypatch.setattr(sandbox, 'run', unavailable)
    with pytest.raises(gate.SubmissionRejected) as caught:
        gate.inspect_harness(root, work_root=tmp_path/'work')
    assert caught.value.problems[0]['code'] == 'service-not-ready'
    assert not marker.exists()


def test_uploaded_smoke_uses_subject_limits_but_readiness_is_uncapped(tmp_path, monkeypatch):
    from eval.containers import sandbox
    from harnessgrad import config
    limits_config = config.load({**os.environ, 'HG_SANDBOX_MEMORY_MB':'256',
                                 'HG_SANDBOX_CPUS':'0.5', 'HG_SANDBOX_PIDS':'64'})
    monkeypatch.setattr(sandbox, 'current_config', lambda: limits_config)
    real_run = sandbox.run
    launches = []
    def observed(argv, **kwargs):
        launches.append((argv, kwargs.get('limits')))
        return real_run(argv, **kwargs)
    monkeypatch.setattr(sandbox, 'run', observed)
    assert gate.inspect_harness(harness(tmp_path), work_root=tmp_path/'work')['version'] == '1'
    assert launches[0] == (['/bin/true'], None)
    assert len(launches) == 2
    assert launches[1][1] == {'memory':'256M', 'cpus':0.5, 'pids':64}


def plan(tmp_path, path, **kwargs):
    assert callable(getattr(gate, 'plan_submission', None)), 'A4 submission planning is missing'
    return gate.plan_submission(path, kwargs.pop('scenario_name', 'demo-smoke'),
                                staging_root=tmp_path/'staging', runs_root=tmp_path/'runs',
                                work_root=tmp_path/'work', budget_dir=tmp_path/'budget', **kwargs)


@pytest.mark.parametrize('kind',['tar','tar.gz','zip'])
def test_upload_preserves_executable_helper_without_special_bits(tmp_path,kind):
    actor = ACTOR.replace("Path(a.workdir, 'answer.txt').write_text('ok')",
        "import subprocess\nsubprocess.run([str(Path(__file__).with_name('helper.sh')),a.workdir],check=True)")
    members={**files(),'agent.py':actor.encode(),
             'helper.sh':b'#!/bin/sh\nprintf ok > "$1/answer.txt"\n'}
    result=plan(tmp_path,archive(tmp_path,members,kind=kind,modes={'helper.sh':0o755}))
    helper=result['harness_dir']/'helper.sh'
    assert stat.S_IMODE(helper.stat().st_mode)&0o111==0o111
    assert not stat.S_IMODE(helper.stat().st_mode)&0o7000
    assert not stat.S_IMODE((result['harness_dir']/'agent.py').stat().st_mode)&0o111


@pytest.mark.parametrize('kind', ['tar', 'tar.gz', 'zip'])
@pytest.mark.parametrize('wrapper', ['', 'wrapped/'])
def test_plan_real_smoke_hashes_and_no_driver(tmp_path, monkeypatch, kind, wrapper):
    real_popen = subprocess.Popen
    def only_sandbox(argv, *args, **kwargs):
        child = argv[argv.index('--')+1:] if Path(argv[0]).name == 'systemd-run' else argv
        assert child == ['/bin/true'] or Path(child[0]).name == 'bwrap', (
            f'plan launched an actor outside the sandbox: {argv}')
        assert str(ROOT/'driver.py') not in argv
        return real_popen(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', only_sandbox)
    path = archive(tmp_path, kind=kind, wrapper=wrapper)
    raw = path.read_bytes()
    result = plan(tmp_path, path)
    assert result['archive_sha256'] == hashlib.sha256(raw).hexdigest()
    expected = hashlib.sha256(b'agent.py\0' + ACTOR.encode() + b'harness.json\0'
                              + files()['harness.json']).hexdigest()
    assert result['tree_sha256'] == expected
    from tools import validate_harness as validator
    assert result['tree_sha256'] == validator.source_identity(result['harness_dir'])['submission_sha256']
    assert result['harness'] == MANIFEST
    assert result['scenario'] == 'demo-smoke'
    assert result['scenario_receipt']['sha256'] == hashlib.sha256((ROOT/'scenarios/demo-smoke.json').read_bytes()).hexdigest()
    assert result['run_id'] == result['submission_id'] and result['run_id'].startswith('sub-')
    assert re.fullmatch(r'sub-\d{8}T\d{6}Z-[0-9a-f]{8}', result['submission_id'])
    argv = result['argv']
    assert argv[:2] == [sys.executable, str(ROOT/'driver.py')]
    for flag, want in [('--harness-path', str(result['harness_dir'])), ('--scenario', 'demo-smoke'),
                       ('--run-id', result['run_id']), ('--improver', 'none'), ('--gpu-budget', '1')]:
        assert argv[argv.index(flag)+1] == want
    assert '--side' not in argv and '--rounds' not in argv
    assert not (tmp_path/'runs').exists() and not (tmp_path/'budget').exists()
    assert not list(tmp_path.rglob('curve.jsonl')) and not list(tmp_path.rglob('run_meta.json'))
    assert list(result['harness_dir'].iterdir()) and path.read_bytes() == raw
    assert 'score' not in result and 'status' not in result


def test_plan_fixes_original_bytes_before_inspection(tmp_path, monkeypatch):
    path = archive(tmp_path)
    raw = path.read_bytes()
    assert callable(getattr(gate, 'plan_submission', None)), 'A4 submission planning is missing'
    real_scan = gate._scan_archive
    def replace_input(snapshot, *args, **kwargs):
        path.write_bytes(b'changed by sender after upload')
        return real_scan(snapshot, *args, **kwargs)
    monkeypatch.setattr(gate, '_scan_archive', replace_input)
    result = plan(tmp_path, path)
    assert result['archive_sha256'] == hashlib.sha256(raw).hexdigest()
    assert (result['harness_dir']/'agent.py').read_text() == ACTOR
