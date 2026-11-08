"""Authored external installations + fake actors: no CLI, model or service runs."""
import json
import os
from pathlib import Path
import shutil
import tempfile

import pytest

from base_harness.codex import codex_runtime
from base_harness.opencode import opencode_runtime
from eval.containers import external_runtime as external
from eval.run import runner
from harnessgrad.loops import eval_side, mode_a


@pytest.fixture(params=['codex', 'dsh'])
def installed(request, tmp_path, monkeypatch):
    for name in ('HG_AGENT_CODEX', 'HG_AGENT_CODEX_COMMAND', 'HG_AGENT_DSH_ROOT',
                 'HG_AGENT_DSH', 'HG_AGENT_NODE', 'HG_AGENT_DSH_COMMAND'):
        monkeypatch.delenv(name, raising=False)
    with tempfile.TemporaryDirectory(prefix='hg-external-content-', dir='/var/tmp') as directory:
        root = Path(directory)
        repo = tmp_path / 'candidate'; repo.mkdir()
        program = root / 'bin' / ('codex' if request.param == 'codex' else 'node')
        program.parent.mkdir()
        shutil.copyfile('/bin/true', program)
        program.chmod(0o755)
        dependency = program.parent / 'dependency.dat'; dependency.write_text('first bytes')
        if request.param == 'codex':
            record = {'schema': 1, 'version': 'fixture', 'selection': [str(program)],
                      **codex_runtime.installation([str(program)], extra=(program.parent,))}
            manifest = {'name': 'codex', '_codex_runtime': record, 'runtime_paths': record['runtime_paths']}
        else:
            helper = external._provider('deepseek-harness/dsh_runtime.py')
            tree = root / 'dsh'; cli = tree / 'apps/cli/lib/bin.js'
            cli.parent.mkdir(parents=True); cli.write_text('// never executed')
            (repo / 'dsh_farm.tsv').write_text('')
            paths = [str(tree), str(program.parent.parent)]
            record = helper.installation(repo, {'root': str(tree), 'node': str(program), 'cli': str(cli)}, paths, [])
            manifest = {'name': 'deepseek-harness', '_dsh_runtime': record, 'runtime_paths': paths}
        manifest.update(entrypoint='agent.py', version='1')
        (repo / 'harness.json').write_text(json.dumps(manifest))
        (repo / 'agent.py').write_text("raise AssertionError('candidate source must not be imported')\n")
        yield repo, dependency, record, request.param


def evaluate(repo, monkeypatch, *, action=None, trials=1, progress_dir=None):
    calls = []
    cache = {}
    def actor(repo, task, **kwargs):
        calls.append(task['task_id'])
        if action:
            action()
        return {'task_id': task['task_id'], 'answer': 'authored', 'trace': '', 'stderr': '',
                'exit_code': 0, 'verdict': {'score': 1., 'passed': True}}
    monkeypatch.setattr(runner, 'run_one', actor)
    monkeypatch.setattr(runner, 'snapshot', lambda _: {})
    def measure():
        return runner._evaluate(repo, [{'task_id': 't', 'goal': 'synthetic'}], {}, cache=cache,
            harness_sha='same-owned-source', trials=trials, progress_dir=progress_dir, persist_trial_report=False)
    return measure, calls, cache


def test_complete_identity_allows_cache_but_same_stat_content_drift_refuses(installed, monkeypatch):
    repo, dependency, _, kind = installed
    assert external.cacheable(external.identities(repo))
    measure, calls, cache = evaluate(repo, monkeypatch)
    first, second = measure(), measure()
    assert first['cached_tasks'] == [] and second['cached_tasks'] == ['t']
    assert calls == ['t'] and len(cache) == 1
    assert second['harness_runtime'][0][kind]['complete']
    before = dependency.stat()
    dependency.write_text('other bytes')
    os.utime(dependency, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = measure()
    assert changed['cached_tasks'] == [] and changed['per_task'] == {}
    assert calls == ['t'] and 't' in changed['invalid']


def test_execution_drift_is_unmeasured_and_never_cached(installed, monkeypatch):
    repo, dependency, _, _ = installed
    measure, calls, cache = evaluate(repo, monkeypatch, action=lambda: dependency.write_text('changed'))
    result = measure()
    assert calls == ['t'] and not cache and not result['per_task']
    assert result['invalid']['t']['stage'] == 'harness runtime identity'


def test_missing_cached_identity_forces_fresh_measurement(installed, monkeypatch):
    repo, _, _, _ = installed
    measure, calls, cache = evaluate(repo, monkeypatch)
    measure()
    next(iter(cache.values())).pop('harness_runtime')
    assert measure()['cached_tasks'] == [] and calls == ['t', 't']


@pytest.mark.parametrize('profile,version', [('1', '1.19.5'), ('2', '2.2.6')])
def test_opencode_wire_is_part_of_measurement_reuse(tmp_path, monkeypatch, profile, version):
    variable = 'HG_AGENT_OPENCODE_WIRE'
    monkeypatch.delenv(variable, raising=False)
    with tempfile.TemporaryDirectory(prefix='hg-opencode-wire-', dir='/var/tmp') as directory:
        package = Path(directory) / 'package'
        program = package / 'bin/opencode'
        program.parent.mkdir(parents=True)
        shutil.copyfile('/bin/true', program)
        program.chmod(0o755)
        (package / 'package.json').write_text(json.dumps({
            'name': opencode_runtime.PACKAGES[profile], 'version': version,
        }))
        record = opencode_runtime.installation(program)
        repo = tmp_path / 'candidate'
        repo.mkdir()
        (repo / 'harness.json').write_text(json.dumps({
            'name': 'opencode', 'entrypoint': 'agent.py', 'version': '0.3.0',
            'runtime_paths': record['runtime_paths'], opencode_runtime.RECORD: record,
        }))
        progress = tmp_path / 'progress'
        observed_wires = []
        measure, _, cache = evaluate(repo, monkeypatch, progress_dir=progress,
                                    action=lambda: observed_wires.append(os.environ.get(variable, 'chat')))
        first = measure()
        chat = external.identities(repo)
        assert not first['cached_tasks'] and chat['opencode']['wire'] == 'chat'
        prior = eval_side._progress_results(progress)
        context = first['verification_context']
        point = {'round': 0, 'identity': {'harness_sha': 'same-owned-source'},
                 'env': external.fields(chat), 'verification_context': context}

        monkeypatch.setenv(variable, 'chat')
        assert external.identities(repo) == chat
        assert measure()['cached_tasks'] == ['t']
        assert eval_side._resume_runtime_mismatch(repo, prior, verification_context=context) is None
        assert mode_a._identical_prior([point], 'same-owned-source', 1, repo=repo,
                                      verification_context=context) is point
        assert external.admit_checkpoint(progress, point, fallback=repo) == chat
        without_wire = {'opencode': {key: value for key, value in chat['opencode'].items() if key != 'wire'}}
        with pytest.raises(external.RuntimeUnavailable, match='missing or changed'):
            external.admit_reuse(repo, external.fields(without_wire))

        monkeypatch.setenv(variable, 'responses')
        responses = external.identities(repo)
        assert responses == {'opencode': {**chat['opencode'], 'wire': 'responses'}}
        assert 'missing or changed' in eval_side._resume_runtime_mismatch(repo, prior, verification_context=context)
        assert mode_a._identical_prior([point], 'same-owned-source', 1, repo=repo,
                                      verification_context=context) is None
        with pytest.raises(external.RuntimeUnavailable, match='missing or changed'):
            external.admit_checkpoint(progress, point, fallback=repo)
        assert not measure()['cached_tasks']
        assert observed_wires == ['chat', 'responses'] and len(cache) == 2

        monkeypatch.setenv(variable, 'invalid')
        refused = measure()
        assert not refused['cached_tasks']
        assert 'HG_AGENT_OPENCODE_WIRE' in refused['invalid']['t']['detail']
        assert observed_wires == ['chat', 'responses'] and len(cache) == 2


def test_missing_provider_flag_is_fresh_only(installed, monkeypatch):
    repo, _, _, kind = installed
    path = repo / 'harness.json'; manifest = json.loads(path.read_text())
    key = '_codex_runtime' if kind == 'codex' else '_dsh_runtime'
    # Old Codex receipts have no content closure either.
    manifest[key].pop('cacheable')
    if kind == 'codex':
        manifest[key].pop('content_inventory'); manifest[key].pop('closure')
    path.write_text(json.dumps(manifest))
    measure, calls, cache = evaluate(repo, monkeypatch)
    assert not measure()['invalid']
    assert not measure()['cached_tasks']
    assert calls == ['t', 't'] and not cache
    with pytest.raises(external.RuntimeUnavailable, match='noncacheable'):
        external.admit_reuse(repo, {'harness_runtime': [external.identities(repo)]})


def test_whole_point_and_checkpoint_require_complete_matching_identity(installed, monkeypatch, tmp_path):
    repo, _, _, _ = installed
    current = external.identities(repo)
    point = {'round': 0, 'identity': {'harness_sha': 'sha'}, 'env': {'harness_runtime': [current]}}
    monkeypatch.setattr(mode_a, '_matching_verification_context', lambda *args: True)
    assert mode_a._identical_prior([point], 'sha', 1, repo=repo) is point
    bare = {**point, 'env': {}}
    assert mode_a._identical_prior([bare], 'sha', 1, repo=repo) is None
    run = tmp_path / 'run'; run.mkdir()
    shutil.copytree(repo, run / 'state_after_round0')
    assert external.admit_checkpoint(run, point) == current
    with pytest.raises(external.RuntimeUnavailable, match='missing'):
        external.admit_checkpoint(run, bare)


def test_progress_resume_needs_all_trial_identities(installed, monkeypatch, tmp_path):
    repo, _, _, _ = installed
    progress = tmp_path / 'run'
    measure, _, _ = evaluate(repo, monkeypatch, trials=2, progress_dir=progress)
    result = measure()
    prior = eval_side._progress_results(progress)
    context = result['verification_context']
    assert eval_side._resume_runtime_mismatch(repo, prior, verification_context=context) is None
    record = prior['t']; assert len(record['runtime_services']) == 2
    record['runtime_services'].pop()
    assert eval_side._resume_runtime_mismatch(repo, prior, verification_context=context)


def test_noncacheable_openclaw_cannot_reuse_point_or_resume(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'; repo.mkdir()
    (repo / 'harness.json').write_text('{"name":"fixture"}')
    current = {'kind': 'openclaw', 'cacheable': False, 'installation_sha256': 'x' * 64}
    monkeypatch.setattr(external.openclaw_runtime, 'identity', lambda *args: current)
    measure, calls, cache = evaluate(repo, monkeypatch)
    assert not measure()['invalid'] and not measure()['cached_tasks']
    assert calls == ['t', 't'] and not cache
    point = {'identity': {'harness_sha': 'sha'}, 'env': {'harness_runtime': [{'openclaw': current}]}}
    monkeypatch.setattr(mode_a, '_matching_verification_context', lambda *args: True)
    assert mode_a._identical_prior([point], 'sha', 1, repo=repo) is None
    with pytest.raises(external.RuntimeUnavailable, match='noncacheable'):
        external.admit_reuse(repo, point)


def test_plain_python_keeps_existing_result_shape(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'; repo.mkdir()
    (repo / 'harness.json').write_text('{"name":"plain"}')
    measure, calls, _ = evaluate(repo, monkeypatch)
    assert external.identities(repo) == {}
    assert measure()['harness_runtime'] == []
    assert measure()['cached_tasks'] == ['t'] and calls == ['t']


def test_direct_run_one_rechecks_after_execution(installed, monkeypatch):
    repo, dependency, _, kind = installed
    def actor(*args, **kwargs):
        dependency.write_text('external changed while actor ran')
        return {'task_id': 't', 'exit_code': 0, 'trace': '', 'stderr': '',
                'verdict': {'score': 1., 'passed': True}}
    monkeypatch.setattr(runner, '_run_one', actor)
    result = runner.run_one(repo, {'task_id': 't', 'goal': 'synthetic'})
    assert result['invalid']['stage'] == 'harness runtime identity'


def test_h0_lookup_and_verification_bind_external_evidence(installed, tmp_path):
    from tools import prepare_h0
    from test_prepare_h0 import _write_run
    from harnessgrad import verification_context
    repo, dependency, _, _ = installed
    current = external.identities(repo)
    config = {'dataset': 'synthetic', 'agent_model': 'fixture', 'agent_backend': 'mock',
              'endpoint': '', 'sampling': 'all', 'harness_sha': 'abc',
              'verification_context': verification_context.create({'t': 'a' * 64}),
              'harness_runtime': [current]}
    run = _write_run(tmp_path, 'h0-synthetic-fixture', per_task={'t': .5}, config=config)
    payload = json.loads((run / 'h0.json').read_text())
    payload['base']['harness_runtime'] = [current]
    (run / 'h0.json').write_text(json.dumps(payload))
    point = json.loads((run / 'curve.jsonl').read_text())
    point['env'] = {'harness_runtime': [current]}
    (run / 'curve.jsonl').write_text(json.dumps(point))
    assert prepare_h0.find_existing(config, tmp_path, source=repo)[0] == run
    # Old configuration copies can still reuse complete historical Bun/CLI
    # evidence; the measurement itself, not configuration presence, is decisive.
    metadata = json.loads((run / 'run_meta.json').read_text())
    metadata['h0_configuration'].pop('harness_runtime')
    metadata['h0_configuration'].pop('harness_sha')
    (run / 'run_meta.json').write_text(json.dumps(metadata))
    assert prepare_h0.find_existing(config, tmp_path, source=repo)[0] == run
    point.pop('env'); (run / 'curve.jsonl').write_text(json.dumps(point))
    assert prepare_h0.find_existing(config, tmp_path, source=repo) is None
    point['env'] = {'harness_runtime': [current]}
    (run / 'curve.jsonl').write_text(json.dumps(point))
    dependency.write_text('drifted external dependency')
    assert prepare_h0.find_existing(config, tmp_path, source=repo) is None


def test_fresh_h0_validation_does_not_promote_noncacheable_to_reuse(tmp_path, monkeypatch):
    from tools import prepare_h0
    from test_prepare_h0 import _write_run
    from harnessgrad import verification_context
    repo = tmp_path / 'repo'; repo.mkdir()
    (repo / 'harness.json').write_text('{"name":"fixture"}')
    identity = {'kind': 'openclaw', 'cacheable': False, 'installation_sha256': 'x' * 64}
    monkeypatch.setattr(external.openclaw_runtime, 'identity', lambda *args: identity)
    current = external.identities(repo)
    config = {'dataset': 'synthetic', 'agent_model': 'fixture', 'agent_backend': 'mock',
              'endpoint': '', 'sampling': 'all', 'harness_sha': 'abc',
              'verification_context': verification_context.create({'t': 'a' * 64}),
              'harness_runtime': [current]}
    run = _write_run(tmp_path, 'h0-synthetic-fixture', per_task={'t': .5}, config=config)
    payload = json.loads((run / 'h0.json').read_text())
    payload['base']['harness_runtime'] = [current]
    (run / 'h0.json').write_text(json.dumps(payload))
    point = json.loads((run / 'curve.jsonl').read_text()); point['env'] = {'harness_runtime': [current]}
    (run / 'curve.jsonl').write_text(json.dumps(point))
    assert prepare_h0.verify(run, None, 1, configuration=config, source=repo)['score'] == .5
    assert prepare_h0.find_existing(config, tmp_path, source=repo) is None


@pytest.mark.parametrize('name', ['codex', 'deepseek-harness', 'openclaw', 'opencode'])
def test_managed_python_name_does_not_invent_external_provenance(tmp_path, name):
    import sys
    repo = tmp_path / 'repo'; repo.mkdir()
    (repo / 'harness.json').write_text(json.dumps({'name': name, 'entrypoint': 'agent.py',
        'runtime': {'kind': 'python', 'python': f'{sys.version_info.major}.{sys.version_info.minor}',
                    'requirements': 'requirements.lock'}}))
    (repo / 'requirements.lock').write_text('# empty\n')
    (repo / 'agent.py').write_text('raise AssertionError("never import")\n')
    assert external.identities(repo) == {}
