"""ADAS's two role prompts: propose the next design, then reflect on it.

The proposal template carries two things upstream's does not need. Upstream's
meta agent writes a ``forward()`` function into a fixed framework, so its
prompt describes a code interface; here the design *is* the harness directory,
so the prompt hands over a checkout and states the component rules that decide
which files may exist. And the account of the edit comes back as prose in the
reply rather than as a docstring in generated code, because the archive is read
as prose by the next round.

Upstream: https://github.com/ShengranHu/ADAS
Commit: TODO -- pin the reviewed upstream revision (network access unavailable
    during this migration; see docs/adas.md).
"""

from __future__ import annotations

PROPOSE = """[ADAS: propose]
You are the meta agent in Automated Design of Agentic Systems. Your job is to
design a better agent than any in the archive below. The current agent is checked out
in your working directory: inspect it, then edit it into your new design.

ARCHIVE OF DESIGNS ALREADY MEASURED (score is held-out accuracy, 0 to 1):
{archive}

Read the whole archive before you edit. Do not repeat a design that is already in it,
and do not simply revert to an earlier one -- propose something the archive does not
yet contain, and prefer an idea that the scores suggest is worth trying. Interesting
new designs are worth more than a safe variation on the leader.

Each file you add or edit must match exactly one of these component rules, or the whole
edit is refused:
{rules}

The agent must stay general: it is measured on held-out tasks it has not seen, so
do not hard-code answers or name specific tasks.

When you are done editing, describe your design in a few sentences bracketed exactly
like this, and nothing after it:
<DESIGN>
what this agent does differently, and why you expect it to score higher
</DESIGN>
This description is what the next round reads beside your score, so write what a
designer would need to build on it.
"""


REFLECT = """[ADAS: reflect]
You are still the meta agent in Automated Design of Agentic Systems, now reviewing
your own design before it is measured. Your edit is in your working directory, exactly
as you left it.

Revise it if you find a problem. Check in particular:
- Is it actually novel against the archive, or a restatement of a design already there?
- Would it run at all? Look for broken imports, references to files that do not exist,
  and syntax the harness cannot load.
- Does it stay general, or does it lean on specifics of the tasks you were shown?
- Does every file you touched still match exactly one of these component rules?
{rules}

Leave the directory alone if you find nothing worth changing. Either way, reply with
your current description of the design, bracketed exactly like this:
<DESIGN>
what this agent does differently, and why you expect it to score higher
</DESIGN>

YOUR DESCRIPTION SO FAR:
{account}
"""


__all__ = ["PROPOSE", "REFLECT"]
