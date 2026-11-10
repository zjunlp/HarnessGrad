"""Public scoring uses current-machine dependencies without historical byte archives."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile

import pytest

from data.scaffolds import public_native
from eval.verification import native_runtime, verifier
from harnessgrad import dataset_runtime

ROOT = Path(__file__).resolve().parents[2]


def bird_spec(tmp_path, monkeypatch):
    from data.datasets import bird_minidev
    dbs=tmp_path/'dbs';(dbs/'authored').mkdir(parents=True)
    with sqlite3.connect(dbs/'authored/authored.sqlite') as db:
        db.execute('create table items (n int)');db.executemany('insert into items values (?)',[(1,),(2,)])
    monkeypatch.setattr(bird_minidev,'DBS',dbs)
    return bird_minidev._verify({'task_id':'authored-public','db_id':'authored','gold_sql':'select n from items'})


def test_all_five_declare_public_execution_recipes():
    for name in public_native.DATASETS:
        recipe=dataset_runtime.execution_descriptor(name)
        assert recipe and recipe['profile']=='execution'
    assert dataset_runtime.execution_descriptor('bird_minidev')['pins']=={}
    assert dataset_runtime.execution_descriptor('chessbench_puzzles')['pins']=={'chess':'1.11.2'}


def test_default_binding_contains_logic_not_host_bytes(tmp_path,monkeypatch):
    spec=bird_spec(tmp_path,monkeypatch);binding=spec['native_runtime']
    assert binding['profile']==public_native.PROFILE and 'manifest' not in binding
    assert all('site-packages' not in path and 'libc.so' not in path for path in binding['sources'])
    assert spec['scorer_input']['files']  # Task inputs remain bound independently.
    assert binding['sha256']==public_native.identity({k:v for k,v in binding.items() if k!='sha256'})


def test_missing_local_dependencies_names_public_preparation_not_archive(tmp_path,monkeypatch):
    spec=bird_spec(tmp_path,monkeypatch)
    def missing(name):
        raise dataset_runtime.DatasetRuntimeUnavailable('prepare_dataset.py --dataset '+name+' --runtime execution')
    monkeypatch.setattr(dataset_runtime,'resolve_execution',missing)
    def no_archive(*args):raise AssertionError('historical archive selected')
    monkeypatch.setattr(native_runtime,'_snapshot',no_archive)
    got=verifier._verify(tmp_path,spec,'',sandbox=True,timeout_s=5)
    assert got['score'] is None and '--runtime execution' in got['unmeasured']
    assert got['native_runtime']['verified'] is False


@pytest.fixture
def local_store(monkeypatch):
    if sys.version_info[:2]!=(3,12) or shutil.which('uv') is None or shutil.which('bwrap') is None:
        pytest.skip('native control needs local CPython 3.12, uv and bubblewrap')
    with tempfile.TemporaryDirectory(prefix='hg-public-native-',dir='/var/tmp') as temporary:
        root=Path(temporary)
        monkeypatch.setenv('UV_OFFLINE','1');monkeypatch.setenv('UV_CACHE_DIR',str(root/'uv-cache'))
        monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT',str(root/'store'))
        monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES',str(root/'absent-historical-archive'))
        selected=dataset_runtime.prepare_execution('bird_minidev',python=sys.executable,root=root/'store',timeout_s=60)
        yield root,selected
        for path in root.rglob('*'):
            if path.is_dir() and not path.is_symlink():path.chmod(stat.S_IMODE(path.stat().st_mode)|0o700)


@pytest.mark.parametrize('query,expected',[('select n from items',1.0),('select n from items where n=1',0.0)])
def test_actual_local_sqlite_scoring_without_archive_and_without_actor_imports(tmp_path,monkeypatch,local_store,query,expected):
    spec=bird_spec(tmp_path,monkeypatch);work=tmp_path/'task';work.mkdir()
    (work/'answer.sql').write_text(query)
    for name in ('sqlite3','random','tempfile'):(work/(name+'.py')).write_text('raise RuntimeError("actor shadow imported")')
    (work/'data').mkdir();(work/'data/__init__.py').write_text('raise RuntimeError("actor package imported")')
    got=verifier._verify(work,spec,'',sandbox=True,timeout_s=30,env={'kind':'files'},task_id='authored-public')
    assert got.get('score')==expected,got
    assert got['native_runtime']['mechanism']==public_native.PROFILE
    assert got['native_runtime']['identity']['execution']['sqlite']
    assert got['evidence']['scorer_input']['verified']


def test_same_logic_accepts_a_second_locally_prepared_runtime(tmp_path,monkeypatch,local_store):
    root,first=local_store;spec=bird_spec(tmp_path,monkeypatch)
    second=dataset_runtime.prepare_execution('bird_minidev',python=sys.executable,root=root/'other-store',timeout_s=60)
    assert first['runtime_id']!=second['runtime_id']
    observed=[]
    for store in (root/'store',root/'other-store'):
        monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT',str(store))
        with native_runtime.admit(spec['native_runtime']) as runtime:observed.append(deepcopy(runtime.receipt))
    assert observed[0]['sha256']==observed[1]['sha256']
    assert observed[0]['identity']['python_runtime']['runtime_id']!=observed[1]['identity']['python_runtime']['runtime_id']
    assert all(x['verified'] for x in observed)


def test_edited_scoring_contract_is_not_silently_executed(tmp_path,monkeypatch):
    spec=bird_spec(tmp_path,monkeypatch);spec['native_runtime']['sha256']='0'*64
    got=verifier._verify(tmp_path,spec,'',sandbox=True,timeout_s=5)
    assert got['score'] is None and 'invalid public native' in got['unmeasured']


@pytest.mark.parametrize('name',sorted(public_native.DATASETS))
def test_each_default_scorer_uses_public_source_without_archive_lookup(name,tmp_path,monkeypatch):
    from harnessgrad import native_runtime_store
    monkeypatch.setattr(native_runtime_store,'load_manifest',lambda *a:pytest.fail('default consulted historical archive'))
    if name=='bird_minidev':
        spec=bird_spec(tmp_path,monkeypatch)
    elif name=='alfworld':
        from data.datasets import alfworld
        spec=alfworld._verifier({'task_id':'authored-public','stripped_game':'{}'})
    elif name=='jericho':
        from data.datasets import jericho
        rom=tmp_path/'authored.z3';rom.write_bytes(b'not executed')
        spec=jericho._verifier({'task_id':'authored-public','rom':rom.name,'path':rom,'seed':0})
    elif name=='scienceworld':
        from data.datasets import scienceworld
        jar=tmp_path/'authored.jar';jar.write_bytes(b'not executed')
        monkeypatch.setattr(scienceworld,'_runtime_payload',lambda:jar)
        monkeypatch.setattr(scienceworld,'JRE_STAGED',tmp_path/'jre')
        spec=scienceworld._verifier({'task_id':'authored-public','task_name':'authored','variation':0})
    else:
        from data.datasets import chessbench_puzzles
        spec=chessbench_puzzles.verify_for({'task_id':'authored-public','gold':{'fen':'authored','moves':[],'puzzle_id':'authored'}})
    assert spec['native_runtime']['profile']==public_native.PROFILE
    assert 'manifest' not in spec['native_runtime']
    assert spec['native_runtime']['sources']


def test_startup_timeout_is_unmeasured_not_a_crash(tmp_path,monkeypatch,local_store):
    import subprocess
    from eval.verification import public_native_runtime
    spec=bird_spec(tmp_path,monkeypatch)
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('authored startup',1)
    monkeypatch.setattr(public_native_runtime.PublicRuntime,'run',timeout)
    got=verifier._verify(tmp_path,spec,'',sandbox=True,timeout_s=5)
    assert got['score'] is None and got['outcome']=='unmeasured'
    assert 'timed out' in got['unmeasured']


@pytest.mark.parametrize('answer,expected',[('e7e5 b8c6',1.0),('illegal',0.0)])
def test_public_chess_in_prepared_local_environment(tmp_path,monkeypatch,answer,expected):
    root=os.environ.get('HG_PUBLIC_NATIVE_TEST_ROOT')
    if not root:pytest.skip('set HG_PUBLIC_NATIVE_TEST_ROOT to a prepared public Chess execution store')
    from data.datasets import chessbench_puzzles
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT',root)
    monkeypatch.setenv('HG_DATA_NATIVE_RUNTIMES',str(tmp_path/'no-historical-archive'))
    spec=chessbench_puzzles.verify_for({'task_id':'authored-public-chess','gold':{
        'fen':'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        'moves':['e2e4','e7e5','g1f3','b8c6','f1b5'],'puzzle_id':'authored','answer':'e7e5 b8c6'}})
    work=tmp_path/'task';work.mkdir();(work/'answer.txt').write_text(answer)
    (work/'chess.py').write_text('raise RuntimeError("actor shadow")')
    got=verifier._verify(work,spec,'',sandbox=True,timeout_s=30,env={'kind':'files'},task_id='authored-public-chess')
    assert got.get('score')==expected,got
    assert got['native_runtime']['mechanism']==public_native.PROFILE
    assert got['evidence']['gold']=={'redacted':True}


def test_input_mutation_still_refuses_scoring(tmp_path,monkeypatch,local_store):
    spec=bird_spec(tmp_path,monkeypatch);work=tmp_path/'task';work.mkdir()
    (work/'answer.sql').write_text('select n from items')
    next(row for row in spec['inputs'] if row['path']=='gold.sql')['content']='select n+1 from items'
    got=verifier._verify(work,spec,'',sandbox=True,timeout_s=30,env={'kind':'files'})
    assert got['score'] is None and got.get('unmeasured'), got


def test_scoring_code_change_is_detected_after_contract_selection(tmp_path,monkeypatch,local_store):
    spec=bird_spec(tmp_path,monkeypatch);binding=spec['native_runtime']
    binding['sources']['check.py']['sha256']='0'*64
    binding['sha256']=public_native.identity({k:v for k,v in binding.items() if k!='sha256'})
    got=verifier._verify(tmp_path,spec,'',sandbox=True,timeout_s=5)
    assert got['score'] is None and 'scoring code changed' in got['unmeasured']
