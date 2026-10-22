"""Pure HyperAgents archive, selector variants, and patch identities."""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Iterable


@dataclass(frozen=True)
class PatchIdentity:
    """Task/meta patches are separate artifacts and rollback is explicit."""

    patch_id: str
    role: str  # task_agent, meta_agent, task_rollback, meta_rollback
    target: str
    parent_patch_id: str | None = None
    applied: bool = False

    def as_dict(self) -> dict:
        return {"patch_id": self.patch_id, "role": self.role, "target": self.target,
                "parent_patch_id": self.parent_patch_id, "applied": self.applied}


@dataclass(frozen=True)
class ArtifactState:
    artifact_id: str
    parent_refs: tuple[str, ...] = ()
    task_patch: PatchIdentity | None = None
    meta_patch: PatchIdentity | None = None
    task_rollback: PatchIdentity | None = None
    meta_rollback: PatchIdentity | None = None
    immutable: bool = True

    def as_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "parent_refs": list(self.parent_refs),
            "task_patch": self.task_patch.as_dict() if self.task_patch else None,
            "meta_patch": self.meta_patch.as_dict() if self.meta_patch else None,
            "task_rollback": self.task_rollback.as_dict() if self.task_rollback else None,
            "meta_rollback": self.meta_rollback.as_dict() if self.meta_rollback else None,
            "immutable": self.immutable,
        }


@dataclass(frozen=True)
class ArchiveNode:
    node_id: str
    agent_score: float | None
    ensemble_score: float | None = None
    parent_refs: tuple[str, ...] = ()
    artifact: ArtifactState | None = None
    valid_parent: bool = True
    measured: bool = True

    @property
    def score(self) -> float | None:
        values = [v for v in (self.agent_score, self.ensemble_score)
                  if isinstance(v, (int, float))]
        return max(values) if values else None


def child_counts(nodes: Iterable[ArchiveNode]) -> dict[str, int]:
    out: dict[str, int] = {}
    for node in nodes:
        for ref in node.parent_refs:
            out[ref] = out.get(ref, 0) + 1
    return out


def patch_identities(files: Iterable[str], *, patch_id: str,
                     parent_patch_id: str | None = None) -> dict[str, PatchIdentity | None]:
    """Classify a generation patch without merging task/meta roles.

    HyperAgents' reset flags are independent.  A patch touching only one file must
    stay task-only/meta-only, and a rollback is a distinct operation from applying
    the patch.  The caller can therefore persist these identities even when the
    trusted runtime has not deployed either one yet.
    """
    names = set(files)
    task = PatchIdentity(patch_id, "task_agent", "task_agent.py", parent_patch_id) \
        if "task_agent.py" in names else None
    meta = PatchIdentity(patch_id, "meta_agent", "meta_agent.py", parent_patch_id) \
        if "meta_agent.py" in names else None
    return {"task": task, "meta": meta, "task_rollback": None, "meta_rollback": None}


def rollback_identity(*, patch_id: str, role: str, target: str,
                      parent_patch_id: str | None = None) -> PatchIdentity:
    if role not in {"task_rollback", "meta_rollback"}:
        raise ValueError("rollback role must be task_rollback or meta_rollback")
    return PatchIdentity(patch_id, role, target, parent_patch_id, applied=False)


def weights_for(nodes: Iterable[ArchiveNode], kind: str = "score_child_prop") -> dict[str, float]:
    candidates = [n for n in nodes if n.valid_parent and n.measured and n.score is not None]
    if not candidates:
        return {}
    scores = {n.node_id: float(n.score) for n in candidates}
    top = sorted(scores.values(), reverse=True)[:3]
    mid = sum(top) / len(top)
    children = child_counts(candidates)
    if kind in {"random", "uniform"}:
        return {n.node_id: 1.0 for n in candidates}
    if kind == "latest":
        return {n.node_id: (1.0 if n is candidates[-1] else 0.0) for n in candidates}
    if kind == "best":
        best = max(scores.values())
        return {n.node_id: (1.0 if scores[n.node_id] == best else 0.0) for n in candidates}
    out = {}
    for n in candidates:
        sigmoid = 1.0 / (1.0 + math.exp(-10.0 * (scores[n.node_id] - mid)))
        if kind == "score_prop":
            out[n.node_id] = sigmoid
        elif kind == "score_child_prop":
            out[n.node_id] = sigmoid * math.exp(-((children.get(n.node_id, 0) / 8.0) ** 3))
        else:
            raise ValueError(f"unknown HyperAgents selector {kind!r}")
    return out


def select_parent(nodes: Iterable[ArchiveNode], *, kind: str = "score_child_prop",
                  seed: int = 0) -> tuple[ArchiveNode | None, dict]:
    nodes = tuple(nodes)
    candidates = tuple(n for n in nodes if n.valid_parent and n.measured and n.score is not None)
    weights = weights_for(candidates, kind)
    if not candidates:
        return None, {"selector": kind, "candidates": [], "seed": seed}
    if not any(weights.values()):
        weights = {n.node_id: 1.0 for n in candidates}
    rng = random.Random(seed)
    chosen_id = rng.choices(list(weights), weights=list(weights.values()), k=1)[0]
    chosen = next(n for n in candidates if n.node_id == chosen_id)
    return chosen, {"selector": kind, "candidates": [n.node_id for n in candidates],
                    "weights": weights, "child_counts": child_counts(nodes),
                    "selected_parent": chosen_id, "seed": seed,
                    "selection_vs_deployment": "deployment_pending_trusted_runtime"}


def sample_parents(nodes: Iterable[ArchiveNode], count: int, *, kind: str,
                   seed: int) -> tuple[tuple[ArchiveNode, ...], dict]:
    """Sample parallel generations with the selected upstream branch."""
    if count < 1:
        return (), {"selector": kind, "count": count, "parents": []}
    nodes = tuple(nodes)
    candidates = tuple(n for n in nodes if n.valid_parent and n.measured and n.score is not None)
    weights = weights_for(candidates, kind)
    if not candidates:
        return (), {"selector": kind, "count": count, "parents": [],
                    "reason": "no eligible measured node"}
    if not any(weights.values()):
        weights = {n.node_id: 1.0 for n in candidates}
    rng = random.Random(seed)
    ids = rng.choices(list(weights), weights=list(weights.values()), k=count)
    by_id = {n.node_id: n for n in candidates}
    return tuple(by_id[i] for i in ids), {"selector": kind, "count": count,
                                         "parents": ids, "with_replacement": True,
                                         "weights": weights}
