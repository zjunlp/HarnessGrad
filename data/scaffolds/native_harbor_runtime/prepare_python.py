"""Explicit Harbor component preparation/read-only checks, never task execution."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from .python_runtime import PROFILE, resolve


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check', 'probe'))
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--python', help='base interpreter for explicit preparation only')
    args = parser.parse_args(argv)
    from harnessgrad import dataset_runtime
    if args.python and args.action != 'prepare':
        parser.error('--python requires prepare')
    try:
        if args.action == 'prepare':
            dataset_runtime.prepare_execution(PROFILE, python=args.python, root=args.runtime_root)
        result = dataset_runtime.check_execution(PROFILE, root=args.runtime_root)
        if not result['present']:
            print(json.dumps(result)); return 2
        binding = resolve(root=args.runtime_root)
        if args.action == 'probe':
            code = ("import json,sys; sys.path.insert(0,sys.argv[1]); "
                    "from data.scaffolds.native_harbor_runtime.python_runtime import admit; "
                    "entry=json.loads(sys.argv[2]); admit(entry,python=sys.executable); "
                    "from harbor.models.trial.config import TrialConfig; from harbor.trial.trial import Trial; "
                    "admit(entry,python=sys.executable); "
                    "print(json.dumps({'version':list(sys.version_info[:2]),'python':sys.executable,"
                    "'modules':['harbor.models.trial.config','harbor.trial.trial'],'task_run':False}))")
            environment = dataset_runtime._child_environment()
            environment.update(PYTHON_DOTENV_DISABLED='1',LITELLM_LOCAL_MODEL_COST_MAP='True',HF_HUB_OFFLINE='1')
            with tempfile.TemporaryDirectory(prefix='hg-native-harbor-import-') as temporary:
                for key in ('HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME','TMPDIR'):
                    directory=Path(temporary)/key.lower(); directory.mkdir()
                    environment[key]=str(directory)
                probe = subprocess.run([binding['python'],'-I','-B','-c',code,
                    str(Path(__file__).resolve().parents[3]),json.dumps({'harbor_runtime':binding,'harbor_python':binding['python']})],
                    env=environment,cwd=temporary,capture_output=True,text=True,check=True,timeout=60)
            result['probe'] = json.loads(probe.stdout)
        dataset_runtime.admit_binding(binding,'verifier',python=binding['python'])
        result['binding'] = binding
        print(json.dumps(result,sort_keys=True)); return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print(json.dumps({'present':False,'error_type':type(exc).__name__,'detail':str(exc)})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
