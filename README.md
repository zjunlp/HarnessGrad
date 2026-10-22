<p align="center">
  <img src="assets/logo.svg" alt="" width="76">
</p>

<h1 align="center">HarnessGrad: Harness as a Continual Learner</h1>

<p align="center"><strong>A harness that improves is a learner, so what it <em>keeps</em> is measured as carefully as what it gains.</strong></p>

<p align="center">
  <a href="https://zjunlp.github.io/HarnessGrad/"><img src="https://img.shields.io/badge/website-live-brightgreen.svg" alt="Website"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10 or newer">
  <img src="https://img.shields.io/badge/runtime%20dependencies-none-brightgreen.svg" alt="No third-party runtime dependencies">
</p>

<p align="center">
  🌐 <a href="https://zjunlp.github.io/HarnessGrad/"><b>Website</b></a> ·
  🚀 <a href="https://zjunlp.github.io/HarnessGrad/getting-started/"><b>Quickstart</b></a> ·
  📊 <a href="https://huggingface.co/datasets/Xubqpanda/HarnessGrad"><b>Data</b></a> ·
  📜 <a href="INTERFACE.md"><b>Contract</b></a>
</p>

---

Every harness-improvement method reports a final delta. None can say what the delta is *made of*,
and none report it on a curve another method can be compared against. HarnessGrad defines that
measurement: the framework owns the tasks, the sandbox, the scoring and the record; a **method**
lives outside the harness, is handed a pristine copy, and returns a candidate that the platform
then measures itself.

The unit of comparison is a **plasticity/stability pair over a curriculum of rounds**, not one
before/after delta — a round can raise the score while silently breaking tasks an earlier round
passed, and that is a measurement, not an anecdote. We do **not** propose improvement methods:
we build the instrument that decides whether any of them worked.

## What one measurement is

| stage | pool | what it decides |
| --- | --- | --- |
| study | train | each round's harness is fitted and scored |
| checkpoint selection | dev | every checkpoint is scored; the best is kept |
| exam | test, reported **per pool** | the kept checkpoint, measured once |

Two pools, never one average: on AppWorld `dev` over-estimates the exam by ~25 points, and the
gap is almost entirely on `test_challenge` (dev .439 → .156 for one method). Dev also ranks
methods differently from the exam (Kendall τ ≈ +0.43 — the dev leader finished fourth), so
selection on dev is itself a measured variable.

## Measured so far

| benchmark | methods | pools | data |
| --- | --- | --- | --- |
| AppWorld | 7 | train 90 / dev 57 / test 585 (`test_normal` 168 + `test_challenge` 417) | [Hub](https://huggingface.co/datasets/Xubqpanda/HarnessGrad/tree/main/Experiments/AppWorld) |
| ALFWorld | 9 (7 plotted) | train 100 / `valid_seen` 140, `valid_unseen` 134, `valid_train` 200 | [Hub](https://huggingface.co/datasets/Xubqpanda/HarnessGrad/tree/main/Experiments/ALFWorld) |

Each published point carries the harness that earned it, the edit that produced it, its cost in
tokens, and the per-task verdicts. A score without those is a claim nobody can check.

## 📦 Install

`bubblewrap`, `git`, `python3 >= 3.10`. The platform itself has **no third-party Python
dependencies**.

```bash
./install.sh --check      # verify only, change nothing
./install.sh              # install what is missing, then verify
```

> [!IMPORTANT]
> `bwrap` being present is not the same as the sandbox working — a kernel with unprivileged user
> namespaces disabled leaves it installed and failing at runtime. `install.sh` builds a real
> namespace and looks for itself inside it, and refuses to call the machine ready if that fails,
> rather than letting a run fall back to `--no-sandbox`.

## 🚀 Run it

Zero network, zero cost: the loop, the curve points, the checkpoints and the cost accounting,
end to end.

```bash
export HG_AGENT_BACKEND=mock
M="python $PWD/methods/echo_base/run.py"
python3 driver.py --dataset demo --sampling all --harness loop --mode A --rounds 2 \
    --run-id control --method-entrypoint "python $PWD/methods/noop/run.py"
python3 driver.py --dataset demo --sampling all --harness loop --mode A --rounds 2 \
    --run-id treat-A --method-entrypoint "$M"
python3 tools/plot_curve.py runs/control runs/treat-A
```

> [!WARNING]
> `--sampling all` selects the ordinary demo path. The CLI default is staged `diagnostic`, which
> requires `--policy-evidence`, one task, one trial and zero improvement rounds. **The demo
> grants no production authorization.**

## 🧪 Test the part you changed

```bash
python3 tools/run_tests.py --dataset gsm8k   # that dataset plus the core platform tests
```

`--basetemp` must be **outside** the platform tree; the runner refuses a work root inside it.

## 📚 Documentation

| | |
| --- | --- |
| 📜 [`INTERFACE.md`](INTERFACE.md) | the measurement contract: what a harness may declare, what the platform guarantees |
| 🗺️ [`docs/architecture.md`](docs/architecture.md) | repository map and dependency direction |
| 🧭 [`docs/design-notes.md`](docs/design-notes.md) | why it is shaped this way, and what building it caught |
| 🎯 [`docs/claims.md`](docs/claims.md) | what the numbers may and may not be used to claim |
| 🛠️ [`docs/writing_a_harness.md`](docs/writing_a_harness.md) · [`docs/dataset-onboarding.md`](docs/dataset-onboarding.md) | contributing a harness, adding a dataset |
| 🧪 [`docs/test-suite.md`](docs/test-suite.md) | test lanes and the `--basetemp` rule |
| 🤖 [`AGENTS.md`](AGENTS.md) | operational rules, including the staged-pilot policy |

## ⚠️ Status and limits

> [!CAUTION]
> The mount namespace constrains the filesystem and nothing else. A harness keeps the network and
> the agent's API key — it cannot run without them — so it could exfiltrate that key. Closing this
> needs egress filtering; see `INTERFACE.md` §4.8.

* 🚫 **`--no-sandbox` is a debugging decision**, never an automatic fallback. Runs with and
  without it are not comparable, and `tools/plot_curve.py` refuses to silently mix them.
* 🔬 **Contamination is not detectable from inside the framework.** Whether the agent model has
  seen the benchmark tasks is the largest threat to any curve this produces.
* 📋 **Per-dataset enforcement gaps are tracked, not hidden** — see
  [`registry/dataset-blockers.json`](registry/dataset-blockers.json). Such a gap is not an
  ordinary zero score.

## 📄 License

MIT — see [`LICENSE`](LICENSE).
