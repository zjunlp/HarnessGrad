"""ACE, ported from FlashRSI's Generator/Reflector/Curator loop.

Upstream: https://github.com/ace-agent/ace
Commit: 82709de050e1db6e6ef2f07bcb0393560b94992a
Port reference: FlashRSI 5efe9b438483710060183c6c3ecbc5062b410a58.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from itertools import islice
from statistics import mean

from flashrsi.algorithms._budgets import budget_reason
from flashrsi.algorithms.ace import _prompts as prompts
from flashrsi.algorithms.ace._playbook import Patch, PlaybookError, Reflection, tag
from flashrsi.core import Algorithm, Snapshot

# Only this suffix is managed by ACE; native instructions remain byte-for-byte.
_MARKER = "\n\n<!-- flashrsi:ace-playbook -->\n"


class _WorkerBudgetExceeded(RuntimeError):
    def __init__(self, reason, error):
        super().__init__(error)
        self.reason = reason


class ACE(Algorithm):
    """Grow a playbook from reflection, adopting every valid ADD without a gate.

    Current keeps learning through regressions, which is the point: a context
    its own optimizer may rewrite collapses into ever shorter summaries of
    itself, so ACE adopts every well-formed addition on the spot and pays for
    the risk once, at the end.

    The two modes differ only in what that payment is. Offline reshuffles the
    training split, validates on dev against a schedule, and hands back the
    best playbook it measured. Online consumes the split once, in its own
    order, with no held-out split to rewind to: each batch is scored before
    anything it teaches can change the playbook that produced it, and the run
    keeps whatever it grew.
    """

    name = "ace"
    description = "Accumulate a playbook through reflection and ADD-only curation."
    evolve_scope = {"prompt"}
    observation_scope = {"prompt"}
    _defaults = {
        # The mode selects the whole promotion policy, so it comes first.
        "mode": "offline",
        "num_epochs": 1,
        "batch_size": 1,
        "max_steps": None,
        "edit_budget": 4,
        "eval_steps": 100,
        "max_reflection_rounds": 3,
        "curator_frequency": 1,
        "playbook_token_budget": 80000,
        "success_threshold": 1.0,
        "max_evidence_chars": 24000,
        "seed": 42,
    }
    _ranges = {
        "mode": ("offline", "online"),
        "num_epochs": (int, 1),
        "batch_size": (int, 1),
        "max_steps": (int | None, 1),
        "edit_budget": (int, 1),
        "eval_steps": (int, 1),
        "max_reflection_rounds": (int, 1),
        "curator_frequency": (int, 1),
        "playbook_token_budget": (int, 1),
        "success_threshold": float,
        "max_evidence_chars": (int, 1),
        "seed": int,
    }

    async def evolve(self) -> Snapshot:
        current = self.target.snapshot
        text = self._read_text(current).get(self._path, "")
        self._prefix, _, self._playbook = text.partition(_MARKER)
        self._rejected, self._streamed = [], []
        offline = self.config.mode == "offline"
        if offline:
            self._best, self._best_score = current, float("-inf")
            await self._validate(current)

        stream = islice(self._batches(), self.config.max_steps)
        for step, tasks in enumerate(stream, start=1):
            batch = await self._evaluate(current, tasks, label="train")
            if not offline:
                # The stream is scored here and nowhere else: before this batch
                # can teach the playbook that produced it, and before a retry
                # hands the target its own diagnosis. Predicting and scoring
                # ahead of every update is what online means.
                self._streamed.extend(batch.per_task.values())
            reflection, batch = await self._reflect(current, tasks, batch)
            if reflection is not None:
                current = self._adopt(
                    current, tag(self._playbook, reflection.tags), "tally"
                )
                if step % self.config.curator_frequency == 0:
                    current = await self._curate(current, reflection, batch, step)
            # Validation belongs to the schedule, even when curation is skipped
            # or declined. A counter-only update is still a different context.
            if offline and step % self.config.eval_steps == 0:
                await self._validate(current)

        if not offline:
            # A stream has no held-out split to rewind to: every adoption along
            # the way was already the decision, so nothing is left to select.
            # What it reports instead is its own score, each batch measured
            # before it could change the playbook that produced it -- not a
            # held-out result, and not the run's final test.
            self._record(
                "decision",
                {
                    "kind": "stream",
                    "snapshot_id": current.id,
                    "online_score": mean(self._streamed),
                    "online_tasks": len(self._streamed),
                },
            )
            return current
        if self._validated_id != current.id:
            await self._validate(current)
        return self._promote(current, self._best, score=self._best_score)

    def _batches(self):
        """Slice the training split into the stream the Generator consumes.

        Ordering only; ``max_steps`` bounds this from the caller, which is the
        one place a step is counted. Reshuffling presupposes the whole split is
        in hand, so it is offline's privilege; an online stream arrives in its
        own order and is consumed once.
        """
        for epoch in range(self.config.num_epochs):
            tasks = list(self.benchmark.train)
            if self.config.mode == "offline":
                random.Random(self.config.seed + epoch).shuffle(tasks)
            for start in range(0, len(tasks), self.config.batch_size):
                yield tuple(tasks[start : start + self.config.batch_size])

    async def _reflect(self, current, tasks, batch):
        for round_index in range(self.config.max_reflection_rounds):
            try:
                reply = await self.worker.ask(
                    prompts.REFLECT.format(
                        playbook=self._playbook, evidence=self._render_evidence(batch)
                    )
                )
            except _WorkerBudgetExceeded as error:
                self._decline_budget(current, error, "reflect", round=round_index + 1)
                # A missing diagnosis cannot justify tags or Curator input.
                return None, batch
            reflection = Reflection.parse(reply)
            metric = self.benchmark.primary_metric
            if all(
                item.status == "finished"
                and item.metrics[metric] >= self.config.success_threshold
                for item in batch
            ):
                break
            # Retry only between diagnoses: the Curator must see a reflection
            # of the very attempt it receives, never an unreflected last retry.
            if round_index + 1 < self.config.max_reflection_rounds:
                retry = prompts.RETRY.format(reflection=reflection.content)
                retried = tuple(
                    replace(task, question=retry + "\n" + task.question)
                    for task in tasks
                )
                batch = await self._evaluate(current, retried, label="retry")
        return reflection, batch

    async def _curate(self, current, reflection, batch, step):
        try:
            reply = await self.worker.ask(
                prompts.CURATE.format(
                    playbook=self._playbook,
                    reflection=reflection.content,
                    evidence=self._render_evidence(batch),
                    progress=(
                        f"step {step}; budget {self.config.playbook_token_budget} "
                        f"approximate tokens; at most {self.config.edit_budget} "
                        f"ADD operations per update; declined={json.dumps(self._rejected)}"
                    ),
                ),
            )
        except _WorkerBudgetExceeded as error:
            self._decline_budget(current, error, "curate", step=step)
            return current
        try:
            grown = Patch.parse(reply).apply(self._playbook, self.config.edit_budget)
            # Preserve the old port's explicit 4 chars/token approximation;
            # this is a playbook size bound, not model tokenizer accounting.
            if (len(grown) + 3) // 4 > self.config.playbook_token_budget:
                raise PlaybookError("playbook token budget exceeded")
        except PlaybookError as error:
            declined = {"step": step, "problem": str(error)}
            self._rejected = (self._rejected + [declined])[-4:]
            self._record("declined", {"snapshot_id": current.id, **declined})
            return current
        return self._adopt(current, grown, "curate")

    def _observe_worker(self, prompt, result):
        error = super()._observe_worker(prompt, result)
        reason = budget_reason(result)
        if reason is not None:
            raise _WorkerBudgetExceeded(reason, error)
        return error

    def _decline_budget(self, current, error, kind, **details):
        declined = {
            **details,
            "snapshot_id": current.id,
            "kind": kind,
            "reason": "worker_budget_exhausted",
            "budget": error.reason,
            "problem": str(error),
            "accepted": False,
        }
        self._rejected = (self._rejected + [declined])[-4:]
        self._record("declined", declined)

    def _adopt(self, current, playbook, kind):
        if playbook == self._playbook:
            return current
        candidate = self._candidate(
            current, {self._path: self._prefix + _MARKER + playbook}
        )
        self.target.update(candidate)
        self._playbook = playbook
        self._record(
            "decision",
            {
                "kind": kind,
                "parent_id": current.id,
                "candidate_id": candidate.id,
                "accepted": True,
            },
        )
        return candidate

    async def _validate(self, current):
        score = (
            await self._evaluate(current, self.benchmark.dev, label="validation")
        ).score
        self._validated_id = current.id
        # Equal dev scores retain the previously selected playbook.
        if score > self._best_score:
            self._best, self._best_score = current, score

    def _render_evidence(self, batch):
        # Only training attempts reach the Worker. Ground truth is feedback to
        # the Reflector, never added directly to ordinary target execution.
        return self._evidence(
            {
                "task_id": item.task.task_id,
                "input": item.task.question,
                "ground_truth": item.task.ground_truth,
                "output": item.output,
                "status": item.status,
                "scores": dict(item.metrics),
                "error": item.error_message,
            }
            for item in batch
        )

    def _check(self):
        if not self.benchmark.train:
            raise ValueError("ACE requires a non-empty train split")
        # Only offline validates, so only offline needs somewhere to validate.
        if self.config.mode == "offline" and not self.benchmark.dev:
            raise ValueError("offline ACE requires a non-empty dev split")
        self._path = self._instruction_path()
        if self.config.max_reflection_rounds > 1 and any(
            task.env is not None for task in self.benchmark.train
        ):
            raise ValueError(
                "ACE reflection retries require question-based tasks; "
                "environment tasks need max_reflection_rounds=1"
            )


__all__ = ["ACE"]
