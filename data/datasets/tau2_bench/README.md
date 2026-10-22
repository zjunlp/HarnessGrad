# τ²-bench v0.1.0 — telecom payload

What this directory is: the **prepared payload** for the τ² v0.1.0 telecom domain, and the script
that makes it. It is not a second copy of the benchmark; it is the split between what the actor may
see (`cases/<i>/public.json`) and what only the trusted runtime and the verifier see
(`cases/<i>/private.json`: the user's scenario, the ticket, and every evaluation criterion).

The pin
-------
`sierra-research/tau2-bench` **tag `v0.1.0`**, archive sha256
`ddee4dc5655912c8a7c7306107714c1936c86a5c4884ff4c50f00d6c828c3377`. Only this tag publishes the
paper's base split as `tasks.json` (airline 50 + retail 114 + telecom 114 = 278); later trees add
`split_tasks.json` and move the base out of the obvious place, and the repository's own README says
results below 1.0.1 are not comparable with results at or above it. τ³ is therefore a **separate**
adapter on a separate revision — mixing the two would produce a number neither paper supports.

Scope: **telecom only, 114 tasks.** airline and retail are not here on purpose. All 50 airline tasks
carry `nl_assertions` (they need a judge), and 7 airline tasks plus 1 retail task have nothing
actionable to assert — a patient do-nothing agent scores 1.0 on them. Telecom has 114/114 tasks with
`env_assertions`, zero `nl_assertions`, and no vacuous task, which makes it the one domain this
adapter can measure without a judge and without inheriting upstream's empty-result behaviour.

The split does not exist
------------------------
Every τ² tag from `v0.1.0` through `v0.2.0` publishes **no** train/test division: `tasks.json` is the
whole 278-task base split, and `split_tasks.json` first appears in the **τ³** release (v1.0.0 /
v1.0.1, and again in v2.0.0), which also is not comparable with v0.1.x. Checked by listing the git
tree of every tag, not assumed.

So this payload declares no split either: **all 114 telecom tasks are study material**
(`counts: {train: 114, dev: 0, eval: 0}`, `split_rule.kind: "none"`). An earlier version hash-cut
them 68/12/34; a hash cut of a benchmark's evaluation set is not that benchmark's held-out exam, and
inventing one is exactly what this directory is supposed to avoid. If the official 278-task number is
ever wanted it is an explicit full-set evaluation, not a split made here.

Preparation
-----------
Stdlib only, on the platform's Python 3.12; it copies source and reads `tasks.json`, and never
imports `tau2` (which needs 3.13 and the isolated runtime):

```bash
python3 data/datasets/tau2_bench/prepare.py --root /mnt/20t/xubuqiang/datasets/tau2-bench
```

It needs the pinned tarball at `<root>/_src/tau2-bench-v0.1.0.tar.gz` and the extracted tree at
`<root>/tau2-bench-0.1.0`, refuses a non-fresh root, and prints the task count, the side sizes and
the manifest digest.

What the payload contains
-------------------------
    manifest.json          schema, pin, per-file digests, split rule, counts, and one entry per task
    cases/<i>/public.json  task id + domain — copied into the task workspace
    cases/<i>/private.json the full upstream task row — never copied into the workspace
    bundle/src             the pinned `tau2` package (the runtime and the verifier import it)
    bundle/data            the telecom databases, policy and user-simulator guidelines, **without**
                           `tasks*` or `results*`, so a private scenario cannot be read from it

Per-task counts (`n_actions`, `n_env_assertions`, `n_nl_assertions`, `n_communicate_info`,
`reward_basis`) are recorded in the manifest, so a claim about what the metric needs is a field
rather than a comment.

Status
------
Payload: done and pinned. **Runtime: written and proven outside the platform** — the half-duplex
relay (`start/say/call/calls/status` over the platform tool transport) and the trusted verifier that
replays the sealed episode through the official `EvaluationType.ALL` scorer. Two smokes, run with the
pinned 3.13 runtime and the platform's own `eval/service/tool_transport.py`:

* one episode on `tau2_bench/telecom/4`: actor `say` twice, then `call get_customer_by_phone`; the
  official user simulator answered through the platform model socket (2 model calls), the business
  tool executed and returned the customer record, and the episode sealed with 6 messages in the
  official order `assistant / user / assistant / user / assistant / tool`;
* scoring that sealed episode: `measured: true`, official reward **0.0**, actionable basis
  `['ENV_ASSERTION']`. The scripted actor never sent a stop signal, so the termination was
  substituted by the official early-termination rule (`max_steps`) and the evidence records
  `termination_substitution` instead of pretending the budget was hit.

Still to do, and the refusal stays until it is done: `data/datasets/tau2_bench.py` must grow
`SETUP`/`VERIFY`/`SPLIT` around these pieces, then the registry row, `tools/dataset_classes.py`, the
aggregation policy, and the refusal documents and tests move together (the four refusals become
three). Then a driver-level single-task run, and the 114 tasks.

Two upstream behaviours the verifier carries, both measured here:

* `DEFAULT_LLM_NL_ASSERTIONS` is a **module-level constant** bound at import
  (`tau2/evaluator/evaluator_nl_assertions.py`), not a parameter; upstream's default is
  `gpt-4o-mini`. The judge pin is bound before evaluation and must be recorded in the receipt.
  Telecom needs no judge; a domain that later does must not silently skip it.
* `evaluate_simulation` returns **1.0** when the reward basis yields nothing to check (`all([])`) —
  that is how a do-nothing airline episode passed. The scorer refuses instead: a task whose basis has
  no non-empty criterion is `unmeasured`, never a success.
