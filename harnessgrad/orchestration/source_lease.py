"""Owned source review and catalogue lookup, held by the driver's ExitStack.

A lease is registered before review starts. Refusals therefore retain the original
report-before-cleanup order, and later checkpoint/publication resources still close
before the reviewed snapshot. Operator input directories are never adopted as Git worktrees.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
from collections.abc import Callable

from eval.ckpt.git_state import manifest
from eval.ckpt.source_tree import copy_harness
from tools.validate_harness import check as check_harness, source_identity, filesystem_identity
from harnessgrad.loops.eval_side import _eval_source, _eval_from_record


class SourceRefused(Exception):
    def __init__(self, *messages):
        super().__init__("\n".join(messages))
        self.messages = messages

    def report(self):
        for message in self.messages:
            print(message, file=sys.stderr)


class SourceLease:
    """Only temporary reviewed source storage; no provider/namespace/process ownership."""
    def __init__(self):
        self._owned = ExitStack()

    def __enter__(self):
        self._owned.__enter__()
        return self

    def __exit__(self, *exc):
        return self._owned.__exit__(*exc)

    def reviewed_directory(self):
        return Path(self._owned.enter_context(tempfile.TemporaryDirectory(prefix='hg-reviewed-source-')))


@dataclass(frozen=True)
class SourceRequest:
    harness: str
    harness_path: str | None
    side: str
    run_id: str | None
    runs_root: str
    work_root: str
    from_run: str | None
    from_round: int | None
    expected_harness_sha256: str | None
    expected_harness_filesystem_sha256: str | None


@dataclass(frozen=True)
class SourceSelection:
    path: Path
    harness: str
    run_id: str | None
    eval_from: dict | None = None
    provenance: dict | None = None
    receipt: dict | None = None
    reviewed_original: Path | None = None


def external_source_snapshot(source: Path, target: Path) -> None:
    """Own the source before Git runs; never adopt an external Git config/hooks.

    Rebase owned links onto this copy so absolute links cannot keep reaching the
    submitted directory after staging. The input directory itself is untouched.
    """
    copy_harness(source, target, symlinks=True, ignore=shutil.ignore_patterns('.git'))
    for path in target.rglob('*'):
        if path.is_symlink():
            origin = source / path.relative_to(target)
            owned = origin.resolve(strict=True)
            if not owned.is_relative_to(source):
                raise ValueError(f'unsafe source link {origin}: target is outside the harness')
            path.unlink()
            path.symlink_to(os.path.relpath(target / owned.relative_to(source), path.parent),
                            target_is_directory=owned.is_dir())


def strip_reviewed_bytecode(root: Path) -> None:
    """Exclude executable caches omitted by the reviewed submission identity.

    -B only prevents cache writes; Python still reads existing bytecode. Validate
    all links before deletion, never follow a cache alias while removing it, and
    let the subsequent full source hash refuse any broken reviewed source alias.
    """
    if root.is_symlink():
        raise ValueError('reviewed snapshot root must not be a symlink')
    owned_root = root.resolve(strict=True)
    paths = list(root.rglob('*'))
    for path in paths:
        if path.is_symlink() and not path.resolve(strict=True).is_relative_to(owned_root):
            raise ValueError('unsafe reviewed snapshot link: target is outside the harness')
    for path in sorted(paths, key=lambda value: len(value.parts), reverse=True):
        if path.name == '__pycache__':
            if path.is_symlink() or not path.is_dir():
                path.unlink()
            else:
                shutil.rmtree(path)
        elif path.name.endswith('.pyc') and not path.is_dir():
            path.unlink()


def harness_roots(platform_root: Path) -> list[Path]:
    """The directories a `--harness <name>` is looked up in, in order.

    Two roots, and the order is the point. `base_harness/` is the shipped set: the
    starting point a method improves and the adapters for external harnesses. The second
    root holds the platform's *test instruments* -- harnesses that exist to make one
    behaviour checkable and that nobody should measure:

      * `loop_plain` is the floor. Under the mock backend its model never answers, so
        every trial ends at the step cap with an empty answer and every task scores 0,
        deterministically and with no server. Fifteen test files use it as the harness
        whose behaviour the platform's own plumbing is asserted against.
      * `cli_agent` is the generic CLI wrapper: it executes one program and sees one exit
        code, so its trace cannot satisfy the adapter contract by construction. It is the
        subject of the tests that check the sandbox can drive a foreign CLI at all.

    They were in `base_harness/` until 2026-11-01, which made the shipped set contain two
    things that are not harnesses a study may start from -- and the manifest of one of them
    said `role: control` while the other looked exactly like a baseline. Moving the files
    is what removes that claim; keeping the *names* resolvable is what lets the tests go on
    asserting the platform rather than the fixtures.
    """
    root = Path(platform_root)
    return [root / "base_harness", root / "tests" / "fixtures" / "base_harness"]


def harness_by_name(name: str, roots: list[Path]) -> Path | None:
    """The first root that has a directory called `name`, or None."""
    for base in roots:
        candidate = base / name
        if candidate.is_dir():
            return candidate
    return None


def inspect_external_source(request: SourceRequest):
    source = Path(request.harness_path)
    if not source.is_absolute():
        raise SourceRefused('--harness-path must be an absolute directory')
    problems = check_harness(source)
    if problems:
        raise SourceRefused(*(f"refusing --harness-path: {problem['detail']}" for problem in problems))
    try:
        canonical = source.resolve(strict=True)
        if request.expected_harness_filesystem_sha256 is not None and source != canonical:
            raise ValueError("reviewed filesystem source must use its canonical non-symlink path")
        source = canonical
        receipt = source_identity(source)
        if (request.expected_harness_sha256 is not None
                and receipt['submission_sha256'] != request.expected_harness_sha256):
            raise ValueError('harness source differs from expected reviewed SHA256')
        provenance = {'path': str(source), 'submission_sha256': receipt['submission_sha256'],
                      'entries': receipt['entries']}
        name = manifest(source)['name']
    except (OSError, ValueError) as exc:
        raise SourceRefused(f'refusing --harness-path: {exc}') from exc
    run_id = request.run_id or f"external-{provenance['submission_sha256'][:12]}-{secrets.token_hex(4)}"
    for output_root in (request.work_root, request.runs_root):
        output = (Path(output_root) / run_id).resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise SourceRefused('refusing --harness-path: input and run/work output directories overlap')
    return SourceSelection(source, name, run_id, provenance=provenance, receipt=receipt)


def review_owned_source(request: SourceRequest, selection: SourceSelection,
                        lease: SourceLease) -> SourceSelection:
    source = selection.path
    try:
        expected_fs = request.expected_harness_filesystem_sha256
        if expected_fs is not None and filesystem_identity(source)['sha256'] != expected_fs:
            raise ValueError('harness filesystem differs from expected reviewed SHA256')
        snapshot = lease.reviewed_directory() / 'source'
        external_source_snapshot(source, snapshot)
        strip_reviewed_bytecode(snapshot)
        if expected_fs is not None and filesystem_identity(snapshot)['sha256'] != expected_fs:
            raise ValueError('owned harness filesystem differs from expected reviewed SHA256')
        if source_identity(snapshot)['submission_sha256'] != request.expected_harness_sha256:
            raise ValueError('owned harness snapshot differs from expected reviewed SHA256')
        if source_identity(source) != selection.receipt:
            raise ValueError('harness source changed while taking reviewed snapshot')
        if expected_fs is not None and filesystem_identity(source)['sha256'] != expected_fs:
            raise ValueError('harness filesystem changed while taking reviewed snapshot')
        name = manifest(snapshot)['name']
    except (OSError, ValueError, RuntimeError) as exc:
        raise SourceRefused(f'refusing --harness-path: {exc}') from exc
    return SourceSelection(snapshot, name, selection.run_id, provenance=selection.provenance,
                           receipt=selection.receipt, reviewed_original=source)


def select_source(request: SourceRequest, lease: SourceLease, *, roots: Callable[[], list[Path]],
                  named_source: Callable[[str], Path | None]) -> SourceSelection:
    if request.harness_path is not None:
        selection = inspect_external_source(request)
        return (review_owned_source(request, selection, lease)
                if request.expected_harness_sha256 is not None else selection)
    if request.side == "eval":
        try:
            source, round_no = _eval_source(request, Path(request.runs_root))
            origin = _eval_from_record(Path(request.runs_root), request.from_run, round_no, source=source)
        except SystemExit as exc:
            raise SourceRefused(str(exc)) from exc
        except (OSError, TypeError, ValueError) as exc:
            raise SourceRefused(f"refusing --from-run: {exc}") from exc
        return SourceSelection(source, request.harness, request.run_id, eval_from=origin)
    source = named_source(request.harness)
    if source is None:
        raise SourceRefused("no such harness: " + request.harness
                            + " (searched " + ", ".join(str(path) for path in roots()) + ")")
    return SourceSelection(source, request.harness, request.run_id)
