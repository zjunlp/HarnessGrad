"""Artifact bridge for ten upstream policy ports.

The platform evaluates whole candidates between calls. Policies reconstruct their
search state solely from staged study history and return complete harness trees.
No target execution, benchmark import, answer key, or held-out score is used here.
"""

from __future__ import annotations

import copy
from functools import lru_cache
import importlib.util
import json
import math
import os
import random
import sys
from pathlib import Path

METHODS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(METHODS))
import _siblings

editor = _siblings.load("editor")
protocol = _siblings.load("protocol")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


P = _load(Path(__file__).with_name("policies.py"), "_hg_upstream_policies")


@lru_cache(maxsize=5)
def _proposal_stage(name):
    """Load only the extracted mechanism used by the selected policy port."""
    if name not in {"skillopt_fast", "skillopt_patch", "ace_proposal", "evo_harness_proposal", "gepa_proposal"}:
        raise ValueError("unknown proposal stage")
    return _load(Path(__file__).with_name(name + ".py"), "_hg_" + name)


DEFAULTS = {
    "opro": {
        "num_candidates": 8,
        "history_size": 20,
        "score_threshold": 0.1,
        "few_shot_size": 3,
        "max_steps": 200,
    },
    "adas": {"num_reflections": 2, "max_steps": 10},
    "ace": {
        "mode": "offline",
        "edit_budget": 4,
        "curator_frequency": 1,
        "max_reflection_rounds": 1,
        "playbook_token_budget": 80000,
    },
    "gepa": {
        "candidate_selection_strategy": "pareto",
        "acceptance_criterion": "strict_improvement",
        "reflection_minibatch_size": 3,
        "skip_perfect_score": True,
        "use_merge": False,
    },
    "evoprompt": {
        "population_size": 10,
        "variant": "ga",
        "selection": "wheel",
        "survivor_selection": "topk",
        "donor_random": False,
        "max_steps": 10,
    },
    "darwinx": {
        "merge_every": 3,
        "exploration_probability": 0.5,
        "regression_tolerance": 0.0,
        "solved_threshold": 1.0,
        "max_steps": 10,
    },
    "arbor": {"max_cycles": 10, "max_depth": 3, "merge_threshold": 0.0},
    "modular_rsi": {
        "modules": {},
        "num_epochs": 3,
        "num_rollouts": 3,
        "edit_budget": 1,
        "integration_epochs": 1,
        # Read by the contrast gate (`_evolve_branch`) and by native's own config
        # (`vendor/modular_rsi/strategy.py.reference:61`). It was missing here, so
        # `settings()` refused the field as unknown and the gate was stuck at the
        # literal 1.0 fallback: a task scored 0.9 could never sit between "failure" and
        # "success", which is the whole point of the threshold.
        "success_threshold": 1.0,
    },
    "skillopt": {
        "edit_budget": 4,
        "reflection_minibatch_size": 8,
        "merge_batch_size": 8,
        "use_slow_update": True,
        "use_meta_skill": True,
        "epoch_batches": 1,
        "slow_update_samples": 20,
    },
    "evo_harness": {
        "general_min_tasks": 3,
        "max_skill_chars": 2000,
        "max_general_skills": 10,
        "max_topic_skills": 30,
        "max_library_chars": 48000,
    },
}


def settings(name):
    raw = os.environ.get("HG_POLICY_CONFIG", "{}")
    cfg = json.loads(raw)
    if not isinstance(cfg, dict):
        raise ValueError("HG_POLICY_CONFIG must be a JSON object")
    allowed = set(DEFAULTS[name]) | {"seed", "max_evidence_chars", "instruction_path"}
    unknown = set(cfg) - allowed
    if unknown:
        raise ValueError(
            "unsupported native/port configuration fields: "
            + ", ".join(sorted(unknown))
        )
    options = {"seed": 42, "max_evidence_chars": 24000, **DEFAULTS[name], **cfg}
    for field, value in options.items():
        if field in ("seed", "max_evidence_chars", "instruction_path", "modules"):
            continue
        default = DEFAULTS[name].get(field)
        if isinstance(default, bool) and type(value) is not bool:
            raise ValueError(f"{field} must be boolean")
        if isinstance(default, int) and not isinstance(default, bool):
            minimum = 0 if field in ("num_reflections", "merge_every") else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{field} must be an integer >= {minimum}")
        if isinstance(default, float) and (not P.finite(value) or value < 0):
            raise ValueError(f"{field} must be finite and nonnegative")
    if "exploration_probability" in options and options["exploration_probability"] > 1:
        raise ValueError("exploration_probability must be <= 1")
    if name == "arbor" and options["max_depth"] < 2:
        raise ValueError("Arbor max_depth must be >=2")
    if name == "modular_rsi" and options["num_rollouts"] < 2:
        raise ValueError("ModularRSI num_rollouts must be >=2")
    if name == "skillopt" and options["merge_batch_size"] < 2:
        raise ValueError("merge_batch_size must be >=2")
    if name == "evoprompt" and options["variant"] not in ("ga", "de"):
        raise ValueError("variant must be ga or de")
    if name == "ace" and options["max_reflection_rounds"] != 1:
        raise ValueError(
            "native reflection retries require new target evaluations; this port supports max_reflection_rounds=1 only"
        )
    for field in ("seed", "max_evidence_chars"):
        if type(options[field]) is not int or (field != "seed" and options[field] < 1):
            raise ValueError(f"invalid {field}")
    return options


def _safe(root, name):
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or Path(name).is_absolute()
        or ".." in Path(name).parts
    ):
        raise ValueError("resource path must be inside the harness")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("resource path leaves the harness")
    return path


def _text(root, path):
    return _safe(Path(root), path).read_text(encoding="utf-8")


def instruction_path(root, cfg):
    path = cfg.get("instruction_path")
    if path is None:
        choices = [
            p
            for p in ("prompts/system.md", "prompt.md", "CLAUDE.md", "AGENTS.md")
            if (Path(root) / p).is_file()
        ]
        if len(choices) != 1:
            raise ValueError(
                "declare instruction_path in HG_POLICY_CONFIG; no unique loaded instruction file is available"
            )
        path = choices[0]
    _text(root, path)
    if path == "harness.json":
        raise ValueError("manifest is not a prompt")
    return path


def data(base):
    points = editor.load_history(base)
    rp = editor.channel(base) / "round.json"
    if rp.is_file():
        latest = json.loads(rp.read_text())
        if not any(p.get("round") == latest.get("round") for p in points):
            points.append(latest)
    points.sort(key=lambda p: p.get("round", 0))
    return points


def members(base, req):
    points = data(base)
    out = []
    current = points[-1] if points else {}
    for point in points:
        # A refused point, invalid task set or non-finite value is not a search fitness.
        score = protocol.studied_score(point)
        per = protocol.studied_per_task(point)
        if (
            not P.finite(score)
            or not per
            or any(not P.finite(v) for v in per.values())
            or point.get("unmeasured")
            or point.get("n_invalid", 0)
        ):
            continue
        rid = point.get("round")
        tree = editor.channel(base) / "states" / f"round-{rid}"
        if rid == current.get("round"):
            tree = Path(base)
        elif rid == 0 and req.get("pristine_harness"):
            tree = Path(req["pristine_harness"])
        if not (tree / "harness.json").is_file():
            raise ValueError(
                f"measured historical state round {rid} is unavailable; uncap HG_METHOD_HISTORY_ROUNDS rather than substitute another parent"
            )
        info = dict((point.get("method_reported") or {}).get("policy_port") or {})
        info["_runtime_failed"] = bool(
            point.get("n_harness_failed", 0) or point.get("harness_failed")
        )
        info["_harness_sha"] = (point.get("identity") or {}).get("harness_sha")
        out.append(P.Member(rid, float(score), dict(per), str(tree), info))
    if not out:
        raise ValueError(
            "no paired, adaptation-visible platform measurement with a staged harness tree"
        )
    return out


def _evidence(base, cfg, selected=None, round_no=None):
    root = editor.channel(base) / "tasks"
    rows = []
    if round_no is not None:
        point = next((p for p in data(base) if p.get("round") == round_no), {})
        rows.append({"round": round_no, "per_task": protocol.studied_per_task(point)})
        traces = editor.channel(base) / "history" / "traces" / f"round-{round_no}"
        if traces.is_dir():
            for source in sorted(traces.rglob("*.jsonl")):
                tid = source.relative_to(traces).as_posix().removesuffix(".jsonl")
                if selected is None or tid in selected:
                    rows.append(
                        {
                            "task_id": tid,
                            "trace": source.read_text(encoding="utf-8")[
                                : cfg["max_evidence_chars"]
                            ],
                        }
                    )
            encoded = json.dumps(rows, ensure_ascii=False)
            return encoded[: cfg["max_evidence_chars"]] + (
                "[truncated]" if len(encoded) > cfg["max_evidence_chars"] else ""
            )
        if round_no != data(base)[-1].get("round"):
            rows.append(
                {
                    "trace_unavailable": True,
                    "reason": "no staged trace for selected historical parent; latest traces are not substituted",
                }
            )
            return json.dumps(rows, ensure_ascii=False)
    for path in sorted(root.rglob("*.json")) if root.exists() else []:
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict) or (
            selected is not None and row.get("task_id") not in selected
        ):
            continue
        # Only public actor execution + platform feedback; never expected answers.
        safe = {
            k: row[k]
            for k in (
                "task_id",
                "goal",
                "score",
                "harness",
                "verdict",
                # The staged page calls the extracted loop facts `loop`: `evaluate()` runs
                # `eval.observe.tracing.loop_facts` on the task's trace and writes the
                # result under that key (`harnessgrad/channel.py:215`). This whitelist used
                # to ask for `loop_facts`, the *function's* name, so the key was never in a
                # staged row and eight of the ten ports silently saw no loop facts at all
                # -- the one piece of evidence that separates "the artifact was wrong" from
                # "the harness died before it wrote one".
                "loop",
                "trace",
                "task",
            )
            if k in row
        }
        rows.append(safe)
    encoded = json.dumps(rows, ensure_ascii=False)
    return encoded[: cfg["max_evidence_chars"]] + (
        "\n[truncated]" if len(encoded) > cfg["max_evidence_chars"] else ""
    )


def _history(ms, path=None, cfg=None):
    rows = []
    for m in ms:
        row = {
            "round": m.round,
            "score": m.score,
            "per_task": m.per_task,
            "account": m.info.get("hypothesis", "baseline"),
        }
        if path:
            try:
                row["text"] = _text(m.tree, path)
            except (OSError, ValueError):
                continue
        rows.append(row)
    return rows


def schema(prompt, base, **kwargs):
    # This role requests structured content, not a checkout edit. A CLI improver's
    # output-to-edit conversion cannot represent it, so use the declared endpoint.
    # Scope-specific prompt/file contents and staged feedback are explicitly supplied
    # by each role; do not inject the entire runtime into a one-file prompt edit.
    return editor.ask(
        prompt,
        system="Return ONLY the requested JSON object. Never execute tasks, inspect private graders, invent measurements, or include reference answers.",
        base=None,
        endpoint_only=True,
        skill=False,
        **kwargs,
    )


def prose(prompt, base):
    reply = schema(prompt + '\nReturn JSON {"text":"your analysis"}.', base)
    if not isinstance(reply, dict) or not isinstance(reply.get("text"), str):
        raise ValueError("analysis role requires text")
    return reply["text"]


def _edit(parent, base, dest, prompt, allowed=None):
    reply = editor.ask(
        prompt + "\n" + editor.EDIT_EXPECT, base=base, parent=Path(parent)
    )
    if "no_change" in reply:
        editor.apply_edits(Path(parent), dest, [])
        return [], str(reply["no_change"]), reply
    edits = reply.get("files")
    if not isinstance(edits, list):
        raise ValueError("proposal requires files list")
    paths = [e.get("path") for e in edits if isinstance(e, dict)]
    for name in paths:
        _safe(Path(parent), name)
        if allowed is not None and name not in allowed:
            raise ValueError(f"out-of-scope edit: {name}")
    changed, files, problems = editor.apply_edits(Path(parent), dest, edits)
    if problems:
        raise ValueError(
            "; ".join(problems) if isinstance(problems, list) else str(problems)
        )
    return files if changed else [], editor.hypothesis_of(reply), reply


def _replace(parent, dest, files):
    # apply_edits' content mode is for new files. Existing files are exact replacements.
    edits = []
    for path, new in files.items():
        target = _safe(Path(parent), path)
        if target.is_file():
            edits.append(
                {
                    "path": path,
                    "find": target.read_text(encoding="utf-8"),
                    "replace": new,
                }
            )
        else:
            edits.append({"path": path, "content": new})
    changed, changed_paths, problems = editor.apply_edits(Path(parent), dest, edits)
    if problems:
        raise ValueError(
            "; ".join(problems) if isinstance(problems, list) else str(problems)
        )
    return changed_paths if changed else []


def prompt_proposals(name, base, req, cfg, ms, rng, call):
    path = instruction_path(base, cfg)
    steps = []
    state = {}
    limit = call["remaining"]
    generation = call["index"]
    if name == "opro":
        parent = P.best(ms)
        history = sorted(
            [r for r in _history(ms, path) if r["score"] >= cfg["score_threshold"]],
            key=lambda r: r["score"],
        )[-cfg["history_size"] :]
        sample = rng.sample(
            list(parent.per_task), min(cfg["few_shot_size"], len(parent.per_task))
        )
        state = {
            "few_shot_task_ids": sample,
            "history_rounds": [r["round"] for r in history],
        }
        prompt = (
            '[OPRO: propose] Optimize one instruction from its ascending history. Scores are platform study measurements; do not invent scores. Return JSON {"instruction":"complete instruction"}.\n'
            + json.dumps(history, ensure_ascii=False)
            + "\nEvidence:\n"
            + _evidence(base, cfg, sample, parent.round)
        )
        parents = [parent] * min(cfg["num_candidates"], limit)
    elif name == "gepa":
        admitted = [ms[0]]
        for child in ms[1:]:
            parent = next(
                (m for m in admitted if m.round == child.info.get("parent_round")), None
            )
            if parent and P.admits_gepa(child, parent, cfg["acceptance_criterion"]):
                admitted.append(child)
        parent = P.gepa_parent(admitted, rng, cfg["candidate_selection_strategy"])
        parents = [parent]
        failures = [
            t
            for t, v in parent.per_task.items()
            if v < 1 or not cfg["skip_perfect_score"]
        ]
        sample = rng.sample(
            failures, min(len(failures), cfg["reflection_minibatch_size"])
        )
        prompt = _proposal_stage("gepa_proposal").format_prompt(
            _text(parent.tree, path), _evidence(base, cfg, sample, parent.round)
        )
        state = {
            "admitted_rounds": [m.round for m in admitted],
            "frontier": {
                t: [admitted[i].round for i in owners]
                for t, owners in P.pareto_front(admitted).items()
            },
            "reflection_task_ids": sample,
            "fitness_scope": "study (dev not exposed)",
        }
        if not sample:
            return [], state
    else:
        pop = P.evoprompt_population(
            ms, cfg["population_size"], cfg["variant"], cfg["survivor_selection"]
        )
        state = {
            "population_rounds": [m.round for m in pop],
            "variant": cfg["variant"],
            "generation": generation,
        }
        count = min(
            (
                cfg["population_size"] - len(pop)
                if len(pop) < cfg["population_size"]
                else cfg["population_size"]
            ),
            limit,
        )
        parents = []
        prompt = ""
    seen = {_text(m.tree, path) for m in ms}
    for slot in range(count if name == "evoprompt" else len(parents)):
        info = {**state, "generation": generation, "instruction_path": path}
        if name == "evoprompt":
            if len(pop) < cfg["population_size"]:
                selected = [pop[0]]
                phase = "initialize"
            elif cfg["variant"] == "de":
                selected = P.de_parents(pop, slot, rng, cfg["donor_random"])
                phase = "differential_evolution"
                info["target_slot"] = slot
            else:
                selected = P.ga_parents(pop, rng, cfg["selection"])
                phase = "crossover_mutation"
            parent = selected[0]
            info.update(parents=[m.round for m in selected], phase=phase)
            prompt = (
                f'[EvoPrompt: {phase}] Return JSON {{"instruction":"complete offspring prompt"}}. Preserve task-independent instructions. For GA recombine both parents then mutate; for DE identify differences between donors, apply them to target then crossover.\n'
                + json.dumps(
                    [
                        {
                            "round": m.round,
                            "prompt": _text(m.tree, path),
                            "fitness": m.score,
                        }
                        for m in selected
                    ],
                    ensure_ascii=False,
                )
            )
        else:
            parent = parents[slot]
        reply = schema(prompt, base)
        if name == "gepa":
            text = _proposal_stage("gepa_proposal").instruction_from_reply(reply)
        else:
            text = reply.get("instruction")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("optimizer requires a nonempty instruction")
        if text in seen:
            continue
        seen.add(text)
        dest = editor.candidate_path(req, f"{name}-{slot+1}")
        files = _replace(parent.tree, dest, {path: text})
        info.update(
            parent_round=parent.round, hypothesis=f"{name} instruction proposal"
        )
        steps.append(step(dest, files, info))
    return steps, state


def step(dest, files, info, changed=None):
    changed = bool(files) if changed is None else bool(changed)
    return {
        "harness_dir": dest,
        "changed": changed,
        "files": files,
        "label": info.get("hypothesis", "upstream policy candidate"),
        "edit_kind": "harness_source" if changed else "none",
        "hypothesis": info.get("hypothesis", ""),
        "method_reported": {"policy_port": info},
    }


def ace(base, req, cfg, ms, call):
    path = instruction_path(base, cfg)
    parent = ms[-1]
    text = _text(parent.tree, path)
    native = _load(
        Path(__file__).parent / "vendor/ace/_playbook.py", "_hg_native_ace_playbook"
    )
    candidate_text, tags = _proposal_stage("ace_proposal").propose_playbook(
        text, evidence=lambda: _evidence(base, cfg),
        ask=lambda prompt: schema(prompt, base), native=native,
        call_index=call["index"], curator_frequency=cfg["curator_frequency"],
        edit_budget=cfg["edit_budget"], playbook_token_budget=cfg["playbook_token_budget"],
    )
    dest = editor.candidate_path(req)
    files = _replace(parent.tree, dest, {path: candidate_text})
    info = {
        "parent_round": parent.round,
        "hypothesis": "ACE ADD-only playbook curation",
        "mode": cfg["mode"],
        "tags": tags,
        "instruction_path": path,
        "retry_evaluations": "unavailable; no self-evaluation",
    }
    return [step(dest, files, info)], info


def adas(base, req, cfg, ms, call):
    parent = P.best(ms)
    dest = editor.candidate_path(req)
    prompt = (
        "[ADAS: design] Propose a novel complete agent design after inspecting ALL measured archive accounts including regressions. No score predictions count as measurement.\nArchive:\n"
        + json.dumps(_history(ms), ensure_ascii=False)
    )
    files, hypothesis, reply = _edit(parent.tree, base, dest, prompt)
    for index in range(cfg["num_reflections"]):
        next_dest = editor.candidate_path(req, f"reflection-{index}")
        edits, account, _ = _edit(
            dest,
            base,
            next_dest,
            "[ADAS: reflect] Review the current unmeasured design for novelty, generality and implementation errors; revise it before evaluation. Previous account: "
            + hypothesis,
        )
        dest = next_dest
        if account:
            hypothesis = account
        files = sorted(set(files + edits))
    info = {
        "parent_round": parent.round,
        "archive_rounds": [m.round for m in ms],
        "num_reflections": cfg["num_reflections"],
        "hypothesis": hypothesis,
        "fitness_scope": "study; no dev capability",
    }
    return [step(dest, files, info)], info


def darwinx(base, req, cfg, ms, rng, call):
    archive, invariant, cumulative, decisions = P.darwin_archive(
        ms, tolerance=cfg["regression_tolerance"], threshold=cfg["solved_threshold"]
    )
    lessons = next(
        (
            list(m.info.get("lessons", []))
            for m in reversed(ms)
            if m.info.get("lessons")
        ),
        [],
    )
    learned = {row["round"] for row in lessons}
    for prior in ms[1:]:
        if prior.round in learned or str(prior.round) not in decisions:
            continue
        lesson = prose(
            "[DarwinX: learn] Distill general lessons from this platform-measured candidate and its preserving-extension verdict. Do not claim fresh confirmation.\n"
            + json.dumps(
                {
                    "round": prior.round,
                    "verdict": decisions[str(prior.round)],
                    "per_task": prior.per_task,
                }
            )
            + "\n"
            + _evidence(base, cfg, round_no=prior.round),
            base,
        )
        lessons.append({"round": prior.round, "lesson": lesson})
    attempted = [
        sorted(m.info["parents"]) for m in ms if len(m.info.get("parents", [])) == 2
    ]
    parents = P.darwin_parents(
        archive,
        cumulative,
        rng,
        call["index"] - 1,
        cfg["merge_every"],
        cfg["exploration_probability"],
        attempted,
        solved_threshold=cfg["solved_threshold"],
    )
    invariant = {t for parent in parents for t in parent.info.get("_invariant", [])}
    if len(parents) == 2:
        invariant.update(
            t
            for parent in parents
            for t, v in parent.per_task.items()
            if v >= cfg["solved_threshold"]
        )
    evidence = [
        {
            "round": m.round,
            "sources": editor.load_sources(Path(m.tree)),
            "per_task": m.per_task,
        }
        for m in parents
    ]
    dest = editor.candidate_path(req)
    files, hypothesis, _ = _edit(
        parents[0].tree,
        base,
        dest,
        "[DarwinX: "
        + ("merge" if len(parents) == 2 else "mutate")
        + "] Extend capabilities without losing the solved invariant. Merge complementary branches when given two parents.\nInvariant task IDs (do not hardcode): "
        + json.dumps(sorted(invariant))
        + "\nLearnings: "
        + json.dumps(lessons)
        + "\nParents and lesson evidence:\n"
        + json.dumps(evidence, ensure_ascii=False),
    )
    info = {
        "parents": [m.round for m in parents],
        "parent_round": parents[0].round,
        "archive_rounds": [m.round for m in archive],
        "accepted_rounds": [m.round for m in archive if m.info.get("_accepted")],
        "invariant": sorted(invariant),
        "assessment": decisions,
        "lessons": lessons,
        "hypothesis": hypothesis,
        "fidelity": "one platform full-set measurement; native screen and repeated confirmation unavailable",
    }
    return [step(dest, files, info)], info


def skill_files(parent):
    return {
        p.relative_to(parent).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(parent.rglob("*.md"))
        if p.relative_to(parent).parts[0] == "skills" and "_harnessgrad" not in p.parts
    }


def apply_skill_patch(files, edits, budget):
    """Apply the existing SkillOpt patch contract via the reusable implementation."""
    return _proposal_stage("skillopt_patch").apply_skill_patch(files, edits, budget, safe_path=_safe)


def skillopt(base, req, cfg, ms, call):
    parent = P.best(ms)
    current = skill_files(Path(parent.tree))
    info = {"parent_round": parent.round}
    history = _history(ms)
    patches = []
    optimizer_memory = next(
        (
            m.info.get("meta_skill", "")
            for m in reversed(ms)
            if m.info.get("meta_skill")
        ),
        "",
    )
    slow = (
        (cfg["use_slow_update"] or cfg["use_meta_skill"])
        and call["index"] > cfg["epoch_batches"]
        and call["index"] % (cfg["epoch_batches"] + 1) == 0
    )
    if slow:
        comparison_rows = history[-2:]
        paired = (
            set.intersection(*(set(row["per_task"]) for row in comparison_rows))
            if comparison_rows
            else set()
        )
        sampled = random.Random(cfg["seed"] + call["index"] * 2000).sample(
            sorted(paired), min(len(paired), cfg["slow_update_samples"])
        )
        comparison = json.dumps(
            [
                {**row, "per_task": {t: row["per_task"][t] for t in sampled}}
                for row in comparison_rows
            ],
            ensure_ascii=False,
        )
        info["comparison_task_ids"] = sampled
        meta = (
            prose(
                "[SkillOpt: meta] Compare measured adjacent epoch versions; distill optimizer guidance.\n"
                + comparison,
                base,
            )
            if cfg["use_meta_skill"]
            else ""
        )
        reply = (
            {"guidance": []}
            if not cfg["use_slow_update"]
            else schema(
                '[SkillOpt: slow] Return JSON {"guidance":[{"path":"existing skill path","content":"long-term guidance"}]}. Preserve task-independent instructions. Existing files: '
                + json.dumps(current)
                + "\nComparison: "
                + comparison
                + "\nOptimizer memory: "
                + meta,
                base,
            )
        )
        updated = dict(current)
        for item in reply.get("guidance", []):
            path = item.get("path")
            content = item.get("content")
            start = "<!-- SLOW_UPDATE_START -->"
            end = "<!-- SLOW_UPDATE_END -->"
            if (
                path not in current
                or not isinstance(content, str)
                or start in content
                or end in content
            ):
                raise ValueError(
                    "slow guidance requires existing path and marker-free content"
                )
            old = current[path]
            replacement = start + "\n" + content + "\n" + end
            updated[path] = (
                old[: old.index(start)] + replacement + old[old.index(end) + len(end) :]
                if start in old and end in old
                else old + "\n" + replacement + "\n"
            )
        info.update(phase="slow" if cfg["use_slow_update"] else "meta", meta_skill=meta)
    else:
        tasks = list(parent.per_task)
        width = cfg["reflection_minibatch_size"]
        def batches():
            for offset in range(0, len(tasks), width):
                batch = tasks[offset : offset + width]
                yield (
                    {t for t in batch if not (parent.per_task[t] > 0)},
                    {t for t in batch if parent.per_task[t] > 0},
                )
        updated, edits = _proposal_stage("skillopt_fast").propose_fast(
            current, batches(), edit_budget=cfg["edit_budget"],
            merge_batch_size=cfg["merge_batch_size"], optimizer_memory=optimizer_memory,
            ask=lambda prompt: schema(prompt, base),
            evidence=lambda ids: _evidence(base, cfg, ids, parent.round),
            apply_patch=apply_skill_patch,
        )
        info.update(phase="fast", patches=edits, meta_skill=optimizer_memory)
    dest = editor.candidate_path(req)
    files = _replace(
        parent.tree, dest, {p: v for p, v in updated.items() if current.get(p) != v}
    )
    info["hypothesis"] = (
        "SkillOpt "
        + info["phase"]
        + " skill update, selection delayed to next platform measurement"
    )
    return [step(dest, files, info)], info


def evo_harness(base, req, cfg, ms, call):
    parent = ms[-1]
    failed = (task for task, value in parent.per_task.items() if not value >= 1)
    current, updated, memories = _proposal_stage("evo_harness_proposal").propose_skills(
        failed, cfg=cfg, ask=lambda prompt: schema(prompt, base),
        evidence=lambda task: _evidence(base, cfg, {task}, parent.round),
        skills=lambda: skill_files(Path(parent.tree)),
        safe_path=lambda path: _safe(Path(parent.tree), path),
    )
    dest = editor.candidate_path(req)
    files = _replace(
        parent.tree, dest, {p: v for p, v in updated.items() if current.get(p) != v}
    )
    info = {
        "parent_round": parent.round,
        "hypothesis": "EvoHarness topic/general native skill curation",
        "memories": memories,
        "adoption": "every valid compilation; no score gate",
    }
    return [step(dest, files, info)], info


def _arbor_state(ms):
    old = next(
        (
            m.info.get("state")
            for m in reversed(ms)
            if isinstance(m.info.get("state"), dict)
        ),
        None,
    )
    state = (
        copy.deepcopy(old)
        if old
        else {
            "nodes": {
                "ROOT": {
                    "id": "ROOT",
                    "parent_id": None,
                    "depth": 0,
                    "hypothesis": "Improve reusable harness mechanisms",
                    "status": "root",
                    "insight": "",
                }
            },
            "trunk_round": ms[0].round,
            "processed_rounds": [],
        }
    )
    return state


def arbor(base, req, cfg, ms, call):
    state = _arbor_state(ms)
    nodes = state["nodes"]
    by_round = {m.round: m for m in ms}
    trunk = by_round.get(state["trunk_round"], P.best(ms))
    for m in ms:
        node_id = m.info.get("node_id")
        if node_id not in nodes or m.round in state["processed_rounds"]:
            continue
        node = nodes[node_id]
        node.update(
            status="needs_retry" if m.info.get("_runtime_failed") else "done",
            score=m.score,
            round=m.round,
        )
        if (
            not m.info.get("_runtime_failed")
            and m.score - trunk.score >= cfg["merge_threshold"]
            and m.score > trunk.score
        ):
            trunk = m
            state["trunk_round"] = m.round
            node["status"] = "merged"
        node["insight"] = prose(
            "[Arbor: reflect] Extract a measured experiment lesson; distinguish regression from runtime failure.\nExperiment: "
            + json.dumps(node)
            + "\n"
            + _evidence(base, cfg),
            base,
        )
        ancestor = node.get("parent_id")
        while ancestor is not None:
            children = [
                v
                for v in nodes.values()
                if v.get("parent_id") == ancestor and v.get("insight")
            ]
            nodes[ancestor]["insight"] = prose(
                "[Arbor: synthesize] Synthesize children insights bottom-up. Do not invent scores.\n"
                + json.dumps(children),
                base,
            )
            ancestor = nodes[ancestor].get("parent_id")
        state["processed_rounds"].append(m.round)
    selected = None
    for attempt in range(12):
        action = schema(
            "[Arbor: coordinate] Choose JSON action: expand {node_id,hypotheses:[1-3 strings]}, run {node_id}, prune {node_id,reason}, stop {reason}. Run only pending LEAVES at depth>=2, max_depth="
            + str(cfg["max_depth"])
            + ". Code always starts from current trunk; tree ancestry is ideas, not code ancestry.\nTree: "
            + json.dumps(nodes)
            + "\nTrunk: "
            + json.dumps({"round": trunk.round, "study_score": trunk.score}),
            base,
        )
        kind = action.get("action")
        nid = action.get("node_id")
        if kind == "stop":
            dest = editor.candidate_path(req, "arbor-trunk")
            editor.apply_edits(Path(trunk.tree), dest, [])
            info = {
                "method": "arbor",
                "state": state,
                "stop_requested": True,
                "reason": action.get("reason"),
                "parent_round": trunk.round,
                "phase": "coordinator_stop",
                "hypothesis": "restore measured Arbor trunk",
            }
            return [
                step(
                    dest, [], info, changed=Path(trunk.tree).resolve() != base.resolve()
                )
            ], info
        if nid not in nodes:
            continue
        node = nodes[nid]
        if (
            kind == "expand"
            and node["depth"] < cfg["max_depth"]
            and node["status"] != "pruned"
        ):
            hypotheses = action.get("hypotheses")
            if (
                not isinstance(hypotheses, list)
                or not 1 <= len(hypotheses) <= 3
                or any(not isinstance(v, str) or not v.strip() for v in hypotheses)
            ):
                continue
            count = len([n for n in nodes.values() if n.get("parent_id") == nid])
            for index, hypothesis in enumerate(hypotheses, start=count + 1):
                child = str(index) if nid == "ROOT" else f"{nid}.{index}"
                nodes[child] = {
                    "id": child,
                    "parent_id": nid,
                    "depth": node["depth"] + 1,
                    "hypothesis": hypothesis,
                    "status": "pending",
                    "insight": "",
                }
        elif kind == "prune" and nid != "ROOT":
            for key, value in nodes.items():
                if key == nid or key.startswith(nid + "."):
                    value["status"] = "pruned"
        elif (
            kind == "run"
            and node["depth"] >= 2
            and node["status"] in ("pending", "needs_retry")
            and not any(v.get("parent_id") == nid for v in nodes.values())
        ):
            selected = node
            break
    if selected is None:
        raise ValueError(
            "Arbor coordinator exhausted bounded planning actions without an executable leaf"
        )
    selected["status"] = "running"
    dest = editor.candidate_path(req)
    files, hypothesis, _ = _edit(
        trunk.tree,
        base,
        dest,
        "[Arbor: execute] Implement the selected testable hypothesis from CURRENT TRUNK.\nExperiment: "
        + json.dumps(selected)
        + "\nInherited insights: "
        + json.dumps(nodes),
    )
    info = {
        "parent_round": trunk.round,
        "node_id": selected["id"],
        "state": state,
        "hypothesis": hypothesis,
        "adoption": "strict study-score trunk update; dev gate unavailable",
    }
    return [step(dest, files, info)], info


def modular_rsi(base, req, cfg, ms, call):
    modules = cfg["modules"]
    if (
        not isinstance(modules, dict)
        or not modules
        or any(
            not isinstance(v, list) or not v or any(not isinstance(p, str) for p in v)
            for v in modules.values()
        )
    ):
        raise ValueError(
            "ModularRSI requires explicit nonempty modules {module:[relative file paths]}"
        )
    paths = [p for group in modules.values() for p in group]
    if len(paths) != len(set(paths)):
        raise ValueError("ModularRSI modules must have disjoint file ownership")
    for path in paths:
        _text(ms[0].tree, path)
    state = next(
        (
            copy.deepcopy(m.info.get("state"))
            for m in reversed(ms)
            if isinstance(m.info.get("state"), dict)
        ),
        {"branches": {}, "memory": {}, "cursor": 0},
    )
    schedule = [module for module in modules for _ in range(cfg["num_epochs"])] + [
        "integration"
    ] * cfg["integration_epochs"]
    cursor = state["cursor"]
    module = schedule[min(cursor, len(schedule) - 1)]
    by_round = {m.round: m for m in ms}
    for m in ms:
        if m.info.get("module") in modules and not m.info.get("_runtime_failed"):
            state["branches"][m.info["module"]] = m.round
    parent = by_round.get(state["branches"].get(module), ms[0])
    allowed = paths if module == "integration" else modules[module]
    # Contrast real repeated platform trials of a fixed candidate; no new episodes.
    trials = parent.info.get("_platform_trials") or {}
    contrasting = {
        t: vs
        for t, vs in trials.items()
        if isinstance(vs, list)
        and len(vs) >= 2
        and any(v < cfg.get("success_threshold", 1.0) for v in vs)
        and any(v >= cfg.get("success_threshold", 1.0) for v in vs)
    }
    if not trials or any(
        not isinstance(vs, list) or len(vs) < cfg["num_rollouts"]
        for vs in trials.values()
    ):
        raise ValueError(
            "ModularRSI contrast requires platform per_task_trials (run with --trials >= num_rollouts); no self-evaluation is permitted"
        )
    findings = schema(
        '[ModularRSI: contrast] Compare observed successes and failures for the SAME tasks from repeated platform trials. Return JSON {"findings":[{"target":"owned relative file","problem":"root cause","fix":"general mechanism"}]}. Only module '
        + module
        + ".\nOwned files: "
        + json.dumps(allowed)
        + "\nTrial scores: "
        + json.dumps(trials)
        + "\nContrasting outcomes: "
        + json.dumps(contrasting)
        + "\n"
        + _evidence(base, cfg),
        base,
    )
    hints = findings.get("findings")
    if (
        not isinstance(hints, list)
        or len(hints) > cfg["edit_budget"]
        or any(v.get("target") not in allowed for v in hints)
    ):
        raise ValueError("contrast findings exceed edit budget or module scope")
    branches = {
        key: {
            "round": rid,
            "files": {p: _text(by_round[rid].tree, p) for p in modules[key]},
        }
        for key, rid in state["branches"].items()
        if rid in by_round
    }
    prompt = (
        "[ModularRSI: "
        + ("integrate" if module == "integration" else "edit")
        + "] Change only owned module files. Integrate independently evolved branches when supplied; retain interfaces and general executable behavior.\nFindings: "
        + json.dumps(hints)
        + "\nBranches: "
        + json.dumps(branches)
    )
    dest = editor.candidate_path(req)
    files, hypothesis, _ = _edit(parent.tree, base, dest, prompt, allowed=set(allowed))
    if len(files) > cfg["edit_budget"] and module != "integration":
        raise ValueError("module edit budget exceeded")
    state["cursor"] = min(cursor + 1, len(schedule))
    info = {
        "parent_round": parent.round,
        "module": module,
        "state": state,
        "findings": hints,
        "hypothesis": hypothesis,
        "contrast_source": "platform paired per_task_trials",
        "native_execution_gate": "deferred to next platform evaluation",
    }
    return [step(dest, files, info)], info


def run(name, req=None):
    req = editor.read_request() if req is None else req
    editor.require_api(req)
    base = Path(req["base_harness"])
    try:
        cfg = settings(name)
        ms = members(base, req)
        for m in ms:
            # Preserve trial evidence under a reserved namespace, never a method claim.
            point = next((p for p in data(base) if p.get("round") == m.round), {})
            m.info["_platform_trials"] = protocol.studied_per_task_trials(point)
        remaining = req.get("epochs_remaining")
        if remaining is None:
            remaining = max(
                0, int(req.get("epochs_total", 1)) - int(req.get("epochs_measured", 0))
            )
        remaining = int(remaining)
        index = 1 + max([int(m.info.get("invocation", 0)) for m in ms], default=0)
        call = {"remaining": max(1, remaining - 1), "index": index}
        if remaining <= 0:
            raise ValueError("no candidate-measurement budget remains")
        if cfg.get("mode") not in (None, "online", "offline"):
            raise ValueError("ACE mode must be online or offline")
        if name == "gepa" and cfg["use_merge"]:
            raise ValueError(
                "upstream GEPA itself does not implement system-aware merge"
            )
        rng = random.Random(cfg["seed"] + index)
        # Reserve the last measurement slot for the selected measured state.
        # That makes final deployment real instead of merely naming a kept_round.
        finalizes = name not in ("ace", "evo_harness", "modular_rsi") or (
            name == "ace" and cfg["mode"] == "offline"
        )
        at_policy_limit = index > cfg.get("max_steps", cfg.get("max_cycles", math.inf))
        if (remaining == 1 or at_policy_limit) and finalizes and len(ms) > 1:
            parent = (
                P.best([m for m in ms if not m.info.get("_runtime_failed")])
                if name == "arbor"
                else P.best(ms)
            )
            if name == "darwinx":
                archive, _, _, _ = P.darwin_archive(
                    ms,
                    tolerance=cfg["regression_tolerance"],
                    threshold=cfg["solved_threshold"],
                )
                parent = P.best([m for m in archive if m.info.get("_accepted")])
            # EvoPrompt remembers the global best even if standard GA displaced it.
            dest = editor.candidate_path(req, "selected")
            editor.apply_edits(Path(parent.tree), dest, [])
            changed = Path(parent.tree).resolve() != base.resolve()
            info = {
                "method": name,
                "phase": "final_promotion",
                "parent_round": parent.round,
                "selected_round": parent.round,
                "hypothesis": "deploy measured selected state",
                "selection_scope": "study only",
                "config": cfg,
                "invocation": index,
            }
            return editor.report_many(
                req,
                method=name,
                steps=[step(dest, [], info, changed=changed)],
                stop=True,
                extra={"upstream_policy_port": True},
                note="final measurement slot deploys selected measured tree",
            )
        if name in ("opro", "gepa", "evoprompt"):
            steps, summary = prompt_proposals(name, base, req, cfg, ms, rng, call)
        elif name == "ace":
            steps, summary = ace(base, req, cfg, ms, call)
        elif name == "adas":
            steps, summary = adas(base, req, cfg, ms, call)
        elif name == "darwinx":
            steps, summary = darwinx(base, req, cfg, ms, rng, call)
        elif name == "arbor":
            steps, summary = arbor(base, req, cfg, ms, call)
        elif name == "skillopt":
            steps, summary = skillopt(base, req, cfg, ms, call)
        elif name == "evo_harness":
            steps, summary = evo_harness(base, req, cfg, ms, call)
        elif name == "modular_rsi":
            steps, summary = modular_rsi(base, req, cfg, ms, call)
        else:
            raise ValueError("unknown method")
        if not steps:
            dest = editor.candidate_path(req)
            editor.apply_edits(base, dest, [])
            steps = [
                step(
                    dest,
                    [],
                    {"method": name, **summary, "hypothesis": "no admissible proposal"},
                )
            ]
        for entry in steps:
            info = entry["method_reported"]["policy_port"]
            info.update(
                method=name,
                config=cfg,
                invocation=index,
                variant="upstream-policy-port",
                measurement="platform only; next call receives feedback",
                state_scope="adaptation-visible study channel",
            )
        return editor.report_many(
            req,
            method=name,
            steps=steps,
            stop=bool(summary.get("stop_requested")),
            acceptance_rule="upstream policy with platform study feedback; see fidelity profile",
            extra={
                "upstream_policy_port": True,
                "source_commit": "e9c7667945f959d62da27204649a82f94d1b6705",
            },
            note=f"{name}: {len(steps)} candidate states; scoring remains platform-owned",
        )
    except Exception as exc:
        return editor.fail(
            req,
            method=name,
            exc=exc,
            base=base,
            state={
                "variant": "upstream-policy-port",
                "measurement": "no method-side task evaluation",
            },
        )
