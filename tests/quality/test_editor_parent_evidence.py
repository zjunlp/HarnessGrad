"""Offline process controls for the source-tree / public-channel boundary."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_editor_dispatch import _channel, _harness, editor


@pytest.mark.parametrize('kind', ['missing', 'deleted-state', 'nested-state', 'linked-state', 'linked-channel', 'exam'])
def test_invalid_channel_fails_before_dispatch(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('HG_IMPROVER', '/must-not-start')
    base = _harness(tmp_path / 'run')
    root = _channel(base, {'kind': 'cli', 'path': '/must-not-start'})
    if kind == 'missing':
        (root / 'round.json').unlink()
    elif kind == 'deleted-state':
        base = root / 'states/round-9'
    elif kind == 'nested-state':
        base = _harness(root / 'states/round-1/nested')
    elif kind == 'linked-state':
        other = _harness(tmp_path / 'other')
        base = root / 'states/round-1'
        base.symlink_to(other)
    elif kind == 'linked-channel':
        moved = tmp_path / 'moved'
        root.rename(moved)
        root.symlink_to(moved)
    elif kind == 'exam':
        (root / 'round.json').write_text('{"score_kind":"exam"}')
    with pytest.raises(ValueError, match='evidence'):
        editor.ask('Do not dispatch invalid evidence', base=base)


RUNNER = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, '/code/methods')
import editor
mode = sys.argv[1]
def harness(p, marker):
    p.mkdir(parents=True, exist_ok=True)
    (p/'harness.json').write_text(json.dumps({'name':'fixture','version':'0.2.0','path':'.','entrypoint':'agent.py','backend':'cli','install':'requirements.txt','env_kinds':['files','exec']}))
    (p/'agent.py').write_text('value = '+repr(marker)+'\n')
    (p/'requirements.txt').write_text('')
    return p
run = harness(Path('/work/run'), 'current')
channel = run/'_harnessgrad'
(channel/'states').mkdir(parents=True)
(channel/'round.json').write_text(json.dumps({'marker':'public-run','identity':{'improver':{'kind':'cli','path':'/work/fake'}}}))
(channel/'traces').mkdir()
(channel/'traces/public.jsonl').write_text('public trace\n')
source = harness(channel/'states/round-1', 'historical') if mode in ('history', 'dream') else run
if mode == 'explicit':
    source = harness(Path('/work/other-source'), 'historical')
    own = source/'_harnessgrad'
    own.mkdir()
    (own/'round.json').write_text(json.dumps({'marker':'WRONG','identity':{'improver':{'kind':'endpoint','base_url':'https://must-not-call.invalid'}}}))
if mode == 'none':
    os.environ['HG_IMPROVER'] = '/work/fake'
explicit = False if mode == 'none' else channel if mode == 'explicit' else None
os.environ['EXPECTED_SOURCE'] = 'historical' if mode in ('history','explicit','dream') else 'current'
os.environ['EXPECTED_EVIDENCE'] = 'absent' if mode == 'none' else 'public-run'
req = {'workspace':'/work/output'}
Path(req['workspace']).mkdir()
if mode == 'dream':
    import io
    sys.path.insert(0, '/code/methods/dream_rsi')
    import run as dream
    (channel/'history').mkdir()
    history = [{'round':1,'score_kind':'training','score':0.9,'per_task':{'public':0.9}},
               {'round':2,'score_kind':'training','score':0.1,'per_task':{'public':0.1}}]
    for point in history:
        (channel/'history'/('round-'+str(point['round'])+'.json')).write_text(json.dumps(point))
    (channel/'states/index.json').write_text(json.dumps({'round-1':{'harness_dir':str(source)}}))
    request = dict(req, platform_api_version='0.1.0', mode='A', base_harness=str(run),
                   round_index=3, task_ids=['public'], train_task_ids=['public'], trajectory_out='/work/trajectory.json')
    assert dream.base_round(dream.select(history), history, run)[0] == source
    sys.stdin = io.StringIO(json.dumps(request))
    assert dream.main() == 0
    dest = Path(req['workspace'])/'candidate'
    proof = json.loads(next(Path('/tmp').glob('hg-cli-ask-*/artifacts/proof.json')).read_text())
else:
    reply, dest, problems = editor.propose_and_apply(source, req, editor.DEFAULT_SYSTEM,
        lambda problems: 'Make a fixture edit', evidence_channel=explicit)
    assert not problems, problems
    proof = json.loads(reply['artifacts']['proof.json'])
assert 'changed = True' in (dest/'agent.py').read_text()
assert proof['source'] == os.environ['EXPECTED_SOURCE']
assert proof['evidence'] == os.environ['EXPECTED_EVIDENCE']
assert not proof['private_visible']
assert (source/'agent.py').read_text().endswith(repr(os.environ['EXPECTED_SOURCE'])+'\n')
print("\n"+json.dumps(proof))
'''

FAKE = r'''#!/usr/bin/python3
import json, os
from pathlib import Path
p = Path.cwd()
source = 'historical' if 'historical' in (p/'agent.py').read_text() else 'current'
r = p/'evidence/round.json'
evidence = json.loads(r.read_text())['marker'] if r.exists() else 'absent'
assert source == os.environ['EXPECTED_SOURCE'], source
assert evidence == os.environ['EXPECTED_EVIDENCE'], evidence
assert not Path('/hidden-eval').exists()
if evidence != 'absent':
    assert (p/'evidence/traces/public.jsonl').read_text() == 'public trace\n'
(p/'agent.py').write_text((p/'agent.py').read_text()+'changed = True\n')
(p.parent/'artifacts/proof.json').write_text(json.dumps({'source':source,'evidence':evidence,'cwd':str(p),'private_visible':Path('/hidden-eval').exists()}))
print('Offline fixture process verified selected source and public run evidence.')
'''


@pytest.mark.parametrize('mode', ['current', 'history', 'explicit', 'none', 'dream'])
def test_real_bwrap_fake_cli_source_and_channel(tmp_path, mode):
    """Execute the complete proposer path with platform/private trees unmounted."""
    if not shutil.which('bwrap'):
        pytest.skip('bubblewrap is unavailable')
    root = Path(__file__).resolve().parents[2]
    work = tmp_path / 'public'
    work.mkdir()
    (tmp_path / 'hidden-eval').write_text('PRIVATE SYNTHETIC EVALUATOR')
    (work / 'runner.py').write_text(RUNNER)
    fake = work / 'fake'
    fake.write_text(FAKE)
    fake.chmod(0o755)
    argv = ['bwrap', '--unshare-user', '--unshare-net', '--die-with-parent',
            '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib',
            '--ro-bind', '/lib64', '/lib64', '--symlink', 'usr/bin', '/bin',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--ro-bind', str(root / 'methods'), '/code/methods',
            '--ro-bind', str(root / 'eval/ckpt/bun_dependencies.py'),
            '/code/eval/ckpt/bun_dependencies.py',
            '--bind', str(work), '/work', '--chdir', '/work',
            '--setenv', 'HOME', '/work', '--setenv', 'PATH', '/usr/bin:/bin',
            '/usr/bin/python3', '/work/runner.py', mode]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])['private_visible'] is False
