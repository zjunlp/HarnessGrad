"""Authored adversarial import controls against an explicitly prepared verifier."""
import hashlib
import json
import os
from pathlib import Path
import py_compile
import subprocess
import tarfile

import pytest

from data.scaffolds.scorer_input import bind
from eval.verification.verifier import _verify
from harnessgrad import dataset_runtime

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_SHA = '56c7c42aee979def86b69547ecd6d5a877a0e4940af8923bf4325ca0defecbaf'


@pytest.mark.parametrize('solution,expected', [('print(2 + 3)\n', 1), ('print(2 + 2)\n', 0)])
def test_managed_scorer_ignores_actor_modules_and_unchecked_pyc(tmp_path, monkeypatch, solution, expected):
    store, archive = os.environ.get('HG_TEST_LCB_RUNTIME_ROOT'), os.environ.get('HG_TEST_LCB_ARCHIVE')
    if not store or not archive:
        pytest.skip('requires explicitly prepared synthetic verifier artifact and pinned public source archive')
    archive = Path(archive)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA
    monkeypatch.setenv('HG_DATASET_RUNTIME_ROOT', store)
    artifact = dataset_runtime.resolve_execution('livecodebench')
    binding = dataset_runtime.bind(artifact, 'verifier')
    origin = subprocess.check_output([binding['python'], '-I', '-B', '-c',
                                      'import numpy; print(numpy.__file__)'], text=True).strip()
    assert Path(origin).resolve().is_relative_to(Path(binding['path']).resolve())
    sources = tmp_path / 'sources'; sources.mkdir()
    with tarfile.open(archive) as tar:
        for name in ('testing_util.py', 'pass_k_utils.py'):
            member = next(item for item in tar.getmembers() if item.name.endswith('/lcb_runner/evaluation/' + name))
            (sources/name).write_bytes(tar.extractfile(member).read())
    (sources/'tests.json').write_text(json.dumps({'inputs':[''], 'outputs':['5\n']}))
    work = tmp_path / 'work'; work.mkdir()
    (work/'solution.py').write_text(solution)
    marker = work/'actor-module-executed'
    poison = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('actor code executed')\n"
    (work/'numpy.py').write_text(poison)
    (work/'data').mkdir()
    (work/'data/__init__.py').write_text(poison)
    py_compile.compile(str(work/'data/__init__.py'), doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    spec={'kind':'command','python_runtime':binding,'runtime_paths':binding['runtime_paths'],
          'argv':[binding['python'],'-I','-B','verify/check.py','--tests','tests.json',
                  '--solution','solution.py','--reward-file','verifier/reward.txt',
                  '--evidence-file','verifier/evidence.json'],
          'inputs':[{'path':'verify/check.py','from':str(ROOT/'data/datasets/livecodebench/verify/check.py')},
                    {'path':'data/__init__.py','content':''},
                    {'path':'data/scaffolds/__init__.py','content':''},
                    {'path':'data/scaffolds/scorer_input.py','from':str(ROOT/'data/scaffolds/scorer_input.py')},
                    *[{'path':n,'from':str(sources/n)} for n in ('tests.json','testing_util.py','pass_k_utils.py')]],
          'reward_file':'/verifier/reward.txt','evidence_file':'/verifier/evidence.json',
          'result_contract':{'version':1,'score_range':[0,1],'require_exit_zero':True}}
    bind(spec, 'authored/lcb', ['tests.json','testing_util.py','pass_k_utils.py'])
    monkeypatch.setenv('HARNESSGRAD_WORK_ROOT', str(tmp_path))
    verdict=_verify(work,spec,'',sandbox=True,timeout_s=45,env={'kind':'files'},task_id='authored/lcb')
    assert verdict.get('score') == expected, verdict
    assert verdict.get('evidence',{}).get('scorer_input',{}).get('verified') is True
    assert not marker.exists()
