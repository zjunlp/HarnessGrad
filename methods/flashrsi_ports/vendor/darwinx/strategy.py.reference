"""DarwinX mechanism port: preserve-and-extend, confirm, archive, recombine.

Upstream: https://github.com/SalesforceAIResearch/Beagle
Reviewed revision: 165343b46a85b5d28c6370e6197ac665c7eda871
Paper: https://arxiv.org/abs/2608.07545
This uses Lite's generic snapshots and rollouts, not Beagle's campaign driver.
The deliberate differences and omitted gates are listed in docs/darwinx.md.
"""

from __future__ import annotations

import random
from contextlib import ExitStack
from itertools import combinations
from statistics import mean

from flashrsi.algorithms import _replies
from flashrsi.algorithms.darwinx import _prompts as prompts
from flashrsi.core import Algorithm, Snapshot
from flashrsi.core.snapshot import discard_runtime_files


class DarwinX(Algorithm):
    """Only confirmed, preserving extensions become mutation parents.

    Rejected branches remain measured archive members: complementary specialists
    can be recombined, but their child must preserve both parents' capabilities.
    """

    name = "darwinx"
    description = "Evolve harnesses through preservation-constrained population search."
    evolve_scope = {"*"}
    _defaults = {
        "max_steps": 10,
        "screen_size": 3,
        "screen_samples": 1,
        "confirm_samples": 3,
        "screen_regression_tolerance": 1.0,
        "regression_tolerance": 0.0,
        "solved_threshold": 1.0,
        "exploration_probability": 0.5,
        "merge_every": 3,
        "seed": 42,
        "max_evidence_chars": 24000,
    }
    _ranges = {
        "max_steps": (int, 1),
        "screen_size": (int, 1),
        "screen_samples": (int, 1),
        "confirm_samples": (int, 1),
        "screen_regression_tolerance": (float, 0),
        "regression_tolerance": (float, 0),
        "solved_threshold": (float, 0),
        "exploration_probability": (float, 0),
        "merge_every": (int, 0),
        "seed": int,
        "max_evidence_chars": (int, 1),
    }

    async def evolve(self) -> Snapshot:
        current, train = self.target.snapshot, self.benchmark.train
        rng = random.Random(self.config.seed)
        scores, trials = await self._measure(
            current, train, "baseline", self.config.confirm_samples
        )
        solved = sorted(
            t for t, score in scores.items() if score >= self.config.solved_threshold
        )
        self._archive = [
            {
                "snapshot_id": current.id,
                "parent_id": current.parent_id,
                "account": "seed harness",
                "per_task": scores,
                "score": mean(r.score for r in trials),
                "samples": len(trials),
                "solved": solved,
                "invariant": solved,
                "fidelity": "confirmation",
                "accepted": True,
                "cumulative_gain": 0.0,
                "donor_id": None,
            }
        ]
        # Native objects stay native; archive rows can go straight to prompts/logs.
        self._versions = {current.id: (current, trials)}
        self._lessons, self._declined, attempted = [], [], set()

        for step in range(self.config.max_steps):
            parents = self._select(step, rng, attempted)
            invariant = set().union(*(set(p["invariant"]) for p in parents))
            if len(parents) == 2:
                invariant.update(t for p in parents for t in p["solved"])
            candidate, account = await self._propose(parents, invariant, step)
            if candidate is None:
                continue

            record, trials = await self._assess(candidate, parents, invariant, rng)
            record["account"] = account
            self._archive.append(record)
            self._versions[candidate.id] = (candidate, trials)
            self._record("decision", {"kind": "admit", "step": step, **record})
            reply = await self.worker.ask(
                prompts.LEARN.format(
                    parents=self._evidence(parents),
                    candidate=self._evidence([record]),
                    traces=self._evidence(
                        {"trial": i, **self._trajectory_evidence(t)}
                        for i, results in enumerate(trials)
                        for t in results
                    ),
                )
            )
            self._lessons.append(
                {
                    "snapshot_id": candidate.id,
                    "lesson": _replies.tagged(reply, "LEARNINGS").strip(),
                }
            )
            self._record("learnings", self._lessons[-1])

        best = max(
            (r for r in self._archive if r["accepted"]), key=lambda r: r["score"]
        )
        return self._promote(
            current,
            self._versions[best["snapshot_id"]][0],
            score=best["score"],
            archive=self._archive,
            lessons=self._lessons,
            declined_designs=self._declined,
        )

    def _select(self, step, rng, attempted):
        """Recombine complementary confirmed branches, otherwise explore/exploit."""
        if self.config.merge_every and (step + 1) % self.config.merge_every == 0:
            pairs = [
                (a, b)
                for a, b in combinations(self._archive, 2)
                if a["fidelity"] == b["fidelity"] == "confirmation"
                and frozenset((a["snapshot_id"], b["snapshot_id"])) not in attempted
                and set(a["solved"]) - set(b["solved"])
                and set(b["solved"]) - set(a["solved"])
            ]
            if pairs:
                pair = max(
                    pairs,
                    key=lambda p: (
                        len(set(p[0]["solved"]) | set(p[1]["solved"])),
                        min(p[0]["score"], p[1]["score"]),
                    ),
                )
                attempted.add(frozenset(p["snapshot_id"] for p in pair))
                return sorted(pair, key=lambda p: -p["score"])
        eligible = [r for r in self._archive if r["accepted"]]
        parent = (
            rng.choice(eligible)
            if rng.random() < self.config.exploration_probability
            else max(eligible, key=lambda r: (r["cumulative_gain"], r["score"]))
        )
        return [parent]

    async def _assess(self, candidate, parents, invariant, rng):
        """A screen buys fresh confirmation; only preserving extensions qualify."""
        train = self.benchmark.train
        minibatch = rng.sample(list(train), min(self.config.screen_size, len(train)))
        for fidelity, tasks, samples, tolerance in (
            (
                "screen",
                minibatch,
                self.config.screen_samples,
                self.config.screen_regression_tolerance,
            ),
            (
                "confirmation",
                train,
                self.config.confirm_samples,
                self.config.regression_tolerance,
            ),
        ):
            before = []
            for parent in parents:
                scores, _ = await self._measure(
                    self._versions[parent["snapshot_id"]][0],
                    tasks,
                    f"{fidelity}_parent",
                    samples,
                )
                before.append(scores)
            scores, trials = await self._measure(
                candidate, tasks, f"{fidelity}_candidate", samples
            )
            delta = {t: scores[t] - value for t, value in before[0].items()}
            reference = {t: max(p[t] for p in before) for t in scores}
            gain = sum(delta.values())
            regression = sum(max(reference[t] - scores[t], 0) for t in scores)
            lost = sorted(
                t
                for t in invariant
                if t in scores and scores[t] < self.config.solved_threshold
            )
            screen = fidelity == "screen"
            extends = any(d > 1e-12 for d in delta.values()) if screen else gain > 1e-12
            accepted = (
                extends and regression <= tolerance + 1e-12 and (screen or not lost)
            )
            if not accepted:
                break
        eligible = fidelity == "confirmation" and accepted
        solved = {
            t for t, score in scores.items() if score >= self.config.solved_threshold
        }
        return {
            "snapshot_id": candidate.id,
            "parent_id": candidate.parent_id,
            "donor_id": parents[1]["snapshot_id"] if len(parents) == 2 else None,
            "per_task": scores,
            "score": mean(r.score for r in trials),
            "samples": samples,
            "fidelity": fidelity,
            "solved": sorted(solved),
            "invariant": sorted(invariant | solved if eligible else invariant),
            "accepted": eligible,
            "cumulative_gain": parents[0]["cumulative_gain"] + gain,
            "gain": gain,
            "regression_mass": regression,
            "lost_invariant": lost,
            "delta": delta,
            "parent_per_task": {
                p["snapshot_id"]: scores for p, scores in zip(parents, before)
            },
        }, trials

    async def _measure(self, snapshot, tasks, label, samples):
        """Repeat framework evaluations; keep their native Results for evidence."""
        trials = [
            await self._evaluate(snapshot, tasks, label=label) for _ in range(samples)
        ]
        if any(not 0 <= score <= 1 for r in trials for score in r.per_task.values()):
            raise ValueError("DarwinX requires primary metric scores in [0, 1]")
        return {
            t: mean(r.per_task[t] for r in trials) for t in trials[0].per_task
        }, trials

    async def _propose(self, parents, invariant, step):
        primary, trials = self._versions[parents[0]["snapshot_id"]]
        working = self._run_dir / "working" if self._run_dir else None
        with primary.check_out(working_dir=working) as directory:
            # Nested checkouts travel with the edit directory (including containers)
            # and disappear before commit. Snapshot handles bytes and permissions.
            with ExitStack() as sources:
                merge = []
                if len(parents) == 2:
                    donor = self._versions[parents[1]["snapshot_id"]][0]
                    lineage, base = set(), donor
                    while base.id in self._versions:
                        lineage.add(base.id)
                        if base.parent_id not in self._versions:
                            break
                        base = self._versions[base.parent_id][0]
                    base = primary
                    while base.id not in lineage:
                        base = self._versions[base.parent_id][0]
                    for role, snapshot in (("ancestor", base), ("donor", donor)):
                        path = sources.enter_context(
                            snapshot.check_out(working_dir=directory)
                        )
                        merge.append(
                            {
                                "role": role,
                                "snapshot_id": snapshot.id,
                                "directory": str(path.relative_to(directory)),
                            }
                        )
                reply = await self.worker.edit(
                    directory,
                    prompts.EDIT.format(
                        operation="recombine" if len(parents) == 2 else "mutate",
                        parents=self._evidence(parents),
                        invariant=sorted(invariant),
                        traces=self._evidence(
                            {"trial": i, **self._trajectory_evidence(t)}
                            for i, results in enumerate(trials)
                            for t in results
                        ),
                        merge=self._evidence(merge),
                        archive=self._evidence(reversed(self._archive)),
                        lessons=self._evidence(reversed(self._lessons)),
                        declined=self._evidence(reversed(self._declined)),
                        rules=self._evidence([dict(self.target.component_rules)]),
                    ),
                )
            discard_runtime_files(directory)
            try:
                candidate = self._commit(primary, directory)
                if not primary.changed_paths(candidate):
                    raise ValueError("the harness was left unchanged")
                if any(
                    not snapshot.changed_paths(candidate)
                    for snapshot, _ in self._versions.values()
                ):
                    raise ValueError("duplicate archived harness")
            except ValueError as error:
                details = {
                    "kind": "decline",
                    "step": step,
                    "accepted": False,
                    "parent_id": primary.id,
                    "donor_id": parents[1]["snapshot_id"]
                    if len(parents) == 2
                    else None,
                    "problem": str(error),
                }
                self._declined.append(details)
                self._record("decision", details)
                return None, ""
        return candidate, _replies.tagged(reply, "DESIGN").strip()

    def _check(self):
        if not self.benchmark.train:
            raise ValueError("DarwinX requires a non-empty train split")
        ids = [task.task_id for task in self.benchmark.train]
        if len(ids) != len(set(ids)):
            raise ValueError("DarwinX requires unique train task_id values")
        if self.worker.config.get("type", "agent") != "agent":
            raise ValueError(
                "DarwinX requires algorithm.worker.type: agent for directory edits"
            )
        for key in ("solved_threshold", "exploration_probability"):
            if self.config[key] > 1:
                raise ValueError(f"{key} must be at most 1")
        if self.config.solved_threshold == 0:
            raise ValueError("solved_threshold must be greater than 0")
        if self.config.confirm_samples < self.config.screen_samples:
            raise ValueError("confirm_samples must be >= screen_samples")


__all__ = ["DarwinX"]
