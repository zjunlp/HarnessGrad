# τ²-bench v0.1.0 — all 278 tasks, the release's own simulator and scorer

What this directory is: the **prepared payload** for τ² v0.1.0 and the code that runs one task of
it — a private half-duplex runtime, an actor CLI, a trusted verifier and the control. It is not a
second copy of the benchmark. What it splits is what the actor may see
(`cases/<domain>/<id>/public.json`: policy and tool schemas) from what only the trusted runtime and
the verifier see (`cases/<domain>/<id>/private.json`: the user's scenario, the task row and every
evaluation criterion).

The pin
-------
`sierra-research/tau2-bench` **tag `v0.1.0`**, archive sha256
`ddee4dc5655912c8a7c7306107714c1936c86a5c4884ff4c50f00d6c828c3377`. Only this tag publishes the
paper's pool in the obvious place (`tasks.json` **is** the base split: airline 50 + retail 114 +
telecom 114 = **278**); later trees add `split_tasks.json` and move the base out of it, which
changes *which* 278 tasks those are. The repository's own README says results below 1.0.1 are not
comparable with results at or above it, so τ³ is a **separate** adapter on a separate revision —
mixing the two would produce a number neither paper supports. The metric therefore carries the
release: `tau2.v0.1.0.ALL.binary`.

The split does not exist
------------------------
Every τ² tag publishes **no** train/test division: `tasks.json` is the whole 278-task pool. A hash
cut of it would not be the benchmark's held-out exam, so this adapter declares none
(`SPLIT_SOURCE = "none"`, `SPLIT = None`): a run covers all 278 tasks and `OFFICIAL_EVAL = None`
says the pool *is* the scored set.

Where the second party runs
---------------------------
τ² is dual control, and some gold actions belong to the **customer**: measured, 94 of telecom's 114
tasks carry `requestor: "user"` actions, none in airline or retail. Each task's `user_scenario` says
what the customer reveals *when*, so it can neither be in the agent's goal nor be scripted by the
agent.

So the simulator lives outside the agent's workspace. `relay.py` runs under the pinned 3.13 runtime
in a private trusted process, drives the release's own `UserSimulator` and the domain's tools, and
exposes one socket with four operations — `start`, `say`, `call`, `status`. The agent gets
`cli/tau2.py` and the public half. There is no batch operation: v0.1.0's step budget is one
assistant action per turn, and accepting several calls in one request would spend fewer steps than
the release counts.

The verifier replays the sealed episode through `evaluate_simulation(..., EvaluationType.ALL)`.
That evaluator runs **every** part — database or environment assertions, actions, communication and
NL assertions — and then multiplies the parts its reward basis names. Two consequences are counted
in `manifest.json` rather than assumed:

* `needs_judge` is true for **58 of 278** tasks (all 50 airline, 8 retail). The NL evaluator is
  called for any task with `nl_assertions`, even when the default basis `[DB, COMMUNICATE]` then
  multiplies its verdict out; those rows declare `VERIFY[...]["judge"]`.
* `user_actions` is true for **94 of 114** telecom tasks, and false everywhere else.

Preparation
-----------
`prepare.py` copies source and domain data and reads `tasks.json`; it needs the pinned tarball at
`<root>/_src/tau2-bench-v0.1.0.tar.gz` and the extracted tree at `<root>/tau2-bench-0.1.0`. It
imports `tau2` only under the isolated 3.13 runtime:

```bash
runtime/bin/python data/datasets/tau2_bench/prepare.py \
    --source <root>/tau2-bench-0.1.0 --target <root> [--reuse-bundle]
```

What the payload contains:

    manifest.json           schema, pin, per-file digests, per-task flags, counts (checked in)
    cases/<domain>/<id>/public.json   policy + business-tool schemas + user-tool schemas
    cases/<domain>/<id>/private.json  the full upstream task row — never copied into the workspace
    bundle/src              the pinned `tau2` package (runtime and verifier import it)
    bundle/data             the domain databases, policies and simulator guidelines, **without**
                            `tasks*` or `results*`, so a private scenario cannot be read from it
    runtime/                the Python 3.13 venv; `requirements.lock` pins what it holds

The bundle's own manifest is digest-pinned by `lib/bootstrap.py`, and the case manifest by the
adapter, so a hand-edited payload is refused rather than believed.

Controls
--------
Three released tasks, one per domain, both arms, with a deterministic transport fixture standing in
for the model (it is never a model, so the numbers prove reachability, not behaviour):

    python3 data/datasets/tau2_bench/control.py --oracle   # airline/8, retail/0, telecom escalation
    python3 data/datasets/tau2_bench/control.py --noop

The three defaults are chosen by property, and `tests/quality/test_tau2_bench_control.py` checks
each one: assistant-only gold actions, a gold action set that changes state, no `communicate_info`,
and no `NL_ASSERTION` in the basis (the fixture answers the NL judge with `{"results": []}`, which
the release reads as "nothing to grade", so it may not carry part of a score). Measured on this
machine, 2026-11-04:

    oracle arm     1.0 / 1.0 / 1.0     24 s
    no-op arm      0.0 / 0.0 / 0.0     21 s

What the tests pin
------------------
* `tests/quality/test_tau2_bench_inventory.py` — the manifest is the pinned one, two halves per task,
  the per-task flags, and a tampered manifest is refused.
* `tests/quality/test_tau2_bench_runtime.py` — the four operations, the two task identities the
  relay keeps apart, the judge declaration rule, the metric string, the 3.13 pin.
* `tests/quality/test_tau2_bench_official_scorer.py` — the real scorer under the real runtime, one
  gold episode and one empty episode per selected task; it skips where the payload is absent.
* `tests/quality/test_tau2_bench_control.py` — the control's selection properties.
* `tests/quality/test_dataset_controls.py` — runs both control arms.

Two upstream behaviours the verifier carries, both measured here:

* `evaluate_simulation` returns **1.0** when a reward basis yields nothing to check (`all([])`).
  `lib/scoring.py` refuses that instead: a task whose basis has no criterion is *unmeasured*, never
  a success.
* `DEFAULT_LLM_NL_ASSERTIONS` is a **module-level constant** bound at import, not a parameter, so
  the judge pin is bound before evaluation and recorded in the receipt; nothing here assumes a
  default.
