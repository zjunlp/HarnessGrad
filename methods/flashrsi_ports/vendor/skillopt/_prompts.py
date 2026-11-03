"""SkillOpt prompts adapted for a component's file tree, not one fixed skill."""

_PATCH = """
Return ONLY JSON with this schema:
{{"reasoning":"why", "edits":[{{"path":"relative/file.md",
"op":"append", "content":"complete text", "source_type":"failure",
"support_count":1}}]}}
Operations: append (may create a file), insert_after, replace, delete.
Except append, each edit needs target: exact text occurring once in that file.
Except delete, content must be nonempty. Paths must match the skill rules.
Do not edit files or use tools; return the proposed edits as text.
Keep native file structure and frontmatter. Prefer editing existing skills.
For an empty skills/** tree, create skills/<name>/SKILL.md with YAML name and
description frontmatter and useful instructions. Never encode task answers or
task-specific entities. Do not change or introduce SLOW_UPDATE marker regions.

CURRENT TEXT FILES (path -> content):
{files}
"""

_REFLECTION = (
    _PATCH
    + """
SKILL PATH RULES: {rules}
At most {budget} edits. Seek common, generalizable patterns across the evidence.
Include only guidance missing from the current skills.
EVIDENCE (training tasks only):
{evidence}
RECENT REJECTED PATCHES:
{rejected}
OPTIMIZER MEMORY:
{meta_skill}
"""
)

REFLECT_FAILURE = (
    "[SkillOpt: failure]\nIdentify shared failure causes, compare predictions with "
    "the grading feedback and reference answers, and propose corrective guidance.\n"
    + _REFLECTION
)
REFLECT_SUCCESS = (
    "[SkillOpt: success]\nIdentify repeatable successful procedures worth retaining. "
    "Use source_type success for these edits.\n" + _REFLECTION
)
MERGE = (
    "[SkillOpt: merge]\nMerge overlapping proposals without losing their evidence. "
    "Preserve path and source_type. In a cross-source merge, failure fixes have "
    "priority. Remove redundant or contradictory edits.\n"
    + _PATCH
    + "\nSOURCE GROUP: {kind}\nPROPOSALS:\n{patches}\n"
)
RANK = """[SkillOpt: rank]
Select at most {budget} edits by systematic impact, generality and compatibility.
Prioritize failure fixes. Indices are zero-based, unique, and refer to EDITS.
Return ONLY JSON: {{"selected_indices":[0]}}. Do not edit files.
CURRENT FILES: {files}
EDITS: {edits}
"""
META_SKILL = """[SkillOpt: meta]
Compare adjacent epoch versions on the same training tasks. Distill lessons for
the future optimizer, especially regressions and unsuccessful edit strategies.
Return ONLY JSON: {{"meta_skill_content":"concise optimizer guidance"}}.
Do not edit files.
PREVIOUS FILES: {previous}
CURRENT FILES: {current}
COMPARISON: {comparison}
"""
SLOW_UPDATE = """[SkillOpt: slow]
Compare adjacent epoch versions on the same training tasks. Propose concise
long-term instructions for the target, prioritizing regressions and persistent
failures. Return ONLY JSON:
{{"guidance":[{{"path":"existing/skill.md", "content":"instructions"}}]}}.
Use existing skill text files only. Do not include SLOW_UPDATE markers: the
algorithm replaces the protected region. Do not edit files or memorize answers.
PREVIOUS FILES: {previous}
CURRENT FILES: {current}
COMPARISON: {comparison}
"""
