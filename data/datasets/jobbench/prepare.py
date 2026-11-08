#!/usr/bin/env python3
"""Prepare fixed native source without running an actor or judge.

For JobBench, --render explicitly invokes the pinned author's renderer after
source preparation. The renderer requires its declared Python dependencies and
access to the pinned HF source. It never runs an actor or judge.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

NAME = 'jobbench'
SOURCE = json.loads((Path(__file__).parent / 'SOURCE.json').read_text())
RENDERER_SOURCE = 'https://github.com/Job-Bench/job-bench-eval'
RENDERER_REVISION = '660b33a86ed032559df0b2fdc1ad38cf7ceb83f3'


def extract(archive, root):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    with tarfile.open(archive, 'r:*') as native:
        for entry in native.getmembers():
            parts = Path(entry.name).parts
            if not parts or '..' in parts or Path(entry.name).is_absolute():
                raise ValueError('unsafe native archive path')
            if entry.issym() or entry.islnk() or not (entry.isfile() or entry.isdir()):
                raise ValueError('native source archive contains non-plain entry')
            relative = Path(*parts[1:])
            if not relative.parts or entry.isdir():
                continue
            output = root / relative
            if output.is_symlink() or not output.resolve().is_relative_to(root):
                raise ValueError('native extraction path leaves source root')
            data = native.extractfile(entry).read()
            if output.exists() and output.read_bytes() != data:
                raise ValueError(f'pre-existing native file differs: {output}')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            output.chmod(0o644 | (entry.mode & 0o111))
            files[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    if not files:
        raise ValueError('empty native source archive')
    return files


def render(root):
    from harnessgrad import dataset_runtime
    from renderer_identity import admit_source, digest, platform_identity
    binding = dataset_runtime.bind(dataset_runtime.resolve_execution('jobbench_renderer'), 'service')
    identity = admit_source(root)
    worker = Path(__file__).with_name('render_worker.py')
    worker_sha256 = digest(worker)
    controller_sha256 = digest(__file__)
    platform_sources = platform_identity()
    attempt = Path(tempfile.mkdtemp(prefix='renderer-attempt-', dir=root))
    empty_bytecode = attempt / 'empty-bytecode'
    empty_bytecode.mkdir()
    request = {'root': str(root), 'binding': binding, 'renderer': identity,
        'worker_sha256': worker_sha256, 'platform_sources': platform_sources, 'empty_bytecode_path': str(empty_bytecode),
        'repo_id': 'JobBench/job-bench', 'revision': SOURCE['revision']}
    environment = dataset_runtime._child_environment(allow_downloads=True)
    environment.update(HF_HOME=str(attempt / 'hf-home'), HF_HUB_DISABLE_TELEMETRY='1',
        PYTHON_DOTENV_DISABLED='1')
    for key in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'HF_ENDPOINT', 'HF_HUB_OFFLINE', 'HF_HUB_DISABLE_XET'):
        if key in os.environ:
            environment[key] = os.environ[key]
    selected = dataset_runtime.admit_binding(binding, 'service')
    if (admit_source(root) != identity or digest(worker) != worker_sha256
            or platform_identity() != platform_sources):
        raise ValueError('JobBench renderer changed before launch')
    result = subprocess.run([selected['python'], '-I', '-B', str(worker)],
        input=json.dumps(request), text=True, capture_output=True, env=environment)
    dataset_runtime.admit_binding(binding, 'service', python=selected['python'])
    if (admit_source(root) != identity or digest(worker) != worker_sha256
            or digest(__file__) != controller_sha256 or platform_identity() != platform_sources):
        raise ValueError('JobBench preparation source changed during execution')
    if result.returncode:
        (attempt / 'worker.stderr').write_text(result.stderr)
        raise RuntimeError('JobBench renderer failed; no successful preparation receipt was published')
    output = json.loads(result.stdout)
    if output['runtime_id'] != binding['runtime_id']:
        raise ValueError('JobBench renderer returned a different runtime identity')
    receipt = {'schema': 'harnessgrad.jobbench-renderer-preparation.v1', 'binding': binding,
        'renderer': identity, 'platform_sources': platform_sources, 'worker_sha256': worker_sha256, 'controller_sha256': controller_sha256,
        'source_revision': SOURCE['revision'], 'huggingface_hub_version': output['huggingface_hub_version'],
        'generated_fingerprint': output['manifest']['fingerprint']}
    (attempt / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--render', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-runtime', action='store_true')
    mode.add_argument('--check-runtime', action='store_true')
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--python')
    args = parser.parse_args(argv)
    from harnessgrad import dataset_runtime
    if args.runtime_root is not None:
        os.environ['HG_DATASET_RUNTIME_ROOT'] = str(args.runtime_root.resolve())
    if args.prepare_runtime or args.check_runtime:
        if args.root or args.archive or args.render or args.check_runtime and args.python:
            parser.error('runtime preparation/check is a separate action')
        if args.prepare_runtime:
            dataset_runtime.prepare_execution('jobbench_renderer', root=args.runtime_root, python=args.python)
        status = dataset_runtime.check_execution('jobbench_renderer', root=args.runtime_root)
        print(json.dumps(status, sort_keys=True))
        return 0 if status['present'] else 1
    if args.root is None or args.python:
        parser.error('--root is required; --python is only used with --prepare-runtime')
    if args.render:
        # Missing configuration must fail before downloading or writing source.
        dataset_runtime.resolve_execution('jobbench_renderer')
    repo, revision = RENDERER_SOURCE, RENDERER_REVISION
    root = args.root.resolve()
    archive = args.archive or root / '_src' / f'{NAME}-{revision}.tar.gz'
    if not archive.is_file():
        repo_path = repo.removeprefix('https://github.com/').removesuffix('.git')
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f'https://codeload.github.com/{repo_path}/tar.gz/{revision}', timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=archive.parent, delete=False) as temp:
                pending = Path(temp.name)
                try:
                    while chunk := response.read(1 << 20):
                        temp.write(chunk)
                    temp.close()
                    pending.replace(archive)
                finally:
                    pending.unlink(missing_ok=True)
    files = extract(archive, root)
    manifest = {'dataset': NAME, 'source': repo, 'revision': revision,
                'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'files': files}
    (root / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    receipt = render(root) if args.render else None
    print(json.dumps({'prepared_source': True, 'root': str(root), 'files': len(files),
        'renderer_runtime_id': receipt['binding']['runtime_id'] if receipt else None,
        'execution_ready': False, 'detail': 'Native source preparation is not execution admission.'}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('JobBench preparation failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
