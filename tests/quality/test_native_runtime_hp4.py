"""Bounded sealed JVM/QA controls. No released task, dataset or model run."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.verification import native_runtime, verifier

DATASETS = ['scienceworld', 'chessbench_puzzles']


def adapter_spec(dataset):
    if dataset == 'scienceworld':
        from data.datasets import scienceworld
        # Intentionally unknown authored name: no released task/variation is loaded.
        return scienceworld._verifier({'task_id': 'authored/scienceworld',
                                      'task_name': 'authored-no-benchmark-task', 'variation': 0})
    from data.datasets import chessbench_puzzles
    return chessbench_puzzles.verify_for({'task_id': 'authored/chess', 'gold': {
        'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        'moves': ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'], 'puzzle_id': 'authored'}})


def binding(dataset):
    from data.runtimes import NATIVE_PINS
    return NATIVE_PINS[dataset].copy()


@pytest.mark.parametrize('dataset', DATASETS)
def test_adapter_binds_independent_pin(dataset):
    selected = adapter_spec(dataset)['native_runtime']
    assert selected['dataset'] == dataset
    assert selected['sha256'] == hashlib.sha256((ROOT / selected['manifest']).read_bytes()).hexdigest()


def test_java_prefers_sealed_home_and_refuses_missing(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('hp4_scw_env', ROOT / 'data/datasets/scienceworld/lib/scw_env.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    actor = tmp_path / 'jre' / module.JRE_DIRNAME / 'bin'
    actor.mkdir(parents=True)
    (actor / 'java').write_text('actor JVM')
    trusted = tmp_path / 'sealed'
    (trusted / 'bin').mkdir(parents=True)
    (trusted / 'bin/java').write_text('admitted JVM')
    monkeypatch.setenv('HG_SEALED_JAVA_HOME', str(trusted))
    assert module.java_home(tmp_path) == trusted
    (trusted / 'bin/java').unlink()
    with pytest.raises(RuntimeError, match='sealed Java'):
        module.java_home(tmp_path)


@pytest.mark.parametrize('dataset', DATASETS)
def test_route_manifest_payload_and_sandbox_refusal(dataset, tmp_path):
    spec = adapter_spec(dataset)
    spec['native_runtime']['sha256'] = hashlib.sha256(b'foreign closure').hexdigest()
    got = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=20)
    assert got['score'] is None and 'manifest digest mismatch' in got['unmeasured'], got
    assert got['native_runtime']['verified'] is False
    spec = adapter_spec(dataset)
    got = verifier._verify(tmp_path, spec, '', sandbox=False, timeout_s=20)
    assert got['score'] is None and 'sandbox' in got['unmeasured'], got
    spec['argv'][1] = 'actor.py'
    got = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=20)
    assert got['score'] is None and 'host check route' in got['unmeasured'], got
    manifest = json.loads((ROOT / binding(dataset)['manifest']).read_text())
    target = '/opt/hg-jre/lib/server/libjvm.so' if dataset == 'scienceworld' else manifest['interpreter']
    wrong = tmp_path / 'wrong-provider'
    wrong.write_bytes(b'changed provider')
    manifest['files'][target]['source'] = str(wrong)
    path = tmp_path / 'changed.json'
    path.write_text(json.dumps(manifest))
    selected = {**binding(dataset), 'manifest': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(native_runtime.NativeRuntimeUnavailable, match='payload digest mismatch'):
        with native_runtime.admit(selected):
            pytest.fail('modified provider admitted')


def test_chess_actual_bound_check_shadow_zero_and_input_refusal(tmp_path):
    spec = adapter_spec('chessbench_puzzles')
    (tmp_path / 'chess.py').write_text('raise RuntimeError("actor package")')
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/__init__.py').write_text('raise RuntimeError("actor adapter")')
    (tmp_path / 'answer.txt').write_text('e7e5 b8c6')
    got = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=30,
                           task_id='authored/chess')
    assert got['score'] == 1.0, got
    evidence = json.loads((tmp_path / 'verifier/evidence.json').read_text())
    assert evidence['measured'] and evidence['scorer_input']['verified']
    assert evidence['gold'] == {'redacted': True}
    (tmp_path / 'answer.txt').write_text('illegal')
    zero = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=30,
                            task_id='authored/chess')
    assert zero['score'] == 0.0, zero
    next(e for e in spec['inputs'] if e.get('path') == 'gold.json')['content'] = '{}'
    bad = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=30,
                           task_id='authored/chess')
    assert bad['score'] is None and bad.get('unmeasured'), bad
    print(json.dumps({'dataset': 'chessbench_puzzles', 'positive': got, 'zero': zero,
                      'input_mismatch': bad, 'evidence': evidence}, sort_keys=True))


def test_scienceworld_actual_check_unknown_authored_task_is_unmeasured(tmp_path):
    spec = adapter_spec('scienceworld')
    # This is an intentional no-task failure control, never a measured zero.
    (tmp_path / 'scw_env.py').write_text('raise RuntimeError("actor helper")')
    (tmp_path / 'scienceworld.py').write_text('raise RuntimeError("actor package")')
    got = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=30,
                           task_id='authored/scienceworld')
    evidence = json.loads((tmp_path / 'verifier/evidence.json').read_text())
    assert got['score'] is None and got.get('unmeasured'), got
    assert evidence['measured'] is False and evidence['scorer_input']['verified'] is True
    assert evidence['java_home'] == '/opt/hg-jre', evidence
    assert 'Unknown taskName' in evidence['error'], evidence
    next(e for e in spec['inputs'] if e.get('path') == 'scienceworld.jar')['from'] = str(tmp_path / 'wrong.jar')
    (tmp_path / 'wrong.jar').write_bytes(b'changed engine')
    bad = verifier._verify(tmp_path, spec, '', sandbox=True, timeout_s=30,
                           task_id='authored/scienceworld')
    assert bad['score'] is None and bad.get('unmeasured'), bad
    print(json.dumps({'dataset': 'scienceworld', 'unknown_task': got,
                      'input_mismatch': bad, 'evidence': evidence}, sort_keys=True))


@pytest.mark.parametrize('dataset', DATASETS)
def test_parent_and_child_native_maps_and_lifecycle(dataset, tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONPATH', '/task')
    monkeypatch.setenv('LD_PRELOAD', '/task/evil.so')
    monkeypatch.setenv('LD_LIBRARY_PATH', '/task')
    monkeypatch.setenv('JAVA_TOOL_OPTIONS', '-agentpath:/task/evil.so')
    (tmp_path / 'verifier').mkdir()
    with native_runtime.admit(binding(dataset)) as runtime:
        common = '''import json,os,pathlib,sys
assert sys.flags.isolated and sys.flags.no_site
assert not pathlib.Path('/etc/ld.so.cache').exists()
assert not any(k in os.environ for k in ('PYTHONPATH','LD_PRELOAD','JAVA_TOOL_OPTIONS'))
try:
    pathlib.Path('/scorer/check.py').write_text('actor overwrite')
except OSError:
    pass
else:
    raise AssertionError('scorer code writable')
'''
        if dataset == 'scienceworld':
            action = '''assert os.environ.get('JAVA_HOME')=='/opt/hg-jre', dict(os.environ)
import scienceworld
assert os.environ.get('JAVA_HOME')=='/opt/hg-jre', dict(os.environ)
launches=[]
def audit(event,args):
    if event=='subprocess.Popen':
        launches.append({'executable':args[0],'argv':args[1],'cwd':args[2]})
sys.addaudithook(audit)
env=scienceworld.ScienceWorldEnv('')
proc=env._gateway.java_process
java={'pid':proc.pid,'maps':pathlib.Path('/proc/'+str(proc.pid)+'/maps').read_text(),
      'runtime':env._gateway.jvm.java.lang.System.getProperty('java.runtime.version'),
      'home':env._gateway.jvm.java.lang.System.getProperty('java.home'), 'launches':launches,
      'executable':os.readlink('/proc/'+str(proc.pid)+'/exe').removesuffix(' (deleted)')}
assert java['home']=='/opt/hg-jre'
assert len(launches)==1 and launches[0]['executable']=='java', launches
assert java['executable']=='/opt/hg-jre/bin/java', java['executable']
assert launches[0]['argv'][-3:]==['py4j.GatewayServer','--die-on-broken-pipe','0']
env.close()
java['exit_status']=proc.wait(timeout=10)
assert java['exit_status']==0
assert not pathlib.Path('/proc/'+str(proc.pid)).exists()
print(json.dumps({'maps':pathlib.Path('/proc/self/maps').read_text(),'java':java}))
'''
        else:
            action = '''import subprocess,chess
child=subprocess.run([sys.executable,'-I','-S','-B','-c',
    'import json,pathlib; print(json.dumps({"maps":pathlib.Path("/proc/self/maps").read_text()}))'],
    capture_output=True,text=True,check=True,timeout=10)
print(json.dumps({'maps':pathlib.Path('/proc/self/maps').read_text(), 'child':json.loads(child.stdout)}))
'''
        proc = runtime.run(['python3', '-c', common + action], workdir=tmp_path,
                           reward_dir=tmp_path / 'verifier', timeout_s=30)
        assert proc.returncode == 0, proc.stderr
        record = json.loads(proc.stdout)
        for observation in (record, record['java' if dataset == 'scienceworld' else 'child']):
            mapped = {line.split(maxsplit=5)[5].removesuffix(' (deleted)')
                      for line in observation['maps'].splitlines() if len(line.split(maxsplit=5)) == 6
                      and line.split(maxsplit=5)[5].startswith('/')}
            # Java also maps a writable perf counter file in its private tmpfs.
            native = {p for p in mapped if not p.startswith('/tmp/hsperfdata_')}
            assert native <= runtime.fds.keys(), native - runtime.fds.keys()
            observation['admitted_mapped_files'] = {p: runtime.manifest['files'][p]['sha256'] for p in sorted(native)}
            observation.pop('maps')
        print(json.dumps({'dataset': dataset, 'runtime': runtime.receipt, 'control': record}, sort_keys=True))


def test_chess_declared_verify_map_also_binds_pin(monkeypatch):
    from data.datasets import chessbench_puzzles as dataset
    record={'task_id':'authored/chess-map','gold':{'fen':'authored-map-only','moves':[],
                                               'puzzle_id':'authored-map'}}
    monkeypatch.setattr(dataset._SPEC,'ensure',lambda:[record])
    selected=dataset.__getattr__('VERIFY')['authored/chess-map']['native_runtime']
    assert selected==binding('chessbench_puzzles')
