"""Managed candidates cannot enter the validator's legacy smoke interpreter."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from eval.containers import sandbox
from harnessgrad import python_harness_runtime
from tools import validate_harness as validator


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def managed(tmp_path):
    root=tmp_path/'candidate';root.mkdir()
    marker=tmp_path/'host-executed'
    (root/'harness.json').write_text(json.dumps({'name':'managed-smoke-control','version':'1',
        'entrypoint':'agent.py','runtime':{'kind':'python',
        'python':f'{sys.version_info.major}.{sys.version_info.minor}','requirements':'requirements.lock'}}))
    (root/'requirements.lock').write_text('# no third-party packages\n')
    (root/'agent.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n')
    return root,marker


def test_managed_static_validation_remains_available_but_legacy_smoke_never_runs(managed, monkeypatch):
    root,marker=managed
    launch=Mock(side_effect=AssertionError('managed source entered legacy smoke'))
    monkeypatch.setattr(validator,'_smoke',launch)
    assert validator.validate(root,smoke=False)==[]
    problems=validator.validate(root,smoke=True)
    assert len(problems)==1 and 'managed Python smoke is not supported' in problems[0]
    assert 'driver.py --harness-path <dir> --dataset <dataset> --rounds 0' in problems[0]
    launch.assert_not_called()
    assert not marker.exists()


def test_managed_callable_smoke_refuses_before_allocation_runtime_or_process(managed, monkeypatch):
    root,marker=managed
    touched=Mock(side_effect=AssertionError('managed refusal allocated or executed'))
    monkeypatch.setattr(validator,'_smoke_work_root',touched)
    monkeypatch.setattr(validator,'_smoke_copy',touched)
    monkeypatch.setattr(sandbox,'run',touched)
    monkeypatch.setattr(python_harness_runtime,'prepare',touched)
    work=root.parent/'work'
    problems=validator.smoke(root,platform=ROOT,work_root=work,io_budget=touched)
    assert len(problems)==1 and problems[0]['code']=='smoke-unsupported'
    assert 'sandbox enabled' in problems[0]['detail']
    touched.assert_not_called()
    assert not marker.exists() and not work.exists() and not (root/'.state').exists()


def test_managed_smoke_cli_refuses_with_the_canonical_sandbox_route(managed):
    root,marker=managed
    result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/validate_harness.py'),
        str(root),'--smoke'],capture_output=True,text=True,timeout=20,cwd=ROOT)
    assert result.returncode==1,result.stdout+result.stderr
    assert 'managed Python smoke is not supported' in result.stdout
    assert '--rounds 0' in result.stdout and 'sandbox enabled' in result.stdout
    assert 'smoke run produced an answer' not in result.stdout
    assert not marker.exists() and not (root/'.state').exists()
