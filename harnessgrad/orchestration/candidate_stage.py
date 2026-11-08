"""Owned workspace staging, committed-source preparation and its evidence journal.

No namespace/resume publication or H0 scheduling lives here. Stage temporaries
close locally; the driver's source lease remains alive. Preparation admits only
committed source, and cancellation keeps the existing canonical cleanup owners.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

from eval.ckpt.git_state import content_sha, manifest
from eval.ckpt.source_tree import copy_rejected_harness, has_python_runtime
from eval.containers import external_runtime
from eval.observe.console import CONSOLE as console
from tools.validate_harness import check as check_harness, source_identity, filesystem_identity
from harnessgrad import candidate_runtime
from harnessgrad.loops.mode_a import _snapshot_round
from harnessgrad.records import _save_round_state, _write_run_meta
from . import source_lease


@dataclass(frozen=True)
class WorkspaceOps:
    """The composition root's two workspace mutation capabilities."""
    stage: Callable[[Path, Path], None]
    commit: Callable[[Path, str], str]


@dataclass(frozen=True)
class OwnedSource:
    path: Path
    provenance: dict | None
    receipt: dict | None
    reviewed_original: Path | None
    expected_sha256: str | None
    expected_filesystem_sha256: str | None
    resume_initial_source: bool = False


@dataclass(frozen=True)
class PreparationOptions:
    side: str
    from_run: str | None
    h0_from: str | None
    runs_root: Path
    binding_file: Path | None
    sandbox: bool


@dataclass(frozen=True)
class CommittedCandidate:
    fixed_source_sha: str
    binding: dict | None
    preparation: dict | None


class CandidateStageRefused(Exception):
    def __init__(self, message=None):
        super().__init__(message)
        self.message = message

    def report(self):
        if self.message is not None:
            print(self.message, file=sys.stderr)


def managed_python_intent(source: Path) -> bool:
    """Recognize malformed Python declarations without treating them as legacy."""
    try:
        declaration = json.loads((Path(source) / "harness.json").read_text())
    except (OSError, TypeError, ValueError):
        return False
    runtime = declaration.get("runtime") if isinstance(declaration, dict) else None
    return isinstance(runtime, dict) and runtime.get("kind") == "python"


def refuse_managed_source(run_dir: Path, source: Path, error, *, reporter=console) -> int:
    """Keep the original invalid source/refusal journal; never launch/install."""
    receipt = {"status": "invalid", "stage": "source declaration", "error": str(error), "seconds": 0.0}
    saved = candidate_runtime.record_preparation(run_dir, 0, receipt)
    if not (run_dir / "state_after_round0").exists():
        _save_round_state(run_dir, 0, source, rejected_candidate=True)
    elif saved is not None:
        try:
            copy_rejected_harness(source, Path(saved).with_suffix(".source"), ignore=shutil.ignore_patterns(".git"))
        except OSError as exc:
            reporter.note(f"could not retain refused source: {type(exc).__name__}", level="warn")
    if not (run_dir / "curve.jsonl").exists():
        _write_run_meta(run_dir, harness_preparation=receipt,
                        runtime_refusal={"stage": "harness source declaration", "detail": str(error)})
    reporter.note(f"refusing managed Python source: {error}", level="error")
    return 2


@dataclass(frozen=True)
class PreparationJournal:
    run_dir: Path
    run_id: str
    reporter: object = console

    def source(self, provenance):
        _write_run_meta(self.run_dir, harness_source=provenance)

    def reject_source(self, path, error):
        return refuse_managed_source(self.run_dir, path, error, reporter=self.reporter)

    def fixed_snapshot(self, work, fixed_sha):
        _save_round_state(self.run_dir, 0, work)
        _snapshot_round(self.run_dir, 0, self.run_dir / "state_after_round0",
                        run_id=self.run_id, harness_sha=fixed_sha)

    def failed(self, receipt, error):
        candidate_runtime.record_preparation(self.run_dir, 0, receipt)
        _write_run_meta(self.run_dir, harness_preparation=receipt,
                        runtime_refusal={"stage": "harness runtime preparation", "detail": str(error)})
        self.reporter.note(f"refusing harness runtime preparation: {error}", level="error")

    def record(self, preparation):
        candidate_runtime.record_preparation(self.run_dir, 0, preparation)

    def publish(self, prepared: CommittedCandidate):
        # The driver updates its argument bindings before this metadata write.
        _write_run_meta(self.run_dir, **candidate_runtime.fields(prepared.binding),
                        harness_preparation=prepared.preparation)

    def external(self, identities):
        _write_run_meta(self.run_dir, **external_runtime.fields(identities))


def _stage_external(source: OwnedSource, work: Path, ops: WorkspaceOps, journal: PreparationJournal):
    try:
        staged_receipt = source_identity(source.path) if source.resume_initial_source else source.receipt
        with tempfile.TemporaryDirectory(prefix='hg-source-', dir=work.parent) as staging:
            snapshot = Path(staging) / 'source'
            source_lease.external_source_snapshot(source.path, snapshot)
            if source.expected_sha256 is not None:
                source_lease.strip_reviewed_bytecode(snapshot)
            snapshot_identity = source_identity(snapshot)
            if source.expected_sha256 is not None and snapshot_identity['submission_sha256'] != source.expected_sha256:
                raise ValueError('owned harness snapshot differs from expected reviewed SHA256')
            if (source.expected_filesystem_sha256 is not None
                    and filesystem_identity(snapshot)['sha256'] != source.expected_filesystem_sha256):
                raise ValueError('owned harness filesystem differs before staging')
            generated_root = tuple(name for name in ('.git', '.gitignore', '.state')
                                   if not (snapshot / name).exists() and not (snapshot / name).is_symlink())
            ops.stage(snapshot, work)
            if (source.expected_filesystem_sha256 is not None
                    and filesystem_identity(work, generated_root=generated_root)['sha256'] != source.expected_filesystem_sha256):
                raise ValueError('staged harness filesystem differs from reviewed owned snapshot')
            if (source.expected_filesystem_sha256 is not None
                    and filesystem_identity(snapshot)['sha256'] != source.expected_filesystem_sha256):
                raise ValueError('owned harness filesystem changed during staging')
            if source_identity(snapshot) != snapshot_identity:
                raise ValueError('harness source snapshot changed during staging')
        if (source_identity(source.path) != staged_receipt
                or (source.reviewed_original is not None and source_identity(source.reviewed_original) != source.receipt)):
            raise ValueError('harness source changed during staging; use a stable input directory')
        if source.expected_filesystem_sha256 is not None:
            if (filesystem_identity(source.path)['sha256'] != source.expected_filesystem_sha256
                    or filesystem_identity(source.reviewed_original)['sha256'] != source.expected_filesystem_sha256):
                raise ValueError('harness filesystem changed during staging')
            source.provenance['filesystem'] = filesystem_identity(source.path)
        source.provenance['harness_sha'] = content_sha(work)
        journal.source(source.provenance)
        problems = check_harness(work)
        if problems:
            raise ValueError('; '.join(problem['detail'] for problem in problems))
    except (OSError, ValueError) as exc:
        raise CandidateStageRefused(f'refusing --harness-path: {exc}') from exc


def stage_owned_source(source: OwnedSource, work: Path, *, restored: bool,
                       ops: WorkspaceOps, journal: PreparationJournal) -> dict:
    if restored:
        pass
    elif source.provenance is not None:
        _stage_external(source, work, ops, journal)
    else:
        try:
            ops.stage(source.path, work)
        except (OSError, TypeError, ValueError) as exc:
            if not managed_python_intent(source.path):
                raise
            journal.reject_source(source.path, exc)
            raise CandidateStageRefused() from exc
    # This read was outside the source-stage refusal handlers in the driver.
    return manifest(work)


def donor_actor_binding(options: PreparationOptions, eval_from: dict | None):
    binding = None
    if options.binding_file is not None:
        binding = candidate_runtime.validate(json.loads(options.binding_file.read_text()))
    elif options.side == "eval" and eval_from:
        binding = eval_from.get("harness_runtime_identity")
    if options.h0_from:
        curve = options.runs_root / options.h0_from / "curve.jsonl"
        if curve.is_file():
            rows = [json.loads(line) for line in curve.read_text().splitlines() if line.strip()]
            binding = rows[0].get("harness_runtime_identity") if rows else None
    return binding


def prepare_committed_candidate(work: Path, options: PreparationOptions, *, envs, setups,
                                eval_from: dict | None, ops: WorkspaceOps,
                                journal: PreparationJournal) -> CommittedCandidate:
    fixed_sha = ops.commit(work, "fixed source before runtime preparation")
    if has_python_runtime(work):
        journal.fixed_snapshot(work, fixed_sha)
    started = time.monotonic()
    try:
        donor = donor_actor_binding(options, eval_from)
        preparation = candidate_runtime.prepare(work, binding=donor, envs=envs, setups=setups, sandbox=options.sandbox,
            **({'extend_images': True} if options.side == 'eval' and options.from_run
               and (donor or {}).get('kind') == 'python-placements' else {}))
        binding = None if preparation is None else preparation["identity"]
        binding = candidate_runtime.admit(work, binding, envs=envs, setups=setups, sandbox=options.sandbox)
    except (OSError, TypeError, ValueError) as exc:
        receipt = getattr(exc, "receipt", None) or {"status": "failed", "error": str(exc),
                                                   "seconds": round(time.monotonic() - started, 6)}
        journal.failed(receipt, exc)
        raise CandidateStageRefused() from exc
    if preparation is not None:
        journal.record(preparation)
    return CommittedCandidate(fixed_sha, binding, preparation)


def bind_external_candidate(work: Path, journal: PreparationJournal):
    try:
        identities = external_runtime.identities(work)
        journal.external(identities)
    except external_runtime.RuntimeUnavailable as exc:
        journal.reporter.note(f'refusing external actor runtime: {exc}', level='error')
        raise CandidateStageRefused() from exc
    return identities
