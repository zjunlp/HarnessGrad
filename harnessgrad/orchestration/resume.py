"""Read/admit/prepare a resume without publishing the original workspace.

The driver owns namespace, provider/checkpoint lifetimes and later readiness.
A ResumeLease owns only its sibling temporary directory. Publication is single
shot and preserves the existing backup/rename rollback, followed by seam writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import data.scaffolds.registry as datasets
from eval.ckpt.git_state import STATE_DIRNAME
from eval.containers import external_runtime
from eval.observe.console import CONSOLE as console
from harnessgrad import candidate_runtime, dataset_runtime_identity, scoring_identity
from .candidate_stage import WorkspaceOps
from .historical_evidence import reused_measurement, read_progress_results, require_verification_context


class ResumeRefused(RuntimeError):
    """A resume the recorded evidence cannot honour."""


class ResumeNothingToDo(RuntimeError):
    """Every round requested by the budget already exists."""


@dataclass(frozen=True)
class ResumeIdentities:
    scoring: dict | None
    dataset: dict | None
    verification: dict | None


@dataclass(frozen=True)
class ResumeRequest:
    rounds: int
    identities: ResumeIdentities
    across_platforms: str | None = None
    initial_source: Path | None = None
    candidate_source: Path | None = None


@dataclass(frozen=True)
class PlatformEvidence:
    content_sha: Callable[[], str]
    git_sha: Callable[[], str | None]


@dataclass(frozen=True)
class AdmissionRequest:
    side: str
    selected_ids: list[str]
    identities: ResumeIdentities
    setups: dict
    envs: dict
    sandbox: bool


@dataclass(frozen=True)
class ResumeAdmission:
    metadata: dict
    restored: dict | None
    initial_source: Path | None
    candidate_source: Path | None


@dataclass(frozen=True)
class TrainOptions:
    harness: str
    dataset: str
    sampling: str
    trials: int
    seed: str | None


def read_metadata(run_dir):
    path = run_dir / "run_meta.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def _admit_initial_points(run_dir, metadata, current):
    scoring_identity.require_same(metadata.get("scoring_identity"), current.scoring)
    require_verification_context(metadata.get("verification_context"), current.verification)
    dataset_runtime_identity.require_same(metadata.get("dataset_runtime_identity"), current.dataset)
    points = []
    path = run_dir / "curve.jsonl"
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                point = json.loads(line)
                points.append(point)
                require_verification_context(point.get("verification_context"), current.verification,
                    list((point.get("sampling") or {}).get("task_ids") or (point.get("per_task") or {})))
                dataset_runtime_identity.require_same(point.get("dataset_runtime_identity"), current.dataset)
    return points


def _retain_checkpoint_sources(run_dir, points, restored, side, hold):
    if restored is None:
        return None, None
    managed = (restored["initial"] is not None or restored["current"] is not None
               or any(point.get("harness_runtime_identity") is not None for point in points))
    if side != "train" or not points or not managed:
        return None, None
    initial = hold(candidate_runtime.checkpoint_source(run_dir, points[0]))
    last = max(points, key=lambda point: int(point.get("round") or 0))
    current = hold(candidate_runtime.checkpoint_source(run_dir, last))
    return initial, current


def _admit_eval_progress(run_dir, request, source, restored):
    prior = read_progress_results(run_dir)
    expected = dataset_runtime_identity.project_many(request.identities.dataset, prior)
    current = None if restored is None else restored["current"]
    for tid, record in prior.items():
        require_verification_context(record.get("verification_context"), request.identities.verification, [tid])
        if (request.identities.dataset is not None or record.get("dataset_runtime_identity") is not None) and tid not in request.selected_ids:
            raise ValueError("dataset_runtime_identity progress names an unselected task")
        dataset_runtime_identity.require_same(record.get("dataset_runtime_identity"), expected[tid])
        candidate_runtime.require_same(record.get("harness_runtime_identity"), current)
        external_runtime.admit_reuse(source, record, services=True)


def frozen_split_changed(metadata: dict, current_protocol: dict | None) -> bool:
    saved = metadata.get('split_protocol')
    if saved is not None or current_protocol is not None:
        return (saved is None or current_protocol is None
                or saved['file_sha256'] != current_protocol['file_sha256']
                or saved.get('task_content_sha256') != current_protocol.get('task_content_sha256'))
    return False


def admit_resume_bindings(request: AdmissionRequest, run_dir: Path, source: Path, *, metadata: dict, hold) -> ResumeAdmission:
    points = _admit_initial_points(run_dir, metadata, request.identities)
    restored = candidate_runtime.validate_resume(run_dir, metadata, points, source,
        envs=request.envs, setups=request.setups, sandbox=request.sandbox)
    initial, current = _retain_checkpoint_sources(run_dir, points, restored, request.side, hold)
    admitted_source = initial if initial is not None else source
    for point in points:
        external_runtime.admit_checkpoint(run_dir, point, fallback=admitted_source)
    if request.side == "eval":
        _admit_eval_progress(run_dir, request, admitted_source, restored)
    return ResumeAdmission(metadata, restored, initial, current)


def admit_train_options(options: TrainOptions, metadata: dict):
    for field, value in (("harness", options.harness), ("dataset", options.dataset),
                         ("sampling_policy", options.sampling), ("trials", options.trials)):
        if metadata.get(field) is not None and metadata[field] != value:
            raise ResumeRefused(f"{field} differs from the original run")
    seed = ((metadata.get("config") or {}).get("run") or {}).get("seed")
    if seed and options.seed is not None and options.seed != seed:
        raise ResumeRefused("requested seed differs from the original seed")
    return seed if seed else None


@dataclass(frozen=True)
class ResumeEvidence:
    points: list[dict]
    metadata: dict


def read_resume_evidence(run_dir: Path) -> ResumeEvidence:
    path = run_dir / "curve.jsonl"
    if not path.is_file():
        raise ResumeRefused(f"{run_dir.name} has no curve.jsonl; there is nothing to continue")
    points = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not points:
        raise ResumeRefused(f"{run_dir.name}/curve.jsonl holds no measured point")
    return ResumeEvidence(points, read_metadata(run_dir))


def admit_train_evidence(request, evidence, run_dir, work):
    current = request.identities
    try:
        dataset_runtime_identity.admit(current.dataset)
        dataset_runtime_identity.require_same(evidence.metadata.get("dataset_runtime_identity"), current.dataset)
        scoring_identity.require_same(evidence.metadata.get("scoring_identity"), current.scoring)
        require_verification_context(evidence.metadata.get("verification_context"), current.verification)
        for point in evidence.points:
            require_verification_context(point.get("verification_context"), current.verification,
                list((point.get("sampling") or {}).get("task_ids") or (point.get("per_task") or {})))
            dataset_runtime_identity.require_same(point.get("dataset_runtime_identity"), current.dataset)
            scoring_identity.require_same(point.get("scoring_identity"), current.scoring)
            candidate_runtime.checkpoint_binding(run_dir, point)
            external_runtime.admit_checkpoint(run_dir, point, fallback=work)
    except (OSError, TypeError, ValueError) as exc:
        raise ResumeRefused(str(exc)) from exc


def admit_split_and_platform(request, metadata, split, run_dir, platform):
    recorded = metadata.get("split_sha")
    current = datasets.split_sha(split) if split else None
    if recorded and current and recorded != current:
        raise ResumeRefused(f"the run was measured on split_sha {recorded[:12]}, this invocation on "
                            f"{current[:12]}; the old points are not the same task set")
    before = metadata.get("platform_content_sha")
    if not before:
        return None
    after = platform.content_sha()
    if before == after:
        return None
    reason = (request.across_platforms or "").strip()
    if not reason:
        raise ResumeRefused(
            f"the platform content changed since this run started "
            f"({before[:12]} -> {after[:12]}); continuing would "
            f"stitch two code versions into one curve. If you reviewed the diff and "
            f"the change cannot move a measurement, pass "
            f'`--resume-across-platforms "<what you checked>"` and it will be '
            f"recorded under {run_dir.name}/platform-seams/")
    return before, after, reason, metadata.get("platform") or {}


def select_resume_state(request, points, run_dir):
    last = max(int(point.get("round") or 0) for point in points)
    if last >= request.rounds:
        raise ResumeNothingToDo(f"round {last} of a {request.rounds}-round budget is already measured")
    view = run_dir / f"state_after_round{last}"
    if not view.is_dir() and request.candidate_source is not None:
        view = Path(request.candidate_source)
    if not view.is_dir():
        raise ResumeRefused(f"{run_dir.name} has no state_after_round{last}/; the state round {last} scored "
                            f"was not kept, so there is nothing to continue from")
    return last, view


def restore_checkpoint_tree(request, run_dir, first_point, last, view, work, ops):
    """Recreate the base Git object, then overlay and commit the retained last state."""
    h0_sha = (first_point.get("identity") or {}).get("harness_sha")
    base_state = run_dir / "state_after_round0"
    if not base_state.is_dir() and request.initial_source is not None:
        base_state = Path(request.initial_source)
    restored_base = None
    if h0_sha and base_state.is_dir():
        ops.stage(base_state, work)
        restored_base = ops.commit(work, "resume: base")
        if restored_base != h0_sha:
            raise ResumeRefused(
                f"{run_dir.name}/state_after_round0/ hashes to {restored_base[:12]} but the "
                f"run's round-0 point scored {h0_sha[:12]}; the saved base is not the state "
                f"the curve published, so continuing would attach new rounds to a different "
                f"harness")
        # Keep `.git` (that is where the base object now lives) and put the state we
        # continue from on top of it, exactly as the round loop does after measuring.
        for child in list(work.iterdir()):
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        shutil.copytree(view, work, dirs_exist_ok=True, symlinks=True)
        # `stage_workspace` creates this when it initialises a repo, and the copy above can
        # remove it on a state saved by an older platform. The sandbox mounts a writable
        # tmpfs over it, and a mount target has to exist.
        (work / STATE_DIRNAME).mkdir(exist_ok=True)
        staged_sha = ops.commit(work, f"resume: round {last}")
    else:
        ops.stage(view, work)
        staged_sha = ops.commit(work, f"resume: round {last}")
        if h0_sha and not tree_present(work, h0_sha):
            raise ResumeRefused(
                f"{run_dir.name} keeps no state_after_round0/, and the tree its round-0 point "
                f"scored ({h0_sha[:12]}) is not in this repository's object store; the run "
                f"cannot be restored to the state that score belongs to")

    return h0_sha, staged_sha, restored_base


def admit_restored_candidate(run_dir, existing, last, work, staged_sha):
    recorded_sha = (next(point for point in existing if int(point.get("round") or 0) == last).get("identity") or {}).get("harness_sha")
    # **Restaging is only a resume if it reproduces the state the last score belongs to.**
    # The docstring above promised this check and the code did not make it; a saved state
    # that hashes differently would have continued the curve from a tree no published point
    # describes -- two code versions in one curve, one level below the `platform_content_sha`
    # check above.
    if recorded_sha and staged_sha != recorded_sha:
        raise ResumeRefused(
            f"state_after_round{last}/ hashes to {staged_sha[:12]} but round {last} was "
            f"measured on {recorded_sha[:12]}; the saved state is not the state that score "
            f"belongs to")
    last_point = max(existing, key=lambda point: int(point.get("round") or 0))
    current_binding = candidate_runtime.checkpoint_binding(run_dir, last_point)
    if not last_point.get("unmeasured"):
        candidate_runtime.admit(work, current_binding)
    return recorded_sha, current_binding


def restore_train_workspace(request, run_dir, existing, last, view, work, ops):
    h0_sha, staged_sha, restored_base = restore_checkpoint_tree(
        request, run_dir, existing[0], last, view, work, ops)
    base = reused_measurement(run_dir, existing[0], existing)
    recorded_sha, binding = admit_restored_candidate(run_dir, existing, last, work, staged_sha)
    return {"existing": existing, "last": last, "base": base, "h0_sha": h0_sha or staged_sha,
            "recorded_sha": recorded_sha, "staged_sha": staged_sha, "restored_base": restored_base}, binding



def record_platform_seam(run_dir: Path, before: str, after: str, reason: str,
                          platform_before: dict, *, platform: PlatformEvidence, reporter=console) -> dict:
    """Write the approval that lets this curve cross a platform change, beside the run.

    The guard's answer is "no" by default; this is the record of a human saying "yes, and
    here is what I checked". Two properties matter more than the wording:

      * **it is a file next to the curve, not a field inside `run_meta`.** `run_meta` is
        rewritten as the run continues (and `platform_content_sha` there tracks the tree the
        run is on *now*), so a seam kept only there would be overwritten by the very resume it
        authorised. `platform-seams/<utc>-approved.json` is written once and never touched.
      * **both sides are named.** The sha the curve started on, the sha it continues on, both
        git shas, and the sentence the operator wrote -- a reader can re-do the review instead
        of trusting it.
    """
    import datetime as _dt
    folder = run_dir / "platform-seams"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc)
    record = {
        "kind": "platform seam approved for a resume",
        "approved_utc": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason_reviewed": reason,
        "platform_content_sha": {"before": before, "after": after},
        "git_sha": {"before": platform_before.get("git_sha"), "after": platform.git_sha()},
        "how_to_recheck": ("`git log <before>..<after>` tells you what changed; the review is "
                           "the claim that none of it can move a measured round"),
    }
    path = folder / f"{stamp:%Y%m%dT%H%M%SZ}-approved.json"
    path.write_text(json.dumps(record, indent=1) + "\n")
    reporter.note(
        f"resume across a platform change: {before[:12]} -> {after[:12]}; approved and recorded "
        f"in {path.relative_to(run_dir)}", level="warn")
    return record


def tree_present(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{tree}}"],
                          capture_output=True).returncode == 0


class ResumeLease:
    """One owned staging directory and one publication attempt; no original-data cleanup."""
    def __init__(self, temporary, work, run_dir, seam, metadata, state, binding, platform, reporter):
        self._temporary = temporary
        self.work, self.run_dir, self.seam = work, run_dir, seam
        self.metadata, self.state, self.current_binding = metadata, state, binding
        self.recorded_platform = metadata.get("platform_content_sha")
        self.platform, self.reporter = platform, reporter
        self._consumed = False

    def close(self):
        self._temporary.cleanup()

    def publish(self):
        if self._consumed:
            raise RuntimeError("resume publication already attempted")
        self._consumed = True
        staging = Path(self._temporary.name)
        backup = staging / "previous-workspace"
        if self.work.exists() or self.work.is_symlink():
            self.work.rename(backup)
        try:
            (staging / "workspace").rename(self.work)
        except BaseException:
            if backup.exists():
                backup.rename(self.work)
            raise
        self.close()
        if self.seam is not None:
            record_platform_seam(self.run_dir, *self.seam, platform=self.platform, reporter=self.reporter)
            if not self.metadata.get("platform_content_sha_started"):
                self.metadata["platform_content_sha_started"] = self.recorded_platform
                (self.run_dir / "run_meta.json").write_text(json.dumps(self.metadata, indent=1))


def prepare_resume_workspace_lease(request: ResumeRequest, run_dir: Path, split, work: Path, *,
                                   ops: WorkspaceOps, platform: PlatformEvidence, reporter=console) -> ResumeLease:
    evidence = read_resume_evidence(run_dir)
    admit_train_evidence(request, evidence, run_dir, work)
    seam = admit_split_and_platform(request, evidence.metadata, split, run_dir, platform)
    last, view = select_resume_state(request, evidence.points, run_dir)
    work.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix=".resume-", dir=work.parent)
    try:
        state, binding = restore_train_workspace(request, run_dir, evidence.points, last, view,
                                                 Path(temporary.name) / "workspace", ops)
        task_ids = list((evidence.points[0].get("sampling") or {}).get("task_ids") or [])
        if not task_ids:
            raise ResumeRefused("the run's round 0 recorded no task set")
        return ResumeLease(temporary, work, run_dir, seam, evidence.metadata, state, binding, platform, reporter)
    except BaseException:
        temporary.cleanup()
        raise
