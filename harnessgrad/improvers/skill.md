# The platform's default skill: how to improve a harness

This is the **default** skill. A method that ships its own `skill.md` uses that one instead;
a method that ships none gets this. It is deliberately about *how to change a harness*, and
deliberately silent about *whether to accept the change* -- acceptance, budgets, parent
selection and critics belong to the method, and putting them here would make every method
the same method.

Read `INTERFACE.md` §4.45 and §4.47 before trusting anything below; this file is the
short version an improver is handed.

## What you are looking at

A **harness** is the structured execution layer around a model: the thing that decides how
a task becomes model calls, how tool output comes back, and when the run stops. It is not
the model. On this platform a harness is a directory:

```
harness.json        name, version, entrypoint, env_kinds    <- not editable
agent.py (or any)   the code                                  <- editable
requirements.txt    its own dependencies                      <- editable
```

Two harnesses exist for the same job at once and that is the point: the **base** harness
(`base_harness/loop`) is the floor, and your job is to make a candidate that scores higher
on the same tasks.

## Where the evidence is

Everything you are allowed to read is under `_harnessgrad/` in your workspace:

| path | what it holds |
|---|---|
| `round.json` | this round: the incumbent's score, per-task scores, the identity of what was measured |
| `traces/<task>.jsonl` | **the real conversation** of the incumbent on each task: every model reply, every command, every output. This is where the truth about a harness is |
| `tasks/<task>.json` | the task's instruction, how it is graded (`argv`, `reward_file`, `checks`), and the verifier's `verdict` -- including the check's own output |
| `history/round-<N>.json` | the same summary for earlier rounds |
| `history/traces/round-<N>/` | earlier rounds' traces, newest window only |
| `states/round-<N>/` + `states/index.json` | complete harness trees from earlier rounds, so you can build on an older state instead of only the incumbent |

The traces are the single most useful input and the most commonly wasted one. Read them
before proposing anything: a round where every task shows `calls: 1` is a different problem
from a round where every task shows `calls: 32, commands: 32, answered: false`.

## What you submit

An **edit sequence** -- a list of `{path, content}` -- plus, in the reply envelope:

```
label          one line saying what you changed
hypothesis     why you think this moves the score (this is recorded and printed; a
               method that says nothing here costs a reader the only explanation)
edit_kind      your own word for the kind of change
claimed_cost   optional, your own count
changed        true/false -- false means "I reject this candidate", which is NOT "stop"
stop           true only when you are finished for good
```

`harness.json` is protected: an edit to it is refused, not applied. So is widening
`env_kinds` past what the run's door allowed. A candidate that does not compile, or whose
`entrypoint` is gone, is recorded as **unmeasured**, not scored 0.

## What you may not do in phase 2

You may analyse your own edit, run static checks, and **write and run your own tests** --
compile it, drive its entrypoint on a task you construct yourself.

You may **not** run a task from the dataset to see how your edit scores. That is structural,
not a rule: a task's environment is either a container (your sandbox has no docker socket)
or a directory you were never given. The score is the platform's to produce.

## Four failures that are worth more than any prompt tuning

These are measured on this platform, in Terminal-Bench runs of `base_harness/loop`. They
are general -- they are about the *shape* of a ReAct loop, not about one benchmark -- and
every one of them costs a whole round.

**1. One unparseable reply ends the task.** The base harness parsed the model's reply with
`json.loads`, then fell back to slicing from the first `{` to the **last** `}`. The measured
model emits two or three JSON objects in one reply:

```
{"tool": "bash", "command": "cat /app/filter.py"}}
{"answer": ""}
```

`json.loads` reports `Extra data`; the slice produces `{...}}{...}`, which is not JSON; the
loop treats that as a harness failure and **breaks**. On `loop-terminal_bench-41133` round 0
four of five tasks died after a single model call (`calls: 1`, `parse_error`). Take the
**first** JSON object and ignore the rest (`JSONDecoder.raw_decode`), feed the parse error
back as the next observation, and retry instead of breaking.

**2. The budget runs out with nothing submitted.** The measured floor is a hard step cap,
and a harness that spends every step exploring scores 0 on tasks whose grading is "write
this file". A loop needs a *submission* mechanism: a step budget it tracks, and a final
turn that must produce the artifact rather than another `ls`.

**3. The deliverable is written where the check does not look.** A task's check reads a
specific path (`/app/out.html`, `/app/move.txt`, a reward file). A harness that leaves its
work in a scratch directory, or writes an **empty answer**, has failed even if it did the
work -- and an empty answer is the worst case, because it is stored as a result.

Before proposing an edit, look at `tasks/<task>.json`: if the verifier is a `command` with a
`reward_file`, the score is about a *file in the environment*, and no amount of reasoning in
the transcript will substitute for it.

**4. The model repeats one command and never submits.** A small model at
`temperature = 0` re-emits the same action once it has an observation it cannot interpret,
and it will do that until the budget is gone. Measured, `fix-code-vulnerability`, three
rounds: `calls: 32, commands: 32, answers: 0, ended: budget_exhausted` in every round, and
the last seven steps of round 3 were the **same command seven times**:

```
step 25  {"tool": "bash", "command": "sed -n '3765,3775p' /app/bottle.py"}
...
step 31  {"tool": "bash", "command": "sed -n '3765,3775p' /app/bottle.py"}
```

A step cap does not stop this: the model is spending the budget, one identical call at a
time, and every repeat is fed back into its own context as another `exit=0` observation.
Two different changes fix it, and they are worth doing together:

* **Break the repetition.** Remember the previous command. When the model asks for it
  again, do not run it: tell the model that this command and its output are already in the
  conversation, and that it must choose a different action. A repeat is not progress, and
  running it again makes the transcript *worse*, not longer.
* **Force a finish near the budget.** In the same run, injecting
  `"You are near the end of your step budget. Stop exploring. These required paths are
  still missing: /app/report.jsonl. Create them and submit your final answer."` at
  `MAX_STEPS - 8` made the model create the missing file on the very next step. It then
  fell back into the repeat loop, so the reminder is not sufficient on its own -- but it is
  the change that produced the artifact, and a repeat-breaker next to it is what stops it
  from being thrown away.

## What "improving" should mean, in order

1. Make the loop **survive** -- a reply it cannot parse must not end the run.
2. Make the loop **finish** -- a step budget, a submission step, and something that breaks
   a repeated command instead of running it again. A loop whose model never submits cannot
   be improved by anything downstream of that.
3. Make the loop **see** -- context management: keep the task goal and the last failed
   attempt, drop the 200 lines of `apt` output.
4. Then make it **smarter** -- better prompts, planning, retries, a critic.
5. Last, and only with the earlier four in place, cost: fewer steps, fewer tokens, same
   score.

A change to (4) before (1)-(3) is the most common wasted round: a better prompt does not
help a loop that stops at step 0.

## Provenance

Whatever you produce is recorded with the identity of the improver that produced it
(`improvers/improvers.json`, `tools/improver.py`) and with the tree hash of the harness you
produced. Two rounds with the same tree hash are the same harness: if you changed nothing,
say `changed: false` rather than re-emitting an unchanged file.
