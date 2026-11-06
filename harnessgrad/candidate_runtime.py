"""Candidate-owned execution dependencies and their ordinary-run lifecycle.

The dataset/scorer environment stays fixed across candidates; these dependencies
may be edited by the method. Every measured checkpoint therefore keeps its own
binding. This module never imports candidate modules or executes package startup.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile

from eval.ckpt.source_tree import runtime_spec
from harnessgrad import python_harness_runtime as python_runtime
from harnessgrad import python_exec_runtime as exec_runtime

KEY = "harness_runtime_identity"
RuntimeUnavailable = python_runtime.RuntimeUnavailable


def has_python(repo):
    spec = runtime_spec(Path(repo))
    return spec is not None and spec["kind"] == "python"


def validate(binding):
    if isinstance(binding, dict) and binding.get('kind') == exec_runtime.KIND:
        return exec_runtime.validate_binding(binding)
    return python_runtime.validate_binding(binding)


def fields(binding):
    return {} if binding is None else {KEY: validate(binding)}


def matches_source(repo, binding):
    """Match owned input bytes without hiding damage to an otherwise matching artifact."""
    if binding is None or not has_python(repo):
        return False
    binding = validate(binding)
    declaration = python_runtime.descriptor(repo)
    declared_sha = (exec_runtime.descriptor(repo)['placements_sha256']
                    if 'exec' in declaration else declaration['declaration_sha256'])
    return (python_runtime.source_fingerprint(repo) == binding["source_sha256"]
            and declared_sha == binding["declaration_sha256"])


def problems(reference, current):
    if reference is None and current is None:
        return []
    for label, binding in (("reference", reference), ("current candidate", current)):
        if binding is None:
            return [f"harness_runtime_identity is missing from the {label}"]
        try:
            validate(binding)
        except (TypeError, ValueError) as exc:
            return [f"invalid harness_runtime_identity on the {label}: {exc}"]
    return [] if reference == current else ["harness_runtime_identity changed: candidate source or execution artifact differs"]


def require_same(reference, current):
    errors = problems(reference, current)
    if errors:
        raise RuntimeUnavailable("; ".join(errors))


def _routes(envs, setups, sandbox, *, allow_exec=False):
    if not sandbox:
        raise RuntimeUnavailable("Managed Python candidates require the native actor sandbox")
    for tid, spec in (envs or {}).items():
        spec = spec or {"kind": "files"}
        if spec.get("backend") is not None:
            raise RuntimeUnavailable(f"Managed Python task {tid}: observed/worker backends are not supported")
        if spec.get("kind", "files") not in ({'files', 'exec'} if allow_exec else {'files'}):
            raise RuntimeUnavailable(f"Managed Python task {tid}: exec/container placements are not supported")
        if spec.get("agent_runtime") is not None:
            raise RuntimeUnavailable(f"Managed Python task {tid}: agent package projections are not supported")
    for tid, setup in (setups or {}).items():
        if (setup or {}).get("workers") is not None:
            raise RuntimeUnavailable(f"Managed Python task {tid}: cooperative/observed workers are not supported")


def validate_routes(envs=None, setups=None, sandbox=True, *, allow_exec=False):
    """Preparation tools can reject unsupported selected routes without building."""
    _routes(envs, setups, sandbox, allow_exec=allow_exec)


def _source_only(repo):
    # Copying a candidate strips these entries. A direct caller must provide the
    # same source-only view, not an ignored environment or stale executable cache.
    manifest = json.loads((Path(repo) / "harness.json").read_bytes())
    if manifest.get("runtime_paths") not in (None, []):
        raise RuntimeUnavailable("Managed Python candidates cannot expose unbound external runtime_paths")
    opaque = {".git", ".state", "_harnessgrad", ".opencode-state"}
    for directory, dirs, files in os.walk(repo, followlinks=False):
        for name in set(dirs + files) & opaque:
            path = Path(directory) / name
            if path.is_symlink():
                raise RuntimeUnavailable("Ignored managed source paths must not be aliases")
            if name == ".state" and Path(directory) == Path(repo) and not path.is_dir():
                raise RuntimeUnavailable("Managed candidate scratch state must be a directory")
        dirs[:] = [name for name in dirs if name not in opaque]
        if any(name in {".venv", "venv", "node_modules", "__pycache__"} for name in dirs + files):
            raise RuntimeUnavailable("Managed Python execution requires a source-only staged candidate")
        if any(name.endswith((".pyc", ".pyo")) for name in files):
            raise RuntimeUnavailable("Managed Python execution refuses precompiled candidate bytecode")


def admit(repo, binding=None, *, envs=None, setups=None, sandbox=True):
    """Recheck a selected candidate before either launch or measurement reuse."""
    if not has_python(repo):
        if binding is not None:
            raise RuntimeUnavailable("harness_runtime_identity requires the original managed Python declaration")
        return None
    execution = exec_runtime.descriptor(repo)
    _routes(envs, setups, sandbox, allow_exec=execution is not None)
    _source_only(repo)
    if execution is not None:
        return exec_runtime.prepare(repo, binding=binding, envs=envs)['identity']
    return python_runtime.identity(Path(repo), binding=binding)


def prepare(repo, *, binding=None, envs=None, setups=None, sandbox=True, extend_images=False):
    """Prepare once after source staging, using only the trusted offline cache."""
    if not has_python(repo):
        if binding is not None:
            raise RuntimeUnavailable("Managed Python candidate cannot drop its runtime declaration")
        return None
    execution = exec_runtime.descriptor(repo)
    _routes(envs, setups, sandbox, allow_exec=execution is not None)
    _source_only(repo)
    if execution is not None:
        return exec_runtime.prepare(repo, binding=binding, envs=envs, build=True, extend_images=extend_images)
    return python_runtime.prepare(Path(repo), binding=binding, build=True, allow_downloads=False)


def record_preparation(run_dir, round_no, receipt):
    """Append an atomic, private receipt; never overwrite an earlier attempt."""
    if receipt is None:
        return None
    if type(round_no) is not int or round_no < 0:
        raise ValueError("Preparation round must be a nonnegative integer")
    root = Path(run_dir)
    directory = root / "harness-preparation" / f"round-{round_no}"
    for path in (root, root / "harness-preparation", directory):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeUnavailable("Preparation receipt directory must be privately owned")
        path.mkdir(exist_ok=True, parents=True)
    value = {"schema": "harnessgrad.harness-preparation.v1", "round": round_no,
             "receipt": receipt}
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        index = 1
        while True:
            target = directory / f"attempt-{index:04d}.json"
            try:
                os.link(temporary, target)
                return target
            except FileExistsError:
                index += 1
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _checkpoint_source(run_dir, point):
    root = Path(run_dir)
    round_no = point.get("round")
    if type(round_no) is not int or round_no < 0:
        raise RuntimeUnavailable("Invalid checkpoint round for harness_runtime_identity")
    source = root / f"state_after_round{round_no}"
    if source.is_symlink():
        raise RuntimeUnavailable("Managed checkpoint source must not be a symlink")
    if source.is_dir():
        yield source
        return
    from harnessgrad import snapshot
    path = snapshot.path_for(root, round_no)
    if not path.is_file():
        if point.get(KEY) is not None:
            raise RuntimeUnavailable("Managed checkpoint has no retained source or portable snapshot")
        yield None
        return
    try:
        payload = snapshot.load(path)
    except snapshot.SnapshotError as exc:
        raise RuntimeUnavailable(f"harness_runtime_identity snapshot is invalid: {exc}") from exc
    expected = (point.get("identity") or {}).get("harness_sha")
    if expected and payload.get("harness_sha") != expected:
        raise RuntimeUnavailable("Managed checkpoint snapshot names a different source state")
    with tempfile.TemporaryDirectory(prefix="hg-python-checkpoint-") as temporary:
        restored = Path(temporary) / "source"
        try:
            snapshot.check_out(path, restored)
        except snapshot.SnapshotError as exc:
            raise RuntimeUnavailable(f"harness_runtime_identity snapshot cannot be restored: {exc}") from exc
        yield restored


def checkpoint_binding(run_dir, point):
    """Validate this checkpoint's own source/artifact, not the last round's deps."""
    if point.get("unmeasured"):
        return None
    binding = point.get(KEY)
    with _checkpoint_source(run_dir, point) as source:
        if source is None:
            return None
        if binding is None and has_python(source):
            raise RuntimeUnavailable("harness_runtime_identity is missing from a managed checkpoint")
        return admit(source, binding)


@contextmanager
def checkpoint_source(run_dir, point):
    """Keep a restored portable source alive for the caller's staging scope."""
    with _checkpoint_source(run_dir, point) as source:
        if source is None:
            raise RuntimeUnavailable("Managed checkpoint source is unavailable")
        yield source


def validate_resume(run_dir, metadata, points, current_source, *, envs=None,
                    setups=None, sandbox=True):
    """Read-only checks before metadata writes or workspace restoration."""
    side = metadata.get("side") or ((metadata.get("config") or {}).get("run") or {}).get("side")
    if side == "eval" or metadata.get("evaluated_from") or metadata.get("from_run"):
        binding = metadata.get(KEY)
        if binding is None and has_python(current_source):
            raise RuntimeUnavailable("harness_runtime_identity is missing from the interrupted eval")
        selected = admit(current_source, binding, envs=envs, setups=setups, sandbox=sandbox)
        for point in points:
            require_same(point.get(KEY), selected)
        return {"initial": selected, "current": selected}
    checked = [checkpoint_binding(run_dir, point) for point in points]
    initial = checked[0] if checked else metadata.get(KEY)
    require_same(metadata.get(KEY), initial)
    if not checked:
        if initial is not None or has_python(current_source):
            raise RuntimeUnavailable("Managed resume requires its original measured checkpoint")
        return {"initial": None, "current": None}
    if any(binding is not None for binding in checked):
        _routes(envs, setups, sandbox, allow_exec=exec_runtime.descriptor(current_source) is not None)
    return {"initial": initial, "current": checked[-1]}
