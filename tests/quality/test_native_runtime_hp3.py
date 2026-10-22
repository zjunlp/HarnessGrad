"""Authored native replay controls; no benchmark inputs or model calls."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.verification import native_runtime, verifier

DATASETS = ['jericho', 'alfworld']


def adapter_spec(dataset, tmp_path):
    if dataset == 'jericho':
        from data.datasets import jericho
        rom = tmp_path / 'authored.z3'
        rom.write_bytes(b'adapter construction only')
        return jericho._verifier({'task_id': 'authored/jericho', 'rom': rom.name,
                                 'path': rom, 'seed': 1})
    from data.datasets import alfworld
    return alfworld._verifier({'task_id': 'authored/alfworld', 'stripped_game': '{}'})


@pytest.mark.parametrize('dataset', DATASETS)
def test_admission_receipt_uses_selected_dataset(dataset, tmp_path):
    # Admission-only control derived from BIRD; never used as a dataset scorer.
    manifest = json.loads((ROOT / 'data/runtimes/bird_minidev-native.json').read_text())
    manifest['dataset'] = dataset
    path = tmp_path / 'admission-control.json'
    path.write_text(json.dumps(manifest))
    selected = {'dataset': dataset, 'manifest': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    with native_runtime.admit(selected) as runtime:
        assert runtime.receipt['dataset'] == dataset
        assert runtime.receipt['verified'] is True


@pytest.mark.parametrize('dataset', DATASETS)
def test_adapter_binds_independent_pin(dataset, tmp_path):
    selected = adapter_spec(dataset, tmp_path)['native_runtime']
    assert selected['dataset'] == dataset
    path = ROOT / selected['manifest']
    assert selected['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def selected_binding(dataset):
    from data.runtimes import NATIVE_PINS
    return NATIVE_PINS[dataset].copy()


@pytest.mark.parametrize('dataset', DATASETS)
def test_route_mismatch_and_payload_refusal(dataset, tmp_path):
    spec = adapter_spec(dataset, tmp_path)
    spec['native_runtime']['sha256'] = hashlib.sha256(b'foreign closure').hexdigest()
    got = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=20)
    assert got['score'] is None and 'manifest digest mismatch' in got['unmeasured'], got
    assert got['native_runtime']['verified'] is False
    spec = adapter_spec(dataset, tmp_path)
    got = verifier._verify(tmp_path, spec, '', sandbox=False, timeout_s=20)
    assert got['score'] is None and 'sandbox' in got['unmeasured'], got
    spec['argv'][1] = 'actor.py'
    got = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=20)
    assert got['score'] is None and 'host check route' in got['unmeasured'], got
    binding = selected_binding(dataset)
    manifest = json.loads((ROOT / binding['manifest']).read_text())
    target = next(p for p in manifest['files'] if p.endswith('libfrotz.so'))
    wrong = tmp_path / 'wrong-library'
    wrong.write_bytes(b'changed native provider')
    manifest['files'][target]['source'] = str(wrong)
    path = tmp_path / 'bad-provider.json'
    path.write_text(json.dumps(manifest))
    binding.update(manifest=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(native_runtime.NativeRuntimeUnavailable, match='payload digest mismatch'):
        with native_runtime.admit(binding):
            pytest.fail('changed provider admitted')


def authored_spec(dataset, tmp_path):
    if not (ROOT / 'tests/fixtures/hg_whp3').is_dir():
        pytest.skip("tests/fixtures/hg_whp3 is internal (see .gitignore) and absent from this clone")
    sys.path.insert(0, str(ROOT / 'tests/fixtures/hg_whp3'))
    if dataset == 'jericho':
        from authored_rom import authored_rom
        from data.datasets import jericho
        rom = tmp_path / 'source.z3'
        rom.write_bytes(authored_rom())
        return jericho._verifier({'task_id': 'authored/jericho', 'rom': 'authored.z3',
                                 'path': rom, 'seed': 1})
    from alfprobe.authored_pddl_probe import authored_game
    from data.datasets import alfworld
    return alfworld._verifier({'task_id': 'authored/alfworld',
                              'stripped_game': json.dumps(authored_game())})


@pytest.mark.parametrize('dataset', DATASETS)
def test_actual_bound_replay_and_input_refusal(dataset, tmp_path):
    work = tmp_path / 'task'
    work.mkdir()
    # An actor helper/package must not shadow admitted scorer code.
    (work / 'gym_env.py').write_text('raise RuntimeError("actor helper")')
    (work / 'jer_env.py').write_text('raise RuntimeError("actor helper")')
    (work / 'data').mkdir()
    (work / 'data/__init__.py').write_text('raise RuntimeError("actor package")')
    for package in ('numpy', 'jericho', 'textworld'):
        (work / (package + '.py')).write_text('raise RuntimeError("actor import")')
    log = work / ('jer_actions.jsonl' if dataset == 'jericho' else 'alf_actions.jsonl')
    actions = ['look', 'inventory'] if dataset == 'jericho' else ['authored invalid command', 'finish']
    log.write_text(''.join(json.dumps({'kind': 'action', 'action': a}) + '\n' for a in actions))
    spec = authored_spec(dataset, tmp_path)
    got = verifier._verify(work, spec, '', sandbox=True, timeout_s=30,
                           task_id=spec['scorer_input']['task_id'])
    assert got.get('score') == (0.0 if dataset == 'jericho' else 1.0), got
    assert got['native_runtime']['sha256'] == selected_binding(dataset)['sha256']
    evidence = json.loads((work / 'verifier/evidence.json').read_text())
    assert evidence['measured'] is True and evidence['scorer_input']['verified'] is True
    if dataset == 'jericho':
        assert evidence['max_score'] == 0 and evidence['actions_replayed'] == 2
    else:
        assert evidence['won'] is True
        log.write_text('')
        zero = verifier._verify(work, spec, '', sandbox=True, timeout_s=30,
                                task_id=spec['scorer_input']['task_id'])
        assert zero.get('score') == 0.0, zero
    # Mutation after binding must retain the existing input-digest refusal.
    key = 'jer_game.json' if dataset == 'jericho' else 'game.tw-pddl'
    next(e for e in spec['inputs'] if e.get('path') == key)['content'] = '{}'
    bad = verifier._verify(work, spec, '', sandbox=True, timeout_s=30,
                           task_id=spec['scorer_input']['task_id'])
    assert bad.get('score') is None and bad.get('unmeasured'), bad
    print(json.dumps({'dataset': dataset, 'verdict': got, 'evidence': evidence}, sort_keys=True))


@pytest.mark.parametrize('dataset', DATASETS)
def test_parent_and_child_replay_only_load_admitted_bytes(dataset, tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONPATH', '/task')
    monkeypatch.setenv('LD_PRELOAD', '/task/evil.so')
    monkeypatch.setenv('LD_LIBRARY_PATH', '/task')
    spec = authored_spec(dataset, tmp_path)
    # Stage only authored inputs. The runtime itself supplies the trusted helper.
    verifier._materialize(tmp_path, spec['inputs'], ROOT)
    (tmp_path / 'verifier').mkdir(exist_ok=True)
    with native_runtime.admit(selected_binding(dataset)) as runtime:
        replay = ("import jer_env; env=jer_env.make_env(pathlib.Path('/task/authored.z3'),seed=1); "
                  "jer_env.replay(env,['look','inventory'])" if dataset == 'jericho' else
                  "import gym_env; result=gym_env.replay('/task/game.tw-pddl',['finish']); assert result['won']")
        # Record exact copied ctypes bytes before they are unlinked by upstream.
        control = '''import ctypes,hashlib,json,os,pathlib,sys
assert sys.flags.isolated and sys.flags.no_site
assert not pathlib.Path('/etc/ld.so.cache').exists()
assert 'PYTHONPATH' not in os.environ and 'LD_PRELOAD' not in os.environ
try:
    pathlib.Path('/scorer/check.py').write_text('actor overwrite')
except OSError:
    pass
else:
    raise AssertionError('scorer code writable')
loads=[]
def audit(event,args):
    if event=='ctypes.dlopen' and args[0] and pathlib.Path(args[0]).is_file():
        loads.append({'path':args[0],'sha256':hashlib.sha256(pathlib.Path(args[0]).read_bytes()).hexdigest()})
sys.addaudithook(audit)
''' + replay + '''
print(json.dumps({'maps':pathlib.Path('/proc/self/maps').read_text(),'ctypes':loads}))
'''
        code = ('import json,pathlib,subprocess,sys; sys.path[:0]=' + repr(runtime.manifest['python_path'])
                + '; control=' + repr(control)
                + '; child=subprocess.run([sys.executable,"-I","-S","-B","-c",'
                + repr('import sys; sys.path[:0]=' + repr(runtime.manifest['python_path']) + ';' + control)
                + '],capture_output=True,text=True,check=True); '
                + 'print(json.dumps({"child":json.loads(child.stdout)})); exec(control)')
        proc = runtime.run(['python3', '-c', code], workdir=tmp_path,
                           reward_dir=tmp_path / 'verifier', timeout_s=30)
        assert proc.returncode == 0, proc.stderr
        records = proc.stdout.splitlines()
        child, parent = json.loads(records[0])['child'], json.loads(records[1])
        payloads = {e['sha256'] for e in runtime.manifest['files'].values()}
        for record in (parent, child):
            copied = {e['path'] for e in record['ctypes'] if e['path'].startswith('/tmp/')}
            assert copied and all(e['sha256'] in payloads for e in record['ctypes'])
            mapped = {line.split(maxsplit=5)[5].removesuffix(' (deleted)')
                      for line in record['maps'].splitlines() if len(line.split(maxsplit=5)) == 6
                      and line.split(maxsplit=5)[5].startswith('/')}
            assert runtime.manifest['interpreter'] in mapped
            assert mapped <= runtime.fds.keys() | copied, mapped - runtime.fds.keys() - copied
        print(json.dumps({'dataset': dataset, 'runtime': runtime.receipt,
                          'parent': parent, 'child': child}, sort_keys=True))


def test_admitted_runtime_can_repeat_readline_startup(tmp_path):
    (tmp_path / 'verifier').mkdir()
    with native_runtime.admit(selected_binding('alfworld')) as runtime:
        for _ in range(2):
            proc = runtime.run(['python3', '-c', 'import readline; print("readline startup ok")'],
                               workdir=tmp_path, reward_dir=tmp_path / 'verifier', timeout_s=30)
            assert proc.returncode == 0 and proc.stdout.strip() == 'readline startup ok', proc.stderr
