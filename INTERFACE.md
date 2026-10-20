# HarnessGrad Interface Contract (v0.1 — FROZEN)

> **This file is the only thing in this repository that cannot change casually.**
> Once a Trainer implements it, changing it means changing every Trainer.
> Every rule below states *why* it exists. A rule without a reason is a rule
> someone will break.

---

## 0. What this framework is

HarnessGrad is a **standardized measurement layer for harness self-improvement**.
It does not propose improvement methods. It defines how improvement is *measured*,
so that two different methods produce comparable curves.

**Division of labour — the whole design is this one table:**

| | Framework owns | Trainer (method author) owns |
| --- | --- | --- |
| Sampling | which tasks, how many, how many rounds | — |
| Execution | running the harness, collecting traces | — |
| Scoring | **all scores, all metrics** | — |
| Records | every curve point, every ckpt, every token | — |
| — | — | reading whatever it wants from the traces |
| — | — | deciding how to modify the harness |

**The framework is the measurement instrument and the referee. The Trainer is the
subject.** A subject that can score itself makes every cross-method comparison
meaningless, so scoring is not delegable.

**Positioning sentence (use this in the README and the paper):**
> We do not propose an RSI method. We build the instrument that makes RSI methods
> comparable — and we are therefore not competing with the methods we measure.

---

## 1. The exchange unit: a whole harness repository

A harness is **a complete, runnable repository**. The framework does not know or
care about its internal structure. There is exactly one interface point:

### 1.1 `harness.json` — the manifest (REQUIRED)

```json
{
  "name": "loop",
  "version": "0.1.0",
  "path": ".",
  "entrypoint": "agent.py",
  "backend": "cli",
  "install": "requirements.txt"
}
```

| field | required | meaning |
| --- | --- | --- |
| `name` | yes | harness identity, reported on every curve point |
| `version` | yes | harness identity, reported on every curve point |
| `path` | no (`"."`) | where the harness lives relative to the repo root. A harness may be a **subdirectory** of a larger project — RRSI's is `third_party/harbor_terminus2/`, DGM's is a subset of its repo root. |
| `entrypoint` | yes | the file the platform invokes |
| `backend` | no (`"cli"`) | how the entrypoint is driven; only `cli` is implemented |
| `install` | no (`null`) | dependency file relative to the harness root — `requirements.txt` (pip) or `.sh` (shell). **Runs once at import time** into the environment, via `tools/import_env.py --harness`; never during a measurement. **Required in practice by any harness that runs in a container and calls a model**: the image does not have your packages and the harness tree is read-only during a run. See §2.5.6. |
| `env_kinds` | no (`["files"]`) | which environments this harness can operate on (§2.5). The platform **refuses** a run whose dataset needs a kind the harness does not declare, rather than scoring it zero on every task. |
| `runtime_paths` | no (`[]`) | absolute trees mounted read-only because the harness needs them — a CLI installed outside `/usr`, a runtime it was built against. Bound *before* the platform is hidden, so declaring the platform's own path does not expose it. **Applies to `files` and to `exec`.** An earlier version restricted it to `files` on the theory that "inside a container the runtime is the image's" — which is false for a task image that ships no runtime at all (measured: the real Terminal-Bench image is `ubuntu:24.04` plus a `COPY`, with no `python3`). |

### 1.1.1 The harness is a command, not a language

The platform invokes `entrypoint` with an interpreter the environment provides. Python,
Node, a shell script and a compiled binary are all acceptable, and so is a **wrapper**
around somebody else's program: `base_harness/cli_agent/` is the worked example, and it
is how an agent CLI (Claude Code, Codex, OpenCode) becomes a harness here.

Three consequences a harness author should know, because each one has bitten a real
harness on this platform:

* **`$HOME` is writable and per task.** The sandbox starts from an empty root; a
  harness that writes session state under the inherited `HOME` would find a path that
  does not exist. The platform provides one, fresh for each task.
* **Trees outside `/usr` must be declared** via `runtime_paths`, or they are not there.
* **The harness may declare what actually ran it** by writing a `harness_identity`
  event into its trace (§3). This is how a wrapper around a CLI — whose model comes
  from the CLI's own configuration, not from `HG_AGENT_*` — keeps the record honest.
  A declaration that disagrees with the platform's environment is recorded as a
  conflict, not silently resolved.

**A contributor-facing version of this contract, with the task and answer
formats spelled out, is [`docs/writing_a_harness.md`](docs/writing_a_harness.md).
Conformance is checked by `tools/validate_harness.py` rather than asserted in
prose.**

**A Trainer MAY overwrite every other file in the repository, including the
entrypoint. A Trainer MAY NOT change `name` or `version`** — otherwise it can
submit work under another harness's identity.

### 1.2 Entrypoint contract

```
<entrypoint> --task <task.json> --workdir <dir>
```

* reads the task description from `task.json`
* may do anything inside `--workdir`
* exit code 0 on completion, non-zero on harness failure

**The harness does not score, and it does not report a score.** Its job ends when
it exits. What it leaves behind in `--workdir` — files, a running service, a
database, and optionally `answer.txt` — is its *result*, and the platform
evaluates that result itself (§2.2).

`answer.txt` used to be the exit of every harness. It is now **one optional
artifact**, read by one kind of verifier and ignored by the others. A task whose
grade is "the program now compiles" never looks at it.

**Deliberately minimal.** Any harness expressible as "given a task, work on it in
a directory" plugs in. Nothing about tools, prompts, control flow, or architecture
is prescribed — that is the point.

**Nothing in `task.json` is secret.** It is handed to the harness, so a task dict
carries only the task's *inputs and description*. The verifier and the state setup
live in separate dataset mappings (§2.4) for exactly this reason, and the registry
refuses a task dict that carries them.

---

## 2. The sampling loop (framework-owned)

```
    ┌─ round 0 ────────────────────────────────────────────────┐
    │ evaluate H0 on the task set   →  curve point 0            │
    └───────────────────────────────────────────────────────────┘
                              ↓  hand to Trainer
    ┌─ Trainer ────────────────────────────────────────────────┐
    │ reads the traces, edits the repo, returns H1              │
    └───────────────────────────────────────────────────────────┘
                              ↓  framework evaluates
    ┌─ round 1 ────────────────────────────────────────────────┐
    │ evaluate H1 on the SAME task set  →  curve point 1        │
    └───────────────────────────────────────────────────────────┘
                              ↓  ... repeat for `rounds` ...
```

### 2.1 The task set is fixed by H0 and never changes

A `SamplingPolicy` selects the task set **once, from H0's scores**, before any
modification happens:

| policy | task set | what it measures |
| --- | --- | --- |
| `all` | the whole evolve split | overall |
| `below_X` | tasks H0 scored `< X` | can the method fix what is broken |
| `above_X` | tasks H0 scored `≥ X` | does the method preserve what works |
| `random_N` | N tasks sampled with a recorded seed | cheap default |

> **Why H0 and not the current harness:** if the task set were re-selected each
> round from the *current* scores, then as the harness improves its task set
> gets harder, and the score can fall while the harness is getting better. The
> curve would be measuring the exam, not the student.
>
> **Why it is a declared, pluggable policy:** the choice of task set is not a
> convenience. It changes what the curve means (`below_X` and `above_X` answer
> different questions), so it is an experimental variable and is recorded on
> every curve point.

### 2.2 Scoring is paired, framework-owned, and takes the result as input

Every round is scored on the identical task set. Because the set is fixed,
rounds are directly comparable and the H0 point is a true baseline.

**The platform decides the score; a harness never produces one.** A number the
harness writes, and a number a method reports, are both recorded beside the
platform's (`method_reported`) and never overwrite it.

Where the check *runs* is a separate question from who owns it. It may run inside
the task's environment — a service that has to be up, a database that has to be in
a particular state, cannot be checked from outside — but it always runs **from a
location and against inputs the harness cannot modify**:

1. **The verifier's definition lives in the dataset**, never in the task dict and
   never written into the working directory.
2. **It runs after the harness has exited**, in its own sandbox invocation, with
   the harness's artifacts mounted **read-only**.
3. **Its inputs are re-materialized at verification time.** If the task ships tests,
   they are written again before the check runs, overwriting whatever the harness
   left. Without this, editing the tests is the cheapest way to score.
4. **The result is one score per task, produced by the platform.**

**Exit code and score are independent.** The harness's exit code says whether the
*harness* finished; the verifier says whether the *task* was completed. A harness
that crashed but left working artifacts still scores — the platform grades the
result, not the process. (This was already true: `eval/run/runner.py` recorded
`exit_code` and never used it in the score.)

**The harness may run the task's tests itself**, for its own feedback. That is its
own copy and the platform does not trust it; the authoritative check uses the
platform's re-materialized inputs.

**That is the *write* half, and the read half is a different rule** — worth stating
because the two are easy to conflate. Something placed in `SETUP` is **readable by the
harness**; that is what `SETUP` is for, and it is why a task can ship its own tests.
`VERIFY`'s `inputs` are not readable: they are written *after* the harness has exited,
so a harness that looks for them during its run finds whatever was in `SETUP`, or
nothing. A dataset that wants a check the harness cannot even *read* — not merely one
it cannot *edit* — must therefore put it **only** in `verify.inputs`. Both halves are
deliberate and neither is the default: shipping a check in `SETUP` is what lets a
harness see whether it is passing, and withholding it is what stops a harness from
fitting itself to the answer. A dataset that puts the same check in both has chosen to
show it, whatever it intended.

§2.5.7's service recordings are the limiting case of this: they are produced *while*
the harness runs, so there is no static copy to withhold, and the honest answer is
that the harness does not see them at all.

**The verifier is withheld from methods**, for the same reason eval traces are
(§2.3): a method that can read the check can fit itself to the check. Curve points
record the verifier's *kind*, never its arguments.

### 2.3 Which tasks are the method's to study: the dataset's default, the run's choice

The platform does not own the train/eval division. It is a property of an **experiment**,
not of a benchmark: the same 2,417 MuSiQue rows are one experiment's exam and another
experiment's study material, and nothing about the rows decides which. So the division has
a **default** that a dataset declares and a **run** that may replace it, and the run
records which one it used.

A dataset module **declares a default**:

```python
SPLIT = {"train": [...task ids...], "eval": [...task ids...]}
```

and states, as a fact about the benchmark rather than about any run, which of its tasks
belong to the benchmark's own evaluation set:

```python
OFFICIAL_EVAL = None        # nothing in this pool is study material -- every task in it
                            # belongs to the benchmark's evaluation set
```

| side | what the method is given | what its score is |
| --- | --- | --- |
| `train` | the **traces** (`traces/<task_id>.jsonl`) and the per-task scores | recorded as `train_score`; a diagnostic, never a result |
| `eval` | the **aggregate** `score` and `score_ci95`, **but neither traces nor per-task scores** | `score` — the number on the curve |

**Who decides the division, and what a run says about it.** `driver.py --split` picks one
of four sources, and every run records the choice as `split_source`, plus a
`split_sha` over the resolved task ids, in `run_meta.json`:

| `--split` | the division it resolves to | `split_source` |
| --- | --- | --- |
| `declared` (default) | the dataset's `SPLIT`, when it declares one; when it does not, nothing divides the pool, the run covers every task and says so loudly | `declared`, or `null` because there was no declaration to name |
| `default` | the platform's rule: a deterministic hash third of the pool, named rather than implied | `default` |
| `official` | `OFFICIAL_EVAL`: the benchmark's own evaluation set, taken from the adapter | `official` |
| `<file.json>` | a division the caller supplies, validated like any other | `file:<resolved path>` |

**Two points belong on one axis only when `split_sha` agrees.** This is §2.1's rule applied
to the second thing that fixes the task set: §2.1 pins *which tasks* are drawn (the sampling
policy) and this pins *which side* each drawn task is on. A curve whose points were scored
on different task sets is not one measurement, and before `split_sha` the only trace was
the dataset's name, which cannot tell `--split official` from `--split declared` on the same
dataset. The sha is order-insensitive and content-sensitive, so reordering a file does not
fork a curve and moving one task does.

**And the oracle's provenance rides on the same record.** §2.5's oracle control submits the
dataset's own reference artifact, and until the authored-reference contract that artifact was
always the benchmark's released answer — a gold answer, ALFWorld's walkthrough, BIRD's gold
SQL. A dataset may now submit one the **platform authored**, validated by the released tests
and held out of the agent's view, and "the benchmark's reference scores 1 here" is a different
claim about the check than "a reference we wrote scores 1 here". So every run records
`oracle_source` (`released` | `authored`) and, for an authored one, `oracle_coverage` — how
much of the pool the authored reference covers. The attribute is **required** of every
runnable dataset by `tools/dataset_lint.py` and is never defaulted, for the same reason
`OFFICIAL_EVAL` refuses silence: the dataset that needs the field is the one whose author has
just written a reference by hand, and a default of `released` is exactly that blind spot.
`null` in a run record means a refused dataset, which has no oracle at all.

**A run evaluates exactly one side (§2.6), so a run-time division is validated only on the
side it evaluates.** `--side eval --split official` is the report run: the benchmark's whole
evaluation set, with no study material in the pool at all. Requiring a filled `train` side
there would make the most comparable run on the platform impossible to express. A
*declaration*, by contrast, claims the benchmark has both a study side and an exam side, so
an empty side in `SPLIT` is a bug in the adapter and is refused as one — absent is not
`None`, and `None` is not an empty list.

**The eval side is not withheld, it is absent** — and that replaced a mechanism rather
than tightening one. What used to be written here described two withholdings: the eval
traces, then the eval per-task breakdown. Both were for the shape §2.6 removed, in which
one curve point carried *both* sides and a method read a point whose `per_task` was the
exam. Stripping it was load-bearing then. It also had a cost that was never priced: the
same strip removed the **train** side's per-task breakdown, because `train_per_task` is
`{}` by design on a single-side run. Measured on two real train runs,
`protocol.studied_per_task()` returned `{}` on every round, so the attribution §2.3
promises — AHE's transition diff, `editor.failing_first` — had no data at all.

A run covers one side now, so the rule is structural instead:

* **A method only ever sees a train point.** `_stage_for_method` is called from the two
  train loops, and `_run_eval_side` has no method in it at all. `_for_method` *asserts*
  this rather than assuming it: staging an exam point raises, so the day somebody
  stages one, it fails loudly instead of handing over the exam.
* **Therefore nothing is withheld from a train point.** `WITHHELD` no longer exists.

What a train point carries, and why each:

| what | why |
| --- | --- |
| the aggregate `score` and `score_ci95` | the reward signal. Withholding it would not make the comparison fairer, it would make every method a random walk — no method could tell whether an edit helped. |
| `per_task`, and `n_scored` / `n_passed` / `n_invalid` | the diagnostic breakdown, which §2.3's table promises. "Which of the tasks I am scored on are failing" is a mechanistic question about the side the method studies, and attribution is the answer to it. |
| the traces | what the harness did — and the command **output**, not only the exit code (`loop` writes `output`; a renderer that looked for `stdout`/`stderr` dropped 441 of 441 bytes on a measured trace). |
| `tasks/<task_id>.json` | the instruction, the verifier **and its gold** (an `answer` verifier's `expected`, a `command` verifier's resolved check program), the per-task verdict, and a note when the initial state is the image. |

**The gold is given to the method deliberately.** A method that fits the tasks it studies
has fitted those tasks, and an eval run is what exposes that. The distinction that matters
is not *who knows the train answers* but **what the harness can reach at run time**,
because the same harness runs the exam: a harness that can read the verifier can read it
on the eval side too. That is the boundary §2.5.9 holds — the check's inputs are copied in
*after* the harness has stopped, into a fresh container — and it is why the expected
answers may live in the **channel** while never being written into a **trace**
(`eval/run/runner.py` writes no `scorable` into a trace, so no harness can read its own grade).

**The channel is not the record.** `curve.jsonl`, the console's curve and per-task detail,
and an exported harness's `PROVENANCE.md` keep everything. The channel is the copy a method
receives, and it is the only copy that can change what the method does next.

### 2.4 What a task is: an environment and a verifier

A dataset module exposes three mappings, all keyed by `task_id`:

```python
TASKS  = [{"task_id": "t01", "goal": "..."}]          # handed to the harness, verbatim
SETUP  = {"t01": {"files": [{"path": "broken.py", "content": "..."}]}}
VERIFY = {"t01": {"kind": "command", "argv": ["pytest", "-q"],
                  "inputs": [{"path": "test_broken.py", "content": "..."}],
                  "pass_when": "exit0"}}
```

**Three mappings and not one, because the three things have different audiences:**

| mapping | who sees it | why it is separate |
| --- | --- | --- |
| `TASKS` | **the harness** (written to `task.json`) | it is the task's description and inputs. Nothing secret may live here, and the registry rejects a task dict carrying `setup` or `verify` |
| `SETUP` | the platform, before the run | it materialises the task's initial state; the harness sees the *result*, not the mapping |
| `VERIFY` | the platform, after the run | it is the grade, and a method that can read it can fit itself to it |

`SCORABLE` is not replaced, it is **the `answer` verifier written the short way**.
A dataset that declares only `TASKS` + `SCORABLE` gets
`VERIFY = {tid: {"kind": "answer", "expected": SCORABLE[tid]}}` and behaves exactly
as it did before this section existed. That equivalence is pinned by a test, not
asserted here.

#### Verifier kinds

| kind | fields | scores 1 when |
| --- | --- | --- |
| `answer` | `expected` | `<workdir>/answer.txt`, stripped and lowercased, equals `expected` |
| `command` | `argv`, `cwd?`, `inputs?`, `pass_when?`, `stdout?`, `timeout_s?`, `reward_file?`, `evidence_file?`, `image?` | `pass_when` holds, or the check writes a numeric `reward_file`; `evidence_file` is copied into the verdict, and a pinned `image` can provide verifier-only dependencies |

A `command` verifier runs **after** the harness exits, in its own sandbox
invocation, with `--workdir` mounted read-only, the platform still hidden, and
`/tmp` writable (a test runner needs scratch space). `inputs` are written into the
workdir immediately before it runs, so a harness that edited them edited a copy
that no longer exists.

#### Setup kinds

`SETUP[task_id]` may declare `files` and an optional public `runtime_prompt`:

```python
{"files": [...], "runtime_prompt": "Use ./client init, then ./client action ..."}
```

`runtime_prompt` must be a nonempty string. The runner appends it to the actor's
`goal` under `Runtime tool instructions:` on both mount and container-state paths.
It describes the adapter's tools, discovery and invocation syntax for any harness
that reads `goal`; it does not select a method, alter the harness's reply format,
or contain reference answers or verifier internals. The source task is unchanged,
and no other setup metadata is copied into `task.json`. Without this field, the
actor receives the original task.

`files` entries describe the initial filesystem:

An entry in `setup.files` or `verify.inputs` may carry `"immutable": true` to
declare an immutable runtime projection, rather than task state. Its `from` must
be an absolute path to an existing regular directory without symlink path
components. The exact declared directory is bound read-only at `<task_dir>/<path>`
(or `<container_workdir>/<path>` for Docker). It is never copied into the host
workdir, staging or a cache; there is no per-task runtime duplication. Sandbox
engines may create empty mount points, which contain no runtime files on the host.
The agent-visible paths and contents are the same as for a directory copy.

Pre-verification re-materialization skips immutable projections: anti-tamper is
stronger because the harness cannot modify the tree at all, rather than being
allowed to modify it and having those edits reverted. Entries without the flag
retain the existing per-task copy and pre-verification restoration behavior.
Verifier-only projections remain unavailable to the harness. Invalid sources or
a boundary that cannot express the read-only bind make the task invalid, with no
writable-bind or copy fallback. Container-state and pristine verification routes
currently refuse this opt-in instead of staging runtimes. The sealed native
verifier uses the same read-only directory projection at `/task/<path>`.

```python
{"path": "relative/path", "content": "literal text"}     # inline
{"path": "relative/path", "from": "/abs/or/rel/source"}  # copied; a directory is copied whole
```

A relative `from` resolves against the dataset module's own directory, so a
dataset can ship fixture trees beside itself. Paths that escape the workdir are
refused rather than clamped — the same rule method edits follow (§4.7).

#### What has not changed

The harness's argv, its `task.json`, its read-only tree, its `.state/` scratch
directory, and the sandbox that hides the platform are all exactly as before. A
dataset with no `SETUP` and no `VERIFY` runs through the identical code path, and
`eval/run/runner.py` short-circuits to the old string comparison.

#### What this deliberately leaves out

* **Container environments.** A task that needs a specific image is not
  expressible yet. That is the next environment kind, and it is opt-in because it
  changes the host's requirements, not just the dataset's.
* **Interactive environments** (a simulated user, a service the task must talk to).
  These need an environment-side model, and therefore a way to record *that* model
  and a seed on every curve point, or two runs are not comparable. Deferred
  deliberately rather than half-built.


### 2.5 Environments: `files` and `exec` (containers)

An **environment** is where a task's work happens and how the harness reaches it. It
is the thing §1.2's `--workdir` names, generalized.

#### 2.5.1 What `files` cannot express

`files` is a directory on the host, and the harness runs beside it. That is enough
whenever the host's own toolchain is the task's toolchain — and it is not enough the
moment a task needs a specific image, a pinned OS, a package the host does not have, or
a service. Those are not edge cases: they are most of the benchmarks anyone would want
to compare against.

#### 2.5.2 The two kinds

A dataset declares an environment in a module-level `ENV` keyed by task id, with an
optional `DEFAULT_ENV` — the same shape as `SETUP` and `VERIFY` (§2.4), and for the same
reason: `TASKS` is written to `task.json` and handed to the harness verbatim, so an
environment that lived in a task dict would be read by the harness as though it were
the one that ran, while the platform used the other one. `registry.py` refuses `env`
inside a task for that reason.

```python
ENV = {"e01": {"kind": "exec", "image": "python:3.11-slim", "workdir": "/app",
               "network": "none", "placement": "inside"}}
```

| kind | the environment is | the harness runs | the verifier runs | unlocks |
| --- | --- | --- | --- | --- |
| `files` (default) | a directory | in the sandbox, beside it | in the sandbox, artifacts read-only | everything the platform did before `exec` existed |
| `exec` | a container from `image` | **inside it** (§2.5.3) | in a **fresh container from the same image** (§2.5.3) | Terminal-Bench-shaped tasks, anything needing its own toolchain |
| `exec` + `services` | a container, plus sidecars on an `--internal` network (§2.5.7) | inside it, with the services reachable by name and the internet not | in a fresh container, **services still up** | a mock API, a database, a browser, a simulated user |
| `exec` + `web` | a container on an `--internal` network plus a host-side web gateway | `HG_SVC_WEB_URL` only when declared; the task has no general internet route | the gateway's JSONL recording in the run directory | recorded-provider web search and fetch |

`workdir` is where the task's files are, *inside* the environment, and defaults to
`/app`. `network` is `none` (default), `services` for an internal service or web gateway, or
`bridge` when the task explicitly needs the internet. `placement` is `inside` (default) or
`beside`. All of them are recorded (§2.5.4).

#### 2.5.3 The harness runs inside an `exec` environment

This is the load-bearing decision, and the alternative is worth stating because it is
the obvious one: run the harness on the host, as today, and let it reach the container
through a helper.

**Rejected, for one reason: it does not work for the harnesses we most want to host.**
An agent CLI's value is its tools — it edits files and runs shell commands. Run it
beside the container and its `bash` runs on the host, its edits land on the host, and
the task it is supposedly doing does not happen. A helper (`HG_ENV_EXEC`) only helps a
harness *written against* that helper, which is us, not Codex.

Running it inside buys three things:

* **Paths are honest.** The harness sees `/app` because that is where the files are.
  No translation, which is the property `eval/containers/sandbox.py` already refuses to give up.
* **A CLI's tools are the task's tools.** `bash`, the file editor and the interpreter
  are the image's.
* **`install` finally has a meaning** (§2.5.6): dependencies go into the environment.

**And it costs one thing that must be stated plainly: the agent's credentials enter the
image.** The key stops being a host secret and becomes something a task image can read
and exfiltrate. That is a real change in exposure, not a detail.

So placement is **declared, not assumed**:

* `placement: "inside"` (default) — what a CLI harness needs.
* `placement: "beside"` — the harness stays on the host with the platform's sandbox
  around it, and the environment is reached through a command helper. Credentials stay
  on the host; a CLI harness will not work this way, and the record says which was used.

**`inside` is the default even though it is the one that exposes credentials, and that
is a decision rather than an oversight.** `beside` reads like the safe choice, but for
the harnesses this platform exists to host it is not a choice at all: it is the mode in
which their tools do not work (§2.5.3). A default that silently produces non-runs is a
worse failure than one that is explicit about its cost, so the cost is stated where the
declaration is read and `placement` is recorded on every curve point (§2.5.4) rather
than left to a reader's assumption.

**The check runs in a fresh container from the same image, not in the harness's.**
`docker exec` cannot be made to satisfy §2.5.6's guarantee: anything the harness
backgrounded is reparented to the container's PID 1 and survives the exec's exit, so
"stop the harness's process tree" is best-effort there. Starting the check in a new
container makes it structural instead. What the two containers share is the task bind
mount and nothing else — which is exactly the set of things a check is supposed to
grade. It also settles what the harness may *not* change about its own environment: a
package installed into the container's writable layer is gone by the time the check
runs, because the environment is the image.

A harness whose manifest declares only `env_kinds: ["files"]` is **refused** for an
`exec` task (§1.1). The platform does not attempt it and report a zero.

#### 2.5.4 What must be recorded, or the curve lies

Every one of these varies between runs and none of them is inferable from the score:

| field | why |
| --- | --- |
| `env.kind` | a `files` curve and an `exec` curve are different quantities |
| `env.image_digest` | **the digest, not the tag.** `:latest` moves, and two runs under one tag are not comparable — the same failure this platform already shipped once for `agent_model` |
| `env.network` | a task with network is a different task from one without |
| `env.placement` | it decides whether credentials entered the image |
| `env.services` | a task with a mock API is a different task from one without, and two runs with different service images or digests are not comparable. Recorded as a sorted list of `{name, image_digest}`; `[]` when there are none, so "no services" is a fact rather than an absent key |
| `env.model_gateway` | whether the platform had to publish the agent's model onto the task's network, and to where (§2.5.7). `null` when it did not |
| `env.model`, `env.seed` | the environment's own model and seed (§2.5.7), non-null exactly when a service declares `needs_model` / `needs_seed`. Recorded as `null` otherwise — a fact about the run, not a missing field |

A run that cannot resolve a digest **refuses to start**. Recording `"image": "foo:latest"`
and calling it comparable is exactly the class of claim this platform exists to stop.

#### 2.5.5 What the platform will not do

* **It will not build a task's image during a run.** The dataset names an image that
  exists, or names a Dockerfile *and* the run opts into building it -- but the build is
  an **import step, not a run step**: it happens once, at the moment a human adds the
  dataset, and its output is pinned as a digest. From then on the dataset references
  that digest and the run never builds anything.

  Stated this way because the alternative is a supply-chain event per run. A dataset is
  content a method may influence; `RUN curl ... | bash` executes with the daemon's
  privileges, before any sandbox exists, and a build that happens inside a run is a
  build whose consent nobody gave. Moving it to import time does not make the build
  safe -- it makes it **rare, attributable and one-time**, which is what "opt-in"
  should have meant.

  A dataset that names a Dockerfile and no digest is therefore not yet runnable: the
  platform refuses it and says to import it first. This is the same rule as §2.5.4 --
  no digest, no start -- applied one level up. `tools/import_env.py` is the only tool in
  the repository that pulls or builds, it is not imported by the platform, and its
  output is the pinned reference a dataset names.
* **It will not run a container without a sandbox decision.** Docker is a different
  boundary from bwrap and the two compose in ways that have to be reasoned about per
  placement, not assumed. `--no-sandbox` and `placement: "beside"` are separate
  switches because they protect different things.
* **It will not let the verifier be edited.** Inputs are re-materialized after the
  harness's process tree is stopped (§2.5.6), in the container where the check runs.

#### 2.5.6 Two rules that generalize the `files` case

Both are stated for `exec` but apply to every environment, and the second one is a
correction to what §2.2 already says:

1. **Re-materialize the verifier's inputs after the harness has stopped.** The harness
   is not running during verification, so the risk is not concurrency — it is what the
   harness *left*. A harness may also leave a background process behind, which is why
   the platform stops the harness's process tree before restoring the inputs and
   running the check. Stated in this order because the order is the guarantee.
2. **`install` runs — once, at import time, into the environment.** The field was
   declared and inert for as long as the harness tree has been read-only by design,
   which is the fact that decides it: **an install has nowhere to write during a run.**
   `tools/import_env.py --harness <dir>` is where it goes — the per-harness half of the
   same one-time build as a task's Dockerfile:

   ```dockerfile
   FROM <the task's image>
   COPY . /opt/harnessgrad-install/
   RUN python -m pip install --no-cache-dir -r /opt/harnessgrad-install/requirements.txt
   ```

   `install` names a `requirements.txt` (pip) or a `.sh` (shell), and anything else is
   **refused rather than guessed at**, because a build step is arbitrary code and a tool
   that invents a command for an unrecognised file runs something nobody wrote.

   Two rejected candidates are recorded so they are not re-proposed:
   * *a per-run writable layer* — real engineering that buys nothing until an
     environment exists to put it in, and it would make "the harness's dependencies" a
     thing that varies per run without appearing in any curve point.
   * *drop the field* — cheapest, but it gives up "a harness may have dependencies",
     which `exec` wants immediately.

   For `files` the field remains inert and that is not a gap: **there is no environment
   to install into.** Installing into the host is refused for four separate reasons,
   any one of which would be enough — the host's interpreter is not ours to mutate, two
   harnesses with conflicting pins would both be broken, the mutation would not appear
   in any curve point, and it is the §2.5.5 supply-chain event under a different name.
   So `install` is meaningful exactly when the environment can hold it, and the record
   says which environment ran (§2.5.4).

#### 2.5.7 Service environments: something runs while the harness runs

A **service** is a container that runs alongside the task and is reachable only from inside it:
a mock API, a database, a browser, a simulated user.

**A service is a property of an `exec` environment, not a third kind.** Any harness that can run
in a container can speak HTTP, so there is nothing for a harness to declare it cannot do and
`env_kinds` gains no value. A `files` task has no boundary to put a database behind and no
network namespace to keep it off the internet, so services under `kind: "files"` are **refused**
rather than half-supported.

##### There is no interactive execution model, and that was checked rather than assumed

The first version of this section said services "change `run_one` from start-and-wait into
something that serves requests". That was wrong, and it was wrong in the direction that would
have cost the most: a second execution model. Two published benchmarks settle it:

* **τ²-bench** is the most agent/user-symmetric environment published — the simulated user has
  its own tools and changes shared state — and it is still explicitly *"only one player acts
  per turn"* ([τ²-Bench](https://ar5iv.labs.arxiv.org/html/2506.07982)).
* **JarvisBench** genuinely interrupts a working agent, and does it *"at an action boundary"*,
  integrating *"through the existing interaction boundary, without modifying the worker loop"*
  ([JarvisBench](https://ar5iv.labs.arxiv.org/html/2608.14870)).

So even interruption is a call the harness makes **between actions**, which is a service call.
`run_one` stays start-and-wait, the entrypoint contract stays `--task` and `--workdir` (§1.2),
and §1.1.1's "a harness is a command, not a language" is untouched.

The consequence for what "interactive" asks of a *harness* is real but belongs to the harness's
own manifest, not to this contract: a harness that ignores the guidance a service returns simply
does not get the benefit, which is the honest measurement.

One design was proposed and dropped on this evidence. Giving a service a shared **directory** to
push messages into buys nothing over an endpoint, because the harness still has to look; it would
add a mount and an undefined polling convention for no capability. An earlier draft of this
document suggested it, and it is recorded here so it is not re-proposed.

##### The declaration

```python
ENV = {"e05": {
    "kind": "exec", "image": "repo/task@sha256:…", "workdir": "/app",
    "network": "services",
    "services": [
        {"name": "api", "image": "repo/mock-api@sha256:…", "port": 8080,
         "health": {"argv": ["curl", "-sf", "http://localhost:8080/health"],
                    "timeout_s": 30},
         "record": True, "needs_seed": True, "needs_model": False},
    ],
}}
```

`name` addresses the service and files its recording; `port` is what it listens on; `health`
is a `command`-shaped check run **inside the service container**. Optional service
`command` is a non-empty argv list replacing the image's Docker CMD; optional
`entrypoint` is a non-empty executable string replacing its Docker ENTRYPOINT.
These use Docker exec semantics, without shell parsing. Omitted fields preserve the
image defaults; an entrypoint override alone follows Docker's rule of clearing CMD.
Launch overrides are carried through normalization and recorded in `env.services`.
Optional service `cpus` (>= 1) and `memory_mb` (>= 64) opt into a separate
per-container resource envelope. Declaring either uses the container defaults for
the other (2 CPUs / 4096 MiB) and fixes the PID cap at 1024; the effective envelope
is recorded as `env.services[].limits`. Without either field, the platform passes
no service CPU/memory/PID flags. Task environment limits do not cover sidecars.
`record`, `needs_seed` and `needs_model` are declarations, and each one has a consequence
below. Every service image is
pinned to a digest by the same rule as the task image (§2.5.4) and for the same reason.

##### Lifecycle, and the order is part of it

| # | step | why in this position |
| --- | --- | --- |
| 1 | create a **per-task** internal network | per task, not per run: task *N*'s service must not be reachable from task *N+1* |
| 2 | start the service containers, recording volume mounted `rw` | they must exist before anything can check them |
| 3 | **health check — and refuse the task if it fails** | a harness that starts before its service is ready scores by race |
| 4 | start the harness container on that network | it needs the addresses from step 2 |
| 5 | stop the harness container | §2.5.6: nothing it started may be running when the check runs |
| 6 | run the check, in a fresh container, **services still up** | §2.2 already allows a check that needs a service in a particular state |
| 7 | read the recordings | after the check, so the check may also read them |
| 8 | tear down services and network, in `finally` | must hold on timeout and crash too |

Four of those had to be pinned down further during implementation, because the table
does not imply the choice and each one is visible in the record:

* **`health.timeout_s` is a total budget, not a per-attempt timeout.** The check is
  re-run every half second until it passes or the budget is spent. "Wait until ready"
  is the useful semantic; one attempt would make the field mean "how long a single try
  may take", which is not the question anyone has.
* **A service is reachable by the name it was declared with**, delivered as a network
  alias, because the container's own docker name must be unique on the daemon and is
  therefore mangled. This is an implementation detail until it is missed, and then it
  is user-visible: the harness gets `Name or service not known` for a service that is
  running and healthy, so every task that touches the service fails for a reason
  nothing records while every task that only touches the filesystem passes. Measured.
* **Recordings live in the run directory**, beside the curve and the round diffs. They
  are evidence the run produced, and the place evidence goes is the record. An earlier
  version put them under the work root, where nothing owned them and nothing removed
  them.
* **The health check's own traffic must not enter the record.** It is infrastructure,
  not interaction. A check that grades "did the harness call the service" by counting
  requests would be satisfied by the platform's own probe — a check that cannot fail,
  and it did not: `s01` in `data/verify_service.py` passed against a harness that never
  made a call, which is how this was found. A service must exclude its health endpoint
  from whatever it records.

##### The recording: the first input the harness cannot read

§2.2's third invariant says the check runs "against inputs the harness cannot modify". Every
input before this one was *static* — the dataset wrote it, and re-materializing it defeated
editing. A recording is produced **while the harness runs**, so there is nothing to re-materialize
and no way to hand the harness a copy. The rule is therefore a mount rule, and it is stricter
than the one it extends:

```
recording  →  service container: rw      verifier container: ro      harness container: not mounted
```

**Not mounted into the harness at all** — not even read-only. This is not a new policy, it is the
existing invariant reaching its conclusion; the harness cannot modify a recording because it
cannot see one. Paths are fixed by the platform, never by the dataset, because the harness must
not be able to name a service into a directory it can write:

```
/recordings/<service-name>/        # inside the service and the verifier
```

`/recordings/<name>/harnessgrad.json` is a **reserved filename** inside each recording, and the
only strong convention this contract puts on a service author:

```json
{"usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0}, "done": true}
```

The platform reads it; everything else in the directory belongs to the dataset's check.

##### Health-check failure is a **dataset** failure, and is not a zero

A service that never becomes healthy, or that dies mid-task, produces a result the platform
records as **invalid for the dataset**: not a `per_task` score, not a zero, and attributed to the
dataset and service by name. The reason is the same as for every other refusal in this document —
the alternative is a curve where a broken mock API and a weak harness are the same number — and
it is now independently attested: JarvisBench reports *"harness or provider failures are marked
invalid rather than assigned a score of zero"*.

The platform's obligation is to provide a workable interface and to say clearly when it was not
met. Adapting to it is the dataset provider's.

**A subset of invalid tasks is recorded; a whole task set of them is refused.** An invalid task is
absent from `per_task` and appears in `invalid` with its stage (`start`, `health` or `died`) and
the service name, and the point carries `n_invalid` beside `n_scored`. But when *no* task produced
a scoreable result there is no measurement to record: `mean([])` is `0.0`, and writing that would
say "the harness scored zero" about a run in which the harness never ran. The run refuses and names
the dataset-side cause. This is the one place where `invalid` and a zero could have been confused
by the arithmetic rather than by a reader, which is why it is stated rather than left to `mean`.

An invalid task is also **not cached**. The evaluation cache is keyed by `(harness_sha, task_id)`,
and the reason an invalid task failed is not the harness — so caching it would carry a dataset bug
into every later round and hide the round that introduced it.

##### Network: `services` is a third value, and a contradiction is refused

`none` (default) → no network. `services` → an **`--internal` network shared by the services, the
harness and the check**, so the services are reachable by name and the internet is not.
`bridge` → the internet as well. Measured on this host: on an `--internal` network a peer is
reachable by name, `1.1.1.1` is unreachable and DNS fails; under `network: none` the service is
unreachable.

An exec task may declare `web: {"search": true, "fetch": true}`. The host starts the gateway on
the task network and exports `HG_SVC_WEB_URL`; an undeclared task receives no such variable. The
gateway records every search and fetch beside `curve.jsonl`, with timestamp, provider, status and
bytes. Its `/health` request is infrastructure and is excluded. `run_meta.json` records the
selected search and fetch providers and the SearXNG endpoint when applicable.

Declaring `services` with `network: "none"` is **refused as a contradiction** rather than
silently upgraded.

##### Two budgets, two prefixes, and the harness gets neither secret

An environment-side model is a **third** model budget, after the harness's and the method's, and
it needs the same separation the other two already have (`harness_env()` strips `HG_METHOD_*`,
`eval/containers/container.py` forwards an allowlist). So there are two prefixes with **two different
audiences**, and the split is the point:

| prefix | who receives it | what it carries |
| --- | --- | --- |
| `HG_SVC_<NAME>_URL` | the **harness** | the address of a service it may call — `http://api:8080` |
| `HG_SVC_WEB_URL` | the **harness** | the host-side web gateway, only when `web` is declared |
| `HG_ENV_*` | the **services** only | the environment's own model, credential and seed |

The harness receives no `HG_ENV_*` and the services receive no `HG_AGENT_*`. Two names rather than
one prefix with two audiences, because a single prefix would make "which of these may the harness
see" a question a reader has to answer by knowing which keys exist — which is how a credential
leaks.

##### The model has to be published onto the task's network

A containerised harness **cannot reach a model listening on the host's loopback**, and
this was measured before it was designed around, because every obvious route fails:

```
container -> 127.0.0.1:8001          the container's own loopback
container -> 172.17.0.1:8001         the bridge gateway; the server binds loopback only
container -> host.docker.internal    not defined on Linux by default
internal  -> <its own gateway>:8001  same, nothing listening there
```

**`--network bridge` is the wrong answer**, and it is the answer that suggests itself: it
would give the task the internet, and "a task with network is a different task from one
without" (§2.5.4) — an `exec` task would quietly become a networked task because of how
the platform delivered its model.

**The platform publishes the endpoint on the network the task already has.** A host
process bound to an `--internal` network's gateway address is reachable from inside that
network and from nowhere else — measured both halves. So the platform runs a host-side
forwarder there and hands the harness its address instead of the loopback one. That keeps
three things true at once: the harness gets a model (without which there is nothing to
measure), the task keeps the network it declared, and `env.model_gateway` records that
this happened.

| situation | what the platform does |
| --- | --- |
| `files`, network omitted | the native sandbox inherits the host network and reaches the model directly |
| `files`, `network: none`, live backend | keeps `--unshare-net`; binds one read-only AF_UNIX model socket and a trusted loopback forwarder, hands the client an ordinary HTTP URL; native plan 4 |
| `exec`, model on this host | creates the internal network if the task has none, publishes the endpoint, rewrites `HG_AGENT_BASE_URL` for the container |
| `exec`, model **not** on this host, `network: bridge` | nothing: the harness reaches it over the internet, which the task asked for |
| `exec`, model **not** on this host, any other network | **refused** — the harness would have no model at all, so every score would be a zero that means nothing, and it would look like a weak harness |
| `mock` backend | nothing: nothing is called |

The native offline route forwards only to the configured platform model endpoint;
it does not give the task a bridge, DNS or external TCP route. It requires a plain
HTTP base URL (including its path); TLS, URL userinfo, queries and fragments fail
closed. The harness client, prompts and budgets are unchanged. The socket and
forwarder are infrastructure mounts, never setup files or task capability metadata,
and are not projected into verification. Native `bridge` and `services` remain
unsupported and refused; omitted native network still means inherited host network.
The backend-observation admission rule is: **refuse a declared policy when, and only
when, this process is not the thing that applies it.** The gate's ownership table is:

| declaration | native `files` route | `exec` / container-state route | backend-observation admission |
| --- | --- | --- | --- |
| `env.agent_timeout_s` | runner applies `phase_timeout` and subprocess deadline | same platform deadline on the agent invocation | no trigger |
| `env.verify_timeout_s`, `verifier.timeout_s` | runner/verifier apply `phase_timeout` and subprocess deadline | same platform deadline on the verifier invocation | no trigger |
| `env.network` | omitted: inherits host network; `none`: platform builds `--unshare-net`; other values fail runtime validation | daemon owns network namespace | triggers only if declared and `kind == "exec"` or `state == "container"` |
| `env.cpus`, `env.memory_mb` | no declaration-driven CPU/memory enforcement | daemon applies resource limits | trigger when declared |
| `env.resources`, `env.resource_policy` | no implementation of these declarations | no trusted backend observation wired | trigger when declared |
| `env.filesystems` | fixed platform mounts do not implement this declaration or its quotas | no trusted backend observation wired | trigger when declared |
| `env.network_policy` | native `network` support does not implement this separate declaration | no trusted backend observation wired | trigger when declared |
| `env.artifact_policy`, `verifier.artifact_policy` | fixed artifact mounts do not implement these declarations or their quotas | no trusted backend observation wired | trigger when declared |
| `env.exec_defaults` (implicit for every `kind == "exec"`) | not requested | daemon applies default CPU/memory/PID bounds | always triggers for `exec` |
| `env.state=container` | no native container-state implementation | daemon owns container state and snapshots | triggers for container state |

This gate runs before storage/backend dispatch. Native isolation still requires
`sandbox=True`; admission here does not bypass runtime validation. A bound operator
authorization permits only an explicitly unmeasured diagnostic attempt and is never
backend observation or production authorization. Declarations without a native
implementation stay refused even for direct callers that bypass registry validation.

`env.model_gateway` is `{"published": true, "target": "<host>:<port>"}`, or `null`. It is
recorded because "the model was reachable from inside the container" is a fact about the
measurement, not a detail — the same argument as `env.image_digest`. The ephemeral port
is deliberately *not* recorded: it changes per task and would make every point look
different for no reason.

##### The harness's `install` and the dataset's image have to meet somewhere

`install` (§2.5.6) bakes a harness's dependencies into an image at import time, and this
is now load-bearing rather than theoretical: the platform's own base harness needs the
`openai` package, a stock `python` image does not have it, and the harness tree is
read-only during a run so it cannot install anything itself. Measured — the containerised
harness died with `ModuleNotFoundError: No module named 'openai'` and scored zero on
every task, which read exactly like a weak harness.

So a dataset's image must contain whatever harness will run in it. The tool exists for
that (`tools/import_env.py --image <base> --harness <dir>`), but **there is no mechanism
yet to reconcile "the dataset names an image" with "the harness declares dependencies"**,
and this is recorded as an open design point rather than papered over: today the
reconciliation is a human running the importer, and the demo datasets name an image built
that way. Anything more general — deriving a per-`(image, harness)` environment at import
time, with the digest recorded — is a change to this section, not a detail of it.

##### Seed and cost: the two things §2.5.7 was originally deferred for

* **`env.seed`** — services with randomness need a seed or two runs are not two measurements of
  one thing. The platform derives a per-task seed from a run-level seed and passes it as
  `HG_ENV_SEED`; the run-level seed goes on every curve point.
* **`env.model`** — the model the environment uses. A curve point can now carry **three**
  distinct models: `agent_model` (the harness's), `method_model` (the method's) and `env.model`
  (the environment's). They are never interchangeable and averaging across a change in any of
  them is invalid.
* **`cost.env_generation_tokens`** — the environment's spend, **never** folded into
  `harness_tokens`. Mixing them does not weaken a cost-aware acceptance rule like RRSI's; it
  feeds it a number its formula is not about. τ²-bench reports the two sides separately
  (agent $0.086 / user simulator $0.059 per task), which is the same distinction.

`needs_seed` and `needs_model` on a service are what make these non-null: an environment that
uses neither records `null` for both, which is a fact about the run rather than a missing field.

#### 2.5.8 Services: what the platform will not do, and what is still not designed

* **It will not run a service as a host process.** A service is a container from a pinned digest,
  for the reason §2.5.3 gives about the harness: a host process's code is on the host's disk, and
  a harness that can read or kill the thing grading its interaction is being graded by something
  it can influence. Services also get **no docker socket**, so a service cannot start a sibling
  with mounts the platform did not choose.
* **It will not build or pull a service image during a run.** Same rule as §2.5.5, same tool
  (`tools/import_env.py`).
* **It will not let a dataset name the recording paths.** See above: a dataset-chosen path is a
  path chosen by something a method may influence.
* **Still not designed:** GPU, mounted devices, and multi-container *tasks* (as opposed to
  sidecar services). Each is a host-requirement change and each needs its own opt-in switch.
* **Still not designed, and now with a reason to expect it stays that way:** an environment that
  must preempt a harness *mid-action* rather than at an action boundary. No published benchmark
  found requires it (see §2.5.7); if one appears, it is a change to the harness contract, not to
  this one.

#### 2.5.9 Task state: a mount, or the container itself

Everything up to here assumes the task's files are a **host directory bind-mounted** into
the container. That covers tasks whose work is files, and it is what makes verification
easy: the platform can read what the harness produced and can freeze it for the check.

Terminal-Bench is not that shape. Its tasks set `WORKDIR /app` in their own image and the
harness works **in the container's filesystem** — measured on the real task set: the image
ships `/app` empty, the instruction says to write `/app/ars.R`, and the check reads `/app`.
A bind mount would put the task somewhere the task itself did not put it.

So the environment declares where its state lives:

```python
ENV = {"t01": {"kind": "exec", "image": "...", "workdir": "/app",
               "state": "container",           # default: "mount"
               "network": "bridge",
               "cpus": 1, "memory_mb": 2048,
               "agent_timeout_s": 900, "verify_timeout_s": 900,
               "services": []}}
```

| `state` | the task's files are | the harness runs | the check grades |
| --- | --- | --- | --- |
| `mount` (default) | a host directory bound at `workdir` | beside them, in a container | the bind-mounted tree, frozen |
| `container` | the container's own filesystem | in it, at the image's `WORKDIR` | a **snapshot** of it |

##### `container` state: commit, then verify against a fresh container

```
1   start the task container from the pinned image; no workdir bind mount
2   copy SETUP in                    (docker cp — most container-state tasks ship their
                                     environment in the image and need none)
3   run the harness, --workdir = the container's workdir
4   the harness stops  ->  docker commit  ->  a snapshot image
5   start a FRESH container from the snapshot
6   copy VERIFY's inputs to their container paths   <- the harness never saw them
7   create the directory holding reward_file        <- nor this
8   run the check
9   read the score from reward_file
10  remove the snapshot image and the task container
```

**This preserves all three invariants of §2.2, and it is worth saying how, because "commit
the whole filesystem" sounds like the opposite of re-materializing an input:**

* The check's **inputs** are the tests, and they are copied in at step 6, *after* the harness
  has stopped. They were never in the image and never in the container the harness ran in.
  Measured: the real Terminal-Bench images do not contain `/tests` at all — the benchmark's
  own design already satisfies this, and the platform does the copying.
* The task **state** — `/app`, and anything else the harness touched — is meant to be
  harness-controlled; that is what is being graded. The snapshot is how it travels.
* The snapshot is an **image**, so it is immutable. The check runs on a writable *copy* of
  it, and whatever the check writes dies with that container. A check cannot alter the
  thing it is grading.
* The fresh container is **stricter than Terminal-Bench's own runner**, which runs the tests
  in the same container the agent was in: anything the agent backgrounded is reparented to
  PID 1 and is still there. Starting from a snapshot makes §2.5.6's guarantee structural
  here too rather than best-effort.

`docker commit` rather than copying `/app` out, because the check may read outside it —
measured: the real task set references `/tmp/frame.bmp` in six places, so a handful of tasks
put their output somewhere `/app` alone would lose.

##### Whose interpreter runs the harness

The harness is invoked with **the image's interpreter when the image has one**, and with
the platform's own otherwise. Both halves are measured, and both are needed:

* §2.5.3 says the reason to run the harness inside the environment is that "`bash`, the
  file editor and **the interpreter** are the image's". That is preserved wherever it can
  be: an image with its own Python keeps it, and keeps its own installed packages.
* But a task image need not have one — the real Terminal-Bench image is `ubuntu:24.04`
  plus a `COPY`, with no `python3`, no `R` and no `gcc`; the task expects the agent to
  `apt-get install` what it needs. So assuming the image provides an interpreter assumes
  something the benchmark does not promise.

When the platform's is used, `sys.prefix` **and** `sys.base_prefix` are both mounted
read-only, and that is measured too: a virtualenv's `bin/python3` resolves to its base
prefix, so mounting the venv alone gives `not found` inside the container. Together they
give a working interpreter and the packages the platform installed for its harnesses.

Mounting it only when it will be used, rather than always, keeps an image that brings its
own from also getting the host's Python on its disk.

##### The harness's own dependencies: an overlay, not a derived image (§2.5.6 rule 2)

§2.5.6 puts a harness's `install` into the environment at import time. Doing that as a
**derived image per task image** is `O(#task images)` per harness — 89 of them for
Terminal-Bench — so anyone arriving with their own harness pays that before their first
run, which is the opposite of the exchange unit §1 asks for.

So `install` may instead be materialized as an **overlay**: a directory outside every
image, holding the harness's declared dependencies resolved for a specific Python version,
mounted read-only into the harness's container (or bwrap namespace) with `PYTHONPATH`
pointing at it. The **image's interpreter still runs the harness** — this is an addition to
`sys.path`, not a replacement of the runtime, so the section above is unchanged.

| | derived image | overlay |
| --- | --- | --- |
| cost per new harness | `O(#task images)` = 89 builds, ~3.7 GB | `O(#Python versions)` = 5 builds, ~112 MB |
| the image | modified (a new digest, ours, not upstream's) | untouched; upstream digests stay upstream |
| reproducibility | needs the derivation recorded | needs the install hash + version + resolved package list recorded |

The key is **`(sha256 of the install file, Python minor version)`** and it is not optional.
Measured: a `3.13` overlay imported cleanly on four different 3.13 task images and failed on
four 3.12 ones with `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'` —
these are compiled extensions and the ABI is per-version, so a shared overlay would be
silent corruption. Measured cost, one build: ~12 s and ~42 MB, using **the task image's own
`pip`** so the wheels match its interpreter and no base image has to be pullable.
`tools/configure_harness.py` builds and verifies (by importing every distribution's
`RECORD`-listed modules — `top_level.txt` is written by only 3 of the 14 distributions in a
real `openai` install, and the ones it omits are the compiled extensions), and refuses to
write an overlay whose interpreter does not report the version it was asked for.

Three things follow, and each is on the record:

* **Every curve point carries `env.harness_runtime`**, a list because the version is a
  property of each task's image and one run may cross several. It reads as exactly one of
  three shapes: `overlay` (the image's interpreter, dependencies from outside), `platform`
  (the platform's interpreter, which only happens when the image has none of its own — see
  above), or `declared` (the harness declares dependencies, none were provided, and the
  interpreter is the **image's**, so there is no fallback).
* **`PYTHONPATH` is inherited by the harness's subprocesses**, so a `python3 foo.py` the
  harness runs also sees these packages. Measured harmless on Terminal-Bench — the closure
  overlaps no task's installs and no task's tests or solution imports any of them — but it
  is a mechanism rather than a proof, which is why the fact is recorded rather than assumed.
* **An overlay cannot reach the graded snapshot.** It is a bind mount, and `docker commit`
  records the mount target and nothing under it: measured, the target exists in the snapshot
  and is empty. The verifier's fresh container therefore never has the harness's packages on
  its `sys.path`.

`source: "declared"` is the shape that used to be invisible, and it is the one a reader
should look for: `loop` on Terminal-Bench was it, and the run said `score 0.000` and
`no edit` — indistinguishable from a method that decided nothing needed changing. The
harness had died on its first model call with `ModuleNotFoundError`, exit code 1 and a
0-byte trace. Runs now warn at the door when a declared runtime has no overlay, naming the
command that fixes it, and record per-task harness failures separately from scores
(`n_harness_failed`, kept apart from `n_invalid` because the task *was* measured).

##### Resources and timeouts are declared, because the defaults cannot hold them

`cpus`, `memory_mb`, `agent_timeout_s` and `verify_timeout_s` move into the environment.
The platform's defaults (2 cpus, 4 GB, 300 s, 300 s) were chosen for short tasks on one
host, and the real task set spans 1–4 cpus, 2–8 GB, 600–12000 s for the agent and
360–12000 s for the check. A platform that ran those on a 300-second default would report a
harness as failing a task it was never given time to attempt. All four are recorded, for the
same reason as `network`: a task with 4 cpus is a different task from the same task with 1.

##### `reward_file`: the exit code is not the score, and here it is not even close

A `command` verifier scores `pass_when`. Terminal-Bench's checks do not work that way —
measured across all 89 tasks, every `test.sh` ends by writing `1` or `0` to
`/logs/verifier/reward.txt` and **exits 0 either way**, because the `if` that writes the file
is the last statement. The exit code carries no information at all.

```python
VERIFY = {"kind": "command", "argv": ["bash", "/tests/test.sh"],
          # an input may name where it goes *inside the container*, not only a path
          # relative to the workdir; `from` may be a directory
          "inputs": [{"dst": "/tests", "from": "<task>/tests"}],
          "reward_file": "/logs/verifier/reward.txt"}
```

The file is parsed as a number, so a fraction is a score and `1`/`0` is the common case. The
platform creates the directory holding it, and that directory exists only after the harness
has stopped.

`evidence_file` is optional and must share the reward file's directory. A check may write a
JSON object there, including `measured: false` when the environment could not be evaluated;
the platform preserves that distinction from a measured zero. Evidence fields are dataset-owned
and passed through in the per-task verdict. For GAIA, the check records the released `level`
label (1/2/3) beside the reward; progress records retain it, and curve points expose additive
`per_level` rows with measured count, count scored 1, and accuracy, plus `unknown` for missing
labels. This report partitions measured tasks and does not change the headline score.
`image` is optional for an
exec command verifier and names a pinned image used only for the fresh check container. It
defaults to the task image, so a dataset may keep a small agent image separate from an image
that contains its released evaluator and data.

##### A dataset's check can be unreachable, and only a control will say so

A harness that scores 0 on a whole dataset has two possible explanations that look
identical from the curve: the harness is weak, or the platform cannot reach the check.
Every task shape this document adds can break in the second way, and a broken check is
*quieter* than a weak harness because it produces a plausible zero.

So the platform ships `base_harness/oracle`: the environment hands it the dataset's own
reference solution and it does nothing else. If the oracle does not score, no harness's
score on that dataset means anything yet. **It is not optional and it is not a
formality** — building Terminal-Bench support it found three defects in the platform
(a `docker cp` that landed the task one directory too deep, a `mkdir` issued to a
container that was not running, and a verifier with no network while all 89 real checks
install their own test dependencies at verify time). The third would have made every
Terminal-Bench task score zero for a reason nothing recorded.

**And the oracle is available per task, not per dataset**, which is a property of the
benchmark rather than of this platform. Measured on five tasks whose solutions looked
tractable: two passed, and the other three failed for reasons outside the platform —
`build-pmars` pins `dpkg-dev=1.22.21`, which has left the Ubuntu archive;
`configure-git-webserver` expects an `sshd_config` its image does not have;
`count-dataset-tokens` downloads from `huggingface.co`, which the host running the
measurement could not reach. In all three the check ran, read its reward, and told the
truth. What that means is narrow and worth stating in any write-up: **a score of 0 on a
task whose oracle has not been shown to pass is uninterpretable**, and which tasks those
are cannot be worked out by reading the dataset — it has to be measured.

**A reference the platform wrote carries its own provenance.** Some benchmarks ship the tests
and not the solution: LiveCodeBench's release carries public and private test cases but no
reference completion, and AppWorld's task directories record `num_solution_code_lines` for a
program that was never released. For those, the oracle's artifact is **authored** here, and
accepted only because the *released tests* validate it — the tests are the gold and the
reference is something that satisfies them. A dataset doing this must declare
`ORACLE_SOURCE = "authored"` together with `ORACLE_VALIDATED_BY` (what validates it) and
`ORACLE_COVERAGE` (how many tasks have one); the lint refuses an `authored` declaration that
omits either, and the run records both (§2.3). The distinction is not bookkeeping: an authored
reference covering three tasks proves the check is reachable on **three** tasks, and a curve
that did not say so would read as if the whole pool had been shown reachable. Nothing here
relaxes the rule above — a task whose oracle has not been shown to pass is still
uninterpretable, whether the reference came from upstream or from us.


### 2.6 A run is about one side

`SPLIT` used to mean "one run, two sets": every curve point carried `score` (the eval
side) and `train_score` (the diagnostics), and the method was shown the train traces
while being scored on the eval ones. **A run now covers exactly one side**, and the two
are separate runs.

| | a train run | an eval run |
| --- | --- | --- |
| what it does | evolves the harness; the method is called each round | measures one state, once |
| task set | the train side | the eval side |
| `side` | `"train"` | `"eval"` |
| `score_kind` | `"training"` | `"exam"` |
| the method | sees the traces of **the tasks it is scored on** | absent — there is nothing to evolve |
| `evaluated_from` | — | `{run_id, round, harness_sha}` |

##### Why the change, stated as the thing it buys

Under the old shape the platform already withheld the eval traces from the method
channel — a **field it did not send**. Now the eval side is not loaded, not evaluated and
not written by a train run at all, so the method cannot see the exam because the exam is
**not in the run**. Same upgrade as §2.5.3's: the platform is hidden from a harness by not
mounting it, not by checking what it read. An absence does not depend on a check being
correct.

There is a second, quieter gain. The old shape could only store the eval traces by
throwing them away — `_save_traces` wrote the train side only, because writing the exam's
behaviour into the run directory put it "one `cp -r` away from a method". That was sound
and its cost was never priced: measured, 25 eval tasks whose behaviour was gone, and a
trace viewer that could only show the half nobody needed to look at. With the sides
separate, an eval run's traces are written freely and are exactly what makes "why did the
harness fail the exam task" answerable.

##### `score` means two things now, so `score_kind` says which

**A train run's `score` cannot be null.** A method's acceptance rule reads it — RRSI
permits a candidate to spend more only in proportion to the score it gains — so a train
run needs a number. But that number is **inflated by construction**: the method has read
the traces of exactly the tasks it is scored on. It is a progress indicator, not a result.

So every point carries `score_kind`, and `tools/plot_curve.py` **refuses to put a training
curve and an exam curve on one axis** — the same treatment `files` vs `exec` gets (§2.5.4),
and for the same reason. A reader who wants the fitting diagnostic asks it across two
records rather than reading two columns of one.

##### The pair comes from two records

`evaluated_from` on an eval point names the train run, the round, and **that run's**
`harness_sha`, so the train-vs-eval pair is *reconstructed*: "is the exam flat while
training climbs?" becomes a query over a train run and the eval runs pointed at it.

**The join key is `evaluated_from.harness_sha`, not the eval point's own
`identity.harness_sha`, and the two differ.** An earlier draft of this paragraph said
they were the same and that is wrong: the eval run stages the state into its own
workspace and commits it, so it has its own commit with its own sha over the same
content. Measured: `evaluated_from.harness_sha = 87f9412d…` against the eval point's
`identity.harness_sha = 8748bb05…`. The link is explicit precisely because the sha
cannot be it.

What that costs is resolution, and it should be stated rather than discovered: under the
old shape the pair was on **every** point, and now it exists only at the rounds somebody
chose to evaluate. The diagnostic survives; the per-round detail does not.

**So a round's state has to survive the run that made it.** It did not — the round trees
were written into a `tempfile.mkdtemp` outside the platform, which is the right place for a
*method's inputs* (a method must not be able to write into the record) and the wrong place
for something an eval run must later point at. Each round now leaves
`state_after_round<N>/` in the run directory, round 0 included, because "measure H0 on the
exam" is the baseline every later eval is read against.

`--side eval` needs `--from-run`, and refuses without it: an eval run does not evolve
anything, so with nothing to measure there is nothing to do.

#### 2.5.10 Deliberately still not designed

* **Multi-container tasks.** Not designed, and **no Terminal-Bench 2 task needs it** —
  which is worth recording because the first version of this section said two did. Two
  tasks carry `metadata.custom_docker_compose = True` and were excluded on the strength of
  it; the flag is stale. Measured over the whole checkout: two tasks flagged, **zero**
  docker-compose files, and both flagged tasks ship a self-contained `Dockerfile` with the
  line `# Fields moved from docker-compose.yaml`. They are single-container and are
  included. All 89 tasks are covered by §2.5.9.
* **GPU and mounted devices** (`environment.gpus` is `0` for all 89 real tasks, so nothing
  here is blocked by it), and **MCP servers** (`mcp_servers` is empty for all 89).

---

## 3. The record: what a curve point is

**Everything downstream (the UI, cross-method comparison, resumption, export)
reads this and only this.** Get it wrong and everything above it is wrong.

Scorer isolation is declared per bundle, not imposed globally. A verifier's
`scorer_bundle.isolation_class` selects its contract. `pristine-offline` requires
a pinned pristine image, `network=none` and exactly two read-only platform
projections. `snapshot-service` retains actor snapshots and/or the task service
network and recording mounts; Terminal-Bench and AppWorld declare this class.
The [scorer contract](docs/scorer-admission-contract.md#per-bundle-isolation-option-b)
defines the opt-in pristine execution fields and the remaining trust obligations.

Every new verdict and curve point carries `scorer_isolation_class` for the class
that actually ran, with `per_task_scorer_isolation` beside task scores. The
additional classes `actor-workdir` and `in-process` describe existing command and
answer checks. Unobserved or historical classes are `unknown`, never presumed
pristine. Mixed classes are refused across trials, task aggregation and curve
history. Comparisons require matching known classes; the comparison UI marks
mixed or unknown classes invalid and suppresses score and test-rate deltas.
Matching classes are necessary for comparison, not proof that every other
metric, split, image, provenance or measurement requirement agrees.

```json
{
  "run_id": "…",
  "round": 1,

  "identity": {
    "harness_name": "loop",
    "harness_version": "0.1.0",
    "harness_sha": "**content address** of the harness tree (git tree hash)",
    "trainer_name": "…",
    "trainer_version": "…",
    "framework_version": "0.1.0",
    "agent_model": "the model the harness itself uses",
    "trainer_model": "the model the Trainer uses to propose edits"
  },

  "sampling": {
    "policy": "below_0.8",
    "task_ids": ["…"],
    "selection_basis_round": 0,
    "selected_within": "eval"
  },

  "env": {
    "kind": "files",
    "image_digest": null,
    "network": null,
    "placement": null,
    "services": [],
    "model_gateway": null,
    "model": null,
    "seed": null
  },

  "score": 0.42,
  "score_ci95": [0.36, 0.48],
  "per_task": { "task_id": 0.0 },

  "train_score": 0.71,
  "train_per_task": { "task_id": 1.0 },
  "split": { "train": ["…"], "eval": ["…"] },

  "measured_by_platform": true,
  "method_reported": { "score": 0.55 },
  "edit_kind": null,
  "selection_effect": {
    "selected_on_reported_set": null,
    "selection_pool_size": null
  },

  "cost": {
    "evaluation_trials": 20,
    "generation_tokens": 154000,
    "env_generation_tokens": 41000,
    "validation_tokens": 9000,
    "wall_clock_s": 1830
  },
  "cumulative": {
    "evaluation_trials": 40,
    "generation_tokens": 301000,
    "env_generation_tokens": 82000,
    "wall_clock_s": 3600
  },

  "timestamp_utc": "…"
}
```

**Six things here are non-negotiable, each for a reason:**

1. **`identity`** — a number is meaningless without the harness state, the two
   models, and the framework version that produced it. *Reason: a harness's score
   changes by more than 2x when the model under it changes, so a curve without
   `agent_model` is unreadable.*
2. **`sampling`** — which task set produced this point. *Reason: `below_X` and
   `all` curves are different quantities.*
3. **`cumulative`** — the x-axis. *Reason: rounds are not comparable between
   methods (one method's round is 178 trials, another's is 1400 simulations).
   Compute is the only axis that is.*
4. **`cumulative.generation_tokens`** — what the improvement cost, separately
   from what the evaluation cost. *Reason: this is the field's largest reporting
   gap. No prior work accounts for the cost of improving itself.*
5. **`score` and `train_score` together, with `split`** — the exam and the
   diagnostics. *Reason (§2.3): one number cannot tell "the harness got better"
   from "the harness got better at the tasks it was shown".*
6. **`env`** — where the measurement was taken, on every point and not only in
   `run_meta.json`. *Reason (§2.5.4): a `files` point and an `exec` point are
   different quantities, and so are two `exec` points under different image digests.
   It is the same class of error as a curve without `agent_model`, and this platform
   has already shipped that one once.* `services` records **which** services ran and
   at which digests — a task with a mock API is not the same task without one.
   `model` and `seed` are the environment's own (§2.5.7) and are `null` exactly when
   no service declares `needs_model` / `needs_seed` — recorded rather than omitted, so
   "not applicable" is distinguishable from "the platform forgot". `cost` carries the
   environment's spend as `env_generation_tokens`, **never** folded into
   `harness_tokens`: mixing them feeds a cost-aware acceptance rule a number its
   formula is not about. A point may carry `env.variants` when the scored tasks do not
   share one environment.

### 3.1 Two measurement parameters, and the fields they add

A point's `score` is the **mean over `n_trials` samples of each task**, measured with up to
`jobs` tasks running at once. They are separate axes and they multiply: `trials` buys a
spread, `jobs` buys wall clock.

* **`--trials N`** (default 1). Each `(harness, task)` pair is measured N times; the task's
  score is the mean over those samples and `per_task_std[task_id]` is their **population**
  standard deviation (these are all the trials this run took, not a sample of a larger
  population). The point carries `n_trials` and `score_std` **only when `N > 1`**: with one
  sample there is no spread to report, and a recorded `0.0` would claim there was none.
  *Reason: measured on this platform. `local-floor-7` and `local-floor-8` ran the **same
  harness tree** (`harness_sha 134a6ccf88c4`), the same model and the same `--jobs 5`, and
  `build-pmars` scored **1.0 in the first and 0.0 in the second**. Their aggregate scores also
  differed (0.333 vs 0.000), but those two runs measured three tasks and five tasks — so the
  aggregate difference is partly the denominator, and the per-task flip is the part that is a
  fact about noise. The resolution of this platform's screens agrees: on `local-screen-1` the
  same harness and task produced `2 passed 2 failed` and then `4 failed` on consecutive
  measurements. A curve that does not say how noisy it is cannot support a claim that an edit
  helped; a **single** trial on a binary task reports 0.0 or 1.0 and cannot even say which.*
* **`--jobs N`** (default 1). Up to N tasks are measured concurrently, and `run_meta.json`
  records the value. The tasks are independent by construction — own container, own network,
  own model gateway, own workspace, own cache key — but they share one model endpoint and
  one host, which is why one is the default and why the number used is recorded. *Reason:
  87.8% of a measured three-task run's wall clock (2578 s of 2936 s) was the harness's own
  agent loop, with the platform idle for all of it.*
* **`--gpu-budget N`** (default: unset). A **machine-wide** ceiling on how many tasks are
  being measured concurrently by *every* run sharing this host; `--jobs` stays this run's own
  ceiling. Enforced with one `flock`-ed file per slot under `--gpu-budget-dir`
  (default `$TMPDIR/hg-gpu-slots`), so a killed run cannot leak capacity and no supervisor
  process exists to die. `identity.gpu_budget` records it on every point beside `jobs`.
  *Reason: `--jobs` is a reservation each run makes for itself, and a batch of runs that
  alternate between measuring and proposing cannot use it. Nine methods at a fixed `jobs=6`
  spend **100 of 203 minutes** with the endpoint idle, because all nine enter their improver
  phase together and all nine leave it together; the same nine under a work-conserving global
  budget of 40 finish in **2.21 h against 3.36 h** (rate model fitted to two measured points:
  21 s/task at `jobs=3`, 78 s/task at `jobs=40`). See `docs/measurement-budget.md`.*

Four more fields exist so that two runs can be told apart at all -- and one of them, the
last, is not about telling runs apart but about telling *candidates* apart:

* **`identity.skill_sha`** — sha256 of the platform skill the method was offered
  (`improvers/skill.md`), read when the point is written. `improver` says which tool composed
  the edits; this says what the tool was told, and the same tool with a different skill
  produces different edits. Omitted when no skill was staged.
* **`cost.method_generation_input` / `method_generation_output` / `method_model_calls` /
  `method_model`** — the improver's own spend as its provider reported it, kept beside
  `method_generation_tokens` (which stays the single number a cost-aware rule reads). *The
  split is what distinguishes "read a lot" from "thought a lot": our own improver's round is
  ~24 KB of harness sources in and ~200 tokens out.*
* **`env.model_gateway`** carries the gateway's own connection counts (`connections`,
  `upstream_connect_failed`, `upstream_connect_error`, `upstream_closed_first`,
  `client_closed_first`, `bytes_to_upstream`, `bytes_to_client`, `read_errors`) beside
  `published` and `target`. *Reason: a harness that dies on `APIConnectionError: Connection
  error` and a harness whose model endpoint hung up on an idle keep-alive connection are the
  same string from inside the container and different facts about the measurement.* A
  harness that dies that way is `invalid` with `stage: "harness model call"` — never `0.0`.
* **`candidate_code`** — what the method handed over, *as code*: file and definition counts,
  the modules not reachable from the entrypoint's import closure, and the module-level
  definitions no one names (`harnessgrad/code_metrics.py`, `kind: "python-static-v1"`).
  Present on a round where a method produced a candidate, absent on H0 and on an eval run,
  because neither handed one over. *Reason, measured by HarnessDev on their own evolved
  harnesses: "of 169 new functions or classes, 113 are reachable from the entry point, 31 are
  reachable only through dead code, and 25 have no caller" (§4.3). Without this field,
  this platform scores a candidate that appended 25 uncalled functions exactly like one that
  changed nothing — and dead code looks like work in a diff, in a token count, and in "14
  edits this round".* It is a **diagnostic and never a score**: static, Python-only,
  name-based, with the three ways it is wrong written at the top of that module.
* **`per_task_trials`** — the per-trial scores themselves, one list per task, present only
  when `n_trials > 1`. The mean and the standard deviation were already recorded; the trials
  answer a question neither can: *which* trial failed. *Reason, measured on `B2-noop`
  (2026-10-04): `cobol-modernization` scored 0.667 — two of three — and the saved verdict was
  the **last** trial's, which passed, so the file said `passed: true` beside `score: 0.667`
  and the failing trial was nowhere. The same artifact then scored 2/3 in one measurement and
  3/3 in the next from a byte-identical trajectory, which is a claim about the **check**
  rather than the harness, and it is unverifiable without the other trials.* On disk, each
  trial is also its own file: `round<N>_traces/<task>.trial<i>.jsonl` (numbered from 1) and
  `<task>.json` under `verdicts/round-<N>/` carries `trial_scores` and `trials[]`.
* **`edits_applied` / `files_claimed`** — the first is the files whose **content changed**,
  taken from the platform's own git diff of the round; the second is the list the method said
  it changed. *Reason, measured on `B2-llm` (2026-10-04): `edits_applied` carried the method's
  claim and said `["agent.py"]` for two rounds whose patch was **0 bytes** and whose
  `harness_sha` never moved — the local model rewrote the same broken file with the same
  broken content. Rounds that changed nothing read as new candidates. The claim is kept
  rather than overwritten: a method that names a file it did not change is a finding.*
* **`n_cached` / `cached_tasks`** — present when some tasks were **not measured this round**
  because the same `(harness_sha, task, trials)` had already been measured. *Reason, measured
  on `B2-noop`: a control method that changed nothing scored round 0 = 0.267 and round 1 =
  0.467 on the same `harness_sha`, because the H0 measurement was cached under the literal
  string `"H0"` while every later round keyed on the content hash. An unchanged tree is now
  measured once, and the reuse is a recorded fact rather than something a reader has to infer
  from two coincidentally equal numbers. The spread of a measurement is `--trials`' job.*
* **`identity.method`** — the method that produced this candidate, as the method itself reported
  it (the `provenance.method` of its trajectory, e.g. `noop (control)`). *Reason: the point
  carried `method_model` and the improver's name but never the method's, so the only place the
  method appeared was `run_meta.method_entrypoint` — a path. A curve is read, plotted and
  compared outside its run directory, and there two methods in one figure were
  indistinguishable except by their scores.* Absent on H0, which no method produced, and on an
  eval run.
* **`identity.harness_config`** — the harness's own runtime budget, as the harness declared it
  in its trace (the `config` key of `harness_identity`): `max_steps` and the retry/nudge/output/
  context knobs of the base loop. *Reason, measured: `HG_AGENT_MAX_STEPS=12` and `=32` run the
  same file, and the two runs produced curve points that differed in **no field at all** — same
  `harness_sha` (the file did not change), same `env`, same `agent_model`. Point 7 of
  `local-floor-9` said 0.333 and point 7 of `local-floor-7` said 0.000 on the same tasks, and a
  reader could not tell whether the difference was the budget.* The platform promotes the
  declaration rather than digging it out of `harness_identity`, so one question has one path:
  `identity.harness_config.max_steps`. Absent when the harness declares nothing, which is a
  fact about that harness and not a zero. `identity.harness_identity` still carries the raw
  declaration, including `agent_model` — see §3.
* **`wall_clock_s`** in the trace, per task (`{"model": ..., "commands": ..., "total": ...}`),
  with `model_ms` / `command_ms` on each step. *Reason, measured: `build-pmars` was killed at
  the task's own 900 s budget after 26 steps, and the record could not say whether the model or
  a `pip install` consumed them — the two have opposite fixes (a smaller step cap or a
  different model versus a task that is simply expensive). The first run with this field
  answered it the other way round from our own guess: the local 9B answers in ~0.8 s per call
  and a 12-step trajectory took 10.2 s end to end, so a step cap was the wrong lever.* Not yet
  promoted to a curve field: the per-task numbers stay in the traces, and the round's total is
  already `cost.wall_clock_s`.*

---

## 4. The method interface

**A method is not a plugin. A method is the implementation of two entrypoints
inside the harness repository.**

```json
// harness.json
{
  "improve_entrypoint": ["python", "-m", "method.improve"],
  "accept_entrypoint":  ["python", "-m", "method.accept"],
  "platform_api_version": "0.1.0"
}
```

The platform invokes these as **subprocesses** and talks JSON on stdin/stdout:

```
platform -> method   (stdin)   one JSON object
method   -> platform (stdout)  one JSON object
```

### 4.1 Why processes and not imports

The first draft imported a method as a Python module from a `trainers/`
directory. That draft could not represent its own subject matter.

An imported module is frozen at load time. The thing this platform exists to
measure is a harness that **rewrites its own improvement mechanism** -- so at the
moment we would need to call it again, the object we imported may no longer be
the method. Importing does not merely complicate that case; it makes it
unrepresentable.

Invoking an entrypoint re-read from the manifest every round costs one subprocess
and removes the problem entirely:

* a method edits `method/improve.py` -> the next round runs the new code;
* a method replaces the whole file, or the whole directory -> still reachable,
  because only the entrypoint *name* is contractual;
* a method breaks its own entrypoint -> recorded as `entrypoint_error`, the
  trajectory stops, **and the run survives**. A method destroying its own
  interface is an observation about that method, not a crash to hide.

### 4.2 What this buys: the editable surface becomes observable

Because the method lives inside the harness, the boundary between "the artifact
being improved" and "the thing doing the improving" is a fact about the
repository rather than a claim in a paper. The platform records it every round:

| field | meaning |
| --- | --- |
| `improve_entrypoint` / `accept_entrypoint` | the surface the harness *declares* |
| `editable_surface_touched` | which paths a round *actually* moved, from `git diff` |

`"method/accept.py"` appearing in `editable_surface_touched` means the method
rewrote its own acceptance rule. That is the line most published systems state
they do not cross -- HyperAgents, which pushes self-modification furthest,
reports that selection and evaluation "remain fixed ... they cannot alter the
outer process that determines which agents are selected or how they are
evaluated."

**The platform therefore does not offer a "real RSI" mode.** A declared mode
would be a self-report, and self-reports of self-improvement are exactly what
this platform should not be adding to. The boundary is measured, and the
genuine/pseudo distinction becomes a reading rather than a setting.

### 4.3 What the platform will not do

**The platform never reads, writes, or imports anything under a harness's
`method/` directory**, and never edits `accept`. This is enforced by AST test
(`tests/quality/test_boundaries.py`). If the platform touched the acceptance
rule it would become a participant in the comparison instead of the referee.

### 4.4 Two execution modes

The platform no longer holds methods, so the modes describe *who owns the loop*:

**Mode A -- platform-driven.** The platform owns the sampling loop and calls
`improve_entrypoint` once per round. Best comparability; the method does not
choose its own rhythm.

**Mode B -- method-driven.** The method runs its own loop in its own process and
writes a trajectory file. Use this for any method that already exists and must
not be re-wired in order to be measured. The platform then resolves and scores
the states the method named, on the same fixed task set as every other method,
with the same scorer.

The trajectory file:

```json
{
  "steps": [
    {"label": "", "files": {"agent.py": "<full file contents>"},
     "edit_kind": "<the method's own word for what kind of change this was>",
     "claimed_cost": {}, "method_reported": {"score": 0.4}}
  ],
  "nominated": 3,          // 1-based step number; 0 or absent = no nomination
  "trajectory_shape": "sequence",
  "curve_drawn": "per_step",
  "rhythm": {"own_rounds": 20, "note": "this method's own schedule"},
  "selected_on_reported_set": true,
  "selection_pool_size": 20,
  "provenance": {"acceptance_rule": {"text": "...", "source": "...", "calibrated": true}}
}
```

### 4.45 A step is two phases, and the second one is verifiable

A method's step is **not** "propose something". It is:

    phase 1  observe   read the traces and the per-task page, and produce an **edit
                       sequence** -- `[{path, content}, ...]`
    phase 2  apply     apply it, and verify the result is still a harness

**Both phases must complete for the round to be a step.** A round whose sequence did not
apply is recorded as a *failed step* (`run_meta.json: failed_steps`) with its reason, and
**no curve point is written for it** -- because there is no measurement to write. Before
this, all three of these were the same `score 0.000`:

* the method decided nothing needed changing,
* the edit sequence named a path outside the harness, a protected file, or a file it
  never wrote,
* the result is not a harness at all -- a manifest that no longer parses, an entrypoint
  that is gone, Python that does not compile, or an `env_kinds` widened past what the
  door approved.

Measured: `build-pmars`, three rounds reported as `no edit`; and a round whose
`UnparseableReply` (the model answered with 2366 characters of prose instead of the JSON
envelope) was reported as `round 1  method error  train 0.000` -- a number about a
candidate the method never produced.

Two definitions, two owners:

* **`eval/run/candidate.py` owns `apply` and `validate`.** It is the platform's definition of
  what it is about to measure, and it reuses `tools/validate_harness.py` so the door, the
  CLI and the run path cannot disagree about what a harness is. The platform applies this
  check to the candidate **itself**, because a method is code the platform did not write:
  a method's own check is a claim, the platform's is the judgement.
* **The retry lives in the method** (`methods/editor.py: propose_and_apply`). Re-proposing
  is the improver's own repair; a platform that called the model itself would be writing
  the method. What the platform owes is the checker, callable from the method so it can
  guarantee its own output before reporting it. The failure text is handed back verbatim:
  the prose reply above ended with, almost word for word, the fix the harness needed.

A candidate that does not validate is **unmeasured**, never scored 0 -- the same
distinction §2.5.7 draws for a task the platform could not measure, one level up. And
`methods/editor.py: report` refuses one outright, so "reported a candidate" and "the edit
sequence applied" are the same statement.

##### Phase 2 may test the harness. It may not evaluate it on a task.

A method is free to analyse its own edit and to **write tests for it** -- compile it, run
its unit tests, drive the harness's own entrypoint on a synthetic task it constructs. What
it may not do is run a task from the dataset to find out how the edit scores. Two reasons,
and the second is the load-bearing one:

* **The score is the platform's.** A method that could evaluate its own candidates would
  report only the ones that scored well, and the curve would describe a selected subset
  while looking like a sequence of attempts.
* **It is structurally impossible, and that is the guarantee.** A task's environment
  either lives in a container -- and a method's sandbox has no `/var/run/docker.sock`, so
  `docker ps` fails with `failed to connect to the docker API` (measured) -- or it lives in
  a directory under the platform, which is not mounted. What a method is given is the
  **channel**: the traces and `tasks/<task_id>.json`. That is the evidence it is allowed to
  study, not the environment it would need to evaluate anything.

### 4.46 A run says what it is doing, while it is doing it

Round granularity is not enough to watch a run, and the gap between "recording a result"
and "recording progress" is where a platform loses its users' trust. Measured on
`loop-terminal_bench-26452`: round 0 ran five Terminal-Bench tasks for ~100 minutes, the
driver worked the whole time, and `events.jsonl` held **eleven records** -- six notes, a
header, a warn and one round. The console UI polls that file every 1.5s and redraws only
when it changes, so it showed one screen for an hour. From outside, *working* and *hung*
were the same picture, and the only way to tell them apart was `docker top`.

So progress is part of the record (`eval/observe/progress.py`), with the same durability rule as
every other event: appended and flushed when it happens, so a run killed mid-task has an
honest record up to the kill.

| kind | when | carries |
|---|---|---|
| `task` | a task in a round starts / finishes | `index`, `total`, `task`, `status`, `elapsed_s`, `score`, `detail` |
| `phase` | one stage of a task starts / finishes | `phase`, `status`, `elapsed_s`, `budget_s`, `detail` |
| `beat` | a phase is still running, every 10s | `phase`, `elapsed_s` |
| `note` (with `phase`) | a phase ran past its own declared budget | `phase`, `elapsed_s`, `budget_s` |

`PHASES` is one flat vocabulary for both environment kinds -- `setup`, `create`,
`prepare`, `agent`, `snapshot`, `verify`, `collect`, `teardown` -- not a free-form label
per call site, because a panel that has never seen this module still has to be able to
say what "agent" means. `agent` deliberately has **no** budget: Terminal-Bench tasks
legitimately run for hours, and a warning that fires on every ordinary task is a warning
nobody reads. The beat is what covers it.

Three properties, each of which cost a decision:

* **The record must not become a second copy of the run.** A beat carries an elapsed time
  and a phase name and nothing else -- no output, no state. A heartbeat that is expensive
  to produce becomes the thing that slows the run down.
* **Heartbeats must survive a blocking call.** The longest waits are single blocking
  calls (`docker exec` on a harness for an hour, `docker exec` on the check), so
  `eval/containers/container.py` streams those with `Popen` and beats while `wait` times out. Every
  other docker call -- `create`, `cp`, `commit` -- stays one `subprocess.run`. The branch
  is on whether a task is in flight, not on a flag per call site, because the three call
  sites that block for minutes do not know whether anyone is watching.
* **Beats go to the event stream, not to stdout.** One every 10s for an hour is 360
  lines, and it would push the round table -- the thing a person scrolls back for -- off
  the screen. The terminal gets the four lines that matter: a task finished, a task
  failed, a phase failed, a phase went over budget.

The reporter reaches the container client through a `ContextVar`, which is an exception
to this codebase's habit of passing things explicitly. It is bounded by the dispatch it
serves (`evaluate` -> `run_one` -> `container.*`, one chain, one thread), and threading a
reporter through it would change eight signatures to move a log line. The context is set
by `evaluate` and by `run_one`; a caller outside a run gets a no-op and no branch.

The lesson worth keeping is the bug the first version had: the events were *only* in the
context, and the driver's own calls to `evaluate` never set one -- so a real run narrated
the method call's phases and wrote nothing for the four tasks it actually spent the hour
on. A log that reports its shortest wait and hides its longest is worse than no log.

##### A score has to arrive with the output that produced it

Progress says *what is running*. The next question -- "why is this 0?" -- had no answer in
the record at all, and that is not a rendering problem: the answer existed inside
`evaluate` and was thrown away. Measured on `loop-terminal_bench-26452c`: five tasks per
round, every score `0.0`, six rounds, and **nothing** on disk said why. The verifier's
`detail` (reward, exit code, and the tail of the check's own stdout) reached a local
variable and died; the harness's stderr was read into `result["stderr"]` and surfaced only
when the exit code was non-zero. Four of those five zeros had to be explained by hand,
reproducing each task's `test.sh` outside the platform.

Two records fix that, and they answer different readers:

* **`runs/<id>/verdicts/round-<N>/<task>.json`** -- on disk, next to the traces, because a
  run gets exported and grepped months later. Fields: `score`, `kind`, `passed`, `detail`
  (the check's own words), `harness` (`exit_code` and both ends of what the harness
  printed: the head, where it says what it is doing, and the tail, where it dies).
* **a `log` event** per task, `source: "check" | "harness" | "method"`, carrying the same
  body. The terminal gets one line (`✗ check · round 1 · sanitize-git-repo — reward 0 from
  /logs/verifier/reward.txt …`); the panel renders the body collapsed. What made this
  worth doing is the first real example: a task at `0.0` whose check reported
  `2 failed, 1 passed` and whose harness had died on `openai.APIConnectionError` -- two
  facts that used to be invisible and that point at different fixes.

The rule this is an instance of: **a curve point may not be the only thing a run says
about a task.**

### 4.47 A harness is identified by its content, not by a commit

`identity.harness_sha` used to be the **commit sha** of the harness repo, on the argument
that a harness is code and git already versions code. A commit is not a content address:
it also carries an author timestamp and a parent, so committing identical content twice
produces two names for one state. Measured on `loop-terminal_bench-26452c`: rounds 2-5
reported four different `harness_sha` values (`fd5a0e59`, `ff7beb56`, `6284989c`,
`807eeac7`) for **one** tree (`git rev-parse <sha>^{tree}` = `88046acf` for all four), and
every `diffs/round-N.patch` after round 1 was empty.

It is not a cosmetic defect, because the sha is load-bearing in three places:

* the **evaluation cache** is keyed by `(harness_sha, task_id)`, so a new name every round
  meant the cache could never hit and the same unchanged harness was re-measured five
  times -- at 98 s, 1773 s, 2841 s and 103 s of agent time for identical code, which reads
  as a property of the harness and is not;
* **`identity.harness_sha` is the join key** between a train point and its eval point
  (§2.6), so two names for one state breaks the pairing the split exists to support;
* it is what a reader uses to see *whether a step changed anything*.

`commit_state` now returns the working tree's **git tree hash**, which is a content
address: equal trees get equal names, `.gitignore` applies by construction (so a harness's
own `.state/` scratch cannot enter its identity), and the object it names is in the object
store -- `materialize` can still `git archive` it, which a hash computed outside git could
not. Two consequences worth stating rather than discovering:

* **`reset_hard` had to take trees as well as commits.** `git checkout --force <tree>`
  fails with `pathspec ... did not match any file(s)`, and `git reset --hard <tree>` fails
  with `... is a tree, not a commit` (both measured). It now branches on `git cat-file -t`
  and uses `read-tree` + `checkout-index -af` + `clean -fd` for a tree, which is what
  `reset --hard` would have done. HEAD is left where it was: a tree cannot be a HEAD, and
  every consumer names its state explicitly.
* **Git lineage in mode A is a fan, not a chain.** Every round commits from `H0` as the
  parent, because each round resets to `H0` and writes the candidate in. The *content*
  lineage is complete (each commit's tree is the whole harness at that round, and the tree
  hash now makes "same as the previous round" visible); the *commit* lineage is shallow.
  That is a design choice, not a bug, and it means `git log` is not how you read a mode A
  record -- `diffs/` and the tree hashes are.

### 4.48 Three bugs that made every round look the same

Both were found by asking "why is every score 0?" of a record that could not answer, and
both are about **a number losing the thing that explains it**.

**`editable_surface_touched` was always against H0.** The field answers "what did this
step move", and it was computed as `diff(H0, current)` instead of
`diff(previous_round, current)`. Measured on `loop-terminal_bench-41133`: rounds 4 and 5
reported `touched: ["agent.py"]` while their harness tree was byte-identical to round 3's
(`diffs/round-4.json` and `round-5.json` both say `files: [], added: 0, removed: 0`). A
record whose own two fields disagree about whether anything happened is worse than one
that omits the field: the reader has to decide which to believe, and the method-facing page
quotes the wrong one as "what moved this round".

**A cached round lost its verdicts.** `evaluate`'s cache is keyed by `(harness_sha,
task_id)` and stored `score` and `trace` but not `verdict`, so a round that was entirely
cache hits wrote five verdict files with `kind: null` and `detail: ""` -- the score, with
the explanation dropped. Measured on the same run, round 5. The cache now carries the
verdict, because "this was measured before" is not a reason for the record to stop saying
why.

The run that exposed both also exposed the third, which is not a bookkeeping bug at all:

**One malformed reply ended a task.** `base_harness/loop`'s `_parse` took the text from
the first `{` to the **last** `}`, so a reply containing two JSON objects -- one per line,
which is what the measured model emits (`{"tool": ...}}
{"answer": ""}`; note the stray
brace) -- produced `{...}}{...}`, which is not JSON. The harness treats an unparseable
reply as a harness failure and **breaks the loop**, so four of five tasks in round 0 ended
after a single model call with an empty answer, and every later round inherited a harness
that could not act. The fix is the parser `base_harness/loop_rrsi_parse/` already shipped:
`JSONDecoder.raw_decode` from the first `{`, which returns the first complete object and
ignores whatever follows. All three base harnesses now share it, byte for byte, so this is
a platform fix rather than a built-in head start for one candidate.

What made the third one hard to see is worth recording too: the platform recorded
`calls: 1` and `parse_error: Extra data ...` in the trace the whole time, and the round
looked like a harness that was merely weak. The trace had the answer; nothing surfaced it.
That is the same shape of gap as §4.46, one level down.

### 4.49 The improver is the tool; the method is the rule

A method was always two things stacked: **an improver** (an agent that reads the harness and
composes an edit) and **a rule** (what to change, when to accept it, how much budget it
gets). The platform recorded only the second -- `identity.method_model` -- and every method
carried its own improver inside its own `run.py`. So when one method's edit landed and
another's did not, the record could not say whether that was a better rule or a better
prompt, and the comparison the platform exists to make was not being made.

The split is now explicit:

| part | what it is | who owns it |
|---|---|---|
| **the improved harness** | the task-solving loop (`base_harness/loop`, `base_harness/cli_agent`, …) | the run |
| **the improver** | an external command that takes a prompt and works in a directory (`improvers/improvers.json`) | the experiment, resolved once per run |
| **the method** | a decision rule, plus optional `skill.md` and a declared `improver` | `methods/<name>/` |

What lands on every curve point is `identity.improver`: `name`, `version`, `sha256`,
`model`, `requested`, `modified`, `path`. The version and the hash are the load-bearing
part, and the reason is measured: **three codex builds coexist on the development machine**
(`0.125.0` with its native binary missing so `--version` crashes, `0.139.0`, `0.156.1`).
Without this field, "the improver was upgraded" and "the method got better" are the same
curve.

##### `latest` is a request, not an identity

Version selection has two failure modes and the design refuses both:

* **pin everything** -- nobody can use a current codex, and a two-year-old binary becomes
  part of the platform's surface;
* **follow `latest`** -- two runs a week apart are not comparable and nothing says so.

The resolution is that the *request* and the *fact* are different fields. `improvers.json`
may say `latest`; the resolver picks the **highest version it verified by running**, and the
resolved version plus the binary's sha256 go on the curve. So `latest` means "do not pin",
never "do not record".

Two consequences follow, and both are implemented in `tools/improver.py`:

* **`PATH` is not consulted as an authority.** The resolver enumerates candidates and
  verifies each by executing `--version`; on the development machine `PATH` resolves to the
  broken install, so "first on PATH" would be a wrong answer. `--list` prints every
  candidate with the reason it was rejected.
* **A version that cannot be honoured is a refusal, not a substitution.** Asking for
  `0.156.1` on a machine that only has `0.139.0` fails with the candidate list, because a
  silent downgrade is a record that lies.

##### A modified codex cannot be governed, but it must not be anonymous

Somebody who patched their own codex has a binary we cannot obtain and must not pretend to.
The platform's stance on a harness applies unchanged: it cannot judge what the code produced,
but it can say which bytes ran. Such an improver is declared in `improvers.json` with a
`path` and `modified: true`; the flag travels onto the curve point, so a reader knows two
runs under it are not two runs of the same thing.

##### The improver's model is read from the improver

`codex` takes its model from `~/.codex/config.toml`, so `HG_METHOD_MODEL` says nothing about
what the improver will call. The resolver reads that file and records what it finds
(`tools/improver.py:declared_model`). A fact the platform cannot see any other way is still a
fact the platform is responsible for recording.

##### The default skill, and why it is not the method

`improvers/skill.md` is the platform's default: it says what a harness is, where the evidence
is (`_harnessgrad/traces/`, `tasks/`, `states/`), what an edit sequence looks like, what
phase 2 may do, and the three failure modes measured on this platform (a loop that ends on
one unparseable reply; a budget that runs out with nothing submitted; a deliverable written
where the check does not look).

It is deliberately silent about acceptance, budgets, parent selection and critics. Those are
the method. A method that ships its own `skill.md` overrides the default; a method that ships
none gets this one -- which is what makes "same skill, different rule" a real comparison
rather than a slogan.

**Who puts it in the prompt is the platform, not each method.** `editor.ask` prepends this
file to every prompt it sends (`editor.skill_block`, `skill=False` to decline), because a
curve point carries `identity.skill_sha` and that field means "the skill this prompt
carried". Until 2026-10-04 each method read the file itself and four of twelve did, so for
the other eight the field described a prompt nobody sent. A method with its own skill passes
it to the model itself and declines the default, and the two cannot then arrive together.

### 4.1.1 The budget counts **measured states**, not method calls

`--rounds N` used to mean "call the method N times, one candidate each". That is the one
shape an RSI-family rule cannot use: its content is *how to spend a budget under noise*
(RRSI's annealed `b_t`, DGM's child counts, SICA's confidence bound over repeated
evaluations). Mode B went the other way -- the method owned the schedule and handed over a
whole sequence, but the platform measured it *afterwards*, so the method spent its budget
without ever seeing a score. Two halves of one loop, and neither could express a selection
rule. Measured: `B2-rrsi` ran three rounds, accepted nothing (`edits_applied: []`,
`harness_sha` unchanged every round) and its own state absorbed a re-measurement as a real
gain (`S*` 0.2 -> 0.4667).

One engine now, and `--rounds N` is a cap on **states measured**:

* a submission carries one or more steps (`trajectory.steps[]`); **every** step is measured,
  in order, and each appends a curve point;
* the platform calls the method again **iff** the budget has room and the method did not
  stop. One step per call is the old mode A (a score is visible between candidates); ten
  steps in one call is the old mode B (a batch, no feedback inside it); three and then three
  more is what neither could say;
* `stop` defaults per submission shape: a single step keeps the old default
  (`not changed`), a **batch** stops unless the method says otherwise -- without that,
  single-step ports would be re-invoked for the rest of the budget;
* a submission larger than the remaining budget is truncated, and the truncation is recorded
  (`run_meta.steps_truncated`) rather than silently dropped. Mode B never enforced the cap at
  all; it does now.

Fields this adds:

| field | where | what it says |
| --- | --- | --- |
| `step_index` | point | which step of its submission this point is (1-based) |
| `steps_in_submission` | point | how many states that call bought; `> 1` means the method saw **no** score between them |
| `kept_round`, `kept_score` | point | which state the method decided to **keep**. A selection rule's whole content is accept-or-keep, and the platform already let a method act on it (hand back an earlier tree from `states/`, which `harnessx`'s port does) -- what was missing was the record: `score` is the *candidate's* measurement, so a curve of candidates cannot show what the rule kept. A step names `incumbent_round`; the point carries that round and its score |
| `steps_measured`, `steps_truncated` | `run_meta` | how much the budget bought and how much of a submission it cut |

`methods/` needs no change: a method that writes one step per call behaves exactly as before.
`--mode` still selects the loop, but the two loops now share this budget arithmetic and
report the same two run-level fields, so their curves can be read on one axis.

### 4.49.1 Open decision: which improver is the *default*, and what a method owes it

`improvers/improvers.json` names **codex -- a CLI -- as the default**, so an ordinary run
resolves a CLI improver, records it in `identity.improver`, and binds its runtime trees.

Meanwhile every method in `methods/` carries its own editor: the seven ports and
`llm_improver` call `editor.ask`, which is a chat endpoint. Two consequences, both measured:

* With the default, the record **names a tool that composed nothing**. `HG_METHOD_BASE_URL`
  is only injected when the resolved improver *is* an endpoint, so a method under the default
  falls back to `.env` and calls whatever is there while the point says `codex`.
* Driving the CLI is possible -- `methods/cli_improver.py` is that implementation, extracted
  when `methods/codex` was deleted for not being a paper's rule -- and wiring it into the
  reference method for one afternoon silently changed what a *default* run does (it started a
  real codex session). That is why it is not wired in.

Two coherent fixes, and no third:

1. **Make the default an endpoint** (`deepseek`), so the default is a tool that works
   unattended and the record is true; `--improver codex` then means "drive the CLI", and the
   method that wants it calls `methods/cli_improver.py`. Cost: a hosted endpoint becomes the
   default path for every run, which needs its key in the environment.
2. **Require every method to drive the resolved improver**, refusing loudly when it cannot
   (`editor.model_settings` is the single place that can say so). Cost: a method can no
   longer carry its own editor without saying why, which is most of what the ports are.

**Decided 2026-10-04: option 1.** `improvers/improvers.json` now names `deepseek` as the
default, so an ordinary run resolves an endpoint, `_method_env` injects its base URL, model
and credential into the method, and the record and the run agree by construction -- every
method in `methods/` reaches a model through `editor.ask`, which reads `HG_METHOD_*`.

What that fixes, measured: with `codex` as the default, `identity.improver` on every point
named a tool that composed nothing, and the method called whatever `.env` happened to hold.
The same defect the `deepseek` entry was written to prevent (`tools/improver.py:resolve`,
"a record that names one endpoint and a method that calls another").

What it costs: the default path now needs `DEEPSEEK_API_KEY` in the environment (`.env` or
the real environment; `.env.example` carries the name). Without it `resolve` returns
`ok=False`, the driver warns at the door, and the run carries **no** improver identity rather
than a wrong one -- which is the correct failure.

`--improver codex` still resolves the CLI, and a method that wants to drive it calls
`methods/cli_improver.py`; none does today, and that is stated there rather than hidden.

### 4.50 Choosing the improver is part of the experiment

§4.49 made the improver a recorded fact. It has to be a **chosen** one too, or the record
describes something the operator could not control: until this existed, `improvers.json`
held the choice and an environment variable held the override, so "which improver produced
this curve" was answerable after the fact and not before it.

    driver.py --improver <name|path> --improver-model <model> --improver-effort <level>

Three properties, each of which is a refusal rather than a convenience:

* **A name that is not in the config is refused, not defaulted.** Asking for `local` and
  quietly getting `codex` is precisely the substitution that makes two experiments
  incomparable, and it would be invisible -- both curves would look like "the method".
* **The resolution is reported, not assumed.** The name may resolve to a version, a hash
  and a model that the operator never typed (`latest` → `codex-cli 0.156.1`, model read
  from *its* `config.toml`). All of it lands on the curve point; the panel shows the same
  resolution before the run starts.
* **A model override travels with the choice.** Codex takes its model from its own config,
  so a platform-side `HG_METHOD_MODEL` says nothing about it; `--improver-model` is the
  supported way to point it at a different model, and it is recorded as the model used.

The interface follows the same split as the record: the form has one control for the
**method** (the rule) and a separate one for the **improver** (the tool), because they are
different objects with different owners. A single "method" dropdown is what made the old
comparison confounded -- it silently bundled a prompt, a model and a rule into one name.

##### The improver has to reach the method, and it did not

§4.49 and §4.50 were both written, tested, and unusable in a real run, and the reason is
worth keeping: **a method cannot look the improver up for itself.** A method runs in its own
mount namespace with the platform hidden except `methods/` (§4.8), so:

* `tools/improver.py` -- the resolver -- is not on its disk;
* `improvers/skill.md` -- the default skill §4.49 promises it -- is not on its disk either;
* the improver's own runtime is not on its disk: the namespace binds `/usr`, `/bin`, `/lib`,
  the interpreter's prefix, the work root and the method's own directory, and **nothing under
  `$HOME`**, which is where a node-based CLI and its credentials live.

Measured, on the first run that tried it (`--improver codex`, three tasks, mode A):

```
round 1: method entrypoint failed: exit 1 × 3      stderr: "codex method: no usable improver"
```

and with the improver path supplied by hand, `FileNotFoundError: …/codex.js`. Two messages,
one cause: the improver layer worked only under `--no-sandbox`. **No run had ever recorded an
edit produced by codex**; the previous `--improver codex` runs named the improver on their
curve points because the *platform* resolves it, while the editing was done by the method's
own bundled model.

Three changes, one per missing piece, and each is the platform handing over something it had
already resolved rather than letting a method rediscover it:

| what the method needs | how it now arrives |
|---|---|
| which improver, and where | the request carries `improver` -- the same `name`/`version`/`sha256`/`model`/`path` the curve point records, so the method cannot run a different one |
| the default skill | staged in the channel as `_harnessgrad/SKILL.md` (§4.7), which is where the things a method may read already live -- and injected at the front of every prompt `editor.ask` sends, unless the method declines it (`skill=False`). The injection is the platform's, not each method's, because the point's `identity.skill_sha` names a skill: with twelve methods each deciding for itself, four of them read it and the field described a prompt the other eight never sent |
| the improver's runtime | `tools/improver.py:runtime_trees` reports the package directory, the interpreter named in the launcher's shebang, and the config home (`~/.codex`); the method sandbox binds them **read-only**, exactly like the method's own program |

The last row is the one that needed a decision rather than a bug fix. An improver is a tool
the platform chose and the method executes; a method that can neither find nor run it has been
given a name and nothing else. Binding the tool applies the rule the method sandbox already
states -- the platform is hidden, the things the method must *execute* are not -- and it is
read-only, because a tool is not state the subject may write.

The failure also changed how a method failure is reported: the stderr tail now goes into the
note itself, not only into the `detail` event beside it. Both were always recorded, but a
reader of `console.log` sees the notes, so all three failures above read as a bare `exit 1`
in the human record while the reason sat one event away.

### 4.5 `files`, not a commit id — and why this took two attempts

The first draft of this contract had a step name a `ckpt`, and the platform would
resolve it. That is wrong, and the failure was silent in a way worth recording.

An external method's states live in **its own** git. RRSI names a commit in the
`rrsi` repository; DGM names a variant directory; SICA names a per-iteration code
copy. None of those is a commit in the platform's workspace, and the evaluator
reads the **working tree** — not a commit. So "here is the sha" was being treated
as "the state is in place", the platform measured whatever the working tree
happened to hold, and the first two steps of a test trajectory both scored the
same because both were measured against the *last* step's files.

**A step therefore carries the harness files that step produced**, and the
platform:

1. resets the workspace to the base commit,
2. writes those files,
3. commits that state, and
4. measures it.

A state the platform cannot materialize is a state it will not guess at: a step
with no `files` stops the trajectory with a recorded reason rather than being
scored anyway. **Naming a state and being able to measure it are different
things, and only the second one is worth reporting.**

This is the case the platform's own rule was for: an interface change (a state
must be materializable) was **predicted**, then **withdrawn** when a probe of
DGM's artifacts appeared to disprove it, then **reinstated** when the trace path
was actually executed and the bug showed up. The probe had asked where a snapshot
*lives*; the failure was about how the platform *gets* it.

### 4.55 Where a method lives

**Outside the harness.** A method is handed a pristine copy of the base harness
and a scratch directory, and it produces candidate harnesses. The platform
measures them.

The first draft had the method inside the harness, in `base_harness/<name>/old-method-design/`.
That was wrong in a way that is easy to miss: it made the method inseparable from
one artifact, so two methods could never be compared — each would face a
different starting point. It also meant a method could be part of the thing being
measured.

The exchange, both directions:

```
platform -> method (stdin)
  {platform_api_version, base_harness, workspace, task_ids,
   trajectory_out, mode, [round_index, incumbent_score]}

method -> platform (stdout)
  {steps, changed, generation_tokens, [stop]}

method -> file at trajectory_out
  {steps: [{harness_dir, label, edit_kind, claimed_cost, method_reported}], ...}
```

Each step names **a directory holding a complete harness** — not a diff, not a
commit. It must satisfy [`docs/writing_a_harness.md`](docs/writing_a_harness.md):
the same contract the base harness satisfies. That is the point. A method's
output is measured by the same rules as the base it started from.

### `stop` — rejecting a candidate is not the same as being finished

`changed: false` carries two different meanings and mode A used to act on both as
if they were the first:

| what the method means | `changed` | `stop` |
| --- | --- | --- |
| "here is my candidate" | `true` | omitted |
| "I am finished" | `false` | omitted (= stop) |
| "I reject this candidate; give me the next round" | `false` | **`false`** |

`stop` defaults to `not changed`, so nothing written before the field existed
changes meaning. It exists because the alternative is a systematic bias: RRSI
screen-rejects a candidate and retries after bounded repair, TTHE's rollback gate
keeps the incumbent and waits for the next batch, and AHE's `HARMFUL` verdict
rolls back and re-approaches — under the old rule **all three lost the rest of
their budget to a single rejection**. Measured, not argued: with a base harness
scoring 0.0, `methods/rrsi` and `methods/tthe` stopped after one method round
while every eager method got three. A platform whose promise is a fair comparison
cannot score cautious methods on fewer rounds than eager ones.

The cost of `stop: false` is honest and worth naming: the platform still measures
the candidate the method returned, so a rejection spends a round and a curve
point on a state the method does not endorse. That is the right trade — a method
may not move the incumbent for free — but it is a trade.


### `nominated` — which step the method stands behind

**1-based**, counting steps. `curve[0]` is the H0 baseline, so step *N* is the
*N*-th candidate and lands on the (*N*+1)-th curve point. `0`, `null` or an
out-of-range value means "no nomination", and the platform falls back to the last
checkpoint and says so.

This was written down only after the mechanism turned out to be broken end to end:
`driver.py` read `nominated` from the trajectory to mark the step and **never wrote
it to `run_meta.json`**, while `tools/export_harnesses.py` read it from there. The
export therefore always fell back to the last checkpoint, and a method's nomination
had never been honoured. Two lessons worth the paragraph: a field read in one place
and written in none is not a contract, and "the exporter always picks the last one"
looks like a sensible default rather than a missing wire.

`tools/validate_method.py` checks this range, because the failure mode is silent --
an out-of-range nomination is not an error, it is a fallback.

`changed: false` in the stdout reply is how a method tells the platform that
another round from this state is pointless, which is what keeps a control method
from being run repeatedly at cost.

### 4.6 `edit_kind` — recorded, never interpreted

Each step may declare what kind of change it made, in the method's own
vocabulary. **The platform records the value, copies it to the curve point, and
does nothing else with it.** No whitelist, no mapping, no default, no validation
beyond it being JSON — enforced by test, because the temptation to tidy it into a
controlled vocabulary is exactly how a referee becomes a participant.

Why a free-form field is worth a slot in the contract at all:

* **Seven published methods were checked and none makes this readable.** AHE
  specifies `constraint_level: middleware | tool_impl | tool_desc | skill |
  prompt` in its prompt, and no code in the repository reads it. RRSI is the only
  one that emits a tag per accepted edit (a vocabulary of nine, recovered from
  the diff by regex when the model's own declaration is invalid — so the label
  reflects a heuristic, not the model's intent).
* **A vocabulary that exists only in prose cannot be analysed** — across methods,
  or even across one method's own runs.
* It is the entry point to the question this project cares about: to ask whether
  a change installs something already known, the first thing you need is what
  the change *was*.

The field does not constrain what a method may do. A method that emits nothing
gets `null`, and `null` is a reading too: it says the method does not distinguish
kinds of change.

### 4.7 What the method is given, on disk

```
workspace/
├── <the harness repo, fully writable -- including method/>
└── _harnessgrad/
    ├── round.json               # this round's curve point
    ├── traces/<task_id>.jsonl   # the incumbent's traces, this round only
    ├── tasks/<task_id>.json     # the instruction, the verifier and its gold, and the
    │                            # per-task verdict -- one file per task in this run
    ├── history/round-*.json
    ├── history/traces/round-<n>/  # that round's train traces
    ├── states/round-<n>/        # the complete harness at round n
    └── states/index.json        # which states exist, with their score and sha
```

**History is three things, and one of them was missing.** `history/round-*.json`
carries every past *score*; `states/round-<n>/` carries every past *harness*;
`history/traces/round-<n>/` carries every past *behaviour*. The third was added
because its absence was invisible: the live `traces/` is overwritten each round
with the incumbent's, so a method could see all its past scores and all its past
harness directories and **not one line of what the harness actually did back
then**. Several published methods diagnose by comparing rounds, so this was a
silent limit on which methods the platform could host — and the comment in
`driver.py` claimed the opposite (that a method could "name the round" to get a
past trace) while no past trace existed anywhere.

Both trace sets are **train-only**, and both are capped at `STATES_KEPT` rounds.

**One field is removed from every curve point before it is written here.** The
channel copy drops `per_task` — the eval side's per-task breakdown — while keeping
the aggregate `score` and the whole train side. `driver.py`'s `WITHHELD` is the
single place that decides this; §2.3 gives the reasoning. The point in
`curve.jsonl` is untouched — only the method's copy is narrowed.

**A method sees this and nothing else.** The channel is not a courtesy extended to a
method that behaves; it is the *whole* of what a method may read, and it is enforced by
a sandbox, not by convention:

| what | what it used to be able to read |
| --- | --- |
| its own program (`methods/`) | — |
| `base_harness`, read-only | the whole platform |
| its `workspace`, writable | `data/datasets/*.py` — **the expected answer for every task, eval side included** |
| the channel above | `.env`, `eval/run/runner.py`, `driver.py`, `harnessgrad/` |
| its own model credentials | the agent's credentials |

Measured, with a probe method placed where a real one lives: it read
`data/probe_set.py` and got `{"d05": "ok", "d06": "5"}`, along with `.env` and the
scorer, and held both API keys. Nothing detected it — the platform hashed itself
either side of the call, which sees **writes**.

That is not a small leak. A method that reads the answers can hand back a harness with
them written into it, score 1.0 on every task, and be reported as a strong method, and
the §2.3 split does not help because the answers are not in the channel — they are in a
file.

So a method now runs in the same kind of namespace the harness does, with one
difference: the platform is hidden **except** the directory its own program lives in.
The listing the sandbox presents is `["methods"]`. A method that cannot read its own
program cannot start, so that is the floor; anything above it is a leak.

**All traces are delivered, in full.** How many a method reads is its business.
*(One published method reads only the worst 30 traces plus the best 6 out of 178.
That choice is part of its method, so the platform must not make it.)*

**`states/` is what makes a parent-selection rule expressible at all.** Without
it a method is handed only the incumbent's files, and every rule whose
contribution is *which previous state to build on* collapses to the same
hill-climb: SICA's confidence bound, DGM's archive, HyperAgents' child-count
penalty and RRSI's frontier become indistinguishable from the plain loop, while
each still claims in its own paper to be doing something else. The window is the
most recent `STATES_KEPT` (12) rounds; `index.json` records the window, how many
states exist in total, and each kept state's `sha`, file count and score. A state
that could not be materialized is **absent from the index** rather than listed —
a method that trusts the index and finds an empty directory would report a
failure the platform caused.

`_harnessgrad/` is the framework<->method channel and is gitignored: if it were
committed, a no-op round would mint a new sha and the control curve would appear
to move.

### 4.8 Where that directory lives, and what is checked

The harness working tree and the method workspace are **outside the platform tree**
(`--work-root`, default `../harnessgrad_work`). They used to be `runs/<id>/workspace`,
which is two `..` from the driver, the scorer and `.env`. Measured from the old
layout, a harness could read `driver.py`, read `.env`, and write anywhere in the
platform directory. From the new one, the `..` climb reaches nothing.

What that does and does not buy, because the difference matters when reading a score:

| claim | status | mechanism |
| --- | --- | --- |
| the harness cannot reach the platform at all | **enforced** | it runs in its own mount namespace (`eval/containers/sandbox.py`); the platform is not on its disk |
| the subject cannot reach the platform by `..` | **enforced** | workspace lives outside the platform tree; a run refuses to start if they overlap |
| the harness cannot rewrite the platform | **enforced** | same namespace: `driver.py`, `harnessgrad/` and `eval/` are absent, so there is nothing to write to |
| the method cannot rewrite the platform | **detected** | the platform hashes its own files around every method call |

The first and third rows are new and are the reason the table is worth reading again:
the harness is no longer merely caught after the fact, it cannot get there. Measured
inside the namespace, `HarnessGrad` is an empty mount point: `.env`, `driver.py`,
`harnessgrad/`, `eval/`, `methods/` and every other run's workspace are unreachable -- not `EACCES`,
not present-but-refused, but not there. The
cost is one `bwrap` exec per task: **+11.1 ms** on a 67 ms process, which is **+0.53%**
against a ~2.1 s model call. No image and no container, so `docs/writing_a_harness.md`
§4's promise that the language and runtime are not specified still holds.

Paths are bound at **the same absolute path they have on the host**. A translated
path is a place where a harness's own trace stops matching the filesystem it ran on,
and the platform's job is to keep records, not to make rewriting them necessary.

Inside the namespace the harness's own tree is **read-only**, which is the one place
the sandbox narrows a contract rather than enforcing it. `evaluate()` runs a single
`repo` across the whole task set, so a harness that edited its own source while
answering task 1 would be a different program by task 2 -- the curve would claim one
harness was measured when two were.

A harness has two writable places during a task, and only those two:

* `--workdir` — the task's working directory and the process's cwd;
* `.state/` inside its own tree — for a harness that writes relative to itself
  (`HARNESSGRAD_STATE` names it). It is a **tmpfs**, so what lands there is per-task
  and vanishes with it. Its contents are gitignored and never move a sha.

Each is the **whole** of what persists for that task: private, fresh, and discarded
afterwards. Nothing carries
across tasks, deliberately. A platform-supplied directory that survived the task set
would let a score depend on storage the platform handed out rather than on what the
harness did, and the curve could not distinguish the two. Cross-session memory is
therefore a *task design* question: wrap the sessions into one task, and remembering
becomes part of the measured behaviour.

`--no-sandbox` exists for debugging an instrument that will not start. Curves from
the two modes are not comparable, and `run_meta.json` records which one produced a
run (`sandbox.active`), so a mixed directory of runs is readable rather than
ambiguous.

**Still a gap, and now written down rather than implied:** the sandbox constrains
the filesystem and nothing else. A subject keeps unrestricted network access and it
keeps the agent's API key, because a harness that cannot reach a model cannot be
measured at all. It can therefore send that key anywhere. Closing this needs egress
filtering or a key scoped to the benchmark's endpoint; neither is implemented, and
neither is free -- an egress allowlist has to name every endpoint a contributed
harness might use, and a mistake there presents as "the model call failed", which is
the hardest kind of sandbox bug to see because it looks like a harness defect.

Neither is a sandbox, and the platform does not claim to be one: a subject running
as your user can still open the platform by absolute path. What it cannot do is
change it and have the run continue. A mismatch prints `FAILING RUN`, names the
files that moved, and exits `3` — and no `curve.jsonl` is written, because a
curve that exists is a curve someone will plot. The only file under `runs/<id>/`
is `run_meta.json`, written before round 0 so that even a failed run says which
workspace holds its commits.

What is hashed is the referee, not the candidate: `driver.py`, `harnessgrad/`, `eval/`,
`ckpt/`, `data/`, `tools/`, `methods/`, `tests/`, `docs/`, `INTERFACE.md` and `.env` —
everything the platform reads to decide what a number means, plus the credentials
that paid for it. `base_harness/` is excluded on purpose: it is a candidate, and
every run copies it into a workspace before anything edits it.

The two checks answer different fears. The directory rule stops a *careless*
harness from wandering into the referee, and it costs nothing. The hash stops a
*deliberate* one from rewriting it, which is the fear worth spending on, because a
subject that means to cheat is exactly the subject the platform is built to
produce. Neither is a sandbox; both are cheap enough to run on every call, and
together they make "the score is the platform's opinion" a claim with a mechanism
behind it rather than a convention.

## 4.9 What nine adapters found, and the assumption they did not change

> **This section records a historical finding, not a current capability.** The
> replay route it describes is no longer maintained (see `legacy/adapters/README.md`).
> What outlives it is the *reading*: the boundary table below is why the platform
> records `editable_surface_touched` instead of accepting a declared mode, and the
> nine shapes are why a port has to say which of its rules mode A cannot express.
> Seven of these nine now have ports; MAC and Meta-Harness have none, because
> there is no rule in them to port.

Nine published methods were adapted. Every one of them hit the same wall from a
different direction:

| what the artifact is | method | why the platform cannot run it |
| --- | --- | --- |
| a module tree + its own runner | RRSI | no entrypoint; it is driven by harbor |
| variants in an archive | DGM | invoked by the method's container scaffold |
| per-iteration copies of its own source | SICA | runs under the method's benchmark runner |
| `gen_<id>/` + patches | HyperAgents | copied into a per-run container |
| a Python class | TTHE | needs TTHE's own `config.yaml` |
| declarations for an engine | AHE | needs `nexau` |
| an artifact file | MAC | needs the artifact's key and endpoint |
| a YAML | HarnessX | needs the `harnessx` package and a model config |
| **output only** | Meta-Harness | **its interface includes the sandbox protocol** |

**The platform's unit of exchange is a directory that can solve a task on its own,
and no published method's harness is that.** So this contract could have been
relaxed — accept an engine, a credential, a sandbox — and it was not, for one
reason: being able to run a harness standalone is what makes two methods
comparable at all. A platform that will run anything measures nothing.

That leaves an adapter exactly two jobs, with no third: **make the artifact
self-contained, or report honestly that it is not.** Both outcomes are recorded on
the curve point (`measured_by_platform` plus the reason), so a reader can always
tell which of the two they are looking at.

Meta-Harness is the end of that line: `run(self, instruction, environment:
BaseEnvironment, context: AgentContext)` and a contribution that executes a command
*inside* that sandbox. Materializing it would mean implementing the substrate the
harness is a harness for, which is not adaptation.

## 5. Budget

```python
Budget = {
    "rounds": int,               # framework-fixed, same for all Trainers
    "generation_tokens": int,    # cap on the Trainer's own model usage
    "wall_clock_s": int,         # cap on the whole round
}
```

**`rounds` is framework-fixed so curves are comparable; everything inside a
round is the Trainer's business.**

Budget overrun is not an error: the round is recorded with the overrun in
`cost`, and the loop continues. *Reason: silently truncating would make the
curve uninterpretable; recording it lets the reader see it.*

---

## 6. Dependencies (borrowed from the RISE boundary rules)

These are enforced by AST tests, not by convention.

**R1 — Trainers must not import each other.** Each Trainer knows the interface
and nothing else. *Reason: if Trainer B can import Trainer A, then adding a third
method tempts you to align them, and "methods are comparable" quietly stops being
true.*

**R2 — The framework must not import any concrete Trainer.** Concrete Trainers
register through a registry.

**R3 — Framework code must not import a harness.** Harnesses are data
(repositories), not libraries.

---

## 7. Repository layout

```
HarnessGrad/
├── INTERFACE.md            ← this file, frozen
├── driver.py               ← the entrypoint: argparse, wiring, the order refusals happen
├── harnessgrad/            ← the platform's implementation (not `_harnessgrad/`, below)
│   ├── identity.py         ← what a measurement says it is
│   ├── environments.py     ← what a task needs to run in; what could not be measured
│   ├── records.py          ← curve points, diffs, states, traces, verdicts, run_meta
│   ├── channel.py          ← what a method is shown, and where
│   ├── methods.py          ← calling an external method, keeping it out of the platform
│   └── loops/              ← the three schedules: eval_side, mode_a, mode_b
├── base_harness/
│   └── loop/               ← reference base harness: a minimal ReAct loop
├── methods/                ← methods live HERE, outside every harness
│   ├── protocol.py         ← the stdin/stdout contract
│   ├── noop/               ← control method: returns the base unchanged
│   └── echo_base/          ← reference method: one real edit
├── data/
│   ├── registry.py
│   └── <dataset adapters>
├── eval/                   ← scoring, CI, resumption-safe caching
├── ckpt/                   ← git-backed harness state management
├── tools/                  ← human-facing tooling (the ops layer; the UI is the SPA in web/)
├── runs/                   ← artifacts (gitignored)
└── tests/
    ├── quality/            ← R1/R2/R3
    └── …
```

`_harnessgrad/` (§4.7) is the framework→method channel **inside a run workspace**. It is not
`harnessgrad/`: that directory is the referee's own code, this one is what the referee hands
the subject. The two never appear in the same expression, and the underscore is the whole
difference on disk.

### 7.1 Why the implementation is a package and the entrypoint is a file

`driver.py` is what INTERFACE.md, `tools/serve_ui.py` and every recorded run's `argv` name,
so it stays where it is; the code behind it moved into `harnessgrad/`, one responsibility per
module. The reason is not tidiness. A single 2,640-line file made three things hard that this
platform cannot afford to have hard:

* **A rule could not be tested without a program.** `_identity()` read a module global that
  `main()` assigned into, so "what does this curve point claim about its improver" was only
  answerable by running a whole run. It is now an argument, and the caller is the one place
  that resolves it.
* **A move could not be made safely.** The split was computed from the source (`ast` for the
  bodies, `symtable` for the free variables each new module needs), not retyped, and the
  check found two live bugs in mode B that no test had reached: a `run_dir` that did not
  exist in that scope, and a channel archive written to `runs/<id>/<id>/method_channel/`
  because the parameter holding the run directory was named `runs_dir`.
* **The dependency direction could not be seen.** The layering is now stated once, in
  `harnessgrad/__init__.py`, and a module imports only from the layers below it, so
  `identity` can be exercised without docker and "what a number means" does not depend on
  how it was produced.

The contract this file fixes is unchanged: the same record formats, the same
`PLATFORM` semantics (with `harnessgrad/` added to it — the implementation is the referee),
the same top-level directory names, and the same `driver.py` command line.

**Two open decisions, marked rather than silently chosen:**

* `base_harness/loop/` is the reference base. Whether an *external* harness must be
  copied in or can be referenced in place is **not yet decided**.
* `eval/` must cache by `(harness_sha, task_ids, trials)`. *Reason: re-evaluation
  is the dominant cost, and a caching evaluator is what makes repeated
  candidate proposals affordable.*

---

## 8. What this design deliberately does not decide

Listed so they are not mistaken for oversights:

1. **Which dataset.** The interface is dataset-agnostic; the adapter decides.
   A dataset module exposes `load() -> (tasks, scorable)`, and **may** expose
   `SPLIT` (§2.3). Scoring lives in `eval/`, never in the dataset — a dataset
   that scores itself cannot be swapped without re-auditing the scorer.
2. **The metric.** `score` is whatever the dataset adapter returns, as long as
   it is defined on a task set and supports pairing.
3. **Whether Trainers may run extra cheap validation** (compiling, smoke tests).
   The first version does not budget for it; this is the most likely v0.2 change.
4. **Contamination.** Whether the agent model has seen the benchmark tasks is not
   detectable from inside the framework and is not yet a required field —
   *flagged as the largest known threat to the validity of any curve this
   produces.*

### 4.2.0 The method sees **all** the training history, and the record stays linear

The channel used to stage the last **12** rounds of train traces and states, and that cap was
the platform's, not the method's: a search that cannot see its own early rounds cannot run the
rule it published. The cap is **off by default**. `HG_METHOD_HISTORY_ROUNDS=N` restores a
bound for a run that needs one, and the value is recorded (`round.json`'s `history_rounds`,
`states/index.json`'s `window`) so a reader knows what the method could see.

The reason the cap existed was *cost*, and the cost was real: each round's **archived
snapshot** (`runs/<id>/method_channel/round<N>/`) carried every earlier round's traces as a
fresh copy, so the record grew quadratically -- measured at ~2.1 MB of traces per round
(5 tasks x 3 trials): round0 3.9 MB, round1 6.1 MB, round2 8.2 MB, ~440 MB by round 20.

That is fixed where it belongs. Each snapshot is now built **from the previous snapshot**:
files start as hard links (same bytes, ~no cost) and only what this round actually rewrote is
re-copied (`traces/`, `tasks/`, `round.json`); history is already there by link. Two
preconditions, both true by construction and both written down: a snapshot is written once and
never modified, and every writer of the live channel **recreates it from scratch** each round
(`_stage_for_method` rmtrees it first) -- an in-place write would mutate the inode an older
snapshot points at, and that snapshot would silently become a record of the newest round. The
first version of the test did exactly that and caught it.

### 4.2.1 The evidence a method reads is now complete, indexed, and counted

A method is shown the round's traces, the per-task pages and an aggregate summary. Three
measured defects made that evidence reach it *lossy*, and each was invisible: the method saw
a healthy trajectory.

* **The summary counted the harness's interface events as nothing.** `local-floor-9`,
  `cancel-async-tasks`: `commands: 13`, `parse_errors: 0` -- and **ten of those thirteen
  commands executed the empty string**, because the model's reply repeated the `command` key
  and the parser kept the last (empty) one. The score was 0.0 and the summary said the
  harness was working. `loop_facts` now counts `empty_commands`, `duplicate_keys`,
  `context_trims`, `output_clipped_events`/`_chars` and `history_dropped` beside the fields it
  already had, and `_loop_summary` aggregates them for the round
  (`tasks_with_empty_commands`, `empty_commands_total`, ...), because the summary is the
  first thing a method reads.
* **The renderer threw away the diagnostic tail.** `render_trace` cut every line at 220
  characters; on that trace the real command was in the first 799 and the duplicate
  `, "command": ""` that replaced it was in the last 40. Replies now show head **and** tail
  with the omitted count, an executed empty command is rendered as
  `ran: (EMPTY COMMAND -- nothing ran)`, and the interface events have sentences instead of
  raw JSON. Accounting records (`harness_identity`, `usage`, `wall_clock_s`) no longer
  consume one of the `max_events` slots -- one of them was this platform's own addition.
* **Nothing said where to look.** The platform stages traces **whole** (verified byte-for-byte:
  1.76 MB of `gcode-to-text` reaches the method), but a tool-using improver would have to scan
  them to find the diagnostics. Each task now also gets `traces/<task>.index.json`: per record,
  its `kind`, byte offset, size and a one-line summary -- so "let the improver choose what to
  read" is cheap rather than theoretical. Nothing is removed.

And the noise band RRSI could not calibrate now can be. `methods/rrsi/run.py` said "delta
cannot be calibrated from per-trial rewards", which was true when written: the platform kept
one score per task per round. A point now carries `per_task_trials` and `score_std`, where
`score_std` is the platform's own population sd of the **run-level score across trials of one
measurement** -- which is `sd(S)` for a fixed harness, tasks and budget. Two independent
measurements have `sd(Delta S) = sqrt(2) * sd(S)`, so `delta = z * sqrt(2) * score_std` is
RRSI's own estimator fed with the platform's number. Measured (5 tasks, 3 trials):
`score_std = 0.094` -> **delta ~ 0.27**, which is the size of the re-measurement swing that
made a control arm look like it gained +0.200. `n_trials = 1` carries no `score_std` and the
fallback is used, with the reason in the record.

### 4.49.2 A CLI improver needs a model that emits **standard** `tool_calls`

`--improver codex` means "the improver edits the candidate tree through its own tools". That
only works if the model behind the CLI returns structured `tool_calls`, and this is a
property of the **endpoint**, not of the model's willingness: the model may call the tool
correctly and the wire may still hand the caller text.

Measured 2026-10-04, in this order:

* codex pointed at the local 9B **connected**, used 8,699 tokens, and did nothing. Its
  output was `<tool_call><function=exec_command><parameter=cmd>…`, and codex 0.160 requires
  structured calls on `wire_api = "responses"`. The run would have spent its whole budget
  producing nothing and looked like a weak method.
* The cause was entirely server-side: vLLM was started without
  `--enable-auto-tool-choice --tool-call-parser <name>`, so its `/chat/completions` answered
  `400: "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be
  set` and `/responses` returned a `message` with no `function_call`.
* With those flags on, the parser **must match the model's chat template**. `hermes` was
  wrong for this model (it expects `<tool_call>{"name":…,"arguments":…}</tool_call>`); the
  model emits the XML form, which vLLM 0.29 registers as `qwen3_xml` / `qwen3_coder`.
  Wrong parser, same silent nothing.
* With `qwen3_xml`, both paths return structured calls and codex drives the 9B end to end
  (it ran `echo -n "nine" > note.txt`; the file held exactly `nine`, 9,054 tokens).

Three things follow, and they are the platform's:

1. **`tools/serve_local_model.sh` starts the server with the flags** (parser overridable via
   `HARNESSGRAD_TOOL_PARSER`, default `qwen3_xml`; `none` to disable).
2. **`tools/probe_tool_calls.py` is the check** -- one tool definition, both wires, exit 0
   only when a structured call comes back. A model that fails it is not a worse model; it is
   a model that cannot drive an agent shell, and this is the only thing that distinguishes
   the two.
3. **The gate is at the door.** `driver.py` probes the endpoint a CLI improver's own config
   names, refuses the run when that endpoint cannot emit tool calls, and records the result in
   `run_meta.json` as `improver_tool_calls` + `improver_tool_calls_detail`. A **custom**
   provider declares a `base_url` and can be probed; a **hosted** one cannot, and then the
   result is `null` -- *unprobed*, which is a fact, never a silent pass. `HG_IMPROVER_SKIP_TOOL_GATE=1`
   overrides.

Two record facts changed with it: `tools/improver.py:resolve` now returns `kind` (the gate read
`improver["kind"]`, which did not exist, so the gate silently did nothing until a test that
expected a refusal got a completed run), and the resolved `model` is read from the config of
the **chosen** improver rather than the `default` entry -- `--improver codex` used to report
`model: deepseek-flash`, a model codex never touched.
