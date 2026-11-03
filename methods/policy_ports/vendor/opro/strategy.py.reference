"""OPRO: accumulate scored instructions, re-rank them, promote the best once."""

from __future__ import annotations

import random
from dataclasses import dataclass

from flashrsi.algorithms import _replies
from flashrsi.algorithms.opro import _prompts as prompts
from flashrsi.core import Algorithm, Snapshot


@dataclass(frozen=True, slots=True)
class _Scored:
    """One evaluated instruction: the unit OPRO accumulates and re-ranks."""

    text: str
    score: float
    snapshot: Snapshot


class OPRO(Algorithm):
    """Search one instruction file by showing the optimizer its own scores.

    The accumulated history is the point: OPRO never moves an incumbent during
    the search, so every instruction it has scored stays in the pool and is
    re-ranked on the next step. Only the final promotion changes the version.
    """

    name = "opro"
    description = "Optimize one instruction file from its scored history."
    evolve_scope = {"prompt"}
    observation_scope = {"prompt"}
    _defaults = {
        # Published OPRO uses 200 optimizer steps proposing 8 instructions each.
        "max_steps": 200,
        "num_candidates": 8,
        "history_size": 20,
        "score_threshold": 0.1,
        "few_shot_size": 3,
        "max_evidence_chars": 24000,
        "seed": 42,
    }
    _ranges = {
        "max_steps": (int, 1),
        "num_candidates": (int, 1),
        "history_size": (int, 1),
        "score_threshold": float,
        "few_shot_size": (int, 1),
        "max_evidence_chars": (int, 1),
        "seed": int,
    }

    async def evolve(self) -> Snapshot:
        train, path = self.benchmark.train, self._path
        current = self.target.snapshot
        results = await self._evaluate(current, train, label="baseline")
        # The starting instruction is row zero of the history OPRO re-ranks.
        self._history = [
            _Scored(self._read_text(current).get(path, ""), results.score, current)
        ]
        self._leader = results

        for step in range(self.config.max_steps):
            parent = self._best()
            for text in await self._propose(step):
                candidate = self._candidate(parent.snapshot, {path: text})
                # OPRO scores on the training split itself, not a held-out set.
                results = await self._evaluate(candidate, train, label="candidate")
                scored = _Scored(text, results.score, candidate)
                # Evaluated before appending, so the evidence follows exactly
                # the instruction _best() will return, ties included.
                if scored.score > self._best().score:
                    self._leader = results
                self._history.append(scored)

        best = self._best()
        return self._promote(
            current,
            best.snapshot,
            score=best.score,
            baseline_score=self._history[0].score,
            history=[
                {
                    "text": item.text,
                    "score": item.score,
                    "snapshot_id": item.snapshot.id,
                }
                for item in self._history
            ],
        )

    def _best(self) -> _Scored:
        """The global best so far; ties keep the earlier instruction."""
        return max(self._history, key=lambda item: item.score)

    async def _propose(self, step: int) -> tuple[str, ...]:
        """Sample this step's novel instructions from the scored history.

        The optimizer reads the ascending score trajectory and a few examples
        of what the leading instruction actually produced -- upstream shows raw
        question/answer pairs there; FlashRSI has trajectories.

        A repeat is dropped rather than resampled, so a step may yield fewer
        than num_candidates instructions.
        """
        sample = random.Random(self.config.seed + step).sample(
            self._leader, min(self.config.few_shot_size, len(self._leader))
        )
        prompt = prompts.META.format(
            path=self._path,
            history=self._render_history(),
            evidence=self._evidence(
                {
                    "task": item.task.question,
                    "output": item.output,
                    "metrics": dict(item.metrics),
                }
                for item in sample
            ),
        )
        seen = {item.text for item in self._history}
        proposed = []
        for _ in range(self.config.num_candidates):
            text = _replies.tagged(await self.worker.ask(prompt), "INS")
            if text and text not in seen:
                seen.add(text)
                proposed.append(text)
        return tuple(proposed)

    def _render_history(self) -> str:
        """The ascending prompt/value trajectory the optimizer reads.

        Upstream hands the optimizer integer score buckets over 0--100 while
        FlashRSI keeps benchmark scores in the canonical 0--1 range, so the
        threshold stays in raw score space and only the rendering bucketizes.
        """
        rows = sorted(
            (
                item
                for item in self._history
                if item.score >= self.config.score_threshold
            ),
            key=lambda item: item.score,
        )[-self.config.history_size :]
        return (
            "".join(
                prompts.ROW.format(text=item.text, score=round(item.score * 100))
                for item in rows
            )
            or prompts.NO_HISTORY
        )

    def _check(self) -> None:
        if not self.benchmark.train:
            raise ValueError("OPRO requires a non-empty train split")
        self._path = self._instruction_path()


__all__ = ["OPRO"]
