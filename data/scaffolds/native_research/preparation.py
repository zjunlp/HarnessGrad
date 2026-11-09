"""Explicit acquisition/conversion controller; third-party imports stay in workers."""
import json
import os
from pathlib import Path
import subprocess
from harnessgrad import dataset_runtime


def prepare_component(name, output, config, *, fetch=False, judge_model=None):
    profile = name if name in ('harvey_lab', 'widesearch') else 'native_qa'
    runtime = dataset_runtime.resolve_execution(profile)
    selected = dataset_runtime.bind(runtime, 'service')
    worker = Path(__file__).with_name('prepare_worker.py')
    environment = dataset_runtime._child_environment(allow_downloads=fetch)
    if fetch:
        for key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HF_ENDPOINT'):
            if key in os.environ:
                environment[key] = os.environ[key]
    request = dict(name=name, output=str(output), config=config, fetch=fetch,
                   judge_model=judge_model, runtime=selected)
    proc = subprocess.run([runtime['python'], '-I', '-B', str(worker)],
        input=json.dumps(request), text=True, capture_output=True, env=environment)
    if proc.returncode:
        raise RuntimeError('native preparation component failed; diagnostics may contain private source data')
    return json.loads((Path(output) / 'manifest.json').read_bytes())
