"""SkillOpt fast proposal stage, independent of measurement and execution.

The caller supplies ordered batches of failure/success task identifiers, an
explicit evidence projection and a model callback. This stage generates and
applies bounded skill edits; it does not decide whether a candidate is better,
read a benchmark, create workspace files or publish an artifact.
"""
from __future__ import annotations

import json


def propose_fast(current, batches, *, edit_budget, merge_batch_size,
                 optimizer_memory, ask, evidence, apply_patch):
    """Reflect, merge, rank and apply edits from caller-categorized feedback.

    ``batches`` contains ``(failure_ids, success_ids)`` pairs. Their order is
    preserved; a source with no ids is skipped. ``ask`` accepts a prompt and
    returns a JSON object; ``evidence`` accepts the ids and returns text.
    No score or task outcome is inferred by this module.
    """
    if type(merge_batch_size) is not int or merge_batch_size < 2:
        raise ValueError("merge_batch_size must be >=2")
    grouped = {"failure": [], "success": []}
    for failure_ids, success_ids in batches:
        for source, ids in (("failure", failure_ids), ("success", success_ids)):
            if not ids:
                continue
            reply = ask(
                f'[SkillOpt: {source}] Reflect on this source of execution feedback. Return JSON {{"edits":[{{"path":"skills/main.md","op":"append","content":"procedure","source_type":"{source}"}}]}}. At most '
                + str(edit_budget)
                + " edits; do not edit protected SLOW_UPDATE sections.\nCurrent files: "
                + json.dumps(current)
                + "\nOptimizer memory: "
                + optimizer_memory
                + "\nEvidence: "
                + evidence(ids)
            )
            proposed = reply.get("edits")
            if not isinstance(proposed, list) or len(proposed) > edit_budget:
                raise ValueError("reflection patch exceeds its edit budget")
            for edit in proposed:
                if not isinstance(edit, dict):
                    raise ValueError("reflection requires edit objects")
                edit["source_type"] = source
            grouped[source].append(proposed)
    patches = []
    for source, groups in grouped.items():
        while len(groups) > 1:
            next_groups = []
            for offset in range(0, len(groups), merge_batch_size):
                chunk = groups[offset : offset + merge_batch_size]
                if len(chunk) == 1:
                    next_groups.append(chunk[0])
                    continue
                reply = ask(
                    '[SkillOpt: merge] Merge overlapping proposals in one source group; preserve paths/source types. Return JSON {"edits":[]}.\nSource: '
                    + source + "\n" + json.dumps(chunk)
                )
                if not isinstance(reply.get("edits"), list):
                    raise ValueError("merge requires edits list")
                next_groups.append(reply["edits"])
            groups = next_groups
        if groups:
            patches.append(groups[0])
    edits = [edit for group in patches for edit in group]
    if len(patches) > 1:
        merged = ask(
            '[SkillOpt: merge] Merge overlapping source patches, failure fixes take priority. Return JSON {"edits":[]}.\n'
            + json.dumps(patches)
        )
        edits = merged.get("edits")
        if not isinstance(edits, list):
            raise ValueError("cross-source merge requires edits list")
    if len(edits) > edit_budget:
        ranked = ask(
            '[SkillOpt: rank] Return JSON {"selected_indices":[]}, unique zero-based edits, at most '
            + str(edit_budget) + ".\n" + json.dumps(edits)
        )
        indices = ranked.get("selected_indices")
        if (not isinstance(indices, list) or len(indices) > edit_budget
                or len(indices) != len(set(indices))
                or any(type(i) is not int or not 0 <= i < len(edits) for i in indices)):
            raise ValueError("invalid selected_indices")
        edits = [edits[i] for i in indices]
    return apply_patch(current, edits, edit_budget), edits
