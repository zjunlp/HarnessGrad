---
title: Getting started
description: Install HarnessGrad from source on Linux, verify it with a mock control, and inspect the results.
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
library at runtime, while components can have separate dependencies.

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
to prepare from cached wheels. The embedded control requires no dataset download
or model endpoint. Its scripted scores are fixture results, not benchmark or model
quality evidence. Resolve any failed or incomplete measurement before proceeding.

To check duplicate-ID protection, prepared-run consumption, Console observation
and evidence export with a fresh synthetic run, use:

```bash
python tools/check_first_use.py --output-root "$HOME/.local/share/harnessgrad/install-checks"
```

This check does not install dependencies, download data or contact models. Its
report and logs remain in the printed directory.

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
for prerequisites and preflight; see the
[research recipes and shared H0 guidance](https://github.com/zjunlp/HarnessGrad/blob/main/docs/rsi-experiments.md)
for controlled comparisons. Review task scope, trials and budget before a real
run; its model calls can incur costs.

## Repeat, inspect and export

Use a fresh run ID for each new experiment. Existing result and work namespaces,
including partial runs, are preserved. `--resume` continues only compatible work;
a changed identity, seed, split or runtime is refused. `--prepare-only` reserves
an ID without executing a round; a matching invocation can consume that intact
reservation once. `--check-only` does not reserve an ID.

Results from the example are in the checkout's `runs` directory. Point Console to
the same root:

```bash
export HG_CONSOLE_RUNS="$PWD/runs"
python console/server.py --port 4460
```

Open `http://127.0.0.1:4460/`. If the list is empty, check `HG_CONSOLE_RUNS`;
if the port is occupied, choose another port. Runs, Version and Trace show stored
results. Evidence export downloads filtered records, including failed or unmeasured
attempts, without running an experiment; review the
[evidence export guide](https://github.com/zjunlp/HarnessGrad/blob/main/docs/evidence-export.md)
for scope and privacy.

Observation needs no model or launch budget. Starting an experiment or candidate
trial requires additional [Console setup](https://github.com/zjunlp/HarnessGrad/blob/main/console/README.md).
A zero-round candidate trial can still measure the entire selected pool; if the
task count is unknown, resolve it before committing to the run and budget.
