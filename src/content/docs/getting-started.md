---
title: Getting started
description: Use HarnessGrad on Linux from source or an installed wheel, verify a mock control, and inspect results.
---

## Install on Linux

Use Linux, Git, uv, CPython 3.12 and working Bubblewrap user namespaces. The
platform supports Python 3.12 or newer; the default Loop Harness currently uses
Python 3.12. Keep the virtual environment and its base interpreter outside the
checkout, run and work directories, and `/tmp`.

```bash
git clone https://github.com/zjunlp/HarnessGrad.git
cd HarnessGrad
uv venv --seed --python 3.12 ~/.venvs/harnessgrad
source ~/.venvs/harnessgrad/bin/activate
uv pip install -e .
./install.sh --check
```

`--check` checks prerequisites without installing system packages. Resolve any
Bubblewrap or user-namespace failure before continuing. Creating the environment
and installing build tools may require network access; offline installation needs
those prerequisites already available. The platform uses the Python standard
library at runtime. Harnesses, datasets, services and scorers use their own
declared dependencies; prepare them outside the platform environment.

For an installed wheel in an external Python 3.12+ environment, use module
entrypoints instead of checkout-relative scripts. Follow the
[installed-wheel guide](https://github.com/zjunlp/HarnessGrad/blob/main/docs/wheel-console.md)
for Loop template discovery and preparation, `python -m tools.check_first_use`,
and `harnessgrad run --preset quickstart` with explicit external `--runs-root`
and `--work-root`. A bundled preset does not provision its dependencies.
The commands below use a source checkout.

## Prepare Loop and run the mock control

```bash
python tools/configure_harness.py --harness base_harness/loop --fetch
python tools/configure_harness.py --harness base_harness/loop --list --json

HG_INSTALL_RUN="install-$(python -c 'import uuid; print(uuid.uuid4().hex[:12])')"
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json \
  --improver none --run-id "$HG_INSTALL_RUN" --fail-on-incomplete
python tools/run_summary.py "runs/$HG_INSTALL_RUN"
```

`--fetch` permits downloading locked wheels into Loop's external runtime. Omit it
to prepare from cached wheels. The base interpreter must already exist; use
`--python /absolute/path/to/python` to select it and `--runtime-root` or
`HG_HARNESS_RUNTIME_ROOT` for a custom external store. The embedded control requires no dataset download
or model endpoint. Its scripted scores are fixture results, not benchmark or model
quality evidence. Resolve any failed or incomplete measurement before proceeding.

To check duplicate-ID protection, prepared-run consumption, Console observation
and evidence export with a fresh synthetic run, use:

```bash
python tools/check_first_use.py --output-root "$HOME/.local/share/harnessgrad/install-checks"
```

This check does not install dependencies, download data or contact models. Its
report and logs remain in the printed directory. A missing report or incomplete
measurement is not a completed installation check.

For installed Harness templates, `--check-runtime` checks prepared dependencies;
it does not prove actor admission. If a template contains installer-generated
bytecode, keep the original tree and follow the documented sandboxed `--smoke`
procedure in the [Harness validation guide](https://github.com/zjunlp/HarnessGrad/blob/main/docs/writing_a_harness.md).

## Prepare research data separately

Data download and dependency preparation are separate steps. For GSM8K:

```bash
export HG_DATA_ROOT="$HOME/.local/share/harnessgrad/datasets"
python tools/datasets.py --fetch --dataset gsm8k
python tools/prepare_dataset.py --dataset gsm8k
python tools/prepare_dataset.py --dataset gsm8k --check
python tools/datasets.py --check --dataset gsm8k
```

`prepare_dataset.py` defaults to the provider's loading environment; `--check`
installs and downloads nothing. Execution dependencies are a separate declared
profile. For example, native Math grading needs:

```bash
python tools/prepare_dataset.py --dataset math --runtime execution
python tools/prepare_dataset.py --dataset math --runtime execution --check
```

This prepares the trusted grader, not data or actor dependencies. Follow the
[native QA preparation guide](https://github.com/zjunlp/HarnessGrad/blob/main/data/scaffolds/native_qa/README.md)
and each adapter's recipe in
[DOWNLOADS](https://github.com/zjunlp/HarnessGrad/blob/main/DOWNLOADS.md).
For datasets that declare task Python, follow the
[task-runtime contract](https://github.com/zjunlp/HarnessGrad/blob/main/docs/dataset-runtime-interfaces.md#dataset-owned-task-python-envtask_python);
the task interpreter and Harness runtime serve different roles. The
[dependency support matrix](https://github.com/zjunlp/HarnessGrad/blob/main/DATASETS.md#dependency-boundaries-and-validation-scope)
states which paths have been checked. A catalog entry, prepared artifact or
successful synthetic control does not establish full benchmark readiness.
DS-1000 has a separate [CPU runtime preparation guide](https://github.com/zjunlp/HarnessGrad/blob/main/data/datasets/ds1000/README.md);
preparing that profile does not prove actor execution or full benchmark readiness.
Container routes also require the selected Harness's image-specific preparation.

`native_harbor` and `jobbench_renderer` are dependency component profiles,
not dataset adapters. The former supplies the trusted Harbor verifier; the latter
prepares JobBench author data and is separate from execution and judge runtimes.
Use the [Harbor preparation and migration guide](https://github.com/zjunlp/HarnessGrad/blob/main/data/scaffolds/native_harbor_runtime/README.md)
and [JobBench renderer guide](https://github.com/zjunlp/HarnessGrad/blob/main/data/datasets/jobbench/README.md#renderer-preparation-runtime).
Their `check` / `--check-runtime` operations are read-only. Preparation or a probe
is not task execution evidence; missing evidence remains missing. Old manifests
without the current external runtime binding require explicit component and task
repreparation, then a fresh baseline measurement; do not fabricate restored evidence.

## Configure a research run

A real experiment may need four independently configured model roles:

| Role | Purpose | Configuration |
| --- | --- | --- |
| Solver | Performs tasks with the candidate Harness | `HG_AGENT_BACKEND`, `HG_AGENT_BASE_URL`, `HG_AGENT_MODEL`, `HG_AGENT_API_KEY` |
| Editor | Proposes Harness changes | `HG_METHOD_BASE_URL`, `HG_METHOD_MODEL`, `HG_METHOD_API_KEY`, or the selected CLI improver's configuration |
| Environment model | Drives a declared simulated environment or user, when required | `HG_ENV_BASE_URL`, `HG_ENV_MODEL`, `HG_ENV_API_KEY` |
| Judge | Scores tasks requiring a model judge | `HG_JUDGE_BASE_URL`, `HG_JUDGE_MODEL`, `HG_JUDGE_API_KEY`, or dataset-specific settings |

Configure only the roles the selected recipe requires. A mock solver does not make
an editor free. Prepare the chosen data and runtime separately. Use
[Quick start](https://github.com/zjunlp/HarnessGrad#quick-start) and the
[first-use guide](https://github.com/zjunlp/HarnessGrad/blob/main/docs/getting-started.md)
for prerequisites. Preflight the selected invocation with `--check-only`; add
`--check-net` to probe required endpoints without generating responses. These checks
do not prove task execution. See the
[research recipes and shared H0 guidance](https://github.com/zjunlp/HarnessGrad/blob/main/docs/rsi-experiments.md)
for controlled comparisons. Review task scope, trials and budget before a real
run; its model calls can incur costs.

## Repeat, inspect and export

Use a fresh run ID for each new experiment. Existing result and work namespaces,
including partial runs, are preserved. `--resume` continues only compatible work;
a changed identity, seed, split or runtime is refused. Task inputs and external
runtime artifacts must match the recorded identities. After an incompatible
change, prepare the required artifact again and start a new run; do not reuse
cached scores or add missing identity to an older record. `--prepare-only` reserves
an ID without executing a round; a matching invocation can consume that intact
reservation once. `--check-only` does not reserve an ID.

Older records remain readable and exportable. If a measurement lacks the current
verification identity or uses an earlier input-restoration protocol, restore the
candidate into a new run and remeasure its baseline before reuse or comparison.
A preparation reservation contains configuration, not measurement evidence, and
cannot supply missing identity for an old result.

Results from the example are in the checkout's `runs` directory. Point Console to
the same root. Use `--runs-root` and `--work-root` for custom locations and
keep those locations when inspecting or resuming:

```bash
export HG_CONSOLE_RUNS="$PWD/runs"
python console/server.py --port 4460
```

For an installed wheel, set `HG_CONSOLE_RUNS` to your external result root and use
`python -m console.server --port 4460`; see the
[wheel Console guide](https://github.com/zjunlp/HarnessGrad/blob/main/docs/wheel-console.md)
for writable state locations and launch prerequisites.

Open `http://127.0.0.1:4460/`. If the list is empty, check `HG_CONSOLE_RUNS`;
if the port is occupied, choose another port. Runs, Version and Trace show stored
results. Evidence export downloads filtered records, including failed or unmeasured
attempts, without running an experiment; review the
[evidence export guide](https://github.com/zjunlp/HarnessGrad/blob/main/docs/evidence-export.md)
for scope and privacy.

Observation needs no model or launch budget. Starting an experiment or candidate
trial requires additional [Console setup](https://github.com/zjunlp/HarnessGrad/blob/main/console/README.md).
A zero-round candidate trial can still measure the entire selected pool; if the
task count is unknown, resolve it before committing to the run and budget. Review
the task pool, model roles and shared budget in the launch preview. Missing values
are shown as `—` or an error, rather than a zero budget.

Workbench editing currently supports Codex and DSH headless. OpenCode, MiniSWE,
MiniAgent and OpenClaw remain visible but unavailable as Workbench editors;
they do not fall back to Codex. Capability inspection does not invoke a CLI or
model. Codex resume is scoped to candidate, driver and actual runtime binding;
legacy sessions are retained, and binding drift cannot reuse an old thread.
DSH uses one-shot requests without Codex session reuse. Follow the
[Workbench setup and session guide](https://github.com/zjunlp/HarnessGrad/blob/main/console/WORKBENCH.md)
for complete binding requirements and legacy-session migration.
