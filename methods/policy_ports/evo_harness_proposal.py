"""EvoHarness memory reflection and skill compilation, without task execution.

The caller selects failure identifiers and supplies authorized evidence, a
model callback, a lazy skill reader and its existing path validation. This
module returns in-memory proposals; it does not score, write or adopt them.
"""
from __future__ import annotations

import json


def propose_skills(failed_tasks, *, cfg, ask, evidence, skills, safe_path):
    """Reflect on supplied failures and compile the existing managed skill format."""
    memories = []
    for task in failed_tasks:
        reply = ask(
            '[EvoHarness: reflect] Extract a reusable lesson from ONE failed execution. Return JSON {"lesson":"","trigger":"","evidence":"","scope_hint":""}, or {"skip":true}. Do not inspect the skill library; never memorize the answer.\n'
            + evidence(task),
        )
        if reply.get("skip"):
            continue
        if any(
            not isinstance(reply.get(k), str) or not reply[k].strip()
            for k in ("lesson", "trigger", "evidence", "scope_hint")
        ):
            raise ValueError("memory requires lesson, trigger, evidence and scope_hint")
        memories.append({"task_id": task, **reply})
    current = skills()
    updated = dict(current)
    sources = {m["task_id"] for m in memories}
    for level in ("topic", "general"):
        if not memories or (
            level == "general" and len(sources) < cfg["general_min_tasks"]
        ):
            continue
        reply = ask(
            "[EvoHarness: compile "
            + level
            + '] Return JSON {"skills":[{"path":"skills/name/SKILL.md","name":"name","description":"trigger","source_task_ids":[],"content":"reusable procedure"}]}. Only '
            + level
            + " skills, cite task_ids from THESE memories. Preserve unrelated/native skills. General rules require "
            + str(cfg["general_min_tasks"])
            + " distinct tasks.\nLibrary: "
            + json.dumps(updated)
            + "\nMemories: "
            + json.dumps(memories),
        )
        proposals = reply.get("skills")
        if not isinstance(proposals, list):
            raise ValueError("skill compiler requires skills list")
        if len(proposals) > cfg[f"max_{level}_skills"]:
            raise ValueError("skill count exceeds level budget")
        for item in proposals:
            path = item.get("path")
            safe_path(path)
            ids = item.get("source_task_ids")
            body = item.get("content")
            name = item.get("name")
            description = item.get("description")
            if not path.startswith("skills/") or not path.endswith("/SKILL.md"):
                raise ValueError("managed skills must be native skills/<name>/SKILL.md")
            if (
                not isinstance(ids, list)
                or not ids
                or any(not isinstance(i, str) for i in ids)
                or len(ids) != len(set(ids))
                or not set(ids) <= sources
            ):
                raise ValueError(
                    "skill requires distinct current-batch source task IDs"
                )
            if level == "general" and len(ids) < cfg["general_min_tasks"]:
                raise ValueError("general skill lacks cross-task evidence")
            if (
                not isinstance(body, str)
                or not body.strip()
                or len(body) > cfg["max_skill_chars"]
            ):
                raise ValueError("skill body empty or exceeds budget")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(description, str)
                or not description.strip()
            ):
                raise ValueError("native name/description missing")
            if path.removesuffix("/SKILL.md") + ".md" in current:
                raise ValueError("managed skill shadows native flat skill")
            if path in current:
                try:
                    old = json.loads(current[path].split("---", 2)[1])
                except (ValueError, IndexError):
                    raise ValueError(
                        "refusing to rewrite unmanaged/non-JSON-frontmatter native skill"
                    )
                prov = old.get("upstream", {})
                if prov.get("algorithm") != "evo_harness" or prov.get("level") != level:
                    raise ValueError("curator may only edit its managed level")
            meta = {
                "name": name,
                "description": description,
                "upstream": {
                    "algorithm": "evo_harness",
                    "level": level,
                    "source_task_ids": ids,
                },
            }
            updated[path] = (
                "---\n"
                + json.dumps(meta, ensure_ascii=False, indent=2)
                + "\n---\n"
                + body.strip()
                + "\n"
            )
    for level in ("topic", "general"):
        count = 0
        for path, text in updated.items():
            if not path.endswith("/SKILL.md"):
                continue
            try:
                meta = json.loads(text.split("---", 2)[1])
            except (ValueError, IndexError):
                continue
            if (
                meta.get("upstream", {}).get("algorithm") == "evo_harness"
                and meta["upstream"].get("level") == level
            ):
                count += 1
        if count > cfg[f"max_{level}_skills"]:
            raise ValueError("total managed skill count exceeds level budget")
    if sum(len(v) for v in updated.values()) > cfg["max_library_chars"]:
        raise ValueError("skill library exceeds character budget")
    return current, updated, memories
