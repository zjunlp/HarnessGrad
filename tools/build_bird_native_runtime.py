#!/usr/bin/env python3
"""Capture supported sealed native scorer userlands, without running a dataset.

The manifest is a reviewed admission pin, not auto-updated during verification.
Collection resolution is evidence only; execution sees only the captured files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--dataset', choices=['bird_minidev', 'jericho', 'alfworld',
                                             'scienceworld', 'chessbench_puzzles'],
                        default='bird_minidev')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    raw = args.out / 'commands'
    raw.mkdir(exist_ok=True)
    commands = []

    def capture(argv):
        result = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                                env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        name = f'{len(commands):04d}'
        (raw / f'{name}.stdout.txt').write_text(result.stdout)
        (raw / f'{name}.stderr.txt').write_text(result.stderr)
        commands.append({'argv': argv, 'returncode': result.returncode, 'raw': f'commands/{name}'})
        (args.out / 'invocations.json').write_text(json.dumps(commands, indent=2) + '\n')
        if result.returncode:
            raise RuntimeError(commands[-1])
        return result.stdout, name

    exe = Path(sys.executable).resolve()
    stdlib = Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}'
    entries = {}
    queue = []
    elf = {}

    def add(path, site, source_override=None):
        path = Path(os.path.normpath(path))
        if str(path) in entries:
            return
        source = Path(source_override or path).resolve(strict=True)
        if not source.is_file():
            raise ValueError(source)
        data = source.read_bytes()
        entries[str(path)] = {'source': (str(source.relative_to(Path.cwd()))
                                                       if source.is_relative_to(Path.cwd()) else str(source)), 'sha256': hashlib.sha256(data).hexdigest(),
                              'size': len(data), 'entry': site}
        if data.startswith(b'\x7fELF'):
            queue.append(path)

    def site(path, needle):
        lines = Path(path).read_text().splitlines()
        return f'{path}:{next(i for i, line in enumerate(lines, 1) if needle in line)}'

    adapter = f'data/datasets/{args.dataset}.py'
    check = ('data/scaffolds/qa/verify/check.py' if args.dataset == 'chessbench_puzzles'
             else f'data/datasets/{args.dataset}/verify/check.py')
    entry = (site('data/scaffolds/qa/base.py', '"python3", f"{CHECK_DIR}/{CHECK_NAME}"')
             if args.dataset == 'chessbench_puzzles' else site(adapter, '"python3", "check.py"'))
    add(exe, entry + ' (interpreter selected by native_runtime binding)')
    add(exe.parent / 'python3', entry + ' (closed child PATH alias)')
    # Entire stdlib source/extension superset, excluding ambient site packages and caches.
    for directory, dirs, files in os.walk(stdlib):
        dirs[:] = sorted(d for d in dirs if d not in {'site-packages', '__pycache__'})
        for name in sorted(files):
            if name.endswith(('.py', '.so')):
                add(Path(directory) / name,
                    entry + ' (stdlib source/extension superset)')
    trusted = {
        '/scorer/check.py': check,
        '/scorer/data/__init__.py': 'data/__init__.py',
        '/scorer/data/scaffolds/__init__.py': 'data/scaffolds/__init__.py',
        '/scorer/data/scaffolds/scorer_input.py': 'data/scaffolds/scorer_input.py',
    }
    helper = {'jericho': 'jer_env', 'alfworld': 'gym_env', 'scienceworld': 'scw_env'}.get(args.dataset)
    if helper:
        trusted[f'/scorer/{helper}.py'] = f'data/datasets/{args.dataset}/lib/{helper}.py'
    if args.dataset == 'chessbench_puzzles':
        trusted['/scorer/data/datasets/chessbench_puzzles.py'] = adapter
        trusted['/scorer/data/datasets/__init__.py'] = 'data/datasets/__init__.py'
        for name in ('__init__', 'base', 'read', 'evidence_scorers', 'prediction'):
            source = f'data/scaffolds/qa/{name}.py'
            trusted[f'/scorer/{source}'] = source
    for target, source in trusted.items():
        add(target, source + ':1 (trusted scorer code, separate from actor files)', source)
    packages = stdlib / 'site-packages'
    if args.dataset in {'scienceworld', 'chessbench_puzzles'}:
        roots = (['scienceworld', 'py4j'] if args.dataset == 'scienceworld' else ['chess'])
        for package in roots:
            root = packages / package
            if not root.is_dir():
                raise ValueError(f'missing package root: {root}')
            package_entry = (site(f'data/datasets/scienceworld/lib/scw_env.py', 'from scienceworld import')
                             if args.dataset == 'scienceworld' else site(adapter, 'import chess'))
            for directory, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in {'__pycache__', 'tests', 'test'})
                for name in sorted(files):
                    path = Path(directory) / name
                    if path.suffix not in {'.pyc', '.pyo'}:
                        add(path, package_entry + f' (trusted {package} package/resources superset)')
        if args.dataset == 'scienceworld':
            # Preserve the JRE layout (including non-ELF modules/config), so the
            # child's $ORIGIN and java.home lookup never reach a task/host JRE.
            import importlib.util
            spec = importlib.util.spec_from_file_location('capture_scw_env',
                                                        'data/datasets/scienceworld/lib/scw_env.py')
            scw = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(scw)
            java_home = scw.staged_jre().resolve(strict=True)
            for path in sorted(java_home.rglob('*')):
                if path.is_file():
                    add('/opt/hg-jre/' + str(path.relative_to(java_home)),
                        site(f'data/datasets/scienceworld/lib/scw_env.py', "HG_SEALED_JAVA_HOME")
                        + ' (closed JRE tree)', path)
            from py4j.java_gateway import find_jar_path
            jar = Path(find_jar_path()).resolve(strict=True)
            add(jar, site(packages / 'py4j/java_gateway.py', 'jarpath = find_jar_path()'))
            java_identity, _ = capture([str(java_home / 'bin/java'), '-version'])
            # -version writes its identity on stderr, retained by capture.
            # glibc dlopens this at pthread_exit; it is absent from DT_NEEDED.
            add('/lib/x86_64-linux-gnu/libgcc_s.so.1',
                'glibc pthread_exit unwind provider (observed sealed JVM shutdown)')
    if args.dataset in {'jericho', 'alfworld'}:
        # Closed package/resources superset for the synchronous replay route.
        # Optional NLP/ML modules are not imported; their absent dependencies
        # cannot resolve through the empty namespace to the host.
        roots = ['jericho', 'numpy', 'mkl']
        if args.dataset == 'alfworld':
            roots += ['alfworld', 'textworld', 'fast_downward', 'mementos', 'networkx',
                      'prompt_toolkit', 'psutil', 'tatsu', 'termcolor', 'tqdm',
                      'wcwidth', 'zope', '_distutils_hack']
        package_entry = site(f'data/datasets/{args.dataset}/lib/{helper}.py',
                             'from jericho import FrotzEnv' if args.dataset == 'jericho'
                             else 'import textworld')
        for package in roots:
            root = packages / package
            if not root.is_dir():
                raise ValueError(f'missing package root: {root}')
            for directory, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in {'__pycache__', 'tests', 'test'})
                if package == 'textworld' and Path(directory) == root:
                    # Inform7 compilation/Git-Glulx tooling is not the selected
                    # synchronous .tw-pddl replay backend. Never expose its tools.
                    dirs[:] = [d for d in dirs if d != 'thirdparty']
                for name in sorted(files):
                    path = Path(directory) / name
                    if path.suffix not in {'.pyc', '.pyo'}:
                        add(path, package_entry + f' (trusted {package} package/resources superset)')
        # MKL dispatch uses dlopen beyond DT_NEEDED. Admit installed CPU
        # providers as a conservative superset, without changing NumPy builds.
        for pattern in ('libmkl*.so*', 'libiomp*.so*', 'libtbb*.so*'):
            for path in sorted((Path(sys.base_prefix) / 'lib').glob(pattern)):
                add(path, site(packages / 'numpy/_distributor_init.py', 'import mkl')
                    + ' (MKL/OpenMP dispatch provider superset)')
        # readline's native locale initializer requires the configured C.UTF-8
        # locale payload; without it this installed build dereferences NULL.
        # Capture the data rather than exposing ambient /usr or changing locale.
        locale_files = (sorted(Path('/usr/lib/locale/C.utf8').rglob('*'))
                        if args.dataset == 'alfworld' else [])
        for path in locale_files:
            if path.is_file():
                add(path, site(packages / 'textworld/agents/human.py', 'import readline')
                    + ' (configured C.UTF-8 locale for native readline initialization)')
    while queue:
        path = queue.pop(0)
        if str(path) in elf:
            continue
        source_path = entries[str(path)]['source']
        dynamic, readelf_id = capture(['/usr/bin/readelf', '-lW', '-dW', source_path])
        # JRE internal providers are resolved within the preserved tree. Collection
        # uses its native search dirs; runtime remains confined to the manifest.
        ldd_argv = ['/usr/bin/ldd', source_path]
        if args.dataset == 'scienceworld':
            ldd_argv = ['/usr/bin/env', 'LD_LIBRARY_PATH=' + str(java_home / 'lib')
                        + ':' + str(java_home / 'lib/server'), *ldd_argv]
        providers, ldd_id = capture(ldd_argv)
        if 'not found' in providers:
            raise ValueError(providers)
        graph = []
        for line in providers.splitlines():
            match = re.search(r'=> (/\S+)|^\s*(/\S+)', line)
            if match:
                dep = os.path.normpath(next(g for g in match.groups() if g))
                if args.dataset == 'scienceworld' and Path(dep).is_relative_to(java_home):
                    target = '/opt/hg-jre/' + str(Path(dep).relative_to(java_home))
                    add(target, f'ELF provider from {path}; commands/{ldd_id}.stdout.txt', dep)
                else:
                    target = dep
                    add(dep, f'ELF provider from {path}; commands/{ldd_id}.stdout.txt')
                graph.append(target)
        interp = re.search(r'Requesting program interpreter: ([^\]]+)', dynamic)
        if interp:
            add(interp[1], f'PT_INTERP from {path}; commands/{readelf_id}.stdout.txt')
            graph.append(interp[1])
        elf[str(path)] = {'providers': sorted(set(graph)), 'readelf': readelf_id, 'ldd': ldd_id}
    # The launcher's bytes are also admitted; its own loader is the trusted host TCB.
    bwrap = Path('/usr/bin/bwrap')
    bwrap_hash, _ = capture(['/usr/bin/sha256sum', str(bwrap)])
    identity, _ = capture([str(exe), '-I', '-S', '-c',
        'import sys,sysconfig,sqlite3,json,platform; print(json.dumps(dict(version=sys.version, '
        'executable=sys.executable,soabi=sysconfig.get_config_var("SOABI"),sqlite=sqlite3.sqlite_version,'
        'kernel=platform.release(),machine=platform.machine()),sort_keys=True))'])
    manifest = {'schema': 1, 'dataset': args.dataset, 'interpreter': str(exe),
                'loader': '/lib64/ld-linux-x86-64.so.2',
                'library_path': sorted({str(Path(p).parent) for p in entries
                                        if entries[p]['entry'].startswith(('ELF', 'PT_INTERP'))}),
                'environment': {'PATH': str(exe.parent), 'HOME': '/tmp', 'TMPDIR': '/tmp',
                                'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
                'python_flags': ['-I', '-S', '-B'],
                'identity': json.loads(identity),
                'launcher': {'path': str(bwrap), 'sha256': bwrap_hash.split()[0]},
                'files': dict(sorted(entries.items()))}
    if helper or args.dataset == 'chessbench_puzzles':
        manifest['python_path'] = ['/scorer', str(packages)]
        manifest['environment']['LD_LIBRARY_PATH'] = ':'.join(manifest['library_path'])
    if args.dataset == 'scienceworld':
        manifest['environment']['HG_SEALED_JAVA_HOME'] = '/opt/hg-jre'
        manifest['environment']['JAVA_HOME'] = '/opt/hg-jre'
        # This installed Py4J defaults java_path="java"; JAVA_HOME is consulted
        # only when java_path is false. Both resolutions must name sealed bytes.
        manifest['environment']['PATH'] = '/opt/hg-jre/bin:' + str(exe.parent)
        manifest['identity']['java_release'] = (java_home / 'release').read_text()
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    (args.out / 'manifest.json').write_bytes(args.manifest.read_bytes())
    manifest_hash, _ = capture(['/usr/bin/sha256sum', str(args.manifest)])
    for start in range(0, len(entries), 100):
        paths = list(entries)[start:start + 100]
        hashes, _ = capture(['/usr/bin/sha256sum', *[entries[p]['source'] for p in paths]])
        for target, line in zip(paths, hashes.splitlines(), strict=True):
            digest, source = line.split('  ', 1)
            assert entries[target]['sha256'] == digest
            assert entries[target]['source'] == source
    args.store.mkdir(parents=True, exist_ok=True)
    payload = args.store / (manifest_hash.split()[0] + '.tar.gz')
    with tarfile.open(payload, 'w:gz', compresslevel=1) as tar:
        for path, entry in manifest['files'].items():
            tar.add(entry['source'], arcname=path.lstrip('/'), recursive=False)
    capture(['/usr/bin/sha256sum', str(payload)])
    (args.out / 'elf-graph.json').write_text(json.dumps(elf, indent=2, sort_keys=True) + '\n')
    (args.out / 'invocations.json').write_text(json.dumps(commands, indent=2) + '\n')
    summary = {'manifest_sha256': manifest_hash.split()[0], 'files': len(entries),
               'elf': len(elf), 'bytes': sum(e['size'] for e in entries.values()),
               'payload': str(payload), 'identity': manifest['identity']}
    (args.out / 'build-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
