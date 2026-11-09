# The adapter contract: bringing a harness onto this platform

This is what somebody implementing a harness adapter has to satisfy, and why each clause
exists. It is not a description of the platform (`INTERFACE.md` is that); it is the **five
things an adapter must do**, and the one record it must write.

**One sentence:** an agent is environment + harness + model; this platform fixes the model
and the environment for a run and measures a harness, so an adapter is the translation
between the platform's fixed side and whatever the harness expects.

```
        the platform owns                 the adapter translates                 the harness
  ┌──────────────────────────┐        ┌─────────────────────────┐        ┌───────────────────┐
  │ model: base URL, id, key │ ─────► │ native config / argv    │ ─────► │ own agent loop    │
  │ environment: task.json   │ ─────► │ prompt / goal text      │        │ own tools         │
  │ verifier: NOT given       │        │ (never the score)       │        │ own trace format  │
  └──────────────────────────┘        │ trace: canonical records│ ◄───── │ own trajectory    │
                                      └─────────────────────────┘        └───────────────────┘
```

## 1. The model contract — one contract, every adapter adapts to it

The platform names one endpoint and one model; the harness uses them for **every** call.
There is no second route to a model, and an adapter that lets its CLI reach one is out of
contract.

| variable | meaning | who fills it |
| --- | --- | --- |
| `HG_AGENT_BASE_URL` | an OpenAI-compatible base URL (`…/v1`) | the platform, per task |
| `HG_AGENT_MODEL` | the model id, bare (`qwen3.5-9b-local`) | the platform, per run |
| `HG_AGENT_API_KEY` | the key for that endpoint | the platform |
| `HG_AGENT_BACKEND` | `mock` (no network, no cost) or the provider (`openai`) | the platform |
| `HG_AGENT_DEADLINE_S` | when the task must be over; stop before this | the platform |
| `HG_AGENT_CONTEXT_WINDOW`, `HG_AGENT_MAX_TOKENS` | budgets the harness must not exceed | the run's config |

Three rules, each with a measured reason:

1. **Every model call goes to `HG_AGENT_BASE_URL`.** The platform counts calls at that
   endpoint; a call that goes elsewhere is invisible to the record and unpriced by it.
2. **`HG_AGENT_BACKEND=mock` must work** with no network and no cost. It is how
   `tools/validate_harness.py --smoke` and every model-free smoke run work. A harness that
   cannot answer a trivial task on the mock cannot be tested before it is paid for.
   `base_harness/miniswe/mock_model.py` is the worked example for a CLI that speaks HTTP
   rather than these variables.
3. **Report the model that actually ran.** If the CLI's model did not come from
   `HG_AGENT_MODEL`, write a `harness_identity` record that says so (section 4). A curve
   point whose `agent_model` is wrong is a number about a different experiment.

How the four current adapters translate it — the point is that they all do the *same* job,
not that they look alike:

| harness | the contract arrives as |
| --- | --- |
| `loop` | `HG_AGENT_*` read directly (python, `openai` client) |
| `miniswe` | `-m openai/<model>`, `-c model.model_kwargs.api_base=…`, `-c …api_key=…` |
| `codex` | a generated `config.toml`: `model`, `model_provider.base_url` |
| `opencode` | `OPENCODE_CONFIG_CONTENT` (JSON): `provider.harnessgrad.options.baseURL` |
| `deepseek-harness` | its own profile / `DSH_*` variables |

## 2. The environment contract — in and out, and never the score

**In:** `task.json` (`task_id`, `goal`, `inputs`, `submission`). The harness acts on `goal`;
`inputs` is a convenience and may be ignored; `submission` (when present) says which files
are the result.

**Out:** everything in `--workdir`, plus `answer.txt` when the task's answer is text, plus
`trace.jsonl`. The platform scores the result; the harness does not score, and it does not
receive a score — in either half:

* at **test time** a harness that can see its own score can optimise against the referee,
  and the measurement stops measuring the task;
* at **train time** the *method* gets the traces and the scores; the harness still does not,
  because the harness is the artifact being edited, not the thing reading the feedback.

**Declared capability:** `env_kinds` (`["files"]`, `["exec"]`, or both) says which
environments the harness can run in, and the platform **refuses** a run whose dataset needs
a kind the manifest does not declare — rather than scoring zero on every task for a reason
that has nothing to do with the harness. `runtime_paths` names host trees (a CLI, a
runtime, a package directory) the sandbox must bind read-only; a tree you need and do not
declare is a `ModuleNotFoundError` that reads like a broken harness.

## 3. The editable surface — declared and recorded, never enforced

`harness.json`'s `name` and `version` are identity: a method may not change them. Everything
else in the directory is the method's to edit, and that is not an oversight — the ability to
rewrite its own improvement mechanism is the thing this platform measures, so a list of the
files a method *may* edit would take the measurement back (`INTERFACE.md` §4.2).

What a manifest may add is a **`components` map**: a map for reading, never a fence.

```json
"components": {
  "context": ["prompts/system.md"],
  "control": ["harness.json", "runtime/agent.py"],
  "unattributed": ["README.md", "requirements.txt"]
}
```

The ten components are `harnessgrad/components.py:COMPONENTS` — the same list
`tools/dataset_classes.py` re-exports as `EXERCISES`, so a dataset's demand and a round's edit
are phrased over one vocabulary. Every launchable base harness carries a map (2026-11-02);
`oracle` does not, because it is a control that is never edited. A round's curve point then
carries `editable_surface_touched` (the paths `git diff` moved) and
`editable_surface_components` (the names they belong to), which is what lets "context handling
improved" be a claim with a path behind it. `unattributed` is the one key that is not a
component: it is how a declaration stays *complete*, so a licence, a fixture or a mock model is
declared as belonging to no component instead of being left unnamed.

Three behaviours worth knowing before writing one:

* an edit to a path the map does not name is recorded as `unattributed`, **never refused** —
  including a path declared under `unattributed`, which still appears in the touched list (an
  empty component list *and* an empty touched list would read as "nothing changed");
* a path may not be declared under a component **and** under `unattributed`; that is a
  contradiction, and `declared_components` refuses it;
* a harness with no map has no attribution, so a point from it is not a claim about a
  component (`docs/harness-evolution-protocol.md` §4, rule 3).

**Why not enforcement.** It was built and rejected on 2026-11-02. The only allowlist in the
tree is `deepseek-harness/surface.json`, and it describes the DSH *runtime profile* a patch is
applied to — a different tree from the harness source that sits beside it. Enforcing it against
harness source paths refuses the `agent.py` edit that *is* the improvement; honouring its own
`apply.path_root` instead would enforce a fence nobody measured, on a platform whose contract
is that the harness is entirely editable. What is recorded instead is the attribution above,
and `surface.json` now says of itself that it is declared, not enforced.

## 4. The trace contract — the record every adapter writes

`trace.jsonl`, one JSON object per line. The platform reads it with **one** extractor
(`eval/observe/tracing.py:loop_facts`) for every harness, so these five clauses are what
makes a method work across adapters:

| # | clause | what the platform does with it |
| --- | --- | --- |
| 1 | one `harness_identity` record: `{"harness": …, "agent_model": …, "agent_backend": …}` | the curve point's identity (last one wins) |
| 2 | a final `usage` record: `{"calls": N, "input_tokens": …, "output_tokens": …}` | cost accounting, and `ended` (absent `usage` reads as a crash) |
| 3 | one record per model turn, carrying `reply` (the model's text, possibly `""`) | `model_calls` |
| 4 | one record per executed action, carrying `command` **and** `observation` | `commands`, and the environment's answer |
| 5 | a terminal record: `{"submitted": true, "answer": …}` or `{"error": …}` | how the run ended, and why |

Clause 4's `observation` is the training-time feedback interface — the thing the method
learns from, made the same shape for every environment:

```json
{"step": 3, "reply": "list the directory", "command": "ls",
 "observation": {"kind": "shell", "returncode": 0, "output_chars": 42, "truncated": false}}
```

`returncode` is the field that reads the same for a shell, an HTTP call and a file system
(`0`, an HTTP status, `null` where the environment reports nothing). `kind` says which
environment spoke. The platform counts `observations`, `nonzero_returns` and a
`return_codes` histogram, so "twelve commands, all zero" and "twelve commands, all 127"
stop looking identical to a method.

**Check a trace against this with `python3 tools/check_trace_contract.py <trace.jsonl>`.**

### Measured: what the contract is for

The failure it prevents is silent. On the two shapes, `loop_facts` reports:

| trace shape | steps | commands |
| --- | --- | --- |
| `harness_identity` + `codex_event` + `usage` (a working CLI harness) | **0** | **0** |
| `harness_identity` + `step/reply/command` + `usage` (the same work) | 2 | 2 |

An adapter that works, is measured, and tells the method it did nothing. That is why
clause 3 and clause 4 exist and why they are checked rather than suggested.

### Where the six base harnesses stand

Checked with `tools/check_trace_contract.py` where a trace could be produced without a
model; the rest are read from their source.

| harness | 1 identity | 2 usage | 3 turns | 4 feedback | 5 terminal |
| --- | --- | --- | --- | --- | --- |
| `miniswe` | yes | yes | yes | **yes (v1)** | yes |
| `loop` | yes | yes | yes | yes, **v0**: `exit`/`output` inline | v0: no record, `ended` inferred |
| `codex` | yes | yes | yes | yes (`exit_code`; a patch is `apply_patch`) | yes (when it submits) |
| `opencode` | yes | yes | yes | yes, `returncode: null` (the CLI reports none) | yes |
| `deepseek-harness` | yes | yes (real tokens, from the session log) | yes | yes (`tool/call` / `tool/result`, `returncode` from `meta.exitCode`) | yes (`turn/end`) |
| `cli_agent` | yes | no (`calls` unknown) | no (it wraps an arbitrary CLI) | one action: the CLI itself, with its exit code | partial |

### The two opaque adapters, and why they stop where they do

`deepseek-harness` prints its reasoning to **stderr** and its final message to **stdout**,
and for a while this document said that made the feedback clause unreachable -- that DSH
would have to grow a structured mode. That was wrong. DSH **flushes a session log** on exit
(`sessions.flush`, before the headless runner prints its answer):
`<DSH_HOME>/sessions/<cwd-key>/<session-id>/session.v<N>.jsonl[.zstd]`, carrying `turn/*`,
`step/*`, `assistant/message` with `usage`, `tool/call` (`name`, `arguments`) and
`tool/result` (`message`, `error`, `meta`). The adapter was reading the two pipes and
ignoring the file.

Measured on a real log on this host (330 events, 98 tool calls, 29 assistant messages): the
translation yields `model_calls 29, commands 98, observations 98, tokens 233 527` -- the
token count this adapter previously reported as **nothing at all**. It is also the only
adapter besides `codex` whose `returncode` is a real exit code (`meta.exitCode`, from the
bash tool's own `ShellRunResult`), and `null` where a tool reports none.

The log is versioned (`session.v3`), optionally zstd-compressed, and written at the end --
so a killed run may leave none, and the two text streams remain as the fallback. Which path
was taken is on the record (`session_log.translated`).

`cli_agent` wraps an arbitrary CLI and sees an exit code. It records the invocation as one
action with an `observation` (exit code, output size) because that is what it executed, and
it records no `calls` because one CLI invocation is not one model call. **A method that
needs per-step feedback should use the CLI's own adapter** (`codex`, `opencode`, `miniswe`);
the wrapper is for CLIs nobody has written one for, and it is honest about that -- which is
also why it is not shipped in `base_harness/`: since 2026-11-01 it lives in
`tests/fixtures/base_harness/cli_agent/`, because a wrapper that cannot meet clauses 2-4 is
a test instrument, not an example to copy. Its row is kept here so the table still shows
what "cannot see the CLI's turns" costs.

### What a harness hands over, and who decides where

**The task says what to deliver; the harness only keeps it.** That is the whole rule, and
the reason adapter prompts must not impose a universal answer-file requirement.
A prompt may mention `answer.txt` conditionally when the task calls for it.

The platform knows the grading, so the platform writes the delivery requirement into the
task's `goal` (`eval/run/task_contract.py:runtime_task`):

* an **`answer`-kind** task whose goal does not already name the file gets
  `## How to answer -- Write the answer, and nothing else, to answer.txt in your working
  directory.` injected, because the platform compares that file with `expected`;
* a dataset that already ships its own *How to answer* section keeps its wording -- the QA
  scaffold (17 adapters: `gsm8k`, `aime`, `triviaqa`, `hotpotqa`, …) builds exactly such a
  section, naming `answer.txt` or the `qa submit` CLI;
* a **`command`-kind** task gets nothing added. AppWorld, ALFWorld, Jericho,
  ScienceWorld, Terminal-Bench, τ-bench, CostBench, DS-1000, GAIA, LiveCodeBench, LoCoMo,
  MemoryAgentBench, PersonaMem-v2, StrongREJECT, Bird mini-dev, Kumo, CooperBench and
  τ³-bench are graded by their own command against the environment or the recorded
  interaction; telling the agent to write an answer file would invite it to stop early.
  CostBench's goal says so itself: *"answer.txt cannot replace the tool costs and invalid
  calls"*.
* a task that declares file submissions carries `task["submission"] = {"kind": "files",
  "paths": [...]}`. Codex, DeepSeek Harness, OpenCode and OpenClaw check those
  files: leave them in place; a final message cannot replace them.

The platform also renders `cli_task` from the public goal, runtime instructions, public
inputs and file-delivery descriptor. The Codex, DeepSeek Harness, OpenCode and OpenClaw
CLI adapters append this common content to their own editable or native preambles;
standalone adapter invocations without `cli_task` retain a public-field fallback. The
platform regenerates the field rather than trusting one supplied by a dataset. DeepSeek
Harness's preamble remains editable; public task inputs are always included.

What remains the adapter's job is only the *collection*: a file the agent wrote during the
run wins (`answer_source: artifact`), otherwise the final message is written to
`answer.txt` (`final_message`), and the source is on the trace either way.

Verified on `demo/t01` ("Answer with the word alpha", an `answer`-kind fixture) after the
move: all six adapters scored 1.000 with a conformant trace for that fixture.

**What a harness cannot see.** `task.json` carries `goal`, `task_id`, `inputs` and
`submission` and the platform-rendered `cli_task`; it does not carry the verifier kind. The injection happens *before* the task
is written, so a harness reads the requirement as part of the task -- which is the only
channel it needs.

## Two dialects, and why the reader learned the old one

`v1` is the shape above. `v0` is what `base_harness/loop` writes and what **every trace
stored so far** contains: the same answer inline on the action record, as `exit`/`output`.

The reader (`loop_facts`) reads both, and `tools/check_trace_contract.py` says which one it
saw. Measured on a real `loop` trace, before and after:

    before: commands 32, observations 0
    after:  commands 32, observations 32, return_codes {"0": 32}, dialect v0

Two reasons for doing it in the reader rather than by editing `loop`:

* **history.** A contract that made every stored trace unreadable would be a report about
  the future, and this platform's own audit is the archive it would fail to read.
* **identity.** Changing `loop` changes its content address, and under this platform's rules
  that makes it a *different harness* (`harness_sha` is on every curve point). Making a
  report uniform is not a good reason to move it; if it moves, it should be deliberate, with
  a version bump and the cost stated.

What v0 cannot do is carry a non-shell environment's status: `exit` is a shell field, so a
document-retrieval environment has nowhere to put its answer. That is why `v1` exists and
why new adapters write it.

## 5. The answer contract — three sources, one named

The harness writes `answer.txt`, or leaves the task's declared files, or says nothing and
lets its final message be the answer. Whichever it is, the adapter records which:

* `artifact` — the run wrote `answer.txt` itself (compared by content, not by mtime);
* `files` — the task declared `{"submission": {"kind": "files", "paths": [...]}}` and those
  files exist (a missing one is a **harness failure with a named cause**, not a score of
  zero: an empty directory otherwise reads as "the harness did the task badly");
* `final_message` — neither, so the last non-empty model message is written to `answer.txt`.

## 6. Onboarding checklist

1. `harness.json`: `name`, `version`, `entrypoint`, `env_kinds`, `runtime_paths`.
2. `agent.py --task <task.json> --workdir <dir>`: read `task.json`, work in `--workdir`,
   exit non-zero on failure.
3. Model: read the six variables in section 1 and translate them into the CLI's own
   config; add a `mock` path so the harness runs with no network.
4. Environment: work in the workdir, never read a verifier, and follow the task's delivery contract per section 5.
5. Trace: the five clauses of section 4 — run `tools/check_trace_contract.py` until it says
   `OK`.
6. Validate the source and run the smoke check for the declared runtime.
   See [checking your harness](writing_a_harness.md#5-checking-your-harness-before-contributing).
7. Record what the translation did in `harness_identity.config` when it is not obvious
   (`miniswe` records the `api_base`, the `step_limit`, and whether the dependency came from
   a declared tree).

## What this contract deliberately does not decide

Language, tools, prompts, control flow, memory, how many model calls, whether the harness
calls a model at all. Those are what a method exists to change, and specifying them would
make every harness the same harness.

Native delegation can produce child sessions outside the parent transcript. Adapters
should preserve available child evidence separately and mark total token usage unknown
when the full session tree or its usage cannot be verified.
