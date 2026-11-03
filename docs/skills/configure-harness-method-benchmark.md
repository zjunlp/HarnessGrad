# Skill: configure a harness, a method and a benchmark

This is the procedure we follow ourselves, written so an agent — or a person who has never
opened this repository — can follow it end to end. Follow it in order. Each stage ends with a
check that the stage worked; do not start the next stage on a red check.

Everything here is standard library plus git. Commands assume you are at the repository root
and inside the virtual environment from the README's Installation section.

---

## Stage 0 — what you are configuring

Three artefacts, three contracts, and they do not overlap:

| artefact | the question it answers | the file that defines it |
| --- | --- | --- |
| **harness** | how does an agent turn a task into an answer? | `base_harness/<name>/harness.json` + an entrypoint |
| **method** | how does the harness get edited between rounds? | `experiments/method-<name>.json` + a program |
| **benchmark** | what is a task, and what does a score mean? | `data/datasets/<name>.py` (an adapter) |

The platform supplies the fourth thing — rounds, the git-managed candidate workspace, the
sandbox, the receipts — and never needs to know what your three do.

---

## Stage 1 — a harness

**1.1 Copy the smallest working example.** `base_harness/loop` is a complete harness in one
file; `base_harness/miniagent` is the smallest. Copy, do not edit in place.

**1.2 Write `harness.json`.** It must declare:

```json
{
  "name": "my-harness",
  "version": "0.1.0",
  "entrypoint": "agent.py",
  "backend": "cli",
  "env_kinds": ["files", "exec"]
}
```

`env_kinds` is a promise: `files` means your harness runs under `bubblewrap` with the task's
files, `exec` means it runs inside the task's Docker container. Declare only what you have
tested — the platform refuses a pairing you did not declare instead of guessing.

**1.3 Implement the entrypoint contract.** Your program is started as
`<entrypoint> --task task.json --workdir <dir>` and must write `answer.txt` in the workdir, plus
`trace.jsonl` if you want the round's trajectory to be readable. The *task* decides what
`answer.txt` should contain; your harness must not score anything.

**1.4 Check it.** The platform will run this for you and print a table; every `yes` row is a
requirement:

```bash
HG_AGENT_BACKEND=mock harnessgrad run --harness-path base_harness/my-harness \
  --config experiments/quickstart.json --check-only
```

**1.5 Prove it on one round** with the mock backend. A harness that cannot complete the demo
fixture will not survive a real benchmark:

```bash
HG_AGENT_BACKEND=mock harnessgrad run --harness-path base_harness/my-harness \
  --config experiments/quickstart.json --set run_id=my-harness-demo --improver none
```

**1.6 Board row.** `python3 tools/contribute.py harness --name my-harness --path base_harness/my-harness --contributor <handle>`.

---

## Stage 2 — an RSI method

**2.1 Pick the interface.** A method is a program that receives one request (JSON on stdin) and
answers either an edit or a decision not to edit. The request carries the round index, the
trajectory of the last round, the current harness directory, and the workspace to write into.
The reply names the new harness directory and whether the method accepted the round.

**2.2 Start from a real one.** `methods/echo_base/run.py` is the minimal reference;
`methods/dgm/run.py` is a real port with a selection rule; `examples/method-template/run.py` is
the skeleton with the request/response fields written out.

**2.3 Declare the preset.** `experiments/method-<name>.json` names the dataset, harness, side,
sampling, round count and your `method_entrypoint` (an absolute command line is safest). Keep
`rounds` and `trials` small at first — a method that is wrong is wrong in two rounds.

**2.4 Check the preset starts.** This catches the most common failure, an entrypoint that needs
an argument the preset cannot supply:

```bash
harnessgrad run --config experiments/method-<name>.json --check-only
```

**2.5 One round, no model.** With `HG_AGENT_BACKEND=mock` the round is scored by scripted
replies; that is enough to prove the plumbing (request → edit → new tree → scored round):

```bash
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/method-<name>.json --set rounds=1 --improver none
python3 tools/run_summary.py runs/<run-id>
```

**2.6 Board row.** `python3 tools/contribute.py method --name my-method --preset experiments/method-my-method.json --contributor <handle>`.

---

## Stage 3 — a benchmark

**3.1 Write the adapter** `data/datasets/<name>.py`. It declares `KIND = "dataset"` and provides:

- `tasks(...)` or a loader that yields task records;
- `materialize(task, workdir)` — put the task's files where the harness will see them;
- a scorer, either `score(answer, task)` or a command; the official scorer is preferred;
- `downloads()` when the bytes are external, so `tools/datasets.py --fetch` can obtain them.

**3.2 Declare the deviations.** If your metric is not the paper's, say so in the same file next
to the metric and in `DATASETS.md`; the adapter's own docstring is where a reader looks first.
Refusing to measure is allowed: a benchmark whose metric you cannot reproduce is declared
refused with the missing piece named, never approximated under the benchmark's own name.

**3.3 Lint and catalogue.**

```bash
python3 tools/dataset_lint.py --check
python3 tools/datasets_doc.py --write      # regenerates DATASETS.md from the declarations
python3 tools/datasets.py --check --dataset <name>
```

**3.4 Prove it declaratively.** A three-step control on a handful of tasks: the oracle must
score 1.0, the no-op must not, and an invalid answer must be `unmeasured` rather than zero.
`tests/quality/test_dataset_controls.py` shows the shape; `python3 tools/datasets.py --check`
tells you whether the bytes are where the adapter thinks they are.

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

1. the staged check (`--check-only`) passes in your pull request description;
2. `tools/leaderboards.py --check` passes, so the table matches the record;
3. no file outside your contribution changed, and `runs/` is not committed;
4. one sentence per artefact saying what it does, in English, in the file that defines it.

If a stage fails and the message does not name a file, read the check's detail column first: it
names the requirement, not the fix.
