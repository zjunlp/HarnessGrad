"""Original prompts for the ModularRSI mechanism; no upstream prompt copying."""

ANALYZE = """[ModularRSI: analyze]
Module: {module}
Editable module files: {files}
Contrastive task evidence: {evidence}
Accepted evolution history: {history}

Compare successes and failures of the SAME task. Negative tasks with historical
successes include those successes; otherwise diagnose the failures. For positive
tasks look for unnecessary work. A batch-level protocol error does not identify
which task failed; use it to diagnose execution interfaces, not task behavior.
Across tasks, identify recurring behavioral
deficiencies which changing THIS module could remedy. Do not equate involvement
in a failure with a causal defect. Return [] if no justified change exists.

Return JSON only: exactly one array. Each finding has exactly these fields.
Example with a justified finding:
[{{"target": "one exact editable file path", "task_ids": ["supporting task ID"],
  "rationale": "cross-task diagnosis", "change": "general reusable change"}}]
No justified findings:
[]
Do not include Markdown fences, headings, explanations or a bare JSON object.
Only cite task IDs in the supplied evidence. Findings aimed at the same target
will be consolidated and ranked by the number of distinct supporting tasks.
Do not propose task-specific answers, conditions or grader changes.
"""

EDIT = """[ModularRSI: edit]
Module: {module}
Allowed files: {files}
Prioritized findings: {findings}
Accepted evolution history: {history}

Edit the checkout to address these findings, changing only this module's
responsibilities within the allowed files. Preserve other modules' behavior even
when they share those files. Reusable function additions/removal are allowed.
Preserve execution
interfaces, model configuration and credentials. Diagnose and fix mechanisms,
not individual task answers. Do not inspect downstream benchmarks or graders.
Summarize your change after editing. Files are the proposal.
"""

REVIEW = """[ModularRSI: review]
Module: {module}
Findings: {findings}
Proposed source changes: {changes}

Review whether the edit implements a reusable harness improvement, preserves
interfaces and addresses the findings. Reject task-specific solutions, task IDs,
answer memorization, unrelated edits and changes to the underlying model.
Return JSON only: exactly one object with exactly the keys "accept" and "reason".
"accept" must be a JSON boolean; "reason" must be a non-empty string.
Acceptance example:
{{"accept": true, "reason": "The edit preserves interfaces and implements a reusable improvement."}}
Rejection example:
{{"accept": false, "reason": "The edit changes behavior outside the findings."}}
Put all assessment and provenance details inside "reason". Do not add other keys,
Markdown fences, headings, explanations or text outside the JSON object.
"""

INTEGRATE = """[ModularRSI: integrate]
This checkout is the current integration candidate; reference directories retain
the independently evolved module versions.
Module boundaries: {files}
Contrastive evidence from the COMBINED harness: {evidence}
Module evolution histories: {history}

Resolve interactions between modules, conflicting assumptions and duplicated
mechanisms. Stay within the union of the declared editable files.
Batch-level protocol errors do not identify a failing task; repair the execution
interface without drawing task-specific conclusions from those errors.
Preserve interfaces, underlying models and credentials. Make general changes;
do not encode task-specific answers or inspect downstream benchmarks/graders.
Merge redundant functionality where evidence supports doing so. Summarize edits.
"""

MERGE = """[ModularRSI: merge]
The editable checkout is the common seed. Each reference directory contains a
native checkout of one independently evolved functional module.
Module files: {files}
Module evolution histories: {history}

Read the reference versions and integrate their module-specific improvements
into the editable checkout. Shared files contain independent alternatives based
on the SAME seed: combine their changes rather than overwriting one branch with
another. Preserve unchanged resources, permissions and execution interfaces.
Do not copy reference directories into the harness or modify those references.
Stay within the union of the declared module files. Keep the underlying model
and credentials unchanged; do not encode task-specific answers or inspect
downstream benchmarks/graders. Summarize the integrated changes.
"""
