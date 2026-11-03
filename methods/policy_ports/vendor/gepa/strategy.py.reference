"""GEPA: reflect on a parent's own failures, screen cheaply, keep the frontier.

Sampling reference: https://github.com/gepa-ai/gepa-artifact
Commit: cbefbc1aa0f43dd39874ec4bf42211365dbda42e.
"""

from __future__ import annotations

import random
from collections import Counter

from flashrsi.algorithms import _replies
from flashrsi.algorithms.gepa import _population as pop
from flashrsi.algorithms.gepa import _prompts as prompts
from flashrsi.algorithms.gepa._population import Member
from flashrsi.core import Algorithm, Snapshot


class GEPA(Algorithm):
    """Genetic-Pareto search over one instruction file.

    Evolving only the aggregate best gets stuck, so a candidate that wins a
    *single* validation task stays selectable: every admission here reads the
    per-task scores rather than their mean. The reflection minibatch screens
    candidates before full validation; optionally, ties also pass this screen.
    Final validation ties retain the earlier best candidate.

    Upstream rotates over the modules of a compound system. Each harness here
    names a single instruction file, so this port rewrites that one file and
    leaves the rotation out rather than shipping an unused generalisation.
    """

    name = "gepa"
    description = "Evolve instructions by Pareto search over per-task scores."
    evolve_scope = {"prompt"}
    observation_scope = {"prompt"}
    _defaults = {
        # None scales the budget to the data; see _budget. One fixed number is
        # either wasteful or far too small depending on the validation split.
        "max_metric_calls": None,
        "num_epochs": None,
        "reflection_minibatch_size": 3,
        "skip_perfect_score": True,
        "acceptance_criterion": "strict_improvement",
        "candidate_selection_strategy": "pareto",
        "max_evidence_chars": 24000,
        "seed": 42,
        # Upstream's system-aware merge is not part of this migration.
        "use_merge": False,
    }
    _ranges = {
        "max_metric_calls": (int | None, 1),
        "num_epochs": (int | None, 1),
        "reflection_minibatch_size": (int, 1),
        "skip_perfect_score": bool,
        "acceptance_criterion": ("strict_improvement", "improvement_or_equal"),
        "candidate_selection_strategy": ("pareto", "current_best"),
        "max_evidence_chars": (int, 1),
        "seed": int,
        "use_merge": bool,
    }

    async def evolve(self) -> Snapshot:
        train, dev = self.benchmark.train, self.benchmark.dev
        current = self.target.snapshot
        budget, cost = self._resolved_budget

        results = await self._evaluate(current, dev, label="baseline")
        self._population = [
            Member(
                index=0,
                snapshot=current,
                text=self._read_text(current).get(self._path, ""),
                per_task=results.per_task,
                score=results.score,
            )
        ]
        self._metric_calls = len(dev)
        training_batches = self._minibatches(train)

        iteration = 0
        for minibatch in training_batches:
            if self._metric_calls + cost > budget:
                break
            await self._iterate(minibatch, dev, iteration)
            iteration += 1

        best = self._population[pop.best_index(self._population)]
        return self._promote(
            current,
            best.snapshot,
            path=self._path,
            score=best.score,
            baseline_score=self._population[0].score,
            metric_calls=self._metric_calls,
            iterations=iteration,
            acceptance_criterion=self.config.acceptance_criterion,
            tie_break="earlier_candidate",
            population=[
                {
                    "index": member.index,
                    "snapshot_id": member.snapshot.id,
                    "parents": list(member.parents),
                    "text": member.text,
                    "score": member.score,
                    "per_task": dict(member.per_task),
                }
                for member in self._population
            ],
        )

    def _minibatches(self, train):
        """Traverse shuffled epochs, padding with the least-used task indices."""
        rng = random.Random(self.config.seed)
        frequencies = Counter()
        size = min(self.config.reflection_minibatch_size, len(train))
        epoch = 0
        while self.config.num_epochs is None or epoch < self.config.num_epochs:
            indices = list(range(len(train)))
            rng.shuffle(indices)
            frequencies.update(indices)
            # Match the artifact's padding, including a full extra batch when
            # train divides evenly. Reverse insertion order breaks frequency
            # ties, as in its Counter.most_common()[::-1] selection.
            for _ in range(size - len(train) % size):
                index = frequencies.most_common()[-1][0]
                indices.append(index)
                frequencies[index] += 1
            for start in range(0, len(indices), size):
                yield tuple(train[index] for index in indices[start : start + size])
            epoch += 1

    async def _iterate(self, minibatch, dev, iteration: int) -> None:
        """One reflective mutation: select, propose, screen, admit."""
        rng = random.Random(self.config.seed + iteration)
        parent = self._select(rng)
        details = {"iteration": iteration, "parent_index": parent.index}

        # One rollout serves both purposes: it screens the parent and is the
        # evidence the reflection reads.
        before = await self._evaluate(parent.snapshot, minibatch, label="parent")
        self._metric_calls += len(minibatch)

        evidence = tuple(
            item
            for item in before
            if not self.config.skip_perfect_score
            or before.per_task[item.task.task_id] != 1.0
        )
        # skip_perfect_score controls whether perfect batches supply reflection
        # evidence, independently of the configured acceptance criterion.
        if not evidence:
            return self._reject(details, "perfect_minibatch")

        text = await self._propose(parent, evidence)
        # An empty reply is a Worker or parsing problem, not a search outcome;
        # keep it distinct from a proposal that simply repeated the parent.
        if not text:
            return self._reject(details, "empty_proposal")
        if _text_key(text) == _text_key(parent.text):
            return self._reject(details, "unchanged_instruction")
        if any(
            _text_key(text) == _text_key(member.text) for member in self._population
        ):
            return self._reject(details, "duplicate_instruction")

        candidate = self._candidate(parent.snapshot, {self._path: text})
        after = await self._evaluate(candidate, minibatch, label="candidate_minibatch")
        self._metric_calls += len(minibatch)
        details.update(
            acceptance_criterion=self.config.acceptance_criterion,
            parent_minibatch_score=before.score,
            candidate_minibatch_score=after.score,
        )
        # Screen on the minibatch before spending a whole validation pass. Both
        # ran the same tasks, so comparing the means compares the sums.
        if after.score < before.score or (
            after.score == before.score
            and self.config.acceptance_criterion == "strict_improvement"
        ):
            return self._reject(details, "failed_minibatch_screen")

        results = await self._evaluate(candidate, dev, label="candidate_dev")
        self._metric_calls += len(dev)
        self._admit(candidate, text, parent, results, details)

    async def _propose(self, parent: Member, evidence) -> str:
        """Reflect on the parent's own trajectories and rewrite the instruction.

        Like upstream, reflection receives labeled training feedback and the
        parent's execution trace, rather than a scalar score alone. References
        stay in the Worker prompt; the target still receives only the question.
        """
        reply = await self.worker.ask(
            prompts.REFLECT.format(
                path=self._path,
                instruction=parent.text,
                evidence=self._evidence(
                    {
                        "task": item.task.question,
                        "output": item.output,
                        "metrics": dict(item.metrics),
                        "feedback": {
                            "ground_truth": item.task.ground_truth,
                            "status": item.status,
                            "error_message": item.error_message,
                        },
                        "trajectory": self._trajectory_evidence(item)["messages"],
                    }
                    for item in evidence
                ),
            ),
        )
        return _replies.fenced(reply)

    def _select(self, rng: random.Random) -> Member:
        """Pick the parent to mutate: the aggregate best, or from the frontier."""
        population = self._population
        if self.config.candidate_selection_strategy == "current_best":
            return population[pop.best_index(population)]
        front = pop.prune(pop.frontier(population), population)
        # Sampling the union weights a member by how many tasks it owns.
        weighted = [index for members in front.values() for index in sorted(members)]
        if not weighted:
            return population[pop.best_index(population)]
        return population[rng.choice(weighted)]

    def _admit(self, snapshot, text, parent, results, details) -> None:
        """Add a candidate to the population.

        Admission is not promotion: GEPA promotes the aggregate best once, after
        the budget is spent, so the population holds members that never became
        the current version.
        """
        member = Member(
            index=len(self._population),
            snapshot=snapshot,
            text=text,
            per_task=results.per_task,
            score=results.score,
            parents=(parent.index,),
        )
        self._population.append(member)
        self._record(
            "decision",
            {
                **details,
                "kind": "admit",
                "parent_id": parent.snapshot.id,
                "candidate_id": snapshot.id,
                "score": member.score,
                "parent_score": parent.score,
                "accepted": True,
            },
        )

    def _reject(self, details, reason: str) -> None:
        self._record("decision", {**details, "kind": "reject", "reason": reason})

    def _check(self) -> None:
        if not self.benchmark.train or not self.benchmark.dev:
            raise ValueError("GEPA requires non-empty train and dev splits")
        seen = set()
        for task in self.benchmark.dev:
            if task.task_id in seen:
                raise ValueError(
                    "GEPA scores dev tasks individually and needs unique "
                    f"task_id values: {task.task_id}"
                )
            seen.add(task.task_id)
        self._path = self._instruction_path()
        if self.config.use_merge:
            raise ValueError(
                "GEPA's system-aware merge is not implemented; set "
                "algorithm.use_merge to false"
            )
        # Resolved here, before the runner creates an experiment directory, so
        # an unusable budget is reported like any other configuration error.
        self._resolved_budget = self._budget(self.benchmark.train, self.benchmark.dev)

    def _budget(self, train, dev) -> tuple[int, int]:
        """Resolve the rollout budget and check that one iteration fits in it.

        An iteration screens the parent on the minibatch, screens the candidate
        on the same tasks, then evaluates it on the whole dev split.
        """
        minibatch = min(self.config.reflection_minibatch_size, len(train))
        cost = 2 * minibatch + len(dev)
        needed = len(dev) + cost
        budget = self.config.max_metric_calls
        if budget is None:
            # An epoch-limited run needs room for every batch even when all
            # candidates reach dev. Budget-only runs retain ten full iterations.
            iterations = 10
            if self.config.num_epochs is not None:
                padding = minibatch - len(train) % minibatch
                iterations = (
                    self.config.num_epochs * (len(train) + padding) // minibatch
                )
            budget = max(100, len(dev) + iterations * cost)
        if budget < needed:
            raise ValueError(
                f"algorithm.max_metric_calls={budget} cannot complete one "
                f"iteration; this run needs at least {needed}"
            )
        return budget, cost


def _text_key(text: str) -> str:
    """Ignore platform line endings for deduplication, retaining original files."""
    return text.replace("\r\n", "\n").rstrip("\n")


__all__ = ["GEPA"]
