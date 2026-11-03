"""Reflection and native-directory curation roles."""

REFLECT = """[EvoHarness: reflect]
Extract a candidate lesson from this failed or negatively evaluated execution.
The record contains the solver's available actions, observations, outcome and
benchmark feedback. Separate a reusable procedure from the answer to this case.
Identify a concrete failure, its applicability trigger, and supporting evidence.
Do not invent missing steps or treat truncated evidence as a complete trace.
Do not inspect the skill library; this is a candidate memory, not an edit.
Return JSON with non-empty strings: lesson, trigger, evidence, scope_hint.
If there is no supported reusable lesson, return {{"skip": true}}.

Execution record:
{evidence}
"""

COMPILE = """[EvoHarness: compile {level}]
Compile candidate memories into the {level} level of the skill harness.
For topic skills, preserve procedures for recurring task types or interfaces.
For general skills, compare failures across distinct tasks and extract shared
planning, tool-use, verification or recovery patterns. General skills require
at least {general_min_tasks} distinct supporting task ids, from this batch. Old sources belong to the previous rule,
not automatically to a revision. A task id alone does not prove a shared pattern.
Filter weak, contradictory and redundant lessons. Never retain a case's answer,
grading secrets, exact test ids as applicability rules, or benchmark shortcuts.
Prefer merging related lessons or revising a mistaken rule to adding duplicates.
Merge retains useful old guidance; revise corrects guidance contradicted by evidence.
Only edit this level. Each content must be at most {max_skill_chars} characters.
Limits: {max_general_skills} general, {max_topic_skills} topic skills;
{max_library_chars} characters for normalized YAML metadata and procedure bodies.

Edit the provided native harness checkout directly. Create/update/delete only
skills of this level. Leave configuration unchanged; native discovery loads skills.
Preserve unrelated native skills, prompts and all runtime code. No logs or notes.
Use native SKILL.md files with YAML frontmatter:
---
name: short-kebab-name
description: Purpose and applicability trigger for native discovery
flashrsi:
  algorithm: evo_harness
  level: {level}
  source_task_ids: [ids from this batch's candidate memories]
---
Short actionable procedure body, which may include code blocks.

For every changed procedure, cite only supporting memories from THIS batch;
never copy historical ids to satisfy the general evidence threshold. Unchanged
skills retain their metadata. Snapshot history preserves earlier versions.
Use the native skill directory visible in the checkout. Directory skills are
discovered by the solver; no activation configuration edits are needed. Do not
shadow existing flat skills with same-named directories.
Do not inline the library into a system prompt or modify component declarations.
If no supported edit is useful, leave the checkout unchanged. Return a brief
account of your changes; files, rather than that account, define the new state.

Current full library:
{library}

Candidate memories from this batch:
{memories}
"""
