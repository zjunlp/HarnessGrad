<p align="center">
  <img src=".github/assets/logo.svg" alt="" width="76">
</p>

<h1 align="center">HarnessGrad: measure a harness that improves</h1>

<p align="center"><strong>A harness that improves is a learner, so what it <em>keeps</em> is measured as carefully as what it gains.</strong></p>

<p align="center">
  <a href="https://zjunlp.github.io/HarnessGrad-Hub/"><img src="https://img.shields.io/badge/website-live-brightgreen.svg" alt="Website"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12 or newer">
  <img src="https://img.shields.io/badge/runtime%20dependencies-none-brightgreen.svg" alt="No third-party runtime dependencies">
  <a href="https://github.com/zjunlp/HarnessGrad/actions/workflows/quality.yml"><img src="https://github.com/zjunlp/HarnessGrad/actions/workflows/quality.yml/badge.svg" alt="Quality gate"></a>
</p>

## What you can run

A run is one point in a **harness × method × benchmark** space: a *harness* solves tasks, a
*method* proposes edits to that harness between rounds, and a *benchmark* scores each
candidate. HarnessGrad supplies the three middle layers that every RSI paper re-implements
badly — tasks and scoring, an isolated sandbox, and version control over the candidate
harness — so an experiment is one command and a curve.

| axis | what ships | where |
| --- | --- | --- |
| **harnesses** | 8: `loop`, `codex`, `opencode`, `deepseek-harness`, `openclaw`, `miniagent`, `miniswe`, `oracle` | `base_harness/<name>/` |
| **RSI methods** | 19: `dgm`, `rrsi`, `adas`, `ahe`, `ace`, `skillopt`, `evoprompt`, `evo_harness`, `opro`, `tthe`, `dream_rsi`, `sica_ci`, `gepa`, `darwinx`, `arbor`, `hyperagents`, `meta_harness`, `harnessx`, `modular_rsi` | `experiments/method-<name>.json` |
| **benchmarks** | 59 declared, **55 runnable**, 4 refused (†) — every one listed: `agentdojo`, `aime`, `alfworld`, `api_bank` †, `appworld`, `authored_example_fix_bug`, `bbeh`, `bfcl_v3`, `bird_minidev`, `browsecomp`, `chessbench_puzzles`, `cooperbench`, `costbench`, `costbench_dynamic`, `deepsearchqa`, `deepswe`, `ds1000`, `fever`, `gaia`, `gsm8k`, `harvey_lab`, `hle`, `hotpotqa`, `jericho`, `jobbench`, `kumo`, `livecodebench`, `livemathbench`, `locomo`, `math`, `math500`, `memoryagentbench`, `musique`, `nestful` †, `nq`, `officeqa_pro`, `personamem_v2`, `physicsquestions`, `reasoning_gym`, `scienceworld`, `searchqa`, `sotuqa` †, `sportsunderstanding`, `spreadsheetbench`, `spreadsheetbench2`, `strategyqa`, `strongreject`, `supergpqa`, `swe_bench`, `swe_bench_pro`, `tau2_bench` †, `tau3_bench`, `tau_bench`, `terminal_bench`, `travelplanner`, `triviaqa`, `twowikimultihopqa`, `widesearch`, `workbench` | `data/datasets/`, detail in [`DATASETS.md`](DATASETS.md) |

Shipped experiments, each with a documented cost and prerequisites:

| experiment | harness | method | benchmark | cost |
| --- | --- | --- | --- | --- |
| first real run | `loop` | `dgm` | gsm8k, 5 tasks | minutes, no images |
| reasoning | `loop` | `dgm` | [BBEH × DGM](docs/rsi-experiments.md) | one provisioned dataset |
| terminal work | `miniswe` / `codex` | any | Terminal-Bench 2 | per-task container images |
| repository edits | `loop` | any | SWE-bench Pro V2 | per-task container images |
| research/search | `loop` | any | DeepSearchQA | search + judge services |

### Where to start, if you already have something

| you have | what we ask for | start here |
| --- | --- | --- |
| a harness (any language, any framework) | a directory with `harness.json` and an entrypoint that answers `--task` / `--workdir` | [bring-your-own-harness.md](docs/bring-your-own-harness.md) |
| an optimizer that edits files | a program that reads a trajectory and writes a candidate tree | [bring-your-own-method.md](docs/bring-your-own-method.md) |
| a benchmark and its official scorer | an adapter that materialises a task and runs *your* scorer | [dataset onboarding](docs/dataset-onboarding.md) |
| a paper you want to reproduce | nothing yet: pick the recipe and read its cost | [rsi-experiments.md](docs/rsi-experiments.md) |

**59 benchmarks** are declared, and the registry knows how to obtain each one. These four are
declared and *refused* rather than approximated, because the thing they need does not exist
here: `api_bank` (the dialogue state its recorded replies depend on), `nestful` (a Win Rate
its own release does not reproduce), `sotuqa` (a headline judge metric that resolved to a
retired model), `tau2_bench` (no second party). Each lifts when that missing piece is
declared in the receipt's `identity`; until then `load()` raises, and
[`registry/dataset-blockers.json`](registry/dataset-blockers.json) says why.

## Why one command is enough

A harness is a directory with a `harness.json` and an entrypoint that answers
`--task <task.json> --workdir <dir>`; the *task*, not the harness, owns where the answer goes.
That is the whole contract ([`INTERFACE.md`](INTERFACE.md)). Everything a paper would
otherwise write itself is a platform responsibility:

- **tasks and scoring** — a dataset adapter materialises the task and runs the official
  scorer, so a score is the benchmark's number, not ours;
- **isolation** — `files` tasks run under `bubblewrap`, `exec` tasks in Docker, both with a
  declared allowlist, no network unless the dataset declares it, and a resource budget;
- **the candidate's history** — every round is a git commit in a workspace, which is what the
  next section is about;
- **the record** — one run directory with the metadata, the curve and the receipts.

Because the method only ever proposes a *tree*, it does not need to know the benchmark: the
same `experiments/method-dgm.json` runs on gsm8k, BBEH and Terminal-Bench.

## Why the comparison is fair

Three decisions make two curves comparable, and they are the reason to use this instead of a
folder of patches.

1. **Harnesses are versioned with git, one commit per round.** `harness_sha` is the tree hash
   of the candidate the score was measured on, so "which harness produced this number" is a
   commit, and the diff between two rows of a curve is `diffs/round-N.patch`. A snapshot
   (`snapshots/round-N.json`) carries the id and its parent, which is why a run can be
   recovered after a crash instead of being retyped.
2. **The task and method models are separate roles.** A method's edits are proposals, not
   measurements: swapping the solver model changes the number and swapping the editor model
   changes the proposals, and the receipt records both.
3. **A refusal is not a zero.** `COMPLETE`, `EARLY_STOP` (the method declined) and
   `INCOMPLETE` (the measurement failed) are different outcomes; only the last one is a
   failure of the instrument, and it is the only one that makes `--fail-on-incomplete` exit
   nonzero. A dataset that cannot be measured here is refused, never replaced by a proxy under
   the benchmark's own name ([`docs/refusal-policy.md`](docs/refusal-policy.md)).

## Installation

Linux with Python **3.12+**, git, and a working `bubblewrap`. Keep the virtual environment
outside the checkout and outside `/tmp`, so the sandbox can mount its runtime without exposing
your platform or task files.

```bash
git clone https://github.com/zjunlp/HarnessGrad.git
cd HarnessGrad
python3 -m venv ~/.venvs/harnessgrad
source ~/.venvs/harnessgrad/bin/activate
python3 -m pip install -e .
./install.sh --check
```

The platform itself has **no third-party runtime dependencies** and imports only the standard
library; a dataset, a harness or a search provider may need more, and each recipe names what.
`./install.sh` installs missing system prerequisites and verifies the sandbox boundary.

```bash
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json --check-only
```

```
check            req  result  detail
python           yes  ok      python 3.12.4
git              yes  ok      git version 2.34.1
bwrap            yes  ok      /usr/bin/bwrap: namespace builds (bubblewrap 0.6.1)
dataset          yes  ok      demo: 8 tasks (test fixture, not a published dataset)
harness          yes  ok      <checkout>/base_harness/loop (cli, entrypoint agent.py, env_kinds files, exec)
writable         yes  ok      runs root: runs can be created; work root: ../harnessgrad_work exists and is writable
disk             no   ok      .: 93.1 GB free (a run needs about 5 GB)
ready: every required check passed
```

`--check-only` reads the same arguments the real run will read and launches nothing, so its
answer is about that invocation. `--prepare-only` goes one step further and writes the run
directory without starting a round.

### What each task environment needs

A dataset declares one `env_kind`, and that decides what the machine must provide.

| `env_kind` | what runs the task | what you need | typical datasets |
| --- | --- | --- | --- |
| `files` | `bubblewrap` namespace: the task's files plus a read-only runtime | Linux + `bubblewrap` (WSL2 works; native macOS/Windows do not) | gsm8k, bbeh, hotpotqa, most QA |
| `exec` | Docker container built from the task's image | a running Docker daemon and the images | appworld, alfworld, terminal-bench, swe-bench, tau-bench |

A harness declares which kinds it supports in `harness.json`; the platform refuses a pairing
the harness cannot run instead of silently degrading it. Nothing here pulls an image or a
dataset on its own: `tools/datasets.py --fetch` and the dataset's own prepare step are explicit.

## Quick start: one real experiment

The fastest genuinely measured run is **gsm8k × `loop` × `dgm`**: 5 tasks, no container image,
no judge service.

```bash
export HG_DATA_ROOT="$HOME/harnessgrad-datasets"
python3 tools/datasets.py --fetch --dataset gsm8k
```

Point the two model roles at any OpenAI-compatible server. Local servers take a nonempty
placeholder key; hosted credentials belong in your environment, never in an experiment file.

```bash
export HG_AGENT_BACKEND=openai
export HG_AGENT_BASE_URL=http://127.0.0.1:8001/v1
export HG_AGENT_MODEL=your-served-solver-model
export HG_AGENT_API_KEY=local
export HG_METHOD_BASE_URL=http://127.0.0.1:8001/v1
export HG_METHOD_MODEL=your-served-editor-model
export HG_METHOD_API_KEY=local
```

```bash
harnessgrad run --config experiments/method-dgm.json --check-only --check-net   # ~5 s, no generation
harnessgrad run --config experiments/method-dgm.json                            # one round
python3 tools/run_summary.py runs/dgm-quickstart
```

The summary ends with the round table — score, coverage, and the `harness_sha` of the candidate
that each row was measured on:

```
round  score  coverage          kind      harness_sha
    0  0.400  [2/5]             platform  c9a1de0f3b21
    1  0.600  [3/5]             accepted  41f7b28c9d05
```

**Without a model** (no endpoint, no GPU, ~1 minute): the demo fixture runs the whole pipeline
with a scripted edit, and a `noop` control that declines to edit.

```bash
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json \
  --set run_id=demo-edit --improver none --fail-on-incomplete
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json \
  --set method=noop --set run_id=demo-control --improver none --fail-on-incomplete
python3 tools/plot_curve.py runs/demo-control runs/demo-edit
```

These two are interface controls, **not** benchmark results; `HG_AGENT_BACKEND=mock` is scoped
to its own command and does not leave your next real run in mock mode. What a run writes:

```
runs/<run_id>/run_meta.json    redacted argv, hashes, endpoint binding
              curve.jsonl      one row per round: score, coverage, harness_sha
              diffs/           round-N.patch, the accepted edit as a patch
              snapshots/       round-N.json: snapshot id + parent
              roundN_traces/   the trajectories the round scored
              end-summary.md   the human-readable result
```

Harnesses I did not write enter the system in five minutes
([`docs/bring-your-own-harness.md`](docs/bring-your-own-harness.md)); an optimizer that already
exists enters through a minimal external method
([`docs/bring-your-own-method.md`](docs/bring-your-own-method.md)).

## Contribution boards

Four boards, one record: [`registry/leaderboards.json`](registry/leaderboards.json), rendered by
`python3 tools/leaderboards.py --write`. The three support boards rank **contributors** by how
many harnesses, methods or benchmarks they contributed, because a list with one name repeated
on every row ranks nothing; the count links to
[the details page](docs/leaderboard-details.md), which names each entry, and the
[full boards](https://zjunlp.github.io/HarnessGrad-Hub/leaderboards/) also carry the contact
each contributor chose to publish. A question about one entry therefore has someone to @.

### How to get on a board

Three steps, and the bar is deliberately low: we would rather review a contribution than turn
one away, and the requirements tighten only once there are enough of them to be worth writing
down.

```bash
# 1. add yourself -- the command checks the thing actually exists and can run
python3 tools/contribute.py harness --name my-harness --path base_harness/my-harness \
    --contributor your-handle --contact you@example.com

# 2. re-render the tables
python3 tools/leaderboards.py --write

# 3. open a pull request with the two changed files and one sentence about how you ran it
```

`--contact` is optional and never appears in this README: it goes into the record so the
website's board can show a way to reach you, and the table here shows your handle. A results
entry additionally names a benchmark and metric, a receipt committed in this repository, and
the tree hash the score was measured on — `tools/contribute.py result` asks for exactly those.
Nothing is checked against a target score: a contribution is admitted because it *ran*, and a
reviewer can see that from the receipt.

The [skill](docs/skills/configure-harness-method-benchmark.md) is how we configure a harness,
a method and a benchmark ourselves, written for an agent to follow end to end.

<!-- BEGIN boards (generated by tools/leaderboards.py --write) -->

Ranked by **how many entries a contributor has on the board**, ties by handle. The count links to the [details page](docs/leaderboard-details.md), which names every entry, and the [full boards](https://zjunlp.github.io/HarnessGrad-Hub/leaderboards/) carry the same record with contact details.

### Harnesses

| # | contributor | contributed |
| --- | --- | --- |
| 1 | [@zjunlp](https://github.com/zjunlp) | [8](docs/leaderboard-details.md#harness-zjunlp) |

### RSI methods

| # | contributor | contributed |
| --- | --- | --- |
| 1 | [@zjunlp](https://github.com/zjunlp) | [19](docs/leaderboard-details.md#method-zjunlp) |

### Benchmarks

| # | contributor | contributed |
| --- | --- | --- |
| 1 | [@zjunlp](https://github.com/zjunlp) | [6](docs/leaderboard-details.md#benchmark-zjunlp) |

### Measured results

*No comparable result has been contributed yet. The admission rule is in [docs/leaderboards.md](docs/leaderboards.md): a benchmark+metric group, a receipt in this repository, and the tree hash the score was measured on.*

[Full boards](https://zjunlp.github.io/HarnessGrad-Hub/leaderboards/) keep every contributor; this table keeps the top 10.
<!-- END boards -->

## What the curves look like

![Recorded rounds on AppWorld and ALFWorld: one line per method, no axis numbers](.github/assets/trends.svg)

Two benchmarks we run ourselves, one line per method, from the recorded rounds in
[`.github/assets/trends.json`](.github/assets/trends.json). **The vertical axis carries no
ticks on purpose.** The figure answers one question — did the curve move while the method
edited the harness — and a number read off it would be quoted without the harness tree hash,
the task set and the scorer identity that make a score comparable. The receipt carries those
identifiers for every line, and `python3 tools/render_trends.py --check` keeps the drawing
equal to the receipt.

Held-out scores, per-benchmark tables and every recorded run are on the
[website](https://zjunlp.github.io/HarnessGrad-Hub/), which is also where the full
leaderboards live.

The admission rule for the results board is in [`docs/leaderboards.md`](docs/leaderboards.md):
a benchmark+metric group, a receipt committed in this repository, and the tree hash the score
was measured on. Scores rank inside one group only — a BBEH accuracy and a Terminal-Bench pass
rate are not the same axis, and a table that mixes them invents a comparison.

## Documentation

| start here | |
| --- | --- |
| [`INTERFACE.md`](INTERFACE.md) | the harness contract, the task file, the receipt fields |
| [`docs/rsi-experiments.md`](docs/rsi-experiments.md) | the shipped recipes, with prerequisites and cost |
| [`DATASETS.md`](DATASETS.md) | every dataset, its source, metric and declared deviations |
| [`DOWNLOADS.md`](DOWNLOADS.md) | what each dataset needs and where it goes |
| [`docs/bring-your-own-harness.md`](docs/bring-your-own-harness.md) | admit a new harness |
| [`docs/bring-your-own-method.md`](docs/bring-your-own-method.md) | admit a new optimizer |
| [`docs/refusal-policy.md`](docs/refusal-policy.md) | what we refuse to measure, and why |
| [`docs/release-validation.md`](docs/release-validation.md) | what was verified for this release |

## Tests

```bash
python3 tools/run_tests.py --fast      # the suite a contributor runs
python3 tools/run_tests.py             # everything, including the measured-slow files
```

One pytest process per file, two lanes, a temporary root outside the checkout, and a run-alone
recheck of every failure, so a flake is reported as a flake ([`docs/test-lanes.md`](docs/test-lanes.md)).
CI runs the same entry point with `--fast`.

## Status and limits

Version 0.1 — the interface may still change, and every change that touches a recorded field is
called out in the release notes. The platform is the instrument, not a method: it contains no
new RSI algorithm, and the recipes are bounded integration smokes, not full benchmark
evaluations. A score produced here is only as comparable as the receipt that accompanies it.

## Citation

If the platform, the measurement protocol or a curve produced here is useful in your work:

```bibtex
@misc{xu2026harnessgrad,
  title  = {HarnessGrad: Harness As a Continual Learner},
  author = {Buqiang Xu and Yenan Wang and Yutong Yu and Rongyao Li and
            Jintian Zhang and Shuofei Qiao and Xuehai Wang and Zirui Xue and
            Yijun Chen and Ningyu Zhang},
  year   = {2026},
}
```

A result that depends on a specific recorded run should cite the run id as well: the receipt
names the harness tree hash the score was measured on, which is what makes the citation
re-derivable.

## License

MIT. Third-party benchmark code keeps its own licence and provenance next to the code that uses
it (`PROVENANCE.json`, `LICENSE.upstream`).
