"""Image-owned Python venvs and exact native/container candidate bindings.

Only trusted image interpreters and uv run during preparation/admission. Candidate
source is never mounted there; installed wheels are inspected without site startup.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
from contextlib import nullcontext

from eval.containers import container
from harnessgrad import python_harness_runtime as native
from harnessgrad.python_artifacts import atomic_json, canonical, digest, freeze, locked, remove_instance

SCHEMA = 'harnessgrad.harness-image-python-artifact.v1'
KIND = 'python-placements'
_IMAGE = re.compile(r'(?:[^@\s]+@)?sha256:[0-9a-f]{64}\Z')


def descriptor(repo):
    spec = native.descriptor(repo)
    if spec is None or 'exec' not in spec:
        return None
    locks = {}
    for minor, relative in spec['exec']['requirements'].items():
        raw = (Path(repo) / relative).read_bytes()
        locks[minor] = {'sha256': hashlib.sha256(raw).hexdigest(), 'pins': native._pins(raw)}
    return {**spec, 'locks': locks, 'placements_sha256': hashlib.sha256(canonical({
        'native': spec['declaration_sha256'], 'locks': locks})).hexdigest()}


def _signed(value):
    return {**value, 'sha256': hashlib.sha256(canonical(value)).hexdigest()}


def validate_binding(value):
    if (not isinstance(value, dict) or set(value) != {
            'version', 'kind', 'native', 'images', 'source_sha256', 'declaration_sha256', 'sha256'}
            or type(value['version']) is not int or value['version'] != 1 or value['kind'] != KIND
            or not isinstance(value['images'], dict)):
        raise native.RuntimeUnavailable('Invalid Python placement binding')
    base = native.validate_binding(value['native'])
    if value['source_sha256'] != base['source_sha256']:
        raise native.RuntimeUnavailable('Python placement source identity mismatch')
    for key in ('source_sha256', 'declaration_sha256', 'sha256'):
        if not isinstance(value[key], str) or not native._HASH.fullmatch(value[key]):
            raise native.RuntimeUnavailable('Invalid Python placement digest')
    for image, item in value['images'].items():
        if (not isinstance(image, str) or not _IMAGE.fullmatch(image)
                or not isinstance(item, dict) or set(item) != {'base', 'artifact'}):
            raise native.RuntimeUnavailable('Invalid Python image placement')
        # The physical-path contract is shared with native artifacts; no filesystem
        # admission is performed by this structural validator.
        native.validate_binding(_signed({key: val for key, val in {
            **base, 'artifact': item['artifact']}.items() if key != 'sha256'}))
        image_base = item['base']
        if image_base is None:
            if item['artifact'] != base['artifact']:
                raise native.RuntimeUnavailable('No-Python image must use the exact native artifact')
        elif (not isinstance(image_base, dict) or set(image_base) != {
                'executable', 'sha256', 'minor', 'version', 'base_prefix'}
                or not isinstance(image_base['executable'], str)
                or not Path(image_base['executable']).is_absolute()
                or not native._HASH.fullmatch(image_base.get('sha256', ''))
                or not re.fullmatch(r'3\.[0-9]+', image_base.get('minor', ''))
                or not isinstance(image_base['version'], str)
                or not isinstance(image_base['base_prefix'], str)):
            raise native.RuntimeUnavailable('Invalid image interpreter identity')
    if _signed({key: val for key, val in value.items() if key != 'sha256'}) != value:
        raise native.RuntimeUnavailable('Python placement binding digest mismatch')
    return json.loads(canonical(value))


def _run(image, argv, *, directory=None, paths=(), extra=(), env=None, timeout_s=120, network='none'):
    with tempfile.TemporaryDirectory(prefix='hg-image-python-') as temporary:
        work = Path(directory or temporary)
        result = container.run([str(arg) for arg in argv], image=image,
            task_dir=work, task_mount=str(work), cwd=str(work),
            readonly_paths=tuple(Path(path) for path in paths),
            extra_mounts=('--entrypoint', '', '--no-healthcheck', *extra),
            env=env or {'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
            timeout_s=timeout_s, network=network)
    if result.returncode:
        raise native.RuntimeUnavailable(f'Image Python command failed ({result.returncode}): '
                                        + (result.stderr or result.stdout)[-2400:])
    return result.stdout


_BASE = '''import hashlib,json,os,sys
if sys.implementation.name != 'cpython': raise RuntimeError('Managed image Python requires CPython')
p=os.path.realpath(getattr(sys,'_base_executable',sys.executable))
print(json.dumps({'executable':p,'sha256':hashlib.sha256(open(p,'rb').read()).hexdigest(),
'minor':str(sys.version_info[0])+'.'+str(sys.version_info[1]),'version':sys.version,
'base_prefix':sys.base_prefix}))'''


def image_base(image):
    """A failed probe is an error; only an explicit empty result means no Python."""
    found = _run(image, ['sh', '-c', 'command -v python3 || command -v python || :']).strip()
    if not found:
        return None
    if not found.startswith('/') or '\n' in found:
        raise native.RuntimeUnavailable('Image Python discovery returned an invalid executable')
    return json.loads(_run(image, [found, '-I', '-S', '-B', '-c', _BASE]))


def _inventory(venv, base):
    """Image interpreter links resolve in the image, never against the host."""
    rows = {}
    for path in sorted(venv.rglob('*')):
        relative = path.relative_to(venv).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            absolute = Path(os.path.normpath(path.parent / target))
            if not absolute.is_relative_to(venv) and not (
                    relative.startswith('bin/python') and str(absolute) == base['executable']):
                raise native.RuntimeUnavailable('Image venv contains an escaping link: ' + relative)
            rows[relative] = {'kind': 'link', 'target': target}
        elif path.is_file():
            rows[relative] = {'kind': 'file', 'sha256': digest(path),
                              'mode': stat.S_IMODE(path.stat().st_mode)}
        elif not path.is_dir():
            raise native.RuntimeUnavailable('Image venv contains a non-regular entry')
    return rows


_PACKAGES = '''import importlib.metadata,json,os,sys
root,minor=sys.argv[1:3]
site=root+'/lib/python'+minor+'/site-packages'
cfg={}
for line in open(root+'/pyvenv.cfg'):
 k,sep,v=line.partition('=')
 if sep: cfg[k.strip().lower()]=v.strip()
print(json.dumps({'packages':[[d.metadata['Name'],d.version] for d in importlib.metadata.distributions(path=[site])],
'config':cfg,'readonly':bool(os.statvfs(root).f_flag & os.ST_RDONLY)}))'''


def _packages(image, base, artifact):
    value = json.loads(_run(image, [base['executable'], '-I', '-S', '-B', '-c', _PACKAGES,
        artifact['path'], base['minor']], paths=artifact['runtime_paths']))
    rows = value['packages']
    packages = {native._package(name): version for name, version in rows}
    if (len(packages) != len(rows) or not value['readonly']
            or value['config'].get('include-system-site-packages') != 'false'
            or value['config'].get('home') != str(Path(base['executable']).parent)):
        raise native.RuntimeUnavailable('Image venv does not exclude ambient packages or is writable')
    return packages


def _key(spec, image, base):
    return hashlib.sha256(canonical({'schema': SCHEMA, 'declaration': spec['placements_sha256'],
                                    'image': image, 'base': base})).hexdigest()


def _reference(folder, ready, key):
    if (not isinstance(ready, dict) or set(ready) != {'instance', 'runtime_id'}
            or not isinstance(ready['instance'], str)
            or not re.fullmatch(r'build-[A-Za-z0-9_-]+', ready['instance'])):
        raise native.RuntimeUnavailable('Invalid image runtime publication')
    venv = folder / 'instances' / ready['instance'] / 'venv'
    return {'runtime_id': ready['runtime_id'], 'manifest_path': str(venv.parent / 'artifact.json'),
            'python': str(venv / 'bin/python'), 'path': str(venv),
            'runtime_paths': [str(venv)], 'dependency_key': key}


def _admit(repo, spec, image, item, *, probe=True):
    base, artifact = item['base'], item['artifact']
    if container.resolve_digest(image) != image:
        raise native.RuntimeUnavailable('Bound image is unavailable')
    if base is None:
        if probe:
            if image_base(image) is not None:
                raise native.RuntimeUnavailable('Image supplies Python; refusing a native fallback binding')
            native_manifest = json.loads(Path(artifact['manifest_path']).read_bytes())
            # -S uses the trusted base and does not execute installed .pth hooks.
            _run(image, [native_manifest['base_python']['executable'], '-I', '-S', '-B', '-c',
                         'import sys; assert sys.implementation.name == "cpython"'],
                 paths=artifact['runtime_paths'])
        return
    key = _key(spec, image, base)
    path = Path(artifact['manifest_path'])
    store = native.store_root(repo, path.parents[3])
    if (path.resolve(strict=True) != path or path.parent.parent.name != 'instances'
            or path.parents[2] != store / key or artifact['dependency_key'] != key):
        raise native.RuntimeUnavailable('Image runtime is outside its exact dependency store')
    venv = Path(artifact['path'])
    if venv.is_symlink() or venv.resolve(strict=True) != venv:
        raise native.RuntimeUnavailable('Image runtime must have its physical venv path')
    manifest = json.loads(path.read_bytes())
    expected = {'schema': SCHEMA, 'dependency_key': key, 'image': image, 'base': base,
                'declaration_sha256': spec['placements_sha256'], 'path': artifact['path']}
    if (any(manifest.get(k) != v for k, v in expected.items())
            or hashlib.sha256(canonical(manifest)).hexdigest() != artifact['runtime_id']
            or artifact['path'] != str(path.parent / 'venv')
            or artifact['runtime_paths'] != [artifact['path']]
            or Path(artifact['python']).is_symlink()
            or digest(artifact['python']) != base['sha256']
            or manifest.get('files') != _inventory(Path(artifact['path']), base)
            or base['minor'] not in spec['locks']
            or manifest.get('packages') != spec['locks'][base['minor']]['pins']
            or any((manifest.get('recipe') or {}).get(key) != expected for key, expected in
                   {'version': 1, 'wheel_only': True, 'complete_lock': True, 'no_deps': True,
                    'copied_image_executable': True}.items())):
        raise native.RuntimeUnavailable('Image runtime identity or file inventory changed')
    if probe and (image_base(image) != base or _packages(image, base, artifact) != manifest['packages']):
        raise native.RuntimeUnavailable('Image interpreter or installed package metadata changed')


def _build(repo, spec, image, base, folder, *, allow_downloads, timeout_s):
    uv = shutil.which('uv')
    if uv is None:
        raise native.RuntimeUnavailable('uv is required for image Python preparation')
    uv = Path(uv).resolve(strict=True)
    cache = native._cache(repo, folder.parent)
    wheelhouse = native._outside(os.environ['HG_HARNESS_WHEELHOUSE'], repo, label='Wheelhouse') \
        if os.environ.get('HG_HARNESS_WHEELHOUSE') else None
    instances = folder / 'instances'
    if instances.is_symlink():
        raise native.RuntimeUnavailable('Image instances directory must not be a link')
    instances.mkdir(exist_ok=True)
    instance = Path(tempfile.mkdtemp(prefix='build-', dir=instances))
    venv = instance / 'venv'
    try:
        lock = instance / 'requirements.lock'
        pins = spec['locks'][base['minor']]['pins']
        lock.write_text(''.join(name+'=='+version+'\n' for name, version in sorted(pins.items())))
        home = instance / 'home'
        for path in (home, home/'tmp', home/'config', home/'cache'):
            path.mkdir(exist_ok=True)
        env = native._environment(home, cache)
        paths = [uv] + ([wheelhouse] if wheelhouse is not None else [])
        extra = ['--mount', f'type=bind,src={cache},dst={cache}']
        common = [uv, '--no-config', '--no-python-downloads']
        options = ([] if allow_downloads else ['--offline'])
        if wheelhouse is not None:
            options += ['--no-index', '--find-links', wheelhouse]
        def run(args):
            return _run(image, args, directory=instance, paths=paths, extra=extra, env=env,
                        timeout_s=timeout_s, network='bridge' if allow_downloads else 'none')
        resolved = instance/'resolved.lock'
        run([*common, 'pip', 'compile', lock, '--python', base['executable'], '--only-binary', ':all:',
             '--no-header', '--no-annotate', '--no-emit-index-url', '--no-emit-find-links',
             '-o', resolved, *options])
        if native._pins(resolved.read_bytes()) != pins:
            raise native.RuntimeUnavailable('Image lock omits or conflicts with transitive dependencies')
        run([*common, 'venv', '--no-project', '--python', base['executable'], venv])
        run([base['executable'], '-I', '-S', '-B', '-c',
             'import pathlib,shutil,sys;p=pathlib.Path(sys.argv[2]);p.unlink();shutil.copy2(sys.argv[1],p)',
             base['executable'], venv/'bin/python'])
        run([*common, 'pip', 'install', '--python', venv/'bin/python', '--no-deps',
             '--only-binary', ':all:', '--link-mode', 'copy', '-r', lock, *options])
        _inventory(venv, base)
        freeze(venv)
        ref = _reference(folder, {'instance': instance.name, 'runtime_id': '0'*64}, folder.name)
        if _packages(image, base, ref) != pins:
            raise native.RuntimeUnavailable('Image packages differ from the complete lock')
        shutil.rmtree(home)
        lock.unlink()
        resolved.unlink()
        manifest = {'schema': SCHEMA, 'dependency_key': folder.name, 'image': image, 'base': base,
                    'declaration_sha256': spec['placements_sha256'], 'path': str(venv),
                    'packages': pins, 'files': _inventory(venv, base),
                    'recipe': {'version': 1, 'wheel_only': True, 'complete_lock': True,
                               'no_deps': True, 'uv_sha256': digest(uv),
                               'copied_image_executable': True,
                               'downloads_allowed': bool(allow_downloads)}}
        atomic_json(instance/'artifact.json', manifest)
        ref['runtime_id'] = hashlib.sha256(canonical(manifest)).hexdigest()
        _admit(repo, spec, image, {'base': base, 'artifact': ref})
        return ref
    except BaseException:
        remove_instance(instance)
        raise


def _images(envs):
    images = set()
    for spec in (envs or {}).values():
        if (spec or {}).get('kind', 'files') == 'exec':
            image = spec.get('image')
            if not isinstance(image, str) or not _IMAGE.fullmatch(image):
                raise native.RuntimeUnavailable('Managed exec requires a pinned image content address')
            if spec.get('image_digest') not in (None, container.digest_of(image)):
                raise native.RuntimeUnavailable('Managed exec image_digest differs from its image')
            images.add(image)
    return sorted(images)


def _prepare(repo, *, envs=None, binding=None, build=False, allow_downloads=False,
            python=None, root=None, timeout_s=120, force=False, extend_images=False):
    started = time.monotonic()
    spec = descriptor(repo)
    if spec is None:
        raise native.RuntimeUnavailable('Harness does not declare image Python runtimes')
    wanted = _images(envs)
    if binding is not None:
        if force or python is not None:
            raise native.RuntimeUnavailable('An exact placement binding cannot be reconfigured')
        binding = validate_binding(binding)
        if (binding['source_sha256'] != native.source_fingerprint(repo)
                or binding['declaration_sha256'] != spec['placements_sha256']):
            raise native.RuntimeUnavailable('Python placement source or declaration changed')
        missing=set(wanted)-set(binding['images'])
        if missing and not (extend_images and build):
            raise native.RuntimeUnavailable('Selected image is absent from the original Harness binding')
        base_receipt = native.prepare(repo, binding=binding['native'], build=False)
        for image, item in binding['images'].items():
            _admit(repo, spec, image, item, probe=envs is not None and image in wanted)
        if not missing:
            return {**base_receipt, 'identity': binding, 'seconds': round(time.monotonic()-started, 6)}
    else:
        base_receipt = native.prepare(repo, build=build, allow_downloads=allow_downloads,
                                     python=python, root=root, timeout_s=timeout_s, force=force)
    images = dict(binding['images']) if binding is not None else {}
    installed = base_receipt['installed']
    for image in (image for image in wanted if image not in images):
        base = image_base(image)
        if base is None:
            images[image] = {'base': None, 'artifact': base_receipt['identity']['artifact']}
            _admit(repo, spec, image, images[image])
            continue
        if base['minor'] not in spec['locks']:
            raise native.RuntimeUnavailable('No complete lock declared for image Python '+base['minor'])
        key = _key(spec, image, base)
        folder = native.store_root(repo, root) / key
        if folder.is_symlink():
            raise native.RuntimeUnavailable('Image dependency folder must not be a link')
        if build:
            folder.mkdir(parents=True, exist_ok=True)
        with locked(folder, timeout_s=timeout_s) if build else nullcontext():
            ready = folder/'ready.json'
            if ready.is_symlink():
                raise native.RuntimeUnavailable('Image runtime publication must not be a link')
            if ready.exists() and not force:
                artifact = _reference(folder, json.loads(ready.read_bytes()), key)
            elif build:
                artifact = _build(repo, spec, image, base, folder,
                                  allow_downloads=allow_downloads, timeout_s=timeout_s)
                installed = True
                if native.source_fingerprint(repo) != base_receipt['identity']['source_sha256']:
                    raise native.RuntimeUnavailable('Harness source changed during image preparation')
                atomic_json(ready, {'instance': Path(artifact['manifest_path']).parent.name,
                                    'runtime_id': artifact['runtime_id']})
            else:
                raise native.RuntimeUnavailable('Prepared image Python runtime missing; configure with --dataset or --builder-image')
            images[image] = {'base': base, 'artifact': artifact}
            _admit(repo, spec, image, images[image])
    value = _signed({'version': 1, 'kind': KIND, 'native': base_receipt['identity'], 'images': images,
                     'source_sha256': base_receipt['identity']['source_sha256'],
                     'declaration_sha256': spec['placements_sha256']})
    validate_binding(value)
    if (native.source_fingerprint(repo) != value['source_sha256']
            or descriptor(repo)['placements_sha256'] != value['declaration_sha256']):
        raise native.RuntimeUnavailable('Harness source or declaration changed during preparation')
    return {**base_receipt, 'identity': value, 'installed': installed,
            'cache_hit': base_receipt['cache_hit'] and not installed,
            **({'extended_from': binding['sha256']} if binding is not None else {}),
            'seconds': round(time.monotonic()-started, 6)}


def prepare(repo, **options):
    started = time.monotonic()
    try:
        return _prepare(repo, **options)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        receipt = getattr(exc, 'receipt', None) or {'status': 'invalid', 'offline': not options.get('allow_downloads'),
            'installed': False, 'cache_hit': False, 'seconds': round(time.monotonic()-started, 6),
            'error_type': type(exc).__name__, 'detail': str(exc)}
        raise native.RuntimeUnavailable(str(exc), receipt=receipt) from exc


def placement(binding, env=None):
    """Select the exact actor artifact; never replace an image-owned interpreter."""
    if binding.get('kind') != KIND:
        return {'base': None, 'artifact': binding['artifact'], 'placement': 'files'}
    if (env or {}).get('kind', 'files') == 'files':
        return {'base': None, 'artifact': binding['native']['artifact'], 'placement': 'files'}
    image = (env or {}).get('image')
    if image not in binding['images']:
        raise native.RuntimeUnavailable('Actor image is not part of the bound runtime')
    item = binding['images'][image]
    task_mount = Path((env or {}).get('workdir') or container.DEFAULT_WORKDIR)
    for path in map(Path, item['artifact']['runtime_paths']):
        if path.is_relative_to(task_mount) or task_mount.is_relative_to(path):
            raise native.RuntimeUnavailable('Actor runtime overlaps the writable container task directory')
    if item['base'] and Path(item['base']['executable']).is_relative_to(task_mount):
        raise native.RuntimeUnavailable('Container task directory hides its bound base interpreter')
    return {**item, 'image': image, 'placement': 'image-python' if item['base'] else 'managed-python-fallback'}


def actor_environment(environment, artifact, *, image=False):
    # In a container, PATH and Python activation belong to the task image. The
    # actor is already selected by absolute executable and isolated with -I -B;
    # activating its venv here would also replace the task shell's interpreter.
    # Omitted variables must remain omitted so Docker inherits the image values,
    # including any startup-script changes on the ordinary mount-state route.
    if image:
        return dict(environment)
    value = {key: val for key, val in environment.items()
             if not key.startswith('PYTHON') and key not in ('VIRTUAL_ENV', 'CONDA_PREFIX')}
    value.update(PATH=str(Path(artifact['python']).parent)+os.pathsep+'/usr/local/bin'+os.pathsep+os.defpath,
                 VIRTUAL_ENV=artifact['path'], PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    return value


def actor_argv(binding, env, repo, entry):
    selected = placement(binding, env)
    return [selected['artifact']['python'], '-I', '-B', '-c',
            "import runpy,sys;from pathlib import Path;root,entry=sys.argv[1:3];"
            "del sys.argv[1:3];sys.path[:0]=[str(Path(entry).parent),root];"
            "sys.argv[0]=entry;runpy.run_path(entry,run_name='__main__')", str(repo), str(repo/entry)]


def record(binding, env=None, *, python_minor=None):
    selected = placement(binding, env)
    return {'source': 'managed-python', 'interpreter': selected['artifact']['python'],
            'python': selected['base']['minor'] if selected['base'] else python_minor,
            'identity': binding, 'placement': selected['placement'],
            'artifact': selected['artifact'], 'image': selected.get('image'),
            'host_site_packages_visible': False}
