"""Pure DGM archive and lineage primitives.

The upstream DGM archive is a tree of immutable commits.  This module keeps that
shape explicit for the HarnessGrad port: a parent is a list of references, child
counts are derived from those references, and retention is separate from parent
selection.  It deliberately has no model, filesystem, or platform imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import random
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ArtifactState:
    """An immutable identity for the harness tree handed to a child."""

    artifact_id: str
    parent_refs: tuple[str, ...] = ()
    task_agent_ref: str | None = None
    meta_agent_ref: str | None = None
    patch_kind: str = "tree"
    immutable: bool = True

    @classmethod
    def from_files(cls, files: Mapping[str, str], *, parent_refs: Iterable[str] = (),
                   task_agent_ref: str | None = None,
                   meta_agent_ref: str | None = None,
                   patch_kind: str = "tree") -> "ArtifactState":
        digest = hashlib.sha256()
        for path, content in sorted(files.items()):
            digest.update(path.encode())
            digest.update(b"\0")
            digest.update(content.encode())
            digest.update(b"\0")
        return cls(digest.hexdigest(), tuple(parent_refs), task_agent_ref,
                   meta_agent_ref, patch_kind)

    def as_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "parent_refs": list(self.parent_refs),
            "task_agent_ref": self.task_agent_ref,
            "meta_agent_ref": self.meta_agent_ref,
            "patch_kind": self.patch_kind,
            "immutable": self.immutable,
        }


@dataclass(frozen=True)
class ArchiveNode:
    node_id: str
    score: float | None
    parent_refs: tuple[str, ...] = ()
    artifact: ArtifactState | None = None
    measured: bool = True
    rejected: bool = False
    compiled: bool = True
    metadata: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "score": self.score,
            "parent_refs": list(self.parent_refs),
            "artifact": self.artifact.as_dict() if self.artifact else None,
            "measured": self.measured,
            "rejected": self.rejected,
            "compiled": self.compiled,
        }


def child_counts(nodes: Iterable[ArchiveNode]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        for parent in node.parent_refs:
            counts[parent] = counts.get(parent, 0) + 1
    return counts


@dataclass(frozen=True)
class DGMArchive:
    """Persistent archive; ``add`` returns a new archive value."""

    nodes: tuple[ArchiveNode, ...] = ()
    retention: str = "keep_all"
    noise_leeway: float = 0.1
    root_id: str | None = None

    def __post_init__(self) -> None:
        if self.retention not in {"keep_all", "keep_better"}:
            raise ValueError("retention must be keep_all or keep_better")
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("archive contains duplicate node IDs")
        known = set(ids)
        for node in self.nodes:
            if node.score is not None and not math.isfinite(float(node.score)):
                raise ValueError("archive score must be finite")
            if any(parent not in known for parent in node.parent_refs):
                raise ValueError(f"node {node.node_id!r} has an unknown parent")
        if self.root_id is not None and self.root_id not in known:
            raise ValueError("archive root_id is not present")

    @property
    def initial_score(self) -> float | None:
        if self.root_id is None:
            return None
        for node in self.nodes:
            if node.node_id == self.root_id and isinstance(node.score, (int, float)):
                return float(node.score)
        return None

    def add(self, node: ArchiveNode) -> "DGMArchive":
        """Retain a compiled node according to DGM's original gate.

        ``keep_all`` intentionally retains rejected and non-best nodes.  Eligibility
        for a *new parent* is evaluated separately by ``parent_candidates``.
        """
        if not node.compiled:
            return self
        known = {n.node_id for n in self.nodes}
        if node.node_id in known:
            raise ValueError(f"duplicate node ID {node.node_id!r}")
        if node.score is not None and not math.isfinite(float(node.score)):
            raise ValueError("archive score must be finite")
        if any(parent not in known for parent in node.parent_refs):
            raise ValueError(f"node {node.node_id!r} has an unknown parent")
        if self.retention == "keep_better" and self.initial_score is not None:
            if node.score is None or node.score < self.initial_score - self.noise_leeway:
                return self
        root_id = self.root_id
        if root_id is None and not self.nodes and not node.parent_refs:
            root_id = node.node_id
        return DGMArchive(self.nodes + (node,), self.retention, self.noise_leeway, root_id)

    def retained(self) -> tuple[ArchiveNode, ...]:
        return self.nodes

    def parent_candidates(self) -> tuple[ArchiveNode, ...]:
        return tuple(n for n in self.nodes if n.measured and n.compiled
                     and isinstance(n.score, (int, float)) and not n.rejected)

    def weights(self) -> dict[str, float]:
        counts = child_counts(self.nodes)
        out = {}
        for node in self.parent_candidates():
            score = float(node.score)  # narrowed by parent_candidates
            x = 10.0 * (score - 0.5)
            sigmoid = 0.0 if x < -60 else 1.0 if x > 60 else 1.0 / (1.0 + math.exp(-x))
            out[node.node_id] = sigmoid / (1.0 + counts.get(node.node_id, 0))
        return out

    def sample_parent(self, seed: int) -> tuple[ArchiveNode | None, dict]:
        candidates = self.parent_candidates()
        weights = self.weights()
        if not candidates:
            return None, {"archive": [n.node_id for n in self.nodes], "weights": {},
                          "seed": seed, "reason": "no eligible measured node"}
        if sum(weights.values()) <= 0:
            weights = {n.node_id: 1.0 for n in candidates}
        rng = random.Random(seed)
        chosen_id = rng.choices(list(weights), weights=list(weights.values()), k=1)[0]
        chosen = next(n for n in candidates if n.node_id == chosen_id)
        return chosen, {"archive": [n.node_id for n in self.nodes],
                        "candidates": [n.node_id for n in candidates],
                        "weights": dict(weights),
                        "child_counts": child_counts(self.nodes),
                        "selected_parent": chosen_id, "seed": seed,
                        "retention": self.retention}

    def sample_parents(self, count: int, seed: int) -> tuple[tuple[ArchiveNode, ...], dict]:
        """Draw the original parallel children (sampling with replacement)."""
        if count < 1:
            return (), {"seed": seed, "count": count, "parents": []}
        candidates = self.parent_candidates()
        weights = self.weights()
        if not candidates:
            return (), {"seed": seed, "count": count, "parents": [],
                        "reason": "no eligible measured node"}
        if sum(weights.values()) <= 0:
            weights = {n.node_id: 1.0 for n in candidates}
        rng = random.Random(seed)
        ids = rng.choices(list(weights), weights=list(weights.values()), k=count)
        by_id = {n.node_id: n for n in candidates}
        return tuple(by_id[node_id] for node_id in ids), {
            "seed": seed, "count": count, "parents": ids,
            "with_replacement": True, "weights": weights,
        }
