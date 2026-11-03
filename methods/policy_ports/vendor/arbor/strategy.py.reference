"""Arbor's hypothesis tree, trunk-based experiments and upward insight synthesis.

Upstream: https://github.com/RUC-NLPIR/Arbor
Commit: 7cdaf1fa6d779b3d5e340052357bf3fe55dfed93
References: src/coordinator/{idea_tree,prompts}.py and tools/{tree_ops,executor_run}.py

Core mechanism port. Upstream iteration/merge signals map to train/dev; sealed
test remains runner-owned. Git worktrees map to native Snapshot checkouts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from flashrsi.algorithms import _replies
from flashrsi.algorithms.arbor import _prompts as prompts
from flashrsi.core import Algorithm, Snapshot
from flashrsi.core.snapshot import discard_runtime_files


class Arbor(Algorithm):
    """Let a Coordinator grow a hypothesis tree and synthesize lessons upward.

    Experiments start from the selected trunk, not their conceptual tree parent.
    Regressions remain evidence; only strictly better dev results move the trunk.
    """

    name = "arbor"
    description = "Evolve harnesses through a hypothesis tree and propagated insights."
    evolve_scope = {"*"}
    _defaults = {
        "max_cycles": 10,
        "max_depth": 3,
        "merge_threshold": 0.0,
        "max_evidence_chars": 30000,
    }
    _ranges = {
        "max_cycles": (int, 1),
        "max_depth": (int, 2),
        "merge_threshold": (float, 0),
        "max_evidence_chars": (int, 1),
    }

    async def evolve(self) -> Snapshot:
        seed = current = self.target.snapshot
        training = await self._evaluate(
            seed, self.benchmark.train, label="baseline-train"
        )
        validation = await self._evaluate(
            seed, self.benchmark.dev, label="baseline-dev"
        )
        if any(row.status != "finished" for row in (*training, *validation)):
            raise ValueError(
                "Arbor baseline train/dev evaluation contains failed tasks"
            )
        dev_score = validation.score
        tree = {
            "ROOT": _Node(
                "ROOT",
                None,
                0,
                "Improve this harness through reusable, evidence-backed mechanisms.",
                status="root",
                snapshot_id=seed.id,
                train_score=training.score,
                dev_score=dev_score,
            )
        }

        for cycle in range(self.config.max_cycles):
            node = await self._coordinate(tree, current, training, dev_score)
            if node is None:
                break
            node.status = "running"
            node.base_snapshot_id = current.id
            candidate, implementation = await self._execute(current, node, tree)
            feedback = []
            node.result = implementation
            if candidate is None:
                node.status = "needs_retry"
            else:
                node.snapshot_id = candidate.id
                measured = await self._evaluate(
                    candidate, self.benchmark.train, label="candidate-train"
                )
                node.train_score = measured.score
                feedback = [self._trajectory_evidence(row) for row in measured]
                node.status = "done"
                if any(row.status != "finished" for row in measured):
                    node.status = "needs_retry"
                    node.result += "\nCandidate train evaluation contains failed tasks."
                elif measured.score - training.score >= self.config.merge_threshold:
                    validation = await self._evaluate(
                        candidate, self.benchmark.dev, label="candidate-dev"
                    )
                    node.dev_score = validation.score
                    if any(row.status != "finished" for row in validation):
                        node.status = "needs_retry"
                        node.result += (
                            "\nCandidate dev evaluation contains failed tasks."
                        )
                    elif validation.score > dev_score:
                        node.status = "merged"
                        current, training, dev_score = (
                            candidate,
                            measured,
                            validation.score,
                        )
                else:
                    node.result += "\nTrain gain did not clear merge_threshold."

            node.insight = (
                await self.worker.ask(
                    prompts.REFLECT.format(
                        experiment=self._evidence([asdict(node)]),
                        evidence=self._evidence(feedback),
                    )
                )
            ).strip()
            await self._backpropagate(tree, node)
            self._record(
                "arbor-tree",
                {
                    "cycle": cycle,
                    "trunk_id": current.id,
                    "nodes": [asdict(item) for item in tree.values()],
                },
            )

        return self._promote(
            seed,
            current,
            score=dev_score,
            tree=[asdict(node) for node in tree.values()],
        )

    async def _coordinate(self, tree, current, training, dev_score):
        """The Coordinator chooses expansion, execution, pruning or stopping.

        Bound planning-only actions as well as experiments: repeated expansion
        or malformed replies must not bypass the experiment budget indefinitely.
        """
        feedback = []
        for _ in range(12):
            reply = await self.worker.ask(
                prompts.COORDINATE.format(
                    tree=self._evidence(asdict(node) for node in tree.values()),
                    trunk=self._evidence(
                        [
                            {
                                "snapshot_id": current.id,
                                "train_score": training.score,
                                "dev_score": dev_score,
                                "files": sorted(current.files),
                                "component_rules": dict(current.component_rules),
                            }
                        ]
                    ),
                    evidence=self._evidence(
                        self._trajectory_evidence(row) for row in training
                    ),
                    max_depth=self.config.max_depth,
                    feedback=self._evidence(feedback),
                )
            )
            try:
                action = json.loads(_replies.tagged(reply, "ARBOR"))
                if not isinstance(action, dict):
                    raise ValueError("return one action object")
                operation = action.get("action")
                if operation == "stop":
                    self._record("arbor-stop", {"reason": action.get("reason", "")})
                    return None
                node_id = action.get("node_id")
                if not isinstance(node_id, str) or node_id not in tree:
                    raise ValueError("node_id must identify an existing tree node")
                node = tree[node_id]
                children = [item for item in tree.values() if item.parent_id == node_id]
                if operation == "run":
                    if node.depth < 2 or node.status != "pending" or children:
                        raise ValueError(
                            "run requires a pending experiment leaf at depth >= 2"
                        )
                    return node
                if operation == "expand":
                    if node.depth >= self.config.max_depth or node.status not in (
                        "root",
                        "direction",
                        "done",
                        "merged",
                    ):
                        raise ValueError(
                            "expand requires an unpruned direction or completed experiment below max_depth"
                        )
                    hypotheses = action.get("hypotheses")
                    if (
                        not isinstance(hypotheses, list)
                        or not 1 <= len(hypotheses) <= 3
                        or any(
                            not isinstance(item, str) or not item.strip()
                            for item in hypotheses
                        )
                    ):
                        raise ValueError("expand requires 1 to 3 nonempty hypotheses")
                    for index, hypothesis in enumerate(hypotheses, len(children) + 1):
                        child_id = (
                            str(index) if node_id == "ROOT" else f"{node_id}.{index}"
                        )
                        tree[child_id] = _Node(
                            child_id,
                            node_id,
                            node.depth + 1,
                            hypothesis.strip(),
                            status="direction" if node.depth == 0 else "pending",
                        )
                elif operation == "prune":
                    if node_id == "ROOT":
                        raise ValueError("the root cannot be pruned")
                    for item in tree.values():
                        if item.id == node_id or item.id.startswith(node_id + "."):
                            item.status = "pruned"
                    node.result = str(action.get("reason", ""))
                else:
                    raise ValueError("action must be expand, run, prune or stop")
                self._record(
                    "arbor-plan",
                    {
                        "action": action,
                        "nodes": [asdict(item) for item in tree.values()],
                    },
                )
            except ValueError as error:
                feedback.append({"problem": str(error)})
                self._record("arbor-plan-rejected", feedback[-1])
        raise ValueError(
            "Arbor Coordinator exhausted 12 planning actions without dispatch or stop"
        )

    async def _execute(self, trunk, node, tree):
        working = self._run_dir / "working" if self._run_dir else None
        with trunk.check_out(working_dir=working) as directory:
            implementation = await self.worker.edit(
                directory,
                prompts.EXECUTE.format(
                    experiment=self._evidence([asdict(node)]),
                    tree=self._evidence(asdict(item) for item in tree.values()),
                    rules=self._evidence([dict(self.target.component_rules)]),
                ),
            )
            discard_runtime_files(directory)
            try:
                candidate = self._commit(trunk, directory)
            except ValueError as error:
                return None, f"Invalid edit: {error}\n{implementation}"
            if not trunk.changed_paths(candidate):
                return None, "No harness files changed.\n" + implementation
            return candidate, implementation

    async def _backpropagate(self, tree, node):
        """Synthesize siblings' lessons bottom-up, as upstream TreePropagate does."""
        ancestor_id = node.parent_id
        while ancestor_id is not None:
            ancestor = tree[ancestor_id]
            children = [
                asdict(item)
                for item in tree.values()
                if item.parent_id == ancestor_id and item.insight
            ]
            ancestor.insight = (
                await self.worker.ask(
                    prompts.SYNTHESIZE.format(
                        hypothesis=ancestor.hypothesis,
                        children=self._evidence(children),
                    )
                )
            ).strip()
            ancestor_id = ancestor.parent_id

    def _check(self):
        if not self.benchmark.train or not self.benchmark.dev:
            raise ValueError("Arbor requires non-empty train and dev splits")
        if (
            self.worker is self.target
            or self.worker.config.get("type", "agent") != "agent"
        ):
            raise ValueError("Arbor requires a separate file-editing Agent Worker")
        ids = [
            task.task_id
            for tasks in (self.benchmark.train, self.benchmark.dev, self.benchmark.test)
            for task in tasks
        ]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "Arbor requires unique task IDs and disjoint train/dev/test splits"
            )


@dataclass
class _Node:
    id: str
    parent_id: str | None
    depth: int
    hypothesis: str
    status: str = "pending"
    insight: str = ""
    result: str = ""
    base_snapshot_id: str | None = None
    snapshot_id: str | None = None
    train_score: float | None = None
    dev_score: float | None = None


__all__ = ["Arbor"]
