<p align="center">
  <img src=".github/assets/logo.svg" alt="" width="76">
</p>

<h1 align="center">HarnessGrad: Harness as a Continual Learner</h1>

<p align="center"><strong>A harness that improves is a learner, so what it <em>keeps</em> is measured as carefully as what it gains.</strong></p>

<p align="center">
  <a href="https://zjunlp.github.io/HarnessGrad-Hub/"><img src="https://img.shields.io/badge/website-live-brightgreen.svg" alt="Website"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10 or newer">
  <img src="https://img.shields.io/badge/runtime%20dependencies-none-brightgreen.svg" alt="No third-party runtime dependencies">
</p>

<p align="center">
  🌐 <a href="https://zjunlp.github.io/HarnessGrad-Hub/"><b>Website</b></a> ·
  🚀 <a href="https://zjunlp.github.io/HarnessGrad-Hub/getting-started/"><b>Quickstart</b></a> ·
  📊 <a href="https://huggingface.co/datasets/Xubqpanda/HarnessGrad"><b>Data</b></a> ·
  📜 <a href="INTERFACE.md"><b>Contract</b></a>
</p>

---

HarnessGrad measures harness self-improvement round by round instead of a single before/after delta: the platform owns the tasks, the sandbox, the scoring and the record, and a **method** is an external program that returns a candidate harness which the platform then measures itself.

The unit is a **plasticity/stability pair** — what a round gains, and what it silently breaks — written as a curve another method can be compared against. We do not propose improvement methods; we build the instrument that decides whether one worked.

## 📦 Install

`bubblewrap`, `git`, `python3 >= 3.10`; the platform itself has **no third-party Python dependencies**.

```bash
./install.sh --check      # verify only, change nothing
./install.sh              # install what is missing, then verify
```

`bwrap` being installed is not the same as a working sandbox, so `install.sh` builds a real namespace and looks for itself inside it, and refuses to call the machine ready if that fails.

## 🚀 Run it

Three steps, no model calls until step 3.

### 1. Is this machine ready? (creates nothing, spends nothing)

```bash
python3 -m pip install -e .          # or: ./install.sh    (bubblewrap + git + python3)
harnessgrad run --config experiments/quickstart.json --check-only
```

```
check     req  result  detail
python    yes  ok      python 3.12.4
git       yes  ok      git version 2.34.1
bwrap     yes  ok      /usr/bin/bwrap: namespace builds (bubblewrap 0.6.1)
dataset   yes  ok      gsm8k: 8792 tasks
harness   yes  ok      <checkout>/base_harness/loop (cli, entrypoint agent.py, env_kinds files, exec)
writable  yes  ok      runs root: runs can be created; work root: ../harnessgrad_work exists and is writable
disk      no   ok      .: 93.1 GB free (a run needs about 5 GB)
ready: every required check passed
```

*(trimmed: the `harness` row names your checkout's absolute path, and the free-space figure is
this machine's; a detail longer than the table's 96-column cell is cut with `...`.)*

`--prepare-only` instead creates the run directory and its redacted `run_meta.json` and
launches nothing. Both flags read the same arguments the real run will read, so the answer
is about *that* invocation.

### 2. Zero network, zero cost: two rounds end to end

`--rounds 2` is an upper bound: the no-op method stops as soon as it has nothing to
submit, while `echo_base` submits a changed candidate and reaches round 2.

```bash
export HG_AGENT_BACKEND=mock
python3 driver.py --dataset demo --sampling all --harness loop --rounds 2 --run-id control \
    --method-entrypoint "python $PWD/methods/noop/run.py"
python3 driver.py --dataset demo --sampling all --harness loop --rounds 2 --run-id treat-A \
    --method-entrypoint "python $PWD/methods/echo_base/run.py"
python3 tools/run_summary.py runs/treat-A          # one page per run
python3 tools/plot_curve.py runs/control runs/treat-A
```

`--sampling all` is the ordinary demo path; the CLI default is the staged `diagnostic`, which requires `--policy-evidence`, one task, one trial and zero improvement rounds. The demo grants no production authorization.

### 3. A real experiment

A checked-in experiment file is the reviewable form of a command line:

```bash
export HG_AGENT_BASE_URL=http://127.0.0.1:8001/v1 HG_AGENT_MODEL=qwen3.5-9b-local
harnessgrad run --config experiments/quickstart.json --set rounds=3 --set run_id=first-study
python3 tools/run_summary.py runs/first-study
python3 tools/results_matrix.py --runs runs --md results.md   # method × dataset → score
```

`--set a.b=c` accepts any field the file supports; an explicit flag after `--config` wins
over the file, so `--config experiments/quickstart.json --dataset alfworld` is the same
experiment on another dataset.

The checked-in example is deliberately small: `gsm8k` on the train side with
`"sampling": "random_5"`, which measures **five** tasks per round (a recorded seed picks them
once, before round 0), one round, one trial. Measured here on 2026-11-02: 12 s end to end on
the mock backend, 24 s against one local `qwen3.5-9b` endpoint, `0.000` at both rounds —
`loop` is a deliberately bare harness, and moving that flat line is exactly what a method is
for. `--set sampling=all` or `--set sampling=below_0.3` are the interesting regimes, and they
cost what their names say: a score-based policy has to measure the whole side once in order to
choose from it. `--set method=dgm --set rounds=10` is where the platform stops being a demo;
`experiments/method-*.json` has one file per published method.

Which datasets are usable here, what their grade is, and whether that grade is the
benchmark's own metric: [`DATASETS.md`](DATASETS.md) (generated — `python3
tools/datasets_doc.py --check` fails when it drifts).

Want your own study/held-out partition, including one carved from an official
validation or test pool? Run `harnessgrad split create --dataset math500
--train-ratio 0.2 --seed 42 --output splits/math500.json`, then pass that same file
with `--split splits/math500.json` (or the experiment's `split` field) to both
runs. [Custom split guide](docs/custom-dataset-splits.md) covers counts, groups,
source provenance and a zero-cost example.

## 🧪 Test

```bash
python3 tools/run_tests.py                  # every lane: parallel plus exclusive
python3 tools/run_tests.py --dataset gsm8k  # that dataset plus the core platform tests
```

`--basetemp` must be **outside** the platform tree, and the runner enforces it.

## 📚 Documentation

| | |
| --- | --- |
| 📜 [`INTERFACE.md`](INTERFACE.md) | the measurement contract: what a harness may declare, what the platform guarantees |
| 🗺️ [`docs/architecture.md`](docs/architecture.md) | repository map and dependency direction |
| 🧭 [`docs/design-notes.md`](docs/design-notes.md) | why it is shaped this way, and what building it caught |
| 🛠️ [`docs/writing_a_harness.md`](docs/writing_a_harness.md) · [`docs/dataset-onboarding.md`](docs/dataset-onboarding.md) | contributing a harness, adding a dataset |
| 🚪 [`docs/bring-your-own-harness.md`](docs/bring-your-own-harness.md) | the on-ramp for an outside harness: a directory with an `agent.py` → a point on a measured curve, every command verified |
| 🧪 [`docs/test-suite.md`](docs/test-suite.md) | test lanes and the `--basetemp` rule |
| 🤖 [`AGENTS.md`](AGENTS.md) | operating rules, including the staged-pilot policy |

## ⚠️ Status and limits

The mount namespace constrains the filesystem and nothing else: a harness keeps the network and the agent's API key, so egress filtering is still open (`INTERFACE.md` §4.8).

* 🚫 `--no-sandbox` is a debugging decision, never an automatic fallback; runs with and without it are not comparable, and `tools/plot_curve.py` refuses to mix them.
* 🔬 Contamination is not detectable from inside the framework — whether the agent model has already seen the benchmark is the largest threat to any curve this produces.
* 📋 Per-dataset enforcement gaps are tracked, not hidden, in [`registry/dataset-blockers.json`](registry/dataset-blockers.json); such a gap is not an ordinary zero score.

## 📄 License

MIT — see [`LICENSE`](LICENSE).
