"""Actual sandboxed driver continuation with a declared, offline method wrapper."""
import json
import os
from pathlib import Path
import subprocess
import sys

from harnessgrad.orchestration.treatment_identity import SCHEMA

ROOT = Path(__file__).resolve().parents[2]


def fixture_files(tmp_path):
    wrapper, helper = tmp_path / 'wrapper.py', tmp_path / 'helper.py'
    helper.write_text("LABEL='authored treatment'\n")
    wrapper.write_text('''import json,shutil,sys
from pathlib import Path
from helper import LABEL
req=json.load(sys.stdin);r=req['round_index'];work=Path(req['workspace'])
with (work/f'invocation-{r}.log').open('x') as f:json.dump({'round':r,'label':LABEL},f)
candidate=work/'candidate';shutil.copytree(req['base_harness'],candidate)
with (candidate/'agent.py').open('a') as f:f.write(f'\\n# authored changed candidate round {r}\\n')
Path(req['trajectory_out']).write_text(json.dumps({'steps':[{'harness_dir':str(candidate),'label':LABEL,'claimed_cost':{'generation_tokens':0}}]}))
print(json.dumps({'steps':1,'changed':True,'stop':False,'method_usage':{'input':0,'output':0,'total_tokens':0}}))
''')
    manifest = tmp_path / 'treatment.json'
    manifest.write_text(json.dumps({'schema': SCHEMA, 'files': {'wrapper': str(wrapper), 'helper': str(helper)},
        'configuration': {'profile': 'authored-v1', 'sensitive_setting': 'never-copy-this-value'},
        'environment': ['AUTHORED_TREATMENT_PROFILE']}))
    return wrapper, helper, manifest


def invoke(tmp_path, wrapper, manifest, *, resume=False, extra=(), environment=None, declare=True, bootstrap=None, improver="none"):
    args = [sys.executable, str(bootstrap) if bootstrap is not None else 'driver.py', '--harness', 'loop_plain', '--dataset', 'demo', '--tasks', 't01',
        '--sampling', 'all', '--rounds', '2' if resume else '1', '--jobs', '1', '--improver', improver,
        '--no-summary', '--run-id', 'contract', '--runs-root', str(tmp_path / 'runs'),
        '--work-root', str(tmp_path / 'work'), '--gpu-budget-dir', str(tmp_path / 'slots'),
        '--method-entrypoint', f'{sys.executable} {wrapper}']
    if declare: args += ['--treatment-manifest', str(manifest)]
    if resume: args += ['--resume']
    args += list(extra)
    env = {**os.environ, 'HG_REVIEWED_ENVIRONMENT_ONLY': '1', 'HG_AGENT_BACKEND': 'mock',
           'AUTHORED_TREATMENT_PROFILE': 'authored-v1', **(environment or {})}
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def evidence(run):
    paths = [run / 'curve.jsonl', run / 'run_meta.json', run / 'method_attempts.json']
    paths += list((run / 'method_logs').rglob('*'))
    return {path.relative_to(run).as_posix(): path.read_bytes() for path in paths if path.is_file()}


def test_resume_rejects_drift_then_first_new_invocation_is_round_two(tmp_path):
    wrapper, helper, manifest = fixture_files(tmp_path)
    first = invoke(tmp_path, wrapper, manifest)
    assert first.returncode == 0, first.stdout[-2000:] + first.stderr[-2000:]
    run = tmp_path / 'runs/contract'
    original = evidence(run)
    original_meta = json.loads(original['run_meta.json'])
    record = original_meta['treatment_identity']
    assert 'never-copy-this-value' not in json.dumps(record)
    assert original_meta['treatment_manifest_declared'] is True
    assert [json.loads(line)['round'] for line in original['curve.jsonl'].splitlines()] == [0, 1]
    assert (run / 'method_logs/round-1/invocation-1.log').is_file()
    wrapper_bytes, helper_bytes, manifest_bytes = wrapper.read_bytes(), helper.read_bytes(), manifest.read_bytes()
    cases = ('wrapper', 'helper', 'missing_wrapper', 'missing_manifest', 'missing_identity',
             'model', 'provider', 'profile', 'configuration', 'environment', 'method_timeout', 'method_cap', 'waiver')
    for case in cases:
        env, extra = {}, ()
        try:
            if case in ('wrapper', 'waiver'): wrapper.write_bytes(wrapper_bytes + b'\n# changed\n')
            if case == 'helper': helper.write_bytes(helper_bytes + b'\n# changed\n')
            if case == 'missing_wrapper': wrapper.unlink()
            if case == 'missing_identity':
                meta = dict(original_meta); meta.pop('treatment_identity')
                (run / 'run_meta.json').write_text(json.dumps(meta))
            if case == 'model': env['HG_METHOD_MODEL'] = 'changed-model'
            if case == 'provider': env['HG_METHOD_BASE_URL'] = 'http://authored.invalid/v1'
            if case in ('profile', 'configuration'):
                value = json.loads(manifest_bytes)
                value['configuration']['profile' if case == 'profile' else 'sensitive_setting'] = 'changed'
                manifest.write_text(json.dumps(value))
            if case == 'environment': env['AUTHORED_TREATMENT_PROFILE'] = 'changed'
            if case == 'method_timeout': extra = ('--method-timeout', '91')
            if case == 'method_cap': env['HG_META_RUN_TOKEN_LIMIT'] = '100'
            if case == 'waiver': extra = ('--resume-across-platforms', 'authored platform review')
            before = evidence(run)
            rejected = invoke(tmp_path, wrapper, manifest, resume=True, extra=extra, environment=env,
                              declare=case != 'missing_manifest')
            assert rejected.returncode == 2, (case, rejected.stdout[-1000:] + rejected.stderr[-1000:])
            assert 'treatment' in rejected.stderr.lower(), (case, rejected.stderr)
            assert evidence(run) == before, case
        finally:
            wrapper.write_bytes(wrapper_bytes); helper.write_bytes(helper_bytes); manifest.write_bytes(manifest_bytes)
            (run / 'run_meta.json').write_bytes(original['run_meta.json'])
    continued = invoke(tmp_path, wrapper, manifest, resume=True)
    assert continued.returncode == 0, continued.stdout[-2500:] + continued.stderr[-2500:]
    curve = [json.loads(line) for line in (run / 'curve.jsonl').read_text().splitlines()]
    assert [point['round'] for point in curve] == [0, 1, 2]
    assert json.loads((run / 'method_logs/round-2/invocation-2.log').read_text())['round'] == 2
    for name, body in original.items():
        if name.startswith('method_logs/'):
            assert (run / name).read_bytes() == body
    assert json.loads((run / 'run_meta.json').read_text())['treatment_identity'] == record
    attempts = json.loads((run / 'method_attempts.json').read_text())['attempts']
    assert [attempt['round'] for attempt in attempts] == [1, 2]
    completed = evidence(run)
    refused = invoke(tmp_path, wrapper, manifest, resume=True, environment={'HG_METHOD_MODEL': 'changed'})
    assert refused.returncode == 2 and 'treatment' in refused.stderr.lower()
    assert evidence(run) == completed
    nothing = invoke(tmp_path, wrapper, manifest, resume=True)
    assert nothing.returncode == 0
    assert evidence(run) == completed


def test_resolved_provider_profile_drift_refuses_before_optional_probe(tmp_path):
    wrapper, _, manifest = fixture_files(tmp_path)
    counter = tmp_path / 'probe-counter.log'
    bootstrap = tmp_path / 'instrumented-driver.py'
    bootstrap.write_text(f'''import hashlib,os,sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
import driver
driver.load_improvers=lambda:{{}}
driver.resolve_improver=lambda *a,**kw:{{'ok':True,'name':'authored-cli','kind':'cli','version':'fixture-v1',
 'path':sys.executable,'sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
 'model':os.environ.get('AUTHORED_RESOLVED_MODEL','authored-model'),
 'profile':os.environ.get('AUTHORED_RESOLVED_PROFILE','authored-profile')}}
driver._cli_provider_base_url=lambda decl:os.environ.get('AUTHORED_RESOLVED_PROVIDER','http://authored.invalid/v1')
def fake_probe(*args):
 with Path({str(counter)!r}).open('a') as stream:stream.write('fake-probe-only\\n')
 return True,'authored offline probe callback'
driver._probe_tool_calls=fake_probe
raise SystemExit(driver.main())
''')
    base_env = {'HG_IMPROVER_SKIP_TOOL_GATE': ''}
    first = invoke(tmp_path, wrapper, manifest, bootstrap=bootstrap, improver='authored', environment=base_env)
    assert first.returncode == 0, first.stdout[-2000:] + first.stderr[-2000:]
    assert counter.read_text().splitlines() == ['fake-probe-only']
    before = evidence(tmp_path / 'runs/contract')
    for variable in ('AUTHORED_RESOLVED_MODEL', 'AUTHORED_RESOLVED_PROFILE', 'AUTHORED_RESOLVED_PROVIDER'):
        rejected = invoke(tmp_path, wrapper, manifest, resume=True, bootstrap=bootstrap, improver='authored',
                          environment={**base_env, variable: 'changed'})
        assert rejected.returncode == 2 and 'treatment' in rejected.stderr.lower()
        assert counter.read_text().splitlines() == ['fake-probe-only']
        assert evidence(tmp_path / 'runs/contract') == before
