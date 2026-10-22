"""Read installed/cached runtime identities only; never import the dataset adapter."""
import hashlib
import importlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import zipfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def file_record(path):
    p = Path(path)
    return {'path': str(p), 'resolved': str(p.resolve()), 'bytes': p.stat().st_size,
            'sha256': digest(p)}


def command(argv):
    p = subprocess.run(argv, capture_output=True, text=True)
    return {'argv': argv, 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}


report = {'python': {'implementation': platform.python_implementation(),
                    'version': sys.version, 'executable': file_record(sys.executable),
                    'soabi': sysconfig.get_config_var('SOABI'),
                    'cache_tag': sys.implementation.cache_tag,
                    'machine': platform.machine(), 'platform': platform.platform()},
          'environment': {k: os.environ.get(k) for k in
                          ('TMPDIR', 'OPENPYXL_LXML', 'OPENPYXL_DEFUSEDXML',
                           'HG_SPREADSHEETBENCH_DATA', 'SAL_USE_VCLPLUGIN', 'DISPLAY')},
          'distributions': {}, 'modules': {}, 'commands': [], 'source': []}
before = set(sys.modules)
import openpyxl
import openpyxl.xml
import openpyxl.compat.numbers
loaded = set(sys.modules) - before
report['openpyxl_flags'] = {'LXML': openpyxl.xml.LXML,
                           'DEFUSEDXML': openpyxl.xml.DEFUSEDXML,
                           'NUMPY': openpyxl.compat.numbers.NUMPY}
for name in sorted(loaded):
    p = getattr(sys.modules[name], '__file__', None)
    if p and 'site-packages/' in p and Path(p).is_file():
        report['modules'][name] = file_record(p)
# et_xmlfile is declared even when lxml replaces its active branch.
for name in ('et_xmlfile', 'uno'):
    try:
        m = importlib.import_module(name)
        report['modules'][name] = file_record(m.__file__)
    except ImportError as exc:
        report['modules'][name] = {'state': 'ABSENT on this interpreter sys.path',
                                   'error': str(exc)}
report['sys_path'] = sys.path
for name in ('openpyxl', 'et-xmlfile', 'numpy', 'lxml', 'defusedxml', 'Pillow',
             'mkl-service', 'cffi', 'pycparser', 'six', 'setuptools'):
    try:
        d = metadata.distribution(name)
        meta_file = Path(d._path) / 'METADATA'
        if not meta_file.is_file():
            meta_file = Path(d._path) / 'PKG-INFO'
        report['distributions'][name] = {
            'version': d.version, 'metadata_path': str(d._path),
            'requires_dist': d.requires or [],
            'metadata': file_record(meta_file)}
    except metadata.PackageNotFoundError:
        report['distributions'][name] = {'state': 'ABSENT on this interpreter metadata path'}

prefix = Path(sys.prefix)
conda_meta = prefix / 'conda-meta'
records = {}
for p in sorted(conda_meta.glob('*.json')):
    d = json.loads(p.read_text())
    records[d['name']] = (p, d)
seeds = {'python', 'openpyxl', 'et_xmlfile', 'numpy', 'numpy-base', 'lxml', 'defusedxml',
         'pillow', 'mkl-service', 'cffi', 'pycparser', 'six', 'setuptools'}
closure = set()
pending = list(seeds)
while pending:
    name = pending.pop()
    if name in closure or name not in records:
        continue
    closure.add(name)
    pending.extend(dep.split()[0] for dep in records[name][1].get('depends', []))
report['conda_closure'] = {}
for name in sorted(closure):
    p, d = records[name]
    report['conda_closure'][name] = {
        k: d.get(k) for k in ('name', 'version', 'build', 'subdir', 'fn', 'sha256', 'depends')}
    report['conda_closure'][name]['metadata'] = file_record(p)

scopes = [ROOT / 'tests/fixtures', prefix / 'lib/python3.12/site-packages',
          Path.home() / '.local/lib/python3.12/site-packages', prefix / 'pkgs',
          Path.home() / '.cache/pip', Path('/usr/bin'),
          Path('/usr/lib/libreoffice'), Path('/opt'),
          Path('/tmp/hg-wwheelhouse-payloads-e5uywviu')]
scopes += sorted(Path('/tmp').glob('pip-ephem-wheel-cache-*'))
report['search'] = {'algorithm': 'os.walk each enumerated root without following directory symlinks; '
                    'match archive basenames against conda closure names and Python distribution names; '
                    'inspect ZIP .body members in pip cache for matching dist-info/METADATA names',
                    'scopes': [str(p) for p in scopes], 'errors': [], 'archives': [],
                    'engine_candidates': [], 'uno_candidates': [], 'visited_files': {}}
tokens = {n.replace('-', '_').lower() for n in closure | seeds | set(report['distributions'])}
for scope in scopes:
    if not scope.exists():
        report['search']['errors'].append({'scope': str(scope), 'error': 'root does not exist'})
        continue
    count = 0
    def error(exc):
        report['search']['errors'].append({'scope': str(scope), 'error': str(exc)})
    for base, dirs, files in os.walk(scope, onerror=error, followlinks=False):
        dirs.sort()
        for filename in sorted(files):
            count += 1
            path = Path(base) / filename
            norm = filename.lower().replace('-', '_')
            archive = filename.endswith(('.whl', '.conda', '.tar.bz2', '.tar.gz', '.deb', '.rpm'))
            matches = any(norm.startswith(n + '_') for n in tokens)
            if filename.endswith('.body') and 'pip' in str(scope):
                try:
                    if zipfile.is_zipfile(path):
                        with zipfile.ZipFile(path) as z:
                            matches = any('/METADATA' in n and '.dist-info/' in n and
                                          any(n.lower().replace('-', '_').startswith(t + '_')
                                              for t in tokens) for n in z.namelist())
                            if matches:
                                record = file_record(path)
                                record['dist_info'] = [n for n in z.namelist() if n.endswith('.dist-info/METADATA')]
                                report['search']['archives'].append(record)
                except (OSError, zipfile.BadZipFile) as exc:
                    error(exc)
            elif archive and matches:
                report['search']['archives'].append(file_record(path))
            if filename in ('soffice', 'soffice.bin', 'libreoffice', 'oosplash'):
                report['search']['engine_candidates'].append(file_record(path))
            if filename in ('uno.py', 'pyuno.so'):
                report['search']['uno_candidates'].append(file_record(path))
    report['search']['visited_files'][str(scope)] = count

for name in ('python3', 'soffice', 'libreoffice', 'bwrap', 'strace'):
    p = shutil.which(name)
    report['commands'].append({'name': name, 'artifact': file_record(p) if p else None,
                               'version': command([p, '--version']) if p else None})
for argv in (['/usr/bin/unshare', '--net', '--', '/usr/bin/true'],
             ['/usr/bin/bwrap', '--unshare-net', '--ro-bind', '/', '/', '--', '/usr/bin/true'],
             ['dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}\n',
              'libreoffice*', 'libuno*', 'uno-libs-private', 'ure', 'python3-uno'],
             ['ldd', '/usr/lib/libreoffice/program/soffice.bin'],
             ['ldd', '/usr/lib/libreoffice/program/libmergedlo.so']):
    report['commands'].append(command(argv))
for rel in ('data/datasets/spreadsheetbench.py', 'data/datasets/spreadsheetbench/verify/check.py',
            'data/scaffolds/scorer_input.py', 'eval/verification/verifier.py', 'eval/run/runner.py',
            'registry/w-metriccmp-sources/spreadsheetbench-evaluation.py.txt',
            'registry/w-metriccmp-sources/provenance.json'):
    report['source'].append(file_record(ROOT / rel))
report['loaded_native_files'] = []
paths = set()
for line in Path('/proc/self/maps').read_text().splitlines():
    p = line.split()[-1]
    if p.startswith('/') and Path(p).is_file():
        paths.add(p)
for p in sorted(paths):
    report['loaded_native_files'].append(file_record(p))
(OUT / 'dependency-inventory.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
print('inventory=tests/fixtures/hg_spreadrt/dependency-inventory.json')
print('python=' + report['python']['version'])
print('abi=' + report['python']['soabi'])
print('openpyxl_flags=' + json.dumps(report['openpyxl_flags'], sort_keys=True))
print('conda_closure_packages=' + str(len(closure)))
print('matching_cached_archives=' + str(len(report['search']['archives'])))
print('search_errors=' + json.dumps(report['search']['errors'], sort_keys=True))
