"""ACE role prompts adapted from the old FlashRSI port.

Upstream: https://github.com/ace-agent/ace
Commit: 82709de050e1db6e6ef2f07bcb0393560b94992a
"""

from __future__ import annotations

__all__ = ["REFLECT", "CURATE", "RETRY"]


REFLECT = """[ACE: reflect]
You are an expert analyst and educator. Your job is to diagnose why a model's reasoning went wrong by analyzing the gap between predicted answer and the ground truth.

Instructions:
- Carefully analyze the model's reasoning trace and environment feedback.
- Identify specific conceptual errors, calculation mistakes, or misapplied strategies.
- Provide actionable, generalizable insights; focus on root causes rather than symptoms.
- Tag playbook bullets as helpful, harmful, or neutral when they affected the result.

Your output must be a JSON object with exactly these fields: reasoning,
error_identification, root_cause_analysis, correct_approach, key_insight, and
bullet_tags. Each bullet_tags item has an id and a tag, where tag is helpful,
harmful, or neutral. Do not use markdown fences.

Question and traces:
{evidence}

Current playbook:
{playbook}
"""


CURATE = """[ACE: curate]
You are a master curator of knowledge. Identify ONLY NEW, actionable insights missing from the current playbook based on the reflection. Do not regenerate the playbook. Return valid JSON only with fields reasoning and operations. Every operation must have type ADD, a section, and concise content; bullet IDs are assigned by the system.

Training progress: {progress}
Current playbook:
{playbook}

Recent reflection:
{reflection}

Source traces:
{evidence}

If no new content is warranted, return an empty operations list. Do not emit
UPDATE, MERGE, DELETE, or CREATE_META operations.
"""


RETRY = """REFLECTION FROM THE PREVIOUS ATTEMPT:
{reflection}
"""
