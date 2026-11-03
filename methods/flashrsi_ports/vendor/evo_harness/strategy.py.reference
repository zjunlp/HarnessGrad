"""Evo-Harness: one-shot failures compiled into a two-level online skill harness.

Upstream: https://github.com/A-EVO-Lab/a-evolve/tree/release/evo-harness
Commit: 3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf.
Paper: https://arxiv.org/abs/2608.15071v2.

Curators edit native SKILL.md files through Worker.edit. Native solver discovery
and loading let the final Snapshot run through ordinary Agent.rollout without
an optimizer at inference.
Reflection uses recorded context rather than a solver conversation continuation.
"""

from __future__ import annotations

import json
import re
from statistics import mean

import yaml

from flashrsi.algorithms.evo_harness import _prompts as prompts
from flashrsi.core import Algorithm, Snapshot
from flashrsi.core.snapshot import discard_runtime_files


class EvoHarness(Algorithm):
    """Consume a stream once, compiling failures for later tasks without a gate.

    Each batch uses the same learned library. Only failures produce candidate
    memories; topic and general curators compile them after the whole batch.
    The solver selects native skills during ordinary execution. Final test uses
    the same frozen Snapshot and base evaluation path as any other algorithm.
    """

    name = "evo_harness"
    description = "Compile one-shot failures into native general and topic skills."
    evolve_scope = {"skill"}
    observation_scope = {"skill", "config"}
    _defaults = {
        "batch_size": 4,
        "max_steps": None,
        "success_threshold": 1.0,
        "general_min_tasks": 3,
        "max_general_skills": 10,
        "max_topic_skills": 30,
        "max_skill_chars": 2000,
        "max_library_chars": 48000,
        "max_evidence_chars": 24000,
    }
    _ranges = {
        "batch_size": (int, 1),
        "max_steps": (int | None, 1),
        "success_threshold": float,
        "general_min_tasks": (int, 2),
        "max_general_skills": (int, 1),
        "max_topic_skills": (int, 1),
        "max_skill_chars": (int, 1),
        "max_library_chars": (int, 1),
        "max_evidence_chars": (int, 1),
    }

    async def evolve(self) -> Snapshot:
        current = self.target.snapshot
        streamed = []
        for step, start in enumerate(
            range(0, len(self.benchmark.train), self.config.batch_size), start=1
        ):
            if self.config.max_steps is not None and step > self.config.max_steps:
                break
            tasks = self.benchmark.train[start : start + self.config.batch_size]
            batch = await self._evaluate(current, tasks, label="train")
            streamed.extend(batch.per_task.values())
            memories = await self._reflect(batch, step)
            if memories:
                for level in ("topic", "general"):
                    current = await self._compile(current, memories, level, step)
        self._record(
            "decision",
            {
                "kind": "stream",
                "snapshot_id": current.id,
                "online_score": mean(streamed),
                "online_tasks": len(streamed),
            },
        )
        return current

    async def _reflect(self, batch, step):
        memories = []
        for item in batch:
            if (
                item.status == "finished"
                and batch.per_task[item.task.task_id] >= self.config.success_threshold
            ):
                continue
            # Feedback belongs to the completed attempt only. No retry and
            # no reflection of held-out tasks are part of this online loop.
            evidence = self._trajectory_evidence(item)
            reply = await self.worker.ask(
                prompts.REFLECT.format(evidence=self._evidence([evidence]))
            )
            try:
                # The reflection prompt asks for JSON; remove only its outer
                # fence, leaving any code snippets inside string values intact.
                wrapper = re.search(
                    r"```(?:[\w-]+)?[ \t]*\r?\n(.*)\r?\n```", reply, flags=re.DOTALL
                )
                memory = json.loads(wrapper[1] if wrapper else reply)
                if not isinstance(memory, dict):
                    raise ValueError("candidate memory must be a JSON object")
                if memory.get("skip") is True:
                    continue
                for field in ("lesson", "trigger", "evidence", "scope_hint"):
                    if (
                        not isinstance(memory.get(field), str)
                        or not memory[field].strip()
                    ):
                        raise ValueError(f"candidate memory requires {field}")
                memories.append(
                    {
                        "task_id": item.task.task_id,
                        **{
                            k: memory[k]
                            for k in ("lesson", "trigger", "evidence", "scope_hint")
                        },
                    }
                )
            except ValueError as error:
                self._record(
                    "declined",
                    {"kind": "memory", "step": step, "problem": str(error)},
                )
        return memories

    async def _compile(self, current, memories, level, step):
        library = self._skills(current)
        working = self._run_dir / "working" if self._run_dir else None
        with current.check_out(working_dir=working) as directory:
            await self.worker.edit(
                directory,
                prompts.COMPILE.format(
                    level=level,
                    general_min_tasks=self.config.general_min_tasks,
                    max_skill_chars=self.config.max_skill_chars,
                    max_general_skills=self.config.max_general_skills,
                    max_topic_skills=self.config.max_topic_skills,
                    max_library_chars=self.config.max_library_chars,
                    library=json.dumps(library, ensure_ascii=False, sort_keys=True),
                    memories=self._evidence(memories),
                ),
            )
            discard_runtime_files(directory)
            try:
                candidate = self._commit(current, directory)
                changed = current.changed_paths(candidate)
                updated = self._skills(candidate)
                task_ids = {memory["task_id"] for memory in memories}
                for path in changed:
                    if path in current.files and path not in library:
                        raise ValueError("curator changed an unmanaged skill")
                    if path in candidate.files and path not in updated:
                        raise ValueError("curator removed or omitted skill provenance")
                    if (
                        path not in current.files
                        and path.removesuffix("/SKILL.md") + ".md" in current.files
                    ):
                        raise ValueError("managed skills may not shadow native skills")
                    previous, skill = library.get(path), updated.get(path)
                    # Use raw Snapshot differences: native frontmatter changes
                    # also need the curator's level and this batch's evidence.
                    if (previous and previous["level"] != level) or (
                        skill and skill["level"] != level
                    ):
                        raise ValueError("curator may only edit its requested level")
                    if skill is None:
                        continue
                    sources = set(skill["source_task_ids"])
                    if not sources <= task_ids:
                        raise ValueError(
                            "curation must cite this batch's candidate memories"
                        )
                    if (
                        level == "general"
                        and len(sources) < self.config.general_min_tasks
                    ):
                        raise ValueError("general skill lacks cross-task evidence")
            except ValueError as error:
                self._record(
                    "declined",
                    {
                        "kind": "compile",
                        "level": level,
                        "step": step,
                        "problem": str(error),
                    },
                )
                return current
        self._record(
            "decision",
            {
                "kind": "compile",
                "level": level,
                "step": step,
                "parent_id": current.id,
                "candidate_id": candidate.id,
                "accepted": bool(changed),
                "changed_paths": changed,
            },
        )
        if not changed:
            return current
        self.target.update(candidate)
        return candidate

    def _skills(self, snapshot):
        """Read the managed native procedures within this algorithm's budgets."""
        library = {}
        for path, text in self._read_text(snapshot).items():
            if not path.endswith("/SKILL.md"):
                continue
            match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
            if not match:
                continue
            try:
                meta = yaml.safe_load(match[1])
            except yaml.YAMLError as error:
                raise ValueError(f"invalid skill frontmatter: {path}") from error
            if not isinstance(meta, dict):
                continue
            provenance = meta.get("flashrsi")
            if (
                not isinstance(provenance, dict)
                or provenance.get("algorithm") != "evo_harness"
            ):
                continue
            skill = {
                "name": meta.get("name"),
                "description": meta.get("description"),
                "level": provenance.get("level"),
                "source_task_ids": provenance.get("source_task_ids"),
                "content": text[match.end() :].strip(),
            }
            if skill["level"] not in ("general", "topic"):
                raise ValueError("skill level must be general or topic")
            for field in ("name", "description", "content"):
                if not isinstance(skill[field], str) or not skill[field].strip():
                    raise ValueError(f"skill requires non-empty {field}")
            sources = skill["source_task_ids"]
            if (
                not isinstance(sources, list)
                or not sources
                or any(not isinstance(source, str) or not source for source in sources)
                or len(set(sources)) != len(sources)
            ):
                raise ValueError("skill requires unique source_task_ids")
            library[path] = skill

        for level in ("general", "topic"):
            if (
                sum(skill["level"] == level for skill in library.values())
                > self.config[f"max_{level}_skills"]
            ):
                raise ValueError(f"{level} skill count exceeds budget")
        if any(
            len(skill["content"]) > self.config.max_skill_chars
            for skill in library.values()
        ):
            raise ValueError("skill content exceeds max_skill_chars")
        if (
            sum(
                len(yaml.safe_dump(skill, allow_unicode=True))
                for skill in library.values()
            )
            > self.config.max_library_chars
        ):
            raise ValueError("skill library exceeds max_library_chars")

        return library

    def _check(self):
        if not self.benchmark.train:
            raise ValueError("EvoHarness requires a non-empty train stream")
        ids = [task.task_id for task in self.benchmark.train]
        if len(set(ids)) != len(ids):
            raise ValueError("EvoHarness requires unique train task_id values")
        if self.worker.config.get("type", "agent") != "agent":
            raise ValueError(
                "EvoHarness needs a file-editing Worker; set algorithm.worker.type to 'agent'"
            )
        self._skills(self.target.snapshot)


__all__ = ["EvoHarness"]
