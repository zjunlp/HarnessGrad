"""Small, offline trusted runner facade.

This module is deliberately a local adapter.  It does not expose an HTTP endpoint,
invoke a model, or change the legacy method stdin/stdout protocol.  A caller supplies
the frozen parent trees, platform-issued capabilities, and (for tests) a runner whose
results are treated as evaluator facts.  All records are written beneath ``run_dir``
and requests are idempotent by operation and key.
"""
from __future__ import annotations

from eval.ckpt.source_tree import copy_harness

import hashlib
import json
import math
import os
import shutil
import stat
from pathlib import Path
from typing import Any, Mapping

from eval.run import candidate as candidate_checks


class TrustedRunnerError(ValueError):
    """A request violated a trusted-side contract."""


class IdempotencyConflict(TrustedRunnerError):
    """The same idempotency key was reused for different bytes."""


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(value if isinstance(value, bytes) else _json(value)).hexdigest()


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _stable(value: Any) -> Any:
    """Return the same insertion order as a persisted canonical JSON record."""
    return json.loads(_json(value))


def _id(prefix: str, value: Any) -> str:
    return f"{prefix}-{hashlib.sha256(_json(value)).hexdigest()[:20]}"


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise TrustedRunnerError(f"invalid_{label}")
    return value


def _tree_sha(root: Path) -> str:
    """Hash path, mode, symlink target, and bytes in a deterministic order."""
    entries: list[dict[str, Any]] = []
    root = Path(root)
    ignored = {".git", ".state", "_harnessgrad", "__pycache__"}
    from eval.ckpt.source_tree import has_bun_runtime
    if has_bun_runtime(root):
        ignored.update({"node_modules", ".opencode-state"})
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in ignored for part in rel.parts) or path.is_socket():
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            entries.append({"path": rel.as_posix(), "mode": mode, "link": os.readlink(path)})
        elif path.is_file():
            entries.append({"path": rel.as_posix(), "mode": mode,
                            "bytes": hashlib.sha256(path.read_bytes()).hexdigest()})
        elif path.is_dir():
            entries.append({"path": rel.as_posix(), "mode": mode, "dir": True})
    return _sha(entries)


def _safe_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise TrustedRunnerError("proposal_path_outside_workspace")
    target = (root / name).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise TrustedRunnerError("proposal_path_outside_workspace") from exc
    if target == root.resolve():
        raise TrustedRunnerError("proposal_path_outside_workspace")
    return target


def _canonical_parent(ref: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"artifact_id", "role", "snapshot_sha", "root"}
    if set(ref) - allowed:
        raise TrustedRunnerError("parent_ref_additional_property")
    role = ref.get("role")
    snap = _require_sha(ref.get("snapshot_sha"), "parent_snapshot_sha")
    if role == "root":
        if ref.get("root") is not True or ref.get("artifact_id") is not None:
            raise TrustedRunnerError("invalid_root_parent_ref")
        return {"role": "root", "root": True, "snapshot_sha": snap}
    if role not in {"base", "donor", "peer", "crossover", "evidence"} or not ref.get("artifact_id"):
        raise TrustedRunnerError("invalid_parent_ref")
    return {"artifact_id": str(ref["artifact_id"]), "role": role, "snapshot_sha": snap}


def _parent_set(parents: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    canonical = [_canonical_parent(p) for p in parents]
    if not canonical:
        raise TrustedRunnerError("parents_required")
    if len({_json(p) for p in canonical}) != len(canonical):
        raise TrustedRunnerError("duplicate_parent")
    canonical.sort(key=lambda p: _json(p))
    return canonical, _sha(canonical)


class TrustedRunnerFacade:
    """A deterministic local implementation of Task23's minimal contract.

    ``parent_snapshots`` maps trusted snapshot hashes to directories.  ``runner`` may
    be a callable or expose ``run``/``evaluate``; it receives ``(candidate_path,
    task, trial_index)``.  A runner is never allowed to create selection or deployment
    facts: those are made solely by this class.
    """

    def __init__(self, run_dir: Path | str, *, parent_snapshots: Mapping[str, Path | str] | None = None,
                 runner: Any = None, capabilities: Mapping[str, Mapping[str, Any]] | None = None,
                 current_snapshot_sha: str | None = None, root_path: Path | str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir = self.run_dir / "workspaces"
        self.work_dir.mkdir(exist_ok=True)
        self.parent_snapshots = {str(k): Path(v) for k, v in (parent_snapshots or {}).items()}
        self.root_path = Path(root_path) if root_path is not None else None
        self.runner = runner
        self.capabilities = {str(k): _copy(v) for k, v in (capabilities or {}).items()}
        self.candidates: dict[str, dict[str, Any]] = {}
        self.evaluations: dict[str, dict[str, Any]] = {}
        self._evaluation_scopes: dict[str, str] = {}
        self.selections: dict[str, dict[str, Any]] = {}
        self.deployments: dict[str, dict[str, Any]] = {}
        self.current_snapshot_sha = current_snapshot_sha
        self._load_records()

    def _load_records(self) -> None:
        for name, target in (("candidates", self.candidates), ("evaluations", self.evaluations),
                             ("selections", self.selections), ("deployments", self.deployments)):
            directory = self.run_dir / name
            directory.mkdir(exist_ok=True)
            for path in sorted(directory.glob("*.json")):
                try:
                    value = json.loads(path.read_text())
                    key = value.get({"candidates": "candidate_id", "evaluations": "evaluation_id",
                                     "selections": "selection_id", "deployments": "deployment_id"}[name])
                    if key:
                        target[key] = value
                        if name == "evaluations":
                            self._evaluation_scopes[key] = "final_report_only" if value.get("task_set", {}).get("visibility") == "final_exam" else value.get("task_set", {}).get("visibility", "study")
                except (OSError, ValueError):
                    continue
        state = self.run_dir / "state.json"
        if state.exists():
            try:
                self.current_snapshot_sha = json.loads(state.read_text()).get("current_snapshot_sha")
            except (OSError, ValueError):
                pass

    def issue_capability(self, capability_id: str, task_set: Mapping[str, Any]) -> dict[str, Any]:
        value = _copy(dict(task_set))
        if value.get("capability_id") != capability_id:
            raise TrustedRunnerError("capability_id_mismatch")
        visibility = value.get("visibility")
        final = visibility == "final_exam"
        if final:
            if value.get("adaptation_visible") is not False:
                raise TrustedRunnerError("final_capability_must_be_private")
        elif visibility not in {"study", "validation"} or value.get("adaptation_visible") is not True:
            raise TrustedRunnerError("capability_not_adaptation_visible")
        tasks = value.get("task_ids")
        if not isinstance(tasks, list) or not tasks or len(set(tasks)) != len(tasks):
            raise TrustedRunnerError("invalid_capability_tasks")
        self.capabilities[capability_id] = value
        return _copy(value)

    def _idem(self, operation: str, key: str, request: Mapping[str, Any]) -> dict[str, Any] | None:
        if not key:
            raise TrustedRunnerError("idempotency_key_required")
        path = self.run_dir / "idempotency" / operation
        path.mkdir(parents=True, exist_ok=True)
        record = path / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        digest = _sha(request)
        if not record.exists():
            return None
        old = json.loads(record.read_text())
        if old.get("request_sha") != digest:
            raise IdempotencyConflict("idempotency_conflict")
        return old["response"]

    def _save_idem(self, operation: str, key: str, request: Mapping[str, Any], response: Mapping[str, Any]) -> None:
        path = self.run_dir / "idempotency" / operation
        path.mkdir(parents=True, exist_ok=True)
        record = path / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        if record.exists():
            return
        tmp = record.with_suffix(".tmp")
        tmp.write_bytes(_json({"request_sha": _sha(request), "response": response}))
        os.replace(tmp, record)

    def _save(self, category: str, key: str, value: Mapping[str, Any]) -> None:
        directory = self.run_dir / category
        directory.mkdir(exist_ok=True)
        path = directory / f"{key}.json"
        if path.exists():
            if json.loads(path.read_text()) != value:
                raise TrustedRunnerError(f"immutable_{category}_conflict")
            return
        path.write_bytes(_json(value))

    def _proposal_bytes(self, proposal: Mapping[str, Any]) -> bytes:
        artifact = proposal.get("artifact")
        if not isinstance(artifact, Mapping):
            raise TrustedRunnerError("proposal_artifact_required")
        if isinstance(artifact.get("content"), str):
            return artifact["content"].encode()
        payload = {k: proposal[k] for k in ("edits", "files", "tree", "config") if k in proposal}
        if not payload:
            payload = {k: artifact[k] for k in ("content", "edits", "files", "tree", "config") if k in artifact}
        if not payload:
            # A typed, immutable proposal may be a reference to bytes stored by a
            # higher layer.  The reference envelope is still canonical input; the
            # caller's declared hash is checked against it, never accepted blindly.
            payload = {k: artifact.get(k) for k in ("artifact_id", "kind", "producer", "visibility")}
        return _json(payload)

    def _apply(self, workspace: Path, proposal: Mapping[str, Any]) -> None:
        artifact = proposal["artifact"]
        kind = proposal.get("proposal_kind") or artifact.get("kind")
        edits = proposal.get("edits", artifact.get("edits"))
        files = proposal.get("files", artifact.get("files"))
        if files is not None:
            if not isinstance(files, Mapping):
                raise TrustedRunnerError("invalid_tree_proposal")
            edits = [{"path": p, "content": content} for p, content in files.items()]
        if edits is None and isinstance(proposal.get("tree"), Mapping):
            edits = [{"path": p, "content": content} for p, content in proposal["tree"].items()]
        if edits is None and kind in {"tree", "full_file", "patch", "config", "prompt", "skill", "memory", "policy"}:
            raise TrustedRunnerError("proposal_bytes_unavailable")
        if edits is None:
            return
        if not isinstance(edits, list):
            raise TrustedRunnerError("invalid_proposal_edits")
        for edit in edits:
            if not isinstance(edit, Mapping) or not isinstance(edit.get("path"), str):
                raise TrustedRunnerError("invalid_proposal_edit")
            target = _safe_path(workspace, edit["path"])
            if edit.get("delete"):
                if target.exists() or target.is_symlink():
                    if target.is_dir() and not target.is_symlink(): shutil.rmtree(target)
                    else: target.unlink()
                continue
            if not isinstance(edit.get("content"), str):
                raise TrustedRunnerError("proposal_edit_content_required")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit["content"], encoding="utf-8")

    def build_validate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request = _copy(dict(request))
        if request.get("operation", "build_validate") != "build_validate":
            raise TrustedRunnerError("operation_mismatch")
        request["operation"] = "build_validate"
        cached = self._idem("build_validate", request.get("idempotency_key", ""), request)
        if cached is not None:
            return cached
        parents, computed = _parent_set(request.get("parents", []))
        if request.get("parent_set_sha") != computed:
            raise TrustedRunnerError("parent_set_sha_mismatch")
        by_snapshot = {p["snapshot_sha"]: p for p in parents}
        used: set[str] = set()
        proposals = request.get("proposals")
        if not isinstance(proposals, list) or not proposals:
            raise TrustedRunnerError("proposals_required")
        if len({p.get("candidate_id") for p in proposals}) != len(proposals):
            raise TrustedRunnerError("duplicate_candidate_id")
        refs: list[dict[str, Any]] = []
        validations: list[dict[str, Any]] = []
        valid_ids: list[str] = []
        for proposal in proposals:
            cid = proposal.get("candidate_id") if isinstance(proposal, Mapping) else None
            if not cid or not isinstance(proposal, Mapping):
                raise TrustedRunnerError("candidate_id_required")
            if cid in self.candidates:
                raise TrustedRunnerError("candidate_id_immutable")
            lineage = proposal.get("lineage")
            if not isinstance(lineage, list) or not lineage:
                raise TrustedRunnerError(f"{cid}:lineage_required")
            lineage_c = [_canonical_parent(x) for x in lineage]
            lineage_snaps = {x["snapshot_sha"] for x in lineage_c}
            root = proposal.get("root") is True
            base = proposal.get("base_snapshot_sha")
            if root:
                if base is not None or not any(x["role"] == "root" for x in lineage_c):
                    raise TrustedRunnerError(f"{cid}:root_origin_required")
            else:
                if base not in by_snapshot:
                    raise TrustedRunnerError(f"{cid}:base_snapshot_not_in_set")
                if not any(x["role"] == "base" and x["snapshot_sha"] == base for x in lineage_c):
                    raise TrustedRunnerError(f"{cid}:base_lineage_required")
                used.add(base)
            if not all(_json(x) in {_json(p) for p in parents} for x in lineage_c):
                raise TrustedRunnerError(f"{cid}:lineage_parent_not_in_set")
            used.update(lineage_snaps)
            artifact = proposal.get("artifact")
            if not isinstance(artifact, Mapping) or not artifact.get("artifact_id"):
                raise TrustedRunnerError(f"{cid}:proposal_artifact_required")
            actual_content_sha = _sha(self._proposal_bytes(proposal))
            declared = artifact.get("content_sha")
            if declared != actual_content_sha:
                raise TrustedRunnerError(f"{cid}:proposal_content_sha_mismatch")
            source = self.parent_snapshots.get(base) if base is not None else None
            if source is None and root:
                source = self.parent_snapshots.get(next(x["snapshot_sha"] for x in lineage_c if x["role"] == "root"), self.root_path)
            workspace = self.work_dir / str(cid)
            status = "valid"
            problems: list[str] = []
            try:
                if source is None or not Path(source).is_dir():
                    raise TrustedRunnerError("parent_workspace_unavailable")
                expected_parent_sha = base
                if expected_parent_sha is None:
                    expected_parent_sha = next(x["snapshot_sha"] for x in lineage_c if x["role"] == "root")
                if _tree_sha(Path(source)) != expected_parent_sha:
                    raise TrustedRunnerError("parent_snapshot_sha_mismatch")
                if workspace.exists(): shutil.rmtree(workspace)
                copy_harness(source, workspace, symlinks=True)
                self._apply(workspace, proposal)
                base_manifest = None
                try:
                    base_manifest = json.loads((Path(source) / "harness.json").read_text())
                except (OSError, ValueError):
                    pass
                problems = candidate_checks.validate(workspace, base_manifest=base_manifest)
                if problems:
                    status = "invalid"
            except TrustedRunnerError as exc:
                status, problems = "invalid", [str(exc)]
            snap = _tree_sha(workspace) if workspace.exists() else _sha({"candidate_id": cid, "error": problems})
            result = {"candidate_id": cid, "status": status, "checks": [
                {"name": "candidate", "status": "passed" if status == "valid" else "failed",
                 "detail": "; ".join(problems)}], "candidate_snapshot_sha": snap}
            if status != "valid":
                result["failure"] = {"class": "invalid_patch", "attribution": "subject", "detail": "; ".join(problems)}
            validation_artifact = {"artifact_id": f"validation-{cid}", "kind": "validation",
                                  "content_sha": _sha(result), "producer": {"role": "framework", "id": "trusted-runner"},
                                  "visibility": "framework"}
            result["validation_artifact"] = validation_artifact
            validations.append(result)
            ref = {"candidate_id": cid, "artifact_id": artifact["artifact_id"],
                   "candidate_snapshot_sha": snap, "immutable": True, "lineage": lineage_c}
            if root: ref["root"] = True
            else: ref["base_snapshot_sha"] = base
            refs.append(ref)
            if status == "valid":
                valid_ids.append(cid)
                self.candidates[cid] = {**ref, "workspace": str(workspace), "validation": result,
                                        "iteration_id": request.get("iteration_id"), "parent_set_sha": computed}
                self._save("candidates", cid, self.candidates[cid])
        for parent in parents:
            if parent["role"] != "evidence" and parent["snapshot_sha"] not in used:
                raise TrustedRunnerError("declared_parent_unused")
        response = {"operation": "build_validate", "operation_id": _id("op", request),
                    "parent_set_sha": computed, "parents": parents, "candidates": refs,
                    "valid_candidate_ids": valid_ids, "validation": validations}
        response = _stable(response)
        self._save_idem("build_validate", request["idempotency_key"], request, response)
        return response

    def _capability(self, task_set: Mapping[str, Any], *, final: bool = False) -> dict[str, Any]:
        if not isinstance(task_set, Mapping):
            raise TrustedRunnerError("task_set_capability_required")
        cap_id = task_set.get("capability_id")
        if not cap_id or cap_id not in self.capabilities:
            raise TrustedRunnerError("unknown_task_capability")
        cap = self.capabilities[cap_id]
        if _json(cap) != _json(dict(task_set)):
            raise TrustedRunnerError("task_capability_payload_mismatch")
        visibility = cap.get("visibility")
        if final:
            if visibility != "final_exam" or cap.get("adaptation_visible") is not False:
                raise TrustedRunnerError("final_report_capability_required")
        elif visibility not in {"study", "validation"} or cap.get("adaptation_visible") is not True:
            raise TrustedRunnerError("adaptation_capability_required")
        return cap

    def _run_trial(self, workspace: Path, task: Mapping[str, Any], trial: int) -> dict[str, Any]:
        if self.runner is None:
            raise TrustedRunnerError("runner_required_for_evaluation")
        def invoke(fn):
            try:
                return fn(workspace, dict(task), trial)
            except TypeError as exc:
                # A two-argument fake runner is useful for deterministic tests and
                # mirrors the old ``run_one(repo, task)`` seam.
                try:
                    return fn(workspace, dict(task))
                except TypeError:
                    raise exc
        if callable(self.runner):
            result = invoke(self.runner)
        elif hasattr(self.runner, "evaluate"):
            result = invoke(self.runner.evaluate)
        elif hasattr(self.runner, "run"):
            result = invoke(self.runner.run)
        else:
            raise TrustedRunnerError("invalid_runner")
        if not isinstance(result, Mapping):
            raise TrustedRunnerError("runner_result_must_be_object")
        out = dict(result)
        verdict = out.get("verdict")
        if isinstance(verdict, Mapping) and "score" not in out and "score" in verdict:
            out["score"] = verdict["score"]
        if out.get("invalid") and "status" not in result:
            out["status"] = "infrastructure_failed"
        status = out.get("status") or ("measured" if "score" in out else "subject_failed")
        if status not in {"measured", "subject_failed", "infrastructure_failed", "cancelled"}:
            raise TrustedRunnerError("invalid_trial_status")
        out["status"] = status
        if status == "infrastructure_failed":
            out.pop("score", None)
        if status in {"subject_failed", "infrastructure_failed", "cancelled"} and not out.get("failure"):
            out["failure"] = {"class": "subject_error" if status == "subject_failed" else
                               ("infrastructure_unavailable" if status == "infrastructure_failed" else "cancelled"),
                               "attribution": "subject" if status == "subject_failed" else "infrastructure"}
        if out.get("failure"):
            failure = dict(out["failure"])
            out["failure"] = {k: v for k, v in failure.items()
                               if k in {"class", "attribution", "stage", "detail", "retryable"}}
        cost = dict(out.get("cost") or {"accounting_revision": "offline-v1", "known": False})
        cost.setdefault("accounting_revision", "offline-v1")
        out["cost"] = {k: v for k, v in cost.items()
                        if k in {"accounting_revision", "input_tokens", "output_tokens",
                                 "model_calls", "wall_clock_ms", "usd_micros", "known"}}
        out["trial_id"] = out.get("trial_id") or _id("trial", {"task": task.get("task_id"), "trial": trial})
        out["score_kind"] = out.get("score_kind") or "study"
        if "trace" in out and "trace_sha" not in out:
            out["trace_sha"] = _sha(str(out["trace"]))
        # Runner-private diagnostics cannot become platform facts merely by being
        # returned in the fake seam. Keep the contract fields and the trusted hash.
        for private in ("trace", "verdict", "invalid", "deployment", "deployed_candidate_ids"):
            out.pop(private, None)
        return out

    def evaluate_candidates(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request = _copy(dict(request))
        request.setdefault("operation", "evaluate_candidates")
        cached = self._idem("evaluate_candidates", request.get("idempotency_key", ""), request)
        if cached is not None:
            return cached
        scope = request.get("evaluation_scope", "study")
        if scope == "final_report_only":
            cap_id = request.get("final_report_capability_id")
            cap = self._capability({**request.get("final_task_set", {}), "capability_id": cap_id}, final=True) \
                if request.get("final_task_set") else self.capabilities.get(cap_id)
            if not cap or cap.get("visibility") != "final_exam" or cap.get("adaptation_visible") is not False:
                raise TrustedRunnerError("final_report_capability_required")
            selection_id = request.get("selection_id")
            selection = self.selections.get(selection_id)
            supplied_seal = request.get("finalization_seal")
            if (selection is None or not isinstance(supplied_seal, str)
                    or not supplied_seal or supplied_seal != selection.get("finalization_seal")):
                raise TrustedRunnerError("finalization_seal_required")
            return self._evaluate(request, cap, final=True)
        if scope not in {"study", "validation"}:
            raise TrustedRunnerError("invalid_evaluation_scope")
        cap = self._capability(request.get("task_set"), final=False)
        if cap.get("visibility") != scope:
            raise TrustedRunnerError("task_set_scope_mismatch")
        return self._evaluate(request, cap, final=False)

    def _evaluate(self, request: dict[str, Any], cap: Mapping[str, Any], *, final: bool) -> dict[str, Any]:
        ids = request.get("candidate_ids")
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            raise TrustedRunnerError("candidate_ids_required")
        k = request.get("k")
        if not isinstance(k, int) or k < 1:
            raise TrustedRunnerError("invalid_k")
        completeness = request.get("completeness")
        if completeness not in {"partial", "staged", "full"}:
            raise TrustedRunnerError("invalid_completeness")
        for cid in ids:
            if cid not in self.candidates:
                raise TrustedRunnerError("candidate_not_built")
        task_ids = list(cap["task_ids"])
        results: list[dict[str, Any]] = []
        for cid in ids:
            candidate = self.candidates[cid]
            task_outcomes = []
            for task_id in task_ids:
                trials = []
                for trial_no in range(k):
                    raw = self._run_trial(Path(candidate["workspace"]), {"task_id": task_id}, trial_no)
                    raw["trial_id"] = f"trial-{cid}-{task_id}-{trial_no + 1}"
                    raw["score_kind"] = "final_exam" if final else cap["visibility"]
                    trials.append(raw)
                measured = [t for t in trials if t.get("score") is not None and t.get("status") != "infrastructure_failed"]
                scores = [float(t["score"]) for t in measured]
                aggregate = {"n_requested": k, "n_terminal": len(trials), "n_measured": len(measured)}
                if scores:
                    aggregate["mean_score"] = sum(scores) / len(scores)
                    aggregate["stddev"] = math.sqrt(sum((x - aggregate["mean_score"]) ** 2 for x in scores) / len(scores))
                task_outcomes.append({"task_id": task_id, "trials": trials, "aggregate": aggregate})
            requested = len(task_ids) * k
            terminal = sum(len(t["trials"]) for t in task_outcomes)
            measured_n = sum(t["aggregate"]["n_measured"] for t in task_outcomes)
            infra = sum(sum(1 for trial in t["trials"] if trial.get("status") == "infrastructure_failed") for t in task_outcomes)
            all_scores = [float(trial["score"]) for t in task_outcomes for trial in t["trials"] if trial.get("score") is not None and trial.get("status") != "infrastructure_failed"]
            status = "eligible" if completeness == "full" and terminal == requested and infra == 0 and measured_n == requested else "incomplete"
            result = {"evaluation_id": _id("eval", {"candidate": cid, "cap": cap["capability_id"], "k": k, "stage": request.get("stage_id"), "scope": request.get("evaluation_scope")}),
                      "candidate_id": cid, "task_set": _copy(cap), "k": k, "completeness": completeness,
                      "measurement_status": "reported" if final else status,
                      "requested_cells": requested, "terminal_cells": terminal, "measured_cells": measured_n,
                      "infrastructure_failed_cells": infra, "task_outcomes": task_outcomes}
            if request.get("stage_id") is not None:
                result["stage_id"] = request["stage_id"]
            if all_scores:
                result["aggregate_score"] = sum(all_scores) / len(all_scores)
            result["aggregate_cost"] = self._cost(task_outcomes)
            result["result_sha"] = _sha(result)
            if final:
                report_artifact = {"artifact_id": f"report-{result['evaluation_id']}", "kind": "final_report",
                                   "content_sha": result["result_sha"], "producer": {"role": "framework", "id": "trusted-runner"},
                                   "visibility": "final_report_private"}
                result["report_artifact"] = report_artifact
            else:
                result["score_artifact"] = {"artifact_id": f"score-{result['evaluation_id']}", "kind": "study_score",
                                             "content_sha": result["result_sha"], "producer": {"role": "framework", "id": "trusted-runner"},
                                             "visibility": "framework"}
            self.evaluations[result["evaluation_id"]] = result
            self._evaluation_scopes[result["evaluation_id"]] = "final_report_only" if final else request["evaluation_scope"]
            self._save("evaluations", result["evaluation_id"], result)
            results.append(result)
        if final:
            response = {"operation": "evaluate_candidates", "operation_id": _id("op", request),
                        "evaluation_scope": "final_report_only", "final_report_results": results,
                        "final_report_private": True}
        else:
            response = {"operation": "evaluate_candidates", "operation_id": _id("op", request),
                        "evaluation_scope": request["evaluation_scope"], "results": results}
        response = _stable(response)
        self._save_idem("evaluate_candidates", request["idempotency_key"], request, response)
        return response

    @staticmethod
    def _cost(task_outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        costs = [trial.get("cost", {}) for task in task_outcomes for trial in task["trials"]]
        out: dict[str, Any] = {"accounting_revision": "offline-v1", "known": all(c.get("known", True) for c in costs)}
        for key in ("input_tokens", "output_tokens", "model_calls", "wall_clock_ms", "usd_micros"):
            values = [c[key] for c in costs if isinstance(c.get(key), int)]
            if values: out[key] = sum(values)
        return out

    def select_deploy(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request = _copy(dict(request))
        action = request.get("action")
        cached = self._idem("select_deploy", request.get("idempotency_key", ""), request)
        if cached is not None: return cached
        if action == "select":
            response = self._select(request)
        elif action == "deploy":
            response = self._deploy(request)
        elif action == "rollback":
            response = self._rollback(request)
        else:
            raise TrustedRunnerError("invalid_select_deploy_action")
        response = _stable(response)
        self._save_idem("select_deploy", request["idempotency_key"], request, response)
        return response

    def _select(self, request: dict[str, Any]) -> dict[str, Any]:
        incumbent = request.get("incumbent_id")
        eval_ids = request.get("evaluation_ids") or []
        evals = [self.evaluations[e] for e in eval_ids if e in self.evaluations and self._evaluation_scopes.get(e, self.evaluations[e].get("task_set", {}).get("visibility")) != "final_report_only"]
        runtime_groups = [[(trial.get("harness_runtime") or {})
                           for task in evaluation.get("task_outcomes", [])
                           for trial in task.get("trials", [])] for evaluation in evals]
        runtime_records = [record for group in runtime_groups for record in group]
        from eval.ckpt.source_tree import has_bun_runtime
        declared_bun = any(has_bun_runtime(Path(candidate["workspace"]))
                           for evaluation in evals
                           if (candidate := self.candidates.get(evaluation["candidate_id"]))
                           and candidate.get("workspace"))
        if declared_bun or any("bun" in record for record in runtime_records):
            from eval.containers.responses_overlay import require_comparable
            try:
                if any(not group for group in runtime_groups):
                    raise ValueError("Missing Responses overlay trial evidence")
                require_comparable(runtime_records, runtime_records)
            except ValueError as exc:
                raise TrustedRunnerError(str(exc)) from exc
        incumbent_evals = [e for e in evals if e["candidate_id"] == incumbent and e.get("aggregate_score") is not None and e.get("measurement_status") == "eligible"]
        selection_id = _id("sel", {"incumbent": incumbent, "candidates": request.get("candidate_ids", []), "evaluations": eval_ids})
        basis = [e.get("score_artifact") for e in evals if e.get("score_artifact")]
        eligible = bool(incumbent_evals)
        selected: list[str] = []
        reason = "incumbent_has_no_eligible_study_score"
        if eligible:
            incumbent_score = incumbent_evals[0]["aggregate_score"]
            choices = [e for e in evals if e["candidate_id"] in set(request.get("candidate_ids", [])) and e.get("aggregate_score") is not None and e.get("measurement_status") == "eligible" and e["aggregate_score"] > incumbent_score]
            if choices:
                best = max(choices, key=lambda e: (e["aggregate_score"], e["candidate_id"]))
                selected = [best["candidate_id"]]
                reason = "candidate_improves_incumbent"
            else: reason = "retain_incumbent"
        if not basis:
            basis = [{"artifact_id": "incumbent-unmeasured", "kind": "validation", "content_sha": _sha({"incumbent": incumbent}), "producer": {"role": "framework", "id": "trusted-runner"}, "visibility": "framework"}]
        selection = {"selection_id": selection_id, "incumbent_id": incumbent, "selected_candidate_ids": selected,
                     "retained_incumbent": not selected, "basis_artifact_ids": [a["artifact_id"] for a in basis],
                     "basis_artifacts": basis,
                     "eligible": eligible, "tentative": False, "reason": reason}
        selection["decision_sha"] = _sha(selection)
        self.selections[selection_id] = selection
        self._save("selections", selection_id, selection)
        return {"operation": "select_deploy", "operation_id": _id("op", request), "action": "select", "selection": selection}

    def _deploy(self, request: dict[str, Any]) -> dict[str, Any]:
        selection = self.selections.get(request.get("selection_id"))
        if selection is None: raise TrustedRunnerError("unknown_selection")
        expected = request.get("expected_from_snapshot_sha")
        if expected != self.current_snapshot_sha: raise TrustedRunnerError("stale_expected_snapshot")
        selected = selection["selected_candidate_ids"]
        to_sha = self.current_snapshot_sha
        if selected:
            to_sha = self.candidates[selected[0]]["candidate_snapshot_sha"]
        deployment_id = _id("dep", {"selection": selection["selection_id"], "from": self.current_snapshot_sha, "to": to_sha})
        requested_status = request.get("deployment_status")
        if requested_status not in {None, "none", "tentative", "deployed", "failed"}:
            raise TrustedRunnerError("invalid_deployment_status")
        status = requested_status or ("deployed" if selected else "none")
        if not selected and status in {"tentative", "deployed"}:
            status = "none"
        if status in {"none", "failed"}:
            to_sha = self.current_snapshot_sha
        applied = selected if status in {"deployed", "tentative"} else []
        deployment = {"deployment_id": deployment_id, "selection_id": selection["selection_id"], "status": status,
                      "deployed_candidate_ids": applied, "from_snapshot_sha": self.current_snapshot_sha,
                      "to_snapshot_sha": to_sha, "deployment_artifact": {"artifact_id": deployment_id, "kind": "deployment",
                      "content_sha": _sha({"from": self.current_snapshot_sha, "to": to_sha}), "producer": {"role": "deployer", "id": "trusted-runner"}, "visibility": "deployment_record"}}
        self.deployments[deployment_id] = deployment
        self._save("deployments", deployment_id, deployment)
        if status in {"deployed", "tentative"}:
            self.current_snapshot_sha = to_sha
        (self.run_dir / "state.json").write_bytes(_json({"current_snapshot_sha": to_sha}))
        return {"operation": "select_deploy", "operation_id": _id("op", request), "action": "deploy", "deployment": deployment}

    def finalize(self, selection_id: str) -> str:
        if selection_id not in self.selections: raise TrustedRunnerError("unknown_selection")
        if self.selections[selection_id].get("finalization_seal"):
            return self.selections[selection_id]["finalization_seal"]
        if not any(d.get("selection_id") == selection_id and d.get("status") in {"none", "tentative", "deployed"}
                   for d in self.deployments.values()):
            raise TrustedRunnerError("deployment_seal_required")
        seal = _sha({"selection": self.selections[selection_id], "deployments": sorted(self.deployments)})
        self.selections[selection_id]["finalization_seal"] = seal
        # Finalization is the one append-only state transition on a selection:
        # the decision bytes remain unchanged and gain a seal exactly once.
        path = self.run_dir / "selections" / f"{selection_id}.json"
        path.write_bytes(_json(self.selections[selection_id]))
        return seal

    def _rollback(self, request: dict[str, Any]) -> dict[str, Any]:
        deployment = self.deployments.get(request.get("deployment_id"))
        if deployment is None or request.get("rollback_of") != deployment["deployment_id"]:
            raise TrustedRunnerError("unknown_rollback_deployment")
        evidence = request.get("evidence") or {}
        if evidence.get("visibility") in {"final_exam", "final_report_private"} or evidence.get("kind") not in {"validation", "runtime_health"}:
            raise TrustedRunnerError("rollback_requires_validation_or_runtime_health")
        if self.current_snapshot_sha != deployment["to_snapshot_sha"]:
            raise TrustedRunnerError("rollback_current_snapshot_mismatch")
        dep_id = _id("dep", {"rollback_of": deployment["deployment_id"], "to": deployment["from_snapshot_sha"]})
        result = {"deployment_id": dep_id, "selection_id": deployment["selection_id"], "status": "rolled_back",
                  "deployed_candidate_ids": [], "from_snapshot_sha": self.current_snapshot_sha,
                  "to_snapshot_sha": deployment["from_snapshot_sha"], "rollback_of": deployment["deployment_id"],
                  "deployment_artifact": {"artifact_id": dep_id, "kind": "deployment", "content_sha": _sha({"rollback_of": deployment["deployment_id"]}), "producer": {"role": "deployer", "id": "trusted-runner"}, "visibility": "deployment_record"}}
        self.deployments[dep_id] = result
        self._save("deployments", dep_id, result)
        self.current_snapshot_sha = result["to_snapshot_sha"]
        (self.run_dir / "state.json").write_bytes(_json({"current_snapshot_sha": self.current_snapshot_sha}))
        return {"operation": "select_deploy", "operation_id": _id("op", request), "action": "rollback", "deployment": result}


__all__ = ["TrustedRunnerFacade", "TrustedRunnerError", "IdempotencyConflict"]
