# Skill: configure a harness, a method and a benchmark

This is the procedure we follow ourselves, written so an agent — or a person who has never
opened this repository — can follow it end to end. Follow it in order. Each stage ends with a
check that the stage worked; do not start the next stage on a red check.

Use an active [platform environment](../getting-started.md) from the repository
root. Prepare the declared dependencies for the components you select.

---

## Stage 0 — what you are configuring

Three artefacts, three contracts, and they do not overlap:

| artefact | the question it answers | the file that defines it |
| --- | --- | --- |
| **harness** | how does an agent turn a task into an answer? | `base_harness/<name>/harness.json` + an entrypoint |
| **method** | how does the harness get edited between rounds? | method entrypoint and an optional experiment preset |
| **benchmark** | what is a task, and what does a score mean? | `data/datasets/<name>.py` (an adapter) |

The platform supplies the fourth thing — rounds, the git-managed candidate workspace, the
sandbox, the receipts — and never needs to know what your three do.

---

## Stage 1 — a harness

**1.1 Copy the closest working example.** `base_harness/loop` is a complete harness in one
file. `base_harness/miniagent` includes a native runtime and a managed CPython 3.12
dependency lock. Copy the selected source to `base_harness/my-harness`.
For a managed Harness, follow its guide to prepare dependencies for the copy.

**1.2 Edit the copied `harness.json`.** Set `name` to `my-harness` and
choose a version for your copy. Keep the source's `entrypoint` and `backend`
unless your implementation changes them. Preserve `runtime`, `install` and
their referenced lockfiles for a managed Harness.

Keep the source's `env_kinds` for an unchanged copy. MiniAgent declares
`["files"]`. Update the declaration when you change and test the supported
environments. `files` uses a task filesystem under Bubblewrap, and `exec`
uses the task's Docker container.

**1.3 Implement the [entrypoint and output contract](../writing_a_harness.md).**
Accept `--task task.json --workdir <dir>`, follow the task's submission
instructions and record the trajectory in `trace.jsonl`. The platform scores
the submission.

**1.4 Check readiness.** The table reports prerequisites. Step 1.5 exercises
the entrypoint.

```bash
harnessgrad run --config experiments/quickstart.json --agent-backend mock \
  --set harness=my-harness --check-only
```

**1.5 Run the mock demo** to exercise the Harness and scoring path. Replace
the example run ID with a fresh one for each attempt.

```bash
harnessgrad run --config experiments/quickstart.json --agent-backend mock \
  --set harness=my-harness --set run_id=my-harness-demo --improver none --fail-on-incomplete
```

**1.6 Board row.** `python3 tools/contribute.py harness --name my-harness --path base_harness/my-harness --contributor <handle>`.

---

## Stage 2 — an RSI method

**2.1 Follow the [method interface](../bring-your-own-method.md).** The method
reads a JSON request on stdin, uses staged study evidence, and writes a
trajectory of candidate Harness directories under its private workspace.
The platform measures candidates and supplies the results on later calls.

**2.2 Start from a real one.** `methods/echo_base/run.py` is the minimal reference;
`methods/dgm/run.py` is a real port with a selection rule; `examples/method-template/run.py` is
the skeleton with the request/response fields written out.

**2.3 Declare the preset.** `experiments/method-<name>.json` names the dataset, harness, side,
sampling and round count. Use the JSON key `method` for the method name
(e.g. `echo_base`) or a command with an absolute script path. The corresponding
CLI flag is `--method-entrypoint`. Keep `rounds` and `trials` small for
the first wiring check.

**2.4 Check readiness.** Preflight checks entrypoint availability. Use a mock
run to verify its arguments and request/trajectory exchange.

```bash
# Set method_config to the path of your experiment preset.
harnessgrad run --config "$method_config" --check-only
```

**2.5 Exercise the exchange with a no-model method.** The mock solver uses
scripted replies. Use the [template smoke](../bring-your-own-method.md#start-with-a-working-process-boundary)
for a method that makes no model calls. With a self-contained no-model preset,
use a fresh run ID and request one improvement round.

```bash
# Set method_run_id to a fresh ID for this attempt.
harnessgrad run --config "$method_config" --agent-backend mock \
  --set rounds=1 --improver none --run-id "$method_run_id" --fail-on-incomplete
python3 tools/analysis/run_summary.py "runs/$method_run_id"
```

**2.6 Board row.** `python3 tools/contribute.py method --name my-method --preset experiments/method-my-method.json --contributor <handle>`.

---

## Stage 3 — a benchmark

**3.1 Write the adapter** `data/datasets/<name>.py`. Follow the
[dataset onboarding contract](../dataset-onboarding.md#2-where-everything-lives)
for `KIND`, `load()`, `downloads()` and task declarations in `ENV`,
`SETUP` and `VERIFY`. Choose a supported verifier for the official scorer.

**3.2 Declare the deviations.** If your metric is not the paper's, say so in the same file next
to the metric and in `DATASETS.md`; the adapter's own docstring is where a reader looks first.
Refusing to measure is allowed: a benchmark whose metric you cannot reproduce is declared
refused with the missing piece named, never approximated under the benchmark's own name.

**3.3 Lint and catalogue.**

```bash
# Set dataset_name to the registered adapter name.
python3 tools/dataset_lint.py --dataset "$dataset_name"
python3 tools/datasets_doc.py --write      # regenerates DATASETS.md from the declarations
python3 tools/datasets.py --check --dataset "$dataset_name"
```

**3.4 Run the [adapter controls](../dataset-onboarding.md#1-the-five-gates).**
Record oracle and no-op outcomes, verify scorer parity, and check that the
adapter classifies invalid inputs and execution failures as declared.

**3.5 Board row.** `python3 tools/contribute.py benchmark --name my-bench --adapter data/datasets/my_bench.py --contributor <handle>`.

---

## Stage 4 — a measured result (optional)

A result is admitted with a receipt, not with a number. `tools/contribute.py result` asks for
the benchmark, the metric, the score, the method and harness it was measured with, the 40-hex
tree or commit id of the candidate, and a receipt committed in this repository. There is no
target score: the entry exists so someone can ask you about it, and the receipt is what they
read.

---

## What a reviewer will look for

Not a score. Four things:

1. the readiness check (`--check-only`) passes in your pull request description;
2. `python3 tools/contributors.py --check` passes, so the drawing matches the record;
3. no file outside your contribution changed, and `runs/` is not committed;
4. one sentence per artefact saying what it does, in English, in the file that defines it.

If a stage fails and the message does not name a file, read the check's detail column first: it
names the requirement, not the fix.
