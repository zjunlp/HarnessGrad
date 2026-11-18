"""ACE reflection and ADD-only playbook proposal, independent of measurement.

The caller supplies authorized evidence and a model callback. This module does
not select a measured parent, run tasks, write candidate files or deploy changes.
The native playbook parser is supplied by the caller so its source and identity
remain under the existing method adapter's control.
"""
from __future__ import annotations

import json


def propose_playbook(text, *, evidence, ask, native, call_index, curator_frequency,
                     edit_budget, playbook_token_budget):
    """Return updated instruction and reflection tags using the existing ACE port.

    ``evidence`` is a zero-argument callback and ``ask`` takes one prompt and
    returns an object. The caller is responsible for their provenance and access
    boundaries, and for evaluating and accepting any resulting candidate.
    """
    marker = "\n\n## ACE PLAYBOOK\n"
    prefix, _, playbook = text.partition(marker)
    reply = ask(
        "[ACE: reflect] Reflect on visible task failures/successes; return JSON with error_identification, root_cause_analysis, correct_approach, key_insight, bullet_tags [{id,tag:helpful|harmful|neutral}]. No ground truths are available.\n"
        + playbook + "\n" + evidence()
    )
    reflection = native.Reflection.parse(json.dumps(reply))
    playbook = native.tag(playbook, reflection.tags)
    if call_index % curator_frequency == 0:
        adds = ask(
            '[ACE: curate] Return JSON {"operations":[{"type":"ADD","section":"general","content":"reusable guidance"}]}. ADD only, no rewrite/deletion. Max additions '
            + str(edit_budget) + ".\nCurrent playbook:\n" + playbook
            + "\nReflection:\n" + reflection.content
        )
        playbook = native.Patch.parse(json.dumps(adds)).apply(playbook, edit_budget)
    if (len(playbook) + 3) // 4 > playbook_token_budget:
        raise ValueError("playbook token budget exceeded")
    return prefix + marker + playbook, list(reflection.tags)
