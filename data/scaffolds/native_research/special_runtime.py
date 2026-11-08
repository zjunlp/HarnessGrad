"""Managed dependency identity and fixed source custody for native workers."""
from __future__ import annotations
import hashlib
from pathlib import Path


def binding(dataset):
    from harnessgrad.dataset_runtime import resolve_execution, bind
    return bind(resolve_execution(dataset), 'verifier')


def admit(selected):
    from harnessgrad.dataset_runtime import admit_binding
    return admit_binding(selected, 'verifier')['python']


def fingerprint(selected, files):
    """Internal launch identity. Never pass this full binding to public evidence."""
    # Include the complete platform-owned native projection, including transitive
    # source imports. This tree contains code, never evaluator task/rubric data.
    root = Path(__file__).resolve().parent
    files = {**{str(p.relative_to(root)): p for p in (root/'vendor').rglob('*.py')},
             'native.py': root/'native.py', **files}
    hashes = {name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
              for name, path in files.items()}
    result = {'binding': selected, 'modules': hashes}
    return result


def harvey_source():
    """Snapshot the exact release wheel, never substitute a registry namesake."""
    import os
    import tempfile
    import zipfile
    value = os.environ.get('HG_JUDGE_HARVEY_WHEEL')
    if not value:
        from harnessgrad.config.areas import ConfigMissing
        raise ConfigMissing('harvey_lab: fetch the pinned release wheel; see data/datasets/harvey_lab/execution-recipe.md; set HG_JUDGE_HARVEY_WHEEL')
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('Harvey wheel must be an absolute regular source artifact')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != '5bfd70664a99c16a97a35bf61a3845a9890477168d11978d58864f986c9f53ba':
        raise ValueError('Harvey release wheel digest mismatch')
    import io
    snapshot = tempfile.TemporaryDirectory(prefix='hg-harvey-source-')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                target = Path(snapshot.name) / info.filename
                if not target.resolve().is_relative_to(Path(snapshot.name)):
                    raise ValueError('unsafe wheel member')
            archive.extractall(snapshot.name)
    except BaseException:
        snapshot.cleanup()
        raise
    return snapshot


def public_identity(identity):
    """A source/runtime commitment with no host paths or private task digests."""
    return {'runtime_id': identity['binding']['runtime_id'],
            'modules': dict(identity['modules'])}


def public_harvey_runtime(runtime):
    """Project the internal probe record before verdicts enter the method channel."""
    fields = {'lab_core', 'anthropic', 'openai', 'pandoc_version', 'judges',
              'judge_profile', 'upstream_revision', 'official_wheel_sha256'}
    result = {key: runtime[key] for key in fields if key in runtime}
    result['execution_identity'] = public_identity(runtime['execution_identity'])
    return result
