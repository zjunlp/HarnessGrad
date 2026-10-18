#!/usr/bin/env python3
"""Prepare pinned Harbor pair inputs and one explicit offline runtime at a time."""
from __future__ import annotations
import argparse
import importlib.util
import json
from itertools import combinations
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
from protocol import (SOURCE_REVISION, HARBOR_REVISION, HARBOR_ADAPTER_SHA256,
                      HARBOR_SCORER_SHA256, sha, read_checked, adapt_scorer, prepare_recipe, image_recipe, recipe)


def command(*argv):
    return subprocess.check_output(argv, text=True).strip()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')


def prepare(root, source, harbor):
    if command('git', '-C', str(source), 'rev-parse', 'HEAD') != SOURCE_REVISION:
        raise ValueError('wrong CooperBench checkout revision')
    if command('git', '-C', str(harbor), 'rev-parse', 'HEAD') != HARBOR_REVISION:
        raise ValueError('wrong Harbor checkout revision')
    for tree in (source, harbor):
        if command('git', '-C', str(tree), 'status', '--porcelain', '--untracked-files=all', '--', 'dataset', 'adapters/cooperbench'):
            raise ValueError('source tree has modified task inputs')
    adapter = harbor / 'adapters/cooperbench/src/cooperbench/adapter.py'
    read_checked(adapter, HARBOR_ADAPTER_SHA256)
    template = adapter.parent / 'task-template/tests/test.sh'
    read_checked(template, HARBOR_SCORER_SHA256)
    spec = importlib.util.spec_from_file_location('cooperbench_harbor_preparation', adapter)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    prepared = root / 'prepared-v1'
    if (prepared / 'manifest.json').exists():
        raise FileExistsError('prepared manifest exists; choose a fresh root to preserve provenance')
    rows = []
    # Use Harbor's own fixes without importing or running any solver sidecar.
    adapter_object = module.CooperBenchAdapter(prepared / 'unused')
    for group in sorted((source / 'dataset').glob('*/task*')):
        features = sorted(group.glob('feature[0-9]*'), key=lambda p: int(p.name[7:]))
        if len(features) < 2:
            continue
        base = re.search(r'git checkout ([0-9a-f]{40})', (group / 'Dockerfile').read_text())
        if not base:
            raise ValueError(f'no exact base commit in {group}')
        repo = module.REPO_FOR_GITHUB_DIR[group.parent.name]
        group_id = group.parent.name + '/' + group.name
        for a, b in combinations(features, 2):
            tid = 'cooperbench/' + group_id + '/f' + a.name[7:] + '-' + b.name[7:]
            folder = prepared / 'pairs' / tid.removeprefix('cooperbench/')
            paths = module.HarborTaskPaths(folder)
            for i, feature in enumerate((a, b), 1):
                (paths.tests_dir / f'tests{i}.patch').write_bytes((feature / 'tests.patch').read_bytes())
            (paths.tests_dir / 'test.sh').write_bytes(template.read_bytes())
            (paths.solution_dir / 'combined.patch').write_bytes((group / 'combined.patch').read_bytes())
            pair = type('Pair', (), {'repo': repo, 'task_id': group.name[4:]})()
            adapter_object._apply_upstream_fixes(pair, paths)
            # Record exactly the transformed source supplied to the scorer.
            scorer = adapt_scorer((paths.tests_dir / 'test.sh').read_text())
            (folder / 'test.sh').write_text(scorer)
            public = [{'feature_id': f.name[7:], 'description': (f / 'feature.md').read_text()} for f in (a, b)]
            (folder / 'public.json').write_text(json.dumps(public, sort_keys=True))
            private = {str(p.relative_to(folder)): sha(p.read_bytes())
                       for p in sorted(folder.rglob('*')) if p.is_file()}
            rows.append({'task_id': tid, 'group': group_id, 'base_commit': base.group(1),
                         'path': str(folder.relative_to(prepared)), 'files_sha256': private,
                         'image_tag': module.get_image_name(group.parent.name, group.name[4:]),
                         'runner_sha256': sha((group / 'runner.sh').read_bytes())})
    (prepared / 'unused').rmdir()
    manifest = {'source_revision': SOURCE_REVISION, 'harbor_revision': HARBOR_REVISION,
                'protocol': 'harbor-merged-pair-naive-union', 'tasks': rows}
    write_json(prepared / 'manifest.json', manifest)
    print(json.dumps({'tasks': len(rows), 'groups': len({r['group'] for r in rows}),
                      'manifest_sha256': sha((prepared / 'manifest.json').read_bytes())}))


def prepare_image(root, source, group, pull, build_network="bridge", source_image=None, runtime_python_image=None):
    manifest = json.loads((root / 'prepared-v1/manifest.json').read_text())
    row = next(r for r in manifest['tasks'] if r['group'] == group)
    runner = read_checked(source / 'dataset' / group / 'runner.sh', row['runner_sha256'])
    _, adapted = prepare_recipe(runner, group)
    image = source_image or row['image_tag']
    if pull:
        subprocess.run(['docker', 'pull', image], check=True)
    imported = json.loads(command('docker', 'image', 'inspect', image))[0]
    image_id = imported['Id']
    python = command('docker', 'run', '--rm', '--network=none', '--entrypoint', 'sh',
                     image_id, '-c', 'command -v python3')
    inspect_code = """import json, subprocess, hashlib, os, sys, tarfile, shutil
# The base interpreter may be upgraded by the preparation recipe.

def read(argv):
    p = subprocess.run(argv, text=True, capture_output=True)
    return p.stdout if p.returncode == 0 else None
workspace_bytes = 0
workspace_inodes = 0
for parent, dirs, files in os.walk('/workspace'):
    for name in dirs + files:
        info = os.lstat(os.path.join(parent, name))
        workspace_bytes += info.st_size
        workspace_inodes += 1
print(json.dumps({'base_commit': read(['git','-C','/workspace/repo','rev-parse','HEAD']).strip(),
 'pip_freeze': read([sys.executable,'-m','pip','freeze','--all']) or '',
 'python_version': sys.version, 'python_prefix': sys.prefix,
 'python_executable': sys.executable, 'python_library_path': os.environ.get('LD_LIBRARY_PATH'),
 'toolchain_versions': {tool: read([tool, 'version' if tool == 'go' else '--version']) for tool in ('git', 'go', 'node', 'pnpm', 'cargo', 'rustc') if shutil.which(tool)},
 'workspace_bytes': workspace_bytes, 'workspace_inodes': workspace_inodes,
 'runner_sha256': hashlib.sha256(open('/usr/local/bin/runner.sh','rb').read()).hexdigest()}))
"""
    identity = json.loads(command('docker', 'run', '--rm', '--network=none', '--entrypoint',
                                  python, image_id, '-c', inspect_code))
    if identity['base_commit'] != row['base_commit'] or identity['runner_sha256'] != row['runner_sha256']:
        raise ValueError('official image base/runner differs from the pinned task source; rebuild the pinned source Dockerfile and import that image explicitly')
    helper_image_id = None
    if runtime_python_image:
        helper_image_id = json.loads(command('docker', 'image', 'inspect', runtime_python_image))[0]['Id']
    runtimes = root / 'runtimes'
    target = runtimes / group.replace('/', '-')
    target.mkdir(parents=True, exist_ok=True)
    if (target / 'runtime.json').exists():
        raise FileExistsError('runtime already prepared; preserve it and use a fresh root for changes')
    (target / 'runner.sh').write_text(adapted)
    dockerfile, preparation, adapted = image_recipe(image_id, runner, group, runtime_python_image=helper_image_id)
    (target / 'prepare-dependencies.sh').write_text(preparation)
    (target / 'Dockerfile').write_text(dockerfile)
    tag = 'hg-cooperbench-' + group.replace('/', '-').replace('_', '-') + '-runtime'
    network = build_network
    subprocess.run(['env', 'DOCKER_BUILDKIT=0', 'docker', 'build', '--network=' + network, '--pull=false',
                    '--build-arg', 'HTTPS_PROXY', '--build-arg', 'HTTP_PROXY', '--build-arg', 'NO_PROXY',
                    '--force-rm', '--memory=8g', '--cpu-quota=200000', '-t', tag, str(target)], check=True)
    digest = json.loads(command('docker', 'image', 'inspect', tag))[0]['Id']
    if helper_image_id:
        python = '/opt/hg-python/bin/python3'
    command('docker', 'run', '--rm', '--network=none', '--entrypoint', python, digest,
            '-c', "import tarfile; assert hasattr(tarfile, 'data_filter'), 'runtime Python needs tarfile.data_filter'")
    identity = json.loads(command('docker', 'run', '--rm', '--network=none', '--entrypoint',
                                  python, digest, '-c', inspect_code))
    (target / 'requirements.freeze.txt').write_text(identity['pip_freeze'])
    # Keep bounded headroom for two patch/test passes and native build output.
    workspace_bytes = max(2 << 30, ((3 * identity['workspace_bytes'] + (1 << 30) + 4095) // 4096) * 4096)
    inodes = max(200000, identity['workspace_inodes'] * 3 + 16384)
    workspace_limit_gib = recipe(group).get('workspace_limit_gib', 16)
    if workspace_bytes > workspace_limit_gib << 30 or inodes > 2000000:
        raise ValueError(f'measured seed {identity["workspace_bytes"]} bytes/{identity["workspace_inodes"]} inodes requires '
                         f'{workspace_bytes} workspace bytes/{inodes} inodes, exceeding the reviewed '
                         f'{workspace_limit_gib} GiB/2M inode envelope')
    runtime = {'group': group, 'image': digest, 'source_image': image_id, 'python': python,
               'source_repo_digests': imported.get('RepoDigests', []), 'runtime_python_image': helper_image_id, 'identity': identity,
               'recipe': recipe(group), 'runner_sha256': sha(adapted.encode()),
               'dockerfile_sha256': sha(dockerfile.encode()),
               'dependencies_sha256': sha(identity['pip_freeze'].encode()),
               'preparation_sha256': sha(preparation.encode()),
               'resources': {'workspace_bytes': workspace_bytes, 'workspace_inodes': inodes,
                             'memory_mb': max(4096, workspace_bytes // (1 << 20) + 2048)},
               'validation': 'prepared; real oracle/noop controls are still required for this image'}
    write_json(target / 'runtime.json', runtime)
    print(json.dumps(runtime))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--harbor-root', type=Path)
    parser.add_argument('--image-group', help='Prepare only this explicit repository/task group')
    parser.add_argument('--source-image', help='Explicit local image ID rebuilt from the pinned source Dockerfile if the published tag is stale')
    parser.add_argument('--runtime-python-image', help='Explicit local compatible Python image for the isolated helper when the task image lacks safe tar extraction; copied to /opt/hg-python without replacing the official toolchain')
    parser.add_argument('--build-network', choices=['bridge', 'host', 'none'], default='bridge',
                        help='Preparation only; host can reach a loopback download proxy. Measurement always has no network.')
    parser.add_argument('--pull', action='store_true', help='Download only the selected official image')
    args = parser.parse_args()
    if args.image_group:
        prepare_image(args.root, args.source_root, args.image_group, args.pull, args.build_network, args.source_image, args.runtime_python_image)
    elif args.harbor_root:
        prepare(args.root, args.source_root, args.harbor_root)
    else:
        parser.error('--harbor-root is required for data preparation')


if __name__ == '__main__':
    main()
