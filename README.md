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

Zero network, zero cost: two rounds, the curve, the checkpoints and the cost accounting, end to end.

```bash
export HG_AGENT_BACKEND=mock
M="python $PWD/methods/echo_base/run.py"
python3 driver.py --dataset demo --sampling all --harness loop --mode A --rounds 2 --run-id control --method-entrypoint "python $PWD/methods/noop/run.py"
python3 driver.py --dataset demo --sampling all --harness loop --mode A --rounds 2 --run-id treat-A --method-entrypoint "$M"
python3 tools/plot_curve.py runs/control runs/treat-A
```

`--sampling all` is the ordinary demo path; the CLI default is the staged `diagnostic`, which requires `--policy-evidence`, one task, one trial and zero improvement rounds. The demo grants no production authorization.

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
| 🧪 [`docs/test-suite.md`](docs/test-suite.md) | test lanes and the `--basetemp` rule |
| 🤖 [`AGENTS.md`](AGENTS.md) | operating rules, including the staged-pilot policy |

## ⚠️ Status and limits

The mount namespace constrains the filesystem and nothing else: a harness keeps the network and the agent's API key, so egress filtering is still open (`INTERFACE.md` §4.8).

* 🚫 `--no-sandbox` is a debugging decision, never an automatic fallback; runs with and without it are not comparable, and `tools/plot_curve.py` refuses to mix them.
* 🔬 Contamination is not detectable from inside the framework — whether the agent model has already seen the benchmark is the largest threat to any curve this produces.
* 📋 Per-dataset enforcement gaps are tracked, not hidden, in [`registry/dataset-blockers.json`](registry/dataset-blockers.json); such a gap is not an ordinary zero score.

## 📄 License

MIT — see [`LICENSE`](LICENSE).
