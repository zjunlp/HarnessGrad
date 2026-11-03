"""SkillOpt: fast batch edits and slow epoch guidance, both gated on dev."""

from __future__ import annotations

import json
import random

from flashrsi.algorithms.skillopt import _prompts as prompts
from flashrsi.algorithms.skillopt._patch import (
    _SOURCES,
    _apply_guidance,
    _field,
    _Patch,
    _PatchError,
    _ranked,
)
from flashrsi.core import Algorithm, Snapshot


class SkillOpt(Algorithm):
    """Evolve skill files on two time scales, both gated on a held-out score.

    A fast update reflects on one batch of training tasks and applies bounded
    edits. A slow update runs at the end of an epoch, compares that epoch's
    version against the previous one, and rewrites a protected region of the
    skill that fast edits may not touch -- so a lesson drawn from a whole epoch
    cannot be undone by the next batch's edit.

    Worker calls produce JSON proposals that SkillOpt applies to a checkout, so
    a text LLM and an Agent Worker use exactly the same protocol.
    """

    name = "skillopt"
    description = "Reflect on successes and failures to evolve reusable skills."
    evolve_scope = {"skill"}
    observation_scope = {"skill"}
    _defaults = {
        "num_epochs": 4,
        "batch_size": 40,
        "reflection_minibatch_size": 8,
        "edit_budget": 4,
        "merge_batch_size": 8,
        "seed": 42,
        "max_steps": None,
        "success_threshold": 0.0,
        "use_slow_update": True,
        "use_meta_skill": True,
        "slow_update_samples": 20,
        "max_evidence_chars": 30000,
    }
    _ranges = {
        "num_epochs": (int, 1),
        "batch_size": (int, 1),
        "reflection_minibatch_size": (int, 1),
        "edit_budget": (int, 1),
        # A merge combines at least two proposals.
        "merge_batch_size": (int, 2),
        "seed": int,
        "max_steps": (int | None, 1),
        "success_threshold": float,
        "use_slow_update": bool,
        "use_meta_skill": bool,
        "slow_update_samples": (int, 1),
        "max_evidence_chars": (int, 1),
    }

    async def evolve(self) -> Snapshot:
        current = self.target.snapshot
        self._rejected, self._meta_skill = [], ""
        self._best = (
            await self._evaluate(current, self.benchmark.dev, label="baseline")
        ).score
        previous_epoch = None
        step = 0

        for epoch in range(self.config.num_epochs):
            tasks = list(self.benchmark.train)
            random.Random(self.config.seed + epoch).shuffle(tasks)
            for start in range(0, len(tasks), self.config.batch_size):
                # An int is never equal to an unset budget, so no branch on None.
                if step == self.config.max_steps:
                    return self.target.snapshot
                step += 1
                batch = tuple(tasks[start : start + self.config.batch_size])
                current = await self._fast_update(current, batch, epoch, step)
            # An epoch that ran to its end still earns its slow update; only a
            # new batch is refused once the budget is spent.
            if previous_epoch is not None and (
                self.config.use_slow_update or self.config.use_meta_skill
            ):
                current = await self._slow_update(previous_epoch, current, epoch)
            previous_epoch = current
        return self.target.snapshot

    async def _fast_update(self, current, batch, epoch, step):
        training = await self._evaluate(current, batch, label="train")
        files = self._read_text(current)
        details = {"epoch": epoch, "step": step}
        try:
            proposals = await self._reflect(current, files, training)
            patch = await self._consolidate(files, proposals)
            updated = patch.apply(files, self.config.edit_budget)
        except _PatchError as error:
            # A malformed proposal rejects this update, not the incumbent.
            # Worker execution failures still propagate with their native trace.
            self._record(
                "decision",
                {
                    **details,
                    "kind": "fast",
                    "reason": "invalid_patch",
                    "error": str(error),
                    "accepted": False,
                    "parent_id": current.id,
                },
            )
            return current
        if not patch.edits:
            self._record(
                "decision",
                {**details, "kind": "fast", "reason": "empty_patch", "accepted": False},
            )
            return current
        candidate = self._candidate(current, updated)
        return await self._select(
            current, candidate, "fast", {**details, "patch": patch.as_mapping()}
        )

    async def _reflect(self, snapshot, files, training):
        proposals = {source: [] for source in _SOURCES}
        templates = {
            "failure": prompts.REFLECT_FAILURE,
            "success": prompts.REFLECT_SUCCESS,
        }
        size = self.config.reflection_minibatch_size
        metric = self.benchmark.primary_metric
        for offset in range(0, len(training), size):
            groups = {source: [] for source in _SOURCES}
            for trajectory in training[offset : offset + size]:
                succeeded = (
                    trajectory.status == "finished"
                    and trajectory.metrics[metric] > self.config.success_threshold
                )
                groups["success" if succeeded else "failure"].append(trajectory)
            for source, group in groups.items():
                if not group:
                    continue
                reply = await self.worker.ask(
                    templates[source].format(
                        files=_dump(files),
                        rules=_dump(list(snapshot.component_rules["skill"])),
                        evidence=self._evidence(
                            [self._trajectory_evidence(item) for item in group]
                        ),
                        rejected=_dump(self._rejected),
                        meta_skill=self._meta_skill,
                        budget=self.config.edit_budget,
                    )
                )
                patch = _Patch.parse(reply, source)
                if patch.edits:
                    proposals[source].append(patch)
        return proposals

    async def _consolidate(self, files, proposals):
        merged = []
        for source, group in proposals.items():
            while len(group) > 1:
                width = self.config.merge_batch_size
                next_level = []
                for start in range(0, len(group), width):
                    merged_patch = await self._merge(
                        files, group[start : start + width], source
                    )
                    next_level.append(merged_patch)
                group = next_level
            merged.extend(group)
        patch = await self._merge(files, merged, "failure_first")
        if len(patch.edits) > self.config.edit_budget:
            reply = await self.worker.ask(
                prompts.RANK.format(
                    files=_dump(files),
                    edits=_dump(patch.as_mapping()["edits"]),
                    budget=self.config.edit_budget,
                )
            )
            patch = patch.select(
                _ranked(reply, size=len(patch.edits), limit=self.config.edit_budget)
            )
        return patch

    async def _merge(self, files, patches, source):
        if not patches:
            return _Patch()
        if len(patches) == 1:
            return patches[0]
        reply = await self.worker.ask(
            prompts.MERGE.format(
                files=_dump(files),
                kind=source,
                patches=_dump([patch.as_mapping() for patch in patches]),
            )
        )
        return _Patch.parse(reply, source if source in _SOURCES else "")

    async def _slow_update(self, previous, current, epoch):
        sample = random.Random(self.config.seed + epoch * 2000).sample(
            list(self.benchmark.train),
            min(self.config.slow_update_samples, len(self.benchmark.train)),
        )
        before = await self._evaluate(previous, sample, label="comparison_before")
        after = await self._evaluate(current, sample, label="comparison_after")
        comparison = [
            {
                "previous": self._trajectory_evidence(a),
                "current": self._trajectory_evidence(b),
            }
            for a, b in zip(before, after, strict=True)
        ]
        fields = {
            "previous": _dump(self._read_text(previous)),
            "current": _dump(self._read_text(current)),
            "comparison": self._evidence(comparison),
        }
        if self.config.use_meta_skill:
            self._meta_skill = _field(
                await self.worker.ask(prompts.META_SKILL.format(**fields)),
                "meta_skill_content",
            )
        if not self.config.use_slow_update:
            return current
        reply = await self.worker.ask(prompts.SLOW_UPDATE.format(**fields))
        files = _apply_guidance(self._read_text(current), reply)
        candidate = self._candidate(current, files)
        return await self._select(current, candidate, "slow", {"epoch": epoch})

    async def _select(self, current, candidate, kind, details):
        """Adopt a candidate only on a strict held-out improvement.

        Both time scales land here, so a slow update is gated exactly like a
        fast one and a tie keeps the incumbent.
        """
        if candidate.files == current.files:
            self._record(
                "decision",
                {**details, "kind": kind, "reason": "no_change", "accepted": False},
            )
            return current
        evaluated = await self._evaluate(
            candidate, self.benchmark.dev, label="candidate_dev"
        )
        score = evaluated.score
        accepted = score > self._best
        self._record(
            "decision",
            {
                **details,
                "kind": kind,
                "parent_id": current.id,
                "candidate_id": candidate.id,
                "score": score,
                "baseline_score": self._best,
                "accepted": accepted,
            },
        )
        if accepted:
            self.target.update(candidate)
            self._best = score
            return self.target.snapshot
        if kind == "fast":
            self._rejected = (
                self._rejected + [{"score": score, "patch": details["patch"]}]
            )[-5:]
        return current

    def _check(self) -> None:
        if not self.benchmark.train or not self.benchmark.dev:
            raise ValueError("SkillOpt requires non-empty train and dev splits")


def _dump(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda item: dict(item) if hasattr(item, "items") else str(item),
    )


__all__ = ["SkillOpt"]
