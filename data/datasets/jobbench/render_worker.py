#!/usr/bin/env python3
"""Bound preparation worker; original fetch_snapshot/generate execute unchanged."""
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]
from harnessgrad import dataset_runtime
from renderer_identity import admit_source, digest, platform_identity


def main():
    request = json.load(sys.stdin)
    binding = request['binding']
    selected = dataset_runtime.admit_binding(binding, 'service', python=sys.executable)
    if Path(sys.prefix).resolve() != Path(selected['path']).resolve():
        raise ValueError('JobBench renderer did not start in the bound runtime')
    root = Path(request['root'])
    identity = admit_source(root)
    if (identity != request['renderer'] or digest(__file__) != request['worker_sha256']
            or platform_identity() != request['platform_sources']):
        raise ValueError('JobBench renderer launch identity changed')
    # Never execute generated/unlisted bytecode from the mutable source tree.
    sys.dont_write_bytecode = True
    sys.pycache_prefix = request['empty_bytecode_path']
    package_path = root / 'harbor/jobbench_harbor'
    spec = importlib.util.spec_from_file_location('jobbench_harbor', package_path / '__init__.py',
        submodule_search_locations=[str(package_path)])
    package = importlib.util.module_from_spec(spec)
    sys.modules['jobbench_harbor'] = package
    spec.loader.exec_module(package)
    from jobbench_harbor.hf_source import fetch_snapshot
    from jobbench_harbor.prepare import generate
    snapshot = fetch_snapshot(root / 'harbor/.cache/huggingface',
        repo_id=request['repo_id'], revision=request['revision'])
    if snapshot.revision != request['revision']:
        raise ValueError('JobBench HF source resolved to a different revision')
    manifest = generate(snapshot.path, root / 'harbor', repo_id=request['repo_id'],
        revision=request['revision'], judge_path=root / 'eval/judge.py', remote_files=snapshot.remote_files)
    if admit_source(root) != identity or platform_identity() != request['platform_sources']:
        raise ValueError('JobBench renderer source changed during generation')
    dataset_runtime.admit_binding(binding, 'service', python=sys.executable)
    print(json.dumps({'manifest': manifest, 'runtime_id': binding['runtime_id'],
        'huggingface_hub_version': importlib.metadata.version('huggingface-hub')}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Private raw files and provider bodies never become platform diagnostics.
        print(type(exc).__name__, file=sys.stderr)
        raise SystemExit(3)
