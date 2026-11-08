"""Standard-library supervisor/worker protocol for the two managed Tau preparations."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import types
import urllib.request
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class PreparationSourceChanged(ValueError):
    """Owned preparation bytes differ from the captured launch identity."""


_OWNED = ('tools/_tau_prepare.py', 'tools/prepare_tau2_bench.py', 'tools/prepare_tau3_bench.py',
          'data/datasets/tau2_bench.py', 'data/datasets/tau3_bench.py',
          'data/datasets/tau2_bench/prepare.py', 'data/datasets/tau3_bench/prepare.py',
          'data/datasets/tau2_bench/lib/bootstrap.py', 'data/datasets/tau3_bench/lib/bootstrap.py',
          'data/datasets/tau3_bench/lib/runtime.py')


def source_identity():
    result = {}
    for relative in _OWNED:
        path = ROOT / relative
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise ValueError('Tau preparation source requires physical owned files')
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    executed = globals().get('__executed_source_sha256__')
    if executed is not None and result['tools/_tau_prepare.py'] != executed:
        raise PreparationSourceChanged('Tau preparation supervisor source changed')
    return result


def verify_sources(expected):
    try:
        actual = source_identity()
    except (OSError, ValueError) as exc:
        raise PreparationSourceChanged('Tau preparation source unavailable or changed') from exc
    if not isinstance(expected, dict) or expected != actual:
        raise PreparationSourceChanged('Tau preparation source identity changed')
    return expected


def owned_module(path, module_name, expected=None):
    path = Path(path)
    identity = expected if expected is not None else globals().get('__tau_source_identity__')
    if identity is None:
        identity = source_identity()
    verify_sources(identity)
    relative = str(path.relative_to(ROOT))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PreparationSourceChanged('Tau preparation source disappeared before compilation') from exc
    if hashlib.sha256(raw).hexdigest() != identity.get(relative):
        raise PreparationSourceChanged('Tau preparation source changed before compilation')
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__dict__['__tau_source_identity__'] = identity
    module.__dict__['__executed_source_sha256__'] = hashlib.sha256(raw).hexdigest()
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    verify_sources(identity)
    return module


def load(name, expected=None):
    if name not in {'tau2_bench', 'tau3_bench'}:
        raise ValueError('unknown Tau preparation profile')
    return owned_module(ROOT / 'data/datasets' / name / 'prepare.py',
                        '_managed_' + name + '_prepare', expected)


def publish(source, target):
    """Atomically publish without replacing even an empty concurrent target."""
    source, target = Path(source), Path(target)
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise ValueError('Tau publication requires Linux renameat2 no-replace')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise ValueError('target appeared during preparation; refusing to overwrite it')
        raise OSError(error, os.strerror(error), str(target))


def environment(private=None):
    # Explicit worker HOME/caches prevent SDK credential discovery outside env keys.
    allowed = ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    if private is not None:
        private = Path(private)
        for key, relative in {'HOME': 'home', 'XDG_CACHE_HOME': 'cache', 'XDG_CONFIG_HOME': 'config',
                              'XDG_DATA_HOME': 'data', 'TMPDIR': 'tmp', 'TMP': 'tmp', 'TEMP': 'tmp'}.items():
            path = private / relative
            path.mkdir(parents=True, exist_ok=True)
            result[key] = str(path)
    result.update(PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1',
                  HF_HUB_OFFLINE='1', LITELLM_LOCAL_MODEL_COST_MAP='True')
    return result


def worker(request, loader=load):
    from harnessgrad import dataset_runtime
    binding = request['binding']
    expected_sources = verify_sources(request['source_identity'])
    name = request['dataset']
    if name not in {'tau2_bench', 'tau3_bench'} or binding.get('dataset') != name:
        raise ValueError('Tau worker binding dataset mismatch')
    # Match the venv executable string, not just its resolved base executable.
    runtime = dataset_runtime.admit_binding(binding, 'verifier', python=sys.executable)
    if sys.executable != binding['python']:
        raise ValueError('Tau worker must run under the exact selected interpreter')
    if request['operation'] == 'probe':
        result = {'python': sys.executable, 'version': list(sys.version_info[:2]),
                  'home': os.environ.get('HOME'), 'cwd': os.getcwd(),
                  'cache': os.environ.get('XDG_CACHE_HOME'), 'tmp': os.environ.get('TMPDIR'),
                  'credentials_present': any(key.startswith(('HG_ENV_', 'HG_AGENT_', 'HG_METHOD_', 'HG_JUDGE_'))
                                             or key in {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'PYTHONPATH'}
                                             for key in os.environ)}
    else:
        module = load(name, expected_sources) if loader is load else loader(name)
        verify_sources(expected_sources)  # Admission precedes all provider imports.
        options = request['options']
        with redirect_stdout(io.StringIO()):
            result = module._managed_operation(request['operation'], options, runtime)
    dataset_runtime.admit_binding(binding, 'verifier', python=sys.executable)
    verify_sources(expected_sources)
    return {**result, 'runtime_id': runtime['runtime_id'], 'source_identity': expected_sources}


def run(name, operation, options, *, runtime_override=None, program=None):
    from harnessgrad import dataset_runtime
    options = {key: str(Path(value).absolute()) if key in {'source', 'target', 'archive'} and value is not None
               else value for key, value in options.items()}
    runtime = dataset_runtime.resolve_execution(name)
    if runtime_override and Path(runtime_override).resolve() != Path(runtime['path']).resolve():
        raise ValueError('--runtime must name the selected managed execution artifact')
    binding = dataset_runtime.bind(runtime, 'verifier')
    sources = globals().get('__tau_source_identity__') or source_identity()
    verify_sources(sources)
    target = Path(options['target']).absolute() if 'target' in options else None
    reuse = options.get('reuse', False)
    if reuse and (target is None or not target.is_dir() or target.is_symlink()):
        raise ValueError('reuse requires an existing valid payload; it never creates or repairs one')
    if operation == 'prepare' and not reuse and (target.exists() or target.is_symlink()):
        raise ValueError('target already exists; use a fresh target')
    if name == 'tau3_bench' and operation == 'prepare' and options.get('fetch') and not options.get('reuse'):
        # Explicit source download keeps controller HOME/proxy/authentication.
        # Provider/generation workers receive only the resulting pinned archive.
        with tempfile.TemporaryDirectory(prefix='tau3-controller-fetch-') as temporary:
            archive = Path(options['archive']) if options.get('archive') else Path(temporary) / 'source.tar.gz'
            revision = load(name, sources).REVISION
            urllib.request.urlretrieve('https://codeload.github.com/sierra-research/tau2-bench/tar.gz/' + revision, archive)
            verify_sources(sources)
            return run(name, operation, {**options, 'archive': str(archive), 'fetch': False},
                       runtime_override=runtime_override, program=program)
    request = {'dataset': name, 'operation': operation, 'options': options, 'binding': binding,
               'source_identity': sources}
    if operation != 'prepare' or reuse:
        return launch(request, program=program)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.' + target.name + '-managed-', dir=target.parent))
    success = False
    request['_phase'] = 'source-capture'
    try:
        staged = temporary / 'payload'
        (temporary / 'source-identity.json').write_text(json.dumps(request['source_identity'], sort_keys=True))
        for relative, expected in request['source_identity'].items():
            try:
                raw = (ROOT / relative).read_bytes()
            except OSError as exc:
                raise PreparationSourceChanged('Tau preparation source disappeared during attempt capture') from exc
            if hashlib.sha256(raw).hexdigest() != expected:
                raise PreparationSourceChanged('Tau preparation source drift during attempt capture')
            snapshot = temporary / 'preparation-sources' / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(raw)
        request['options'] = {**options, 'target': str(staged)}
        request['attempt_path'] = str(temporary / 'worker-attempt.json')
        result = launch(request, program=program)
        request['_phase'] = 'pre-publication-source-admission'
        verify_sources(request['source_identity'])
        request['_phase'] = 'publication'
        publish(staged, target)
        success = True
        return result
    except BaseException as exc:
        phase = request.get('_phase', 'source-capture')
        (temporary / 'failed.json').write_text(json.dumps({'error_type': type(exc).__name__,
                                                         'phase': phase}, sort_keys=True))
        message = ('Tau preparation failed (' + type(exc).__name__ + ', phase=' + phase
                   + '); staging retained at ' + str(temporary))
        if isinstance(exc, (PreparationSourceChanged, dataset_runtime.DatasetRuntimeUnavailable)):
            exc.args = (message,)
            raise exc from None
        raise ValueError(message) from None
    finally:
        if success:
            shutil.rmtree(temporary)


def launch(request, *, program=None):
    from harnessgrad import dataset_runtime
    binding = request['binding']
    request['_phase'] = 'pre-worker-source-admission'
    verify_sources(request['source_identity'])
    request['_phase'] = 'pre-worker-artifact-admission'
    dataset_runtime.admit_binding(binding, 'verifier', python=binding['python'])
    bootstrap = ("import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); raw=p.read_bytes(); "
                 "assert hashlib.sha256(raw).hexdigest()==sys.argv[2], 'Tau worker source changed'; "
                 "sys.argv=[str(p),'--worker']; "
                 "exec(compile(raw,str(p),'exec'),{'__name__':'__main__','__file__':str(p),"
                 "'__executed_source_sha256__':hashlib.sha256(raw).hexdigest()})")
    argv = ([binding['python'], '-I', '-B', str(program), '--worker'] if program else
            [binding['python'], '-I', '-B', '-c', bootstrap, str(Path(__file__)),
             request['source_identity']['tools/_tau_prepare.py']])
    attempt = Path(request['attempt_path']) if request.get('attempt_path') else None
    if attempt:
        attempt.write_text(json.dumps({'runtime_id': binding['runtime_id'], 'state': 'starting',
                                      'source_identity': request['source_identity']}, sort_keys=True))
    request['_phase'] = 'worker-execution'
    with tempfile.TemporaryDirectory(prefix='hg-tau-worker-private-') as private:
        proc = subprocess.run(argv, input=json.dumps(request), capture_output=True, text=True,
                              env=environment(private), cwd=private, check=False)
    if attempt:
        # Raw diagnostics stay in this mode-0700 owned attempt, never the receipt/error.
        attempt.with_name('worker-stdout.txt').write_text(proc.stdout)
        attempt.with_name('worker-stderr.txt').write_text(proc.stderr or '')
        attempt.write_text(json.dumps({'runtime_id': binding['runtime_id'], 'state': 'completed',
                                      'exit_code': proc.returncode,
                                      'source_identity': request['source_identity']}, sort_keys=True))
    if proc.returncode:
        verify_sources(request['source_identity'])
        try:
            failure = json.loads(proc.stdout)
        except (ValueError, TypeError):
            failure = {}
        if isinstance(failure, dict) and failure.get('error_type') == 'PreparationSourceChanged':
            raise PreparationSourceChanged('Tau worker observed preparation source drift')
        # Third-party errors can quote private tasks or provider configuration.
        raise ValueError('managed Tau preparation worker failed (exit ' + str(proc.returncode) + ')')
    request['_phase'] = 'worker-reply'
    result = json.loads(proc.stdout)
    if result.get('runtime_id') != binding['runtime_id']:
        raise ValueError('Tau worker returned a different runtime identity')
    request['_phase'] = 'post-worker-artifact-admission'
    dataset_runtime.admit_binding(binding, 'verifier', python=binding['python'])
    request['_phase'] = 'post-worker-source-admission'
    verify_sources(request['source_identity'])
    if result.get('source_identity') != request['source_identity']:
        raise PreparationSourceChanged('Tau worker returned a different preparation source identity')
    return result


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('internal Tau preparation worker')
    try:
        print(json.dumps(worker(json.load(sys.stdin)), sort_keys=True))
    except Exception as exc:
        kind = 'PreparationSourceChanged' if getattr(exc, 'preparation_source_changed', False) else type(exc).__name__
        print(json.dumps({'error_type': kind}))
        print(kind, file=sys.stderr)
        raise SystemExit(1)
