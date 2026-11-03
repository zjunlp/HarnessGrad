"""ModularRSI: contrastive diagnosis, isolated module evolution, then integration.

Upstream: https://github.com/IQuestLab/ModularRSI
Commit: b5c72c36b0d08ff93f00ee202a8fbdebe849dfb9
Reference: src/harbor/agents/terminus_2_modular/self_evo/
This independently written port uses native harness files rather than
upstream's discoverable function library and task-aware runtime composer.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping
from contextlib import ExitStack
from difflib import unified_diff
from pathlib import Path
from tempfile import TemporaryDirectory

from flashrsi.algorithms.modular_rsi import _prompts as prompts
from flashrsi.core import Algorithm, Snapshot
from flashrsi.core.snapshot import discard_runtime_files

MODULES = (
    "agent_loop",
    "tool_use",
    "observation_management",
    "context_management",
    "task_completion_detection",
)


def _json_reply(reply):
    # JSON strings may themselves contain code fences. Only remove the outer
    # wrapper, never search for the last fence inside the serialized payload.
    text = reply.strip()
    opening, newline, body = text.partition("\n")
    if newline and opening.strip() in ("```", "```json") and body.endswith("```"):
        text = body[:-3].strip()
    return json.loads(text)


class ModularRSI(Algorithm):
    """Contrast repeated executions; evolve isolated module branches from one seed.

    Runtime/diff gates retain general executable changes rather than requiring
    a dev-score increase. Search uses the benchmark's training tasks.
    """

    name = "modular_rsi"
    description = "Contrastive, module-restricted harness self-improvement."
    evolve_scope = {"*"}
    _defaults = {
        "modules": {},
        "num_epochs": 3,
        "batch_size": 10,
        "num_rollouts": 3,
        "integration_epochs": 1,
        "validation_samples": 2,
        "edit_budget": 1,
        "success_threshold": 1.0,
        "seed": 42,
        "max_evidence_chars": 60000,
    }
    _ranges = {
        "num_epochs": (int, 1),
        "batch_size": (int, 1),
        "num_rollouts": (int, 2),
        "integration_epochs": (int, 1),
        "validation_samples": (int, 1),
        "edit_budget": (int, 1),
        "success_threshold": float,
        "seed": int,
        "max_evidence_chars": (int, 1),
    }

    async def evolve(self) -> Snapshot:
        seed = self.target.snapshot
        branches, histories = {}, {}
        rng = random.Random(self.config.seed)
        for module in self.config.modules:
            branches[module], histories[module] = await self._evolve_branch(
                seed, module, rng
            )

        history = [{"module": m, "changes": h} for m, h in histories.items()]
        combined = (
            await self._edit(
                seed,
                "integration",
                prompts.MERGE.format(
                    files=json.dumps(dict(self.config.modules)),
                    history=self._evidence(history),
                ),
                branches=branches,
                findings=history,
            )
            or seed
        )
        # Combining independently valid changes may break their interfaces.
        # Diagnose even a failed merge before choosing a runnable version.
        validated = await self._execution_gate(
            combined, self.benchmark.train, "integration"
        )
        fallback = combined if validated else seed
        current, _ = await self._evolve_branch(
            combined, "integration", rng, history=history, branches=branches
        )
        if (not validated and current == combined) or not await self._execution_gate(
            current, self.benchmark.train, "final"
        ):
            current = fallback
        return self._promote(
            seed, current, modules={m: s.id for m, s in branches.items()}
        )

    async def _evolve_branch(
        self, current, module, rng, *, history=None, branches=None
    ):
        """Each isolated module and the merged harness use the same batch search."""
        integration = module == "integration"
        epochs = (
            self.config.integration_epochs if integration else self.config.num_epochs
        )
        files = (
            dict(self.config.modules) if integration else self.config.modules[module]
        )
        history, memory = [] if history is None else history, {}
        for _ in range(epochs):
            tasks = list(self.benchmark.train)
            rng.shuffle(tasks)
            for start in range(0, len(tasks), self.config.batch_size):
                batch = tasks[start : start + self.config.batch_size]
                evidence = await self._contrast(current, batch, memory, module)
                context = {
                    "module": module,
                    "files": json.dumps(files),
                    "evidence": self._contrast_evidence(evidence),
                    "history": self._evidence(history),
                }
                findings = []
                if integration:
                    prompt = prompts.INTEGRATE.format(**context)
                else:
                    reply = await self.worker.ask(prompts.ANALYZE.format(**context))
                    findings = self._findings(reply, module, batch)
                    if not findings:
                        continue
                    prompt = prompts.EDIT.format(
                        **context, findings=self._evidence(findings)
                    )
                candidate = await self._propose(
                    current, module, prompt, batch, findings, rng, branches=branches
                )
                if candidate is not None:
                    if not integration:
                        history.append(
                            {
                                "parent_id": current.id,
                                "candidate_id": candidate.id,
                                "findings": findings,
                            }
                        )
                    current = candidate
        return current, history

    async def _contrast(self, snapshot, tasks, memory, module):
        rows = {
            task.task_id: {
                "task_id": task.task_id,
                "question": task.question,
                "successes": [],
                "failures": [],
            }
            for task in tasks
        }
        for _ in range(self.config.num_rollouts):
            try:
                results = await self._evaluate(snapshot, tasks, label="contrast")
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                # No graded trajectories exist, so the failing task is unknown.
                # Keep the batch-level error as evidence rather than aborting
                # before the Worker can repair a broken combination.
                failure = {
                    "status": "error",
                    "error_message": str(error),
                    "snapshot_id": snapshot.id,
                    "scope": "batch evaluation; failing task unknown",
                }
                self._record(
                    "contrast_error",
                    {"module": module, "task_ids": list(rows), **failure},
                )
                for row in rows.values():
                    row["failures"].append(failure)
                continue
            for trajectory in results:
                key = (
                    "successes"
                    if trajectory.status == "finished"
                    and results.per_task[trajectory.task.task_id]
                    >= self.config.success_threshold
                    else "failures"
                )
                rows[trajectory.task.task_id][key].append(
                    self._trajectory_evidence(trajectory)
                )
        for task_id, row in rows.items():
            row["group"] = (
                "contrastive"
                if row["successes"] and row["failures"]
                else "positive"
                if row["successes"]
                else "negative"
            )
            if not row["successes"] and task_id in memory:
                row["historical_success"] = memory[task_id]
            if row["successes"]:
                memory[task_id] = {
                    "snapshot_id": snapshot.id,
                    "trajectory": row["successes"][-1],
                }
        self._record(
            "contrast_groups",
            {
                "module": module,
                "snapshot_id": snapshot.id,
                "groups": {tid: row["group"] for tid, row in rows.items()},
            },
        )
        return list(rows.values())

    def _contrast_evidence(self, rows):
        evidence = self._evidence(rows)
        if len(evidence) <= self.config.max_evidence_chars:
            return evidence

        # Full trajectories remain in the records and success memory. For long
        # batches, reserve one example per polarity/task before trimming text.
        compact, texts = [], []

        def example(record):
            item = {"status": record["status"], "metrics": record.get("metrics", {})}
            if "scope" in record:
                item["scope"] = record["scope"]
            for key, value in (
                ("output", record.get("output", "")),
                ("error_message", record.get("error_message") or ""),
                ("trace", json.dumps(record.get("messages", []), ensure_ascii=False)),
            ):
                texts.append((item, key, value))
            return item

        for row in rows:
            item = {
                "task_id": row["task_id"],
                "group": row["group"],
                "success_count": len(row["successes"]),
                "failure_count": len(row["failures"]),
                "successes": [example(r) for r in row["successes"][-1:]],
                "failures": [example(r) for r in row["failures"][-1:]],
            }
            if "historical_success" in row:
                previous = row["historical_success"]
                item["historical_success"] = {
                    "snapshot_id": previous["snapshot_id"],
                    "trajectory": example(previous["trajectory"]),
                }
            texts.append((item, "question", row["question"]))
            compact.append(item)

        limit = self.config.max_evidence_chars // len(texts)
        while True:
            head, tail = limit // 2, limit - limit // 2
            for item, key, value in texts:
                item[key] = (
                    value
                    if len(value) <= limit
                    else value[:head] + "…" + value[len(value) - tail :]
                )
            evidence = self._evidence(compact)
            if len(evidence) <= self.config.max_evidence_chars:
                return evidence
            if limit == 0:
                raise ValueError(
                    "max_evidence_chars is too small for contrast task IDs and paired summaries"
                )
            limit //= 2

    def _findings(self, reply, module, tasks):
        values = _json_reply(reply)
        if not isinstance(values, list):
            raise ValueError("ModularRSI analysis must return a list of findings")
        task_ids, consolidated = {task.task_id for task in tasks}, {}
        for item in values:
            if (
                not isinstance(item, dict)
                or set(item) != {"target", "task_ids", "rationale", "change"}
                or item["target"] not in self.config.modules[module]
                or not isinstance(item["task_ids"], list)
                or not item["task_ids"]
                or any(
                    not isinstance(tid, str) or tid not in task_ids
                    for tid in item["task_ids"]
                )
                or any(
                    not isinstance(item[k], str) or not item[k].strip()
                    for k in ("rationale", "change")
                )
            ):
                raise ValueError(
                    "finding must cite an editable file and supplied tasks"
                )
            row = consolidated.setdefault(
                item["target"],
                {"target": item["target"], "task_ids": set(), "proposals": []},
            )
            row["task_ids"].update(item["task_ids"])
            row["proposals"].append(
                {"rationale": item["rationale"], "change": item["change"]}
            )
        for row in consolidated.values():
            row["task_ids"] = sorted(row["task_ids"])
        return sorted(consolidated.values(), key=lambda row: -len(row["task_ids"]))[
            : self.config.edit_budget
        ]

    async def _edit(self, parent, module, prompt, *, findings=(), branches=None):
        """Edit and review a checkout; reference versions disappear before commit."""
        working = self._run_dir / "working" if self._run_dir else None
        with parent.check_out(working_dir=working) as directory:
            with ExitStack() as references:
                if branches is not None:
                    root = Path(
                        references.enter_context(
                            TemporaryDirectory(prefix="modules-", dir=directory)
                        )
                    )
                    sources = {
                        name: references.enter_context(
                            snapshot.check_out(working_dir=root / name)
                        )
                        .relative_to(directory)
                        .as_posix()
                        for name, snapshot in branches.items()
                    }
                    prompt += (
                        "\nModule reference directories (do not edit): "
                        + json.dumps(sources)
                    )
                await self.worker.edit(directory, prompt)
            discard_runtime_files(directory)
            try:
                candidate = self._commit(parent, directory)
                allowed = (
                    self.config.modules[module]
                    if module != "integration"
                    else [s for files in self.config.modules.values() for s in files]
                )
                changed = parent.changed_paths(candidate)
                if set(changed) - set(allowed):
                    raise ValueError("edit outside module files")
                for path in changed:
                    if path.endswith(".py") and path in candidate.files:
                        compile(candidate.files[path].content, path, "exec")
                if not changed:
                    raise ValueError("harness unchanged")
            except (ValueError, SyntaxError) as error:
                self._record(
                    "decision",
                    {
                        "kind": "decline",
                        "module": module,
                        "parent_id": parent.id,
                        "problem": str(error),
                    },
                )
                return None
        changes = []
        for path in parent.changed_paths(candidate):
            before, after = parent.files.get(path), candidate.files.get(path)
            sources = [
                entry.content.decode("utf-8", errors="replace").splitlines(
                    keepends=True
                )
                if entry
                else []
                for entry in (before, after)
            ]
            changes.append(
                {
                    "path": path,
                    "diff": "".join(
                        unified_diff(
                            *sources,
                            fromfile="before/" + path,
                            tofile="after/" + path,
                        )
                    ),
                    "before_executable": before.executable if before else None,
                    "after_executable": after.executable if after else None,
                }
            )
        # A review of an incomplete patch is not a validation gate. Differences
        # usually fit even when whole harness files exceed the prompt budget.
        if len(self._evidence(changes)) > self.config.max_evidence_chars:
            self._record(
                "decision",
                {
                    "kind": "decline",
                    "module": module,
                    "candidate_id": candidate.id,
                    "problem": "diff exceeds review evidence budget",
                },
            )
            return None
        reply = await self.worker.ask(
            prompts.REVIEW.format(
                module=module,
                findings=self._evidence(findings),
                changes=self._evidence(changes),
            )
        )
        verdict = _json_reply(reply)
        if (
            not isinstance(verdict, dict)
            or set(verdict) != {"accept", "reason"}
            or type(verdict["accept"]) is not bool
            or not isinstance(verdict["reason"], str)
            or not verdict["reason"].strip()
        ):
            raise ValueError(
                "ModularRSI review requires boolean accept and non-empty reason"
            )
        self._record(
            "review",
            {"module": module, "candidate_id": candidate.id, **verdict},
        )
        return candidate if verdict["accept"] else None

    async def _propose(
        self, parent, module, prompt, tasks, findings, rng, *, branches=None
    ):
        """Sample execution before advancing a module's current Snapshot."""
        candidate = await self._edit(
            parent, module, prompt, findings=findings, branches=branches
        )
        if candidate is None:
            return None
        accepted = await self._execution_gate(
            candidate,
            rng.sample(list(tasks), min(len(tasks), self.config.validation_samples)),
            module,
        )
        self._record(
            "decision",
            {
                "kind": "admit" if accepted else "decline",
                "module": module,
                "parent_id": parent.id,
                "candidate_id": candidate.id,
            },
        )
        return candidate if accepted else None

    async def _execution_gate(self, candidate, tasks, module):
        gate = {"module": module, "snapshot_id": candidate.id}
        try:
            results = await self._evaluate(candidate, tasks, label="execution_gate")
            gate["passed"] = all(row.status == "finished" for row in results)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            gate.update(passed=False, problem=str(error))
        self._record("gate", gate)
        return gate["passed"]

    def _check(self):
        if not self.benchmark.train:
            raise ValueError("ModularRSI requires non-empty benchmark.train")
        evolution, heldout = (
            self.benchmark.train,
            (*self.benchmark.dev, *self.benchmark.test),
        )
        ids = {t.task_id for t in evolution}
        # Distinct tasks may share a domain policy or environment directory.
        # Source-level independence is optional; held-out task IDs stay sealed.
        if len(ids) != len(evolution) or any(t.task_id in ids for t in heldout):
            raise ValueError(
                "ModularRSI train task IDs must be unique and disjoint from dev/test"
            )
        if (
            self.worker.config.get("type", "agent") != "agent"
            or self.worker is self.target
        ):
            raise ValueError(
                "ModularRSI requires a separate agent Worker for file editing"
            )
        modules = self.config.modules
        if not isinstance(modules, Mapping) or set(modules) not in (
            set(MODULES),
            {"prompt", "skill", "hook"},
        ):
            raise ValueError(
                "algorithm.modules must map the five runtime modules or "
                "the three extension modules (prompt, skill, hook) to files"
            )
        for module in modules:
            if (
                not isinstance(modules[module], list)
                or not modules[module]
                or any(not isinstance(s, str) or not s for s in modules[module])
            ):
                raise ValueError("each module requires a non-empty list of files")
            for path in modules[module]:
                if path not in self.target.snapshot.files:
                    raise ValueError(
                        f"module file does not name a target resource: {path}"
                    )


__all__ = ["ModularRSI"]
