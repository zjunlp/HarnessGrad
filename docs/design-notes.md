# Design notes: why HarnessGrad is shaped this way

This is the long-form companion to [`../README.md`](../README.md). The README covers
what the platform is, how to install it and how to run it. This file carries the
reasoning behind its shape, the defects that building it caught, the operating rules a
user must not get wrong, and what is still open.

## The problem this exists for

Every harness-evolution method reports a final score delta. None of them can say
what that delta is *made of*, and none of them report it on a curve a reader can
compare against another method's. The field has no shared training signal.

HarnessGrad defines one: a base harness, a fixed task set, a sampling loop, and
a curve point that records the harness state, the two models involved, the score
with an interval, and the compute spent to get there.

This repository does not propose an RSI method. It ships runnable reference methods, but the
contribution is the instrument that decides whether any of them worked — so it is not
competing with the methods it measures.

## The division of labour

| Platform owns | The harness (and its method) owns |
| --- | --- |
| which tasks, how many rounds | reading whatever it wants from the traces |
| running the harness, collecting traces | deciding how to modify the harness |
| **all scoring** | — |
| every curve point, ckpt and token | — |

Scoring is not delegable. A subject that scores itself makes every cross-method
comparison meaningless. See [`../INTERFACE.md`](../INTERFACE.md).

## Where a method lives

**A method is external to the harness.** It is handed a pristine copy of a base
harness and produces candidate harnesses; the platform then measures those
candidates itself, on its own fixed task set, with its own scorer.

```
        base harness (yours, or ours)
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
   method A   method B   control      ← all outside the platform's harness
     └───────────┼───────────┘
                 ▼
      candidate harnesses
                 │
                 ▼
     platform measures every one      ← same tasks, same scorer, for all methods
```

The first design put the method *inside* the harness. That made it inseparable
from one artifact: it could only ever improve the harness it shipped with, and two
methods could not be compared because each faced a different starting point.
Keeping methods external is what makes "the same method applied to any base
harness" possible at all — and it is why a method can no longer be part of what is
being measured.

### Two execution modes

They differ only in who owns the schedule:

* **Mode A — platform-driven.** The platform owns the loop and calls the method
  once per round for one step. Maximum comparability.
* **Mode B — method-driven.** The method runs its own search loop in its own
  process and returns a whole trajectory (it writes a trajectory file; the platform
  resolves and scores the states it names). For methods that already exist and must
  not be re-wired in order to be measured — RRSI owns an annealed 20-round budget,
  DGM an archive.

Both write the same `curve.jsonl`, so curves from either land on one axis.

## The old in-harness method design, and why it went

The method lives **inside the harness repository**, as the implementation of two
entrypoints the harness declares in `harness.json`:

```json
"improve_entrypoint": ["python", "-m", "method.improve"],
"accept_entrypoint":  ["python", "-m", "method.accept"]
```

The platform invokes them as **subprocesses** — never imports. That is not a
style choice: an imported module is frozen at load time, and the thing this
platform exists to measure is a harness that **rewrites its own improvement
mechanism**. Importing does not complicate that case, it makes it
unrepresentable.

Because the method lives inside the artifact under evolution, the boundary
becomes measurable instead of declarable. Every curve point records
`editable_surface_touched`, read from `git diff`:

```
control method (returns the base)      touched: []                score 0.00
a real method  (edits the harness)     touched: ['agent.py']      score 1.00
```

`"method/accept.py"` appearing there would mean the method rewrote its **own
acceptance rule** — the line most published systems state they do not cross. So
the platform has no "real RSI" mode: a declared mode would be a self-report, and
the genuine/pseudo distinction is a reading, not a setting.

## Harness dependencies

A harness that needs a package its task image does not have must say so in `harness.json`:

```json
{"install": "requirements.txt"}
```

Then configure it **once, per harness** — not per task, and not per benchmark:

```bash
python3 tools/configure_harness.py --harness base_harness/loop --dataset terminal_bench
python3 tools/configure_harness.py --harness base_harness/loop --list   # what is built
```

That resolves the dependencies for every Python version the dataset's images use and
places them in an overlay outside every image, which runs then mount read-only with
`PYTHONPATH`. Measured for Terminal-Bench: **4 builds, ~112 MB, about two minutes**,
against 89 derived images and ~3.7 GB for the alternative. The image's own interpreter
still runs the harness, so §2.5.3 is untouched and no task image is modified.

Each build is verified by importing what it installed, and every curve point records
which interpreter ran the harness and where its dependencies came from
(`env.harness_runtime`). A run whose harness declares dependencies but has none
configured says so at the door and names the command above — it does not quietly report
`score 0.000`.

## Where a run's two halves live

Two locations, deliberately not the same one:

| | where | why |
| --- | --- | --- |
| the **record** | `runs/<run-id>/` | `curve.jsonl` and `run_meta.json`; what happened, kept with the platform |
| the **working tree** | `../harnessgrad_work/<run-id>/workspace/` | the harness copy and every checkpoint commit; outside the platform, so a harness cannot reach the driver or `.env` by `..` |

The working tree is where the git history of every checkpoint lives, so it is not
disposable until the run is exported (`tools/export_harnesses.py runs/<run-id>`).
`--work-root` moves it; the driver refuses to start if it would land inside the
platform. `runs/<id>/run_meta.json` records the path, so the export tool does not
have to guess.

Each task's harness runs inside a mount namespace, so it cannot see the platform at
all. `--no-sandbox` turns that off for debugging; the two modes are not comparable,
and `run_meta.json` records which one a run used. `bwrap` must be installed
(`apt install bubblewrap`), or the driver refuses to start rather than quietly
running without it.

## Operating rules a user must not get wrong

**Sampling.** Declaring runtime policies requires trusted backend observations. Missing
enforcement evidence remains blocked and invalid/unmeasured, never an ordinary zero
score; changing sampling or using `--no-sandbox` does not satisfy those gates. The CLI
default is staged `diagnostic`, which requires `--policy-evidence`, one task, one trial,
one job and zero improvement rounds. General sampling (`all`) is not production
authorization, and the local demo examples in the README explicitly use it. Production
diagnostics and pilots remain subject to the staged pilot policy in
[`../AGENTS.md`](../AGENTS.md) and to dataset gates.

**Resource and network policy.** Ordinary entrypoints add no universal
filesystem-quota or backend-observer prerequisite. Existing declared
resource/network policy refusals still apply, including to `exec` tasks; a local GPU
endpoint does not satisfy those gates. `--no-sandbox` is an explicit debugging
decision, never an automatic fallback, and retains an `exec` task's container
boundary. Source review and mocked tests establish neither effective sandbox/quota
enforcement nor production readiness.

The [ordinary-run receipt proposal](task-237-ordinary-run-receipt.md) defines how local
results can record missing or unknown filesystem quotas without quota administration,
while retaining sandbox facts, metric identity and explicit non-production labels. It is
a docs/test contract; runtime integration is pending.

**Optional infrastructure.** Project quotas and a dedicated storage volume are not
prerequisites for ordinary local execution. The
[quota/backend checklist](filesystem-quota-backends.md) describes an optional enhancement
for formal deployments that need verified per-run byte/inode bounds. Local runs without
that evidence cannot claim those guarantees; existing gated diagnostic/pilot requirements
still apply.

## What building it caught before any paid evaluation

Defects the skeleton caught before any paid evaluation was wired in — each is the
kind that would otherwise have been read as a result:

1. **The framework's own channel leaked into the checkpoints.** Staging traces
   under `_harnessgrad/` inside the workspace meant a no-op Trainer still minted
   a new sha every round, so the control curve appeared to move. Fixed by
   gitignoring the channel.
2. **`generation_tokens` was hardcoded to zero.** That is exactly the reporting
   gap this framework criticises other work for. Now a declared field.
3. **A relative repo path silently zeroed every score.** The harness could not
   be launched from the sandbox cwd, and the failure read as "the method did
   nothing" rather than "the instrument is broken".
4. **A harness could read the platform, and a method could rewrite it.** The
   working tree used to sit at `runs/<id>/workspace`, two `..` from the platform,
   `eval/` and `.env`. Measured, not assumed: a harness could read all three and
   write anywhere in the platform directory. Three fixes, in increasing strength:

   * the working tree moved outside the platform (`--work-root`, default
     `../harnessgrad_work`), and the driver refuses to start if the two overlap;
   * the platform hashes its own files around every method call, so a method that
     edits its own scorer fails the run — `FAILING RUN`, the paths named, no
     `curve.jsonl`, exit `3`;
   * the harness now runs in its **own mount namespace** (`eval/containers/sandbox.py`), where
     `HarnessGrad/` is not present at all. Measured from inside: `.env`,
     `driver.py`, `harnessgrad/`, `eval/`, `methods/` and other runs' workspaces do not exist —
     the path is an empty mount point, not a refused read. Cost: **+11.1 ms** per task, +0.53% against a model call, and
     no image, so a harness may still be written in any language.

   The second is detection and stays: it is now what protects the platform from the
   *method*, which still runs as an ordinary subprocess. The third is enforcement.

   One contract narrows, and it is worth knowing why: inside the sandbox the code in
   the harness's own tree is **read-only**. The same copy scores every task in the
   round, so a harness that rewrote `agent.py` while answering task 1 would be a
   different program by task 2 and the curve would report one harness where two ran.

   A harness still has two writable places: `--workdir` (the task's cwd, fresh per
   task) and `.state/` inside its own tree (a per-task tmpfs, gitignored, for harnesses
   that write relative to themselves; `HARNESSGRAD_STATE` names it). Verified in the
   sandbox: writing `.state/cache.json` succeeds, rewriting `agent.py` raises
   `OSError`, and neither the host tree nor the sha moves.
   What is hashed is the referee (`driver.py`, `harnessgrad/`, `eval/`, `ckpt/`,
   `data/`, `tools/`,
   `methods/`, `tests/`, `docs/`, `INTERFACE.md`, `.env`), never `base_harness/` —
   that is the candidate, and every run edits a copy of it.

   `tools/plot_curve.py` labels each run's isolation and **refuses to silently mix
   them**: runs made under different sandbox plans, or before the sandbox existed,
   put numbers on the same axis that were produced under different rules. It says so
   and exits non-zero.

   **Known gap:** the namespace constrains the filesystem and nothing else. A
   harness keeps the network and the agent's API key, because it cannot run without
   them, so it could exfiltrate that key. Closing it needs egress filtering; see
   INTERFACE.md §4.8. `--no-sandbox` exists for debugging, and `run_meta.json`
   records which mode produced a run.

## Open decisions

Listed so they are not mistaken for oversights:

* which dataset;
* the metric definition beyond "defined on a task set and pairable";
* whether Trainers get a budget for cheap pre-validation;
* **contamination** — whether the agent model has seen the benchmark tasks is not
  detectable from inside the framework, and it is the largest known threat to the
  validity of any curve this produces.
