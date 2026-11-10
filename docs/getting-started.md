# First use on Linux

This guide takes a source checkout through installation, component preparation,
an installation control and result inspection. The control uses embedded synthetic
tasks and no model endpoint. A research run needs its own dataset and model setup.

## Install the platform

Use Linux, CPython 3.12, Git, uv and working Bubblewrap user namespaces. The platform
supports Python 3.12 or newer; the default Loop currently declares a CPython 3.12
runtime, so using 3.12 for the platform also supplies its base interpreter.

```bash
git clone https://github.com/zjunlp/HarnessGrad.git
cd HarnessGrad
uv venv --seed --python 3.12 ~/.venvs/harnessgrad
source ~/.venvs/harnessgrad/bin/activate
uv pip install -e .
./install.sh --check
```

Keep the venv and its base Python outside the checkout, run/work directories and
`/tmp`. The sandbox must be able to mount their runtime without exposing private
platform or task files. `--check` changes no system packages; if a prerequisite
is missing, follow its diagnostic before proceeding. Do not bypass a failed
sandbox check to obtain a successful run.

For a Bubblewrap binary outside the system search path, export
`HG_BWRAP=/absolute/path/to/bwrap` before installation, runtime preparation and
service launch. Managed Python preparation and bound Python services honor the
same explicit override; an invalid override fails instead of selecting a different binary.

Creating Python/venvs or installing the project can download Python, pip or build
tools. Offline installation requires these prerequisites to be available already.
The platform uses only the standard library at runtime. Harnesses, datasets,
services and scorers can have separate dependencies.

## Prepare the components you will use

Prepare Loop's locked dependencies outside the platform venv:

```bash
python tools/configure_harness.py --harness base_harness/loop --fetch
python tools/configure_harness.py --harness base_harness/loop --list --json
```

`--fetch` explicitly permits wheel downloads. Omit it for preparation from cached
wheels. The interpreter must already exist; `--python /absolute/path/to/python`
selects one explicitly. Installation inherits configured HTTP(S)/ALL proxy settings
only when downloads are allowed; actor execution does not inherit those settings.
Use `--runtime-root` or `HG_HARNESS_RUNTIME_ROOT` to select another external store.
For a fresh run with the bundled Loop, `--check-only` prints the preparation
command when the native runtime has not been prepared. This is a warning because
the run can also build the runtime from cached wheels. If a published runtime
fails validation, the check exits with its specific error.

Data download and dependency preparation are separate operations. For a research
recipe using GSM8K, set an external data root and prepare its loading environment:

```bash
export HG_DATA_ROOT="$HOME/.local/share/harnessgrad/datasets"
python tools/datasets.py --fetch --dataset gsm8k
python tools/prepare_dataset.py --dataset gsm8k
python tools/prepare_dataset.py --dataset gsm8k --check
python tools/datasets.py --check --dataset gsm8k
```

The embedded installation control below does not require this download.
`prepare_dataset.py` is an explicit dependency installation step; `--check` installs
and downloads nothing. GSM8K, TriviaQA, HotpotQA and ChessBench puzzles use this
provider preparation flow. CostBench and LiveCodeBench have execution runtime
profiles; AppWorld uses a separate runtime for [explicit data preparation](../data/datasets/appworld/PREPARATION.md).
Other adapters follow their own preparation recipes in [DOWNLOADS](../DOWNLOADS.md)
and the [dependency support matrix](../DATASETS.md#dependency-boundaries-and-validation-scope).
Declaration in the catalog does not establish local readiness.

Dataset dependency builders receive only installation-related environment settings,
including explicitly configured proxy, package index and TLS settings during
preparation. Use variables such as `UV_DEFAULT_INDEX` and `UV_NATIVE_TLS` when
required by your deployment; implicit uv/pip user configuration is not read. Probe
and admission processes receive neither these network settings nor model credentials.
Exact version pins and installed-file inventories identify the prepared artifact;
they do not by themselves authenticate every upstream source archive.

Loop and MiniSWE declare managed `files` and `exec` routes; MiniAgent currently
declares managed `files`. Container routes need their local task images and
image-specific preparation. See the selected Harness's README before using an
`exec` dataset. No model quality or benchmark result follows from preparation.

## Verify the installation without model calls

After preparing Loop, run the embedded control from the checkout:

```bash
HG_INSTALL_RUN="install-$(python -c 'import uuid; print(uuid.uuid4().hex[:12])')"
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json \
  --improver none --run-id "$HG_INSTALL_RUN" --fail-on-incomplete
python tools/run_summary.py "runs/$HG_INSTALL_RUN"
```

This exercises task execution, a scripted reference method, scoring and record
creation. Its scores describe the fixture, not a model or published benchmark.
The summary should report a completed experiment or an explicit valid method stop,
with no missing task measurements. A failed setup or measurement is an error to
resolve, not a zero to replace with a plausible number.

The default result directory is `runs/<run-id>` in the checkout. Workspaces live
under `../harnessgrad_work/<run-id>`, outside the platform. Use `--runs-root` and
`--work-root` for explicit locations, and reuse those locations when inspecting or
resuming that experiment.

For a repeatable installation check after Loop preparation, run:

```bash
python tools/check_first_use.py --output-root "$HOME/.local/share/harnessgrad/install-checks"
```

This creates a fresh control directory, verifies duplicate-ID protection and
prepared-run consumption, then starts its own temporary loopback Console to
check result inspection and evidence export. It does not install dependencies,
download data or contact models. Logs and `report.json` remain under the printed
control directory; a failed check exits nonzero. Use `--runtime-root` when your
prepared Loop store is in a custom location. When a systemd user session is
available, the checker retains its connection settings for the host launcher so
mock runs can use the configured resource limits. Harness processes do not
inherit the session settings.

## Configure a research run

| Role | Configuration | Purpose |
| --- | --- | --- |
| Solver | `HG_AGENT_BACKEND`, `HG_AGENT_BASE_URL`, `HG_AGENT_MODEL`, `HG_AGENT_API_KEY` | Performs tasks with the candidate Harness |
| Editor | `HG_METHOD_BASE_URL`, `HG_METHOD_MODEL`, `HG_METHOD_API_KEY`, or the selected CLI improver's configuration | Proposes Harness changes |
| Environment model | `HG_ENV_BASE_URL`, `HG_ENV_MODEL`, `HG_ENV_API_KEY` when required | Drives a declared simulated environment or user |
| Judge | `HG_JUDGE_BASE_URL`, `HG_JUDGE_MODEL`, `HG_JUDGE_API_KEY` or dataset-specific settings when required | Scores tasks that use a model-based judge |

Configure only roles required by the selected recipe. Setting the solver to
`mock` does not make an arbitrary improvement method free: its editor is a
separate role. The embedded control uses a scripted method and `--improver none`.
CLI improvers may use their own provider configuration; a Console assistant is
also separate from the solver and editor used by an experiment.

Use the [README Quick start](../README.md#quick-start) or a documented
[research recipe](rsi-experiments.md), with your own endpoints and credentials.
Preflight with `--check-only`; add `--check-net` to probe the required endpoints.
Endpoint checks do not generate responses and do not prove a complete task will
execute. The subsequent run can incur solver, editor, environment and judge costs.
Review its selected tasks, trials and round budget before starting.

## Preserve runs when repeating or resuming work

Use a new run ID for a new experiment. Existing result and work namespaces are
not scratch space and cannot be silently reused. This also protects partial runs.
An explicit `--resume` continues compatible recorded work; mismatched identity,
seed, split or runtime is a refusal, not permission to start clean.

Current measurements also bind the selected verification inputs and source bytes
in a private, versioned `verification_context`. Older records remain readable and
exportable, but an absent context cannot prove that cached results or a resumed
measurement use the same inputs. Restore the candidate into a new run and remeasure
its baseline when that evidence is missing; do not manufacture a context for an
old result. Neither the raw commitments nor their task mapping enter method inputs
or public evidence exports.

The current measurement protocol also binds input restoration: private files
replace destination inodes, and symbolic destination paths are refused. Contexts
recorded before this change remain readable but differ from current measurements.
Restore the candidate into a new run and remeasure its baseline before reuse or
comparison under the current protocol.
The actual sandbox setting and native sandbox plan also participate in the private
context, so changing isolation policy requires fresh measurements.

`--prepare-only` reserves a new ID and records its effective configuration without
executing a round. A later invocation with matching configuration and roots can
consume that intact reservation once. Legacy, changed, partial or already consumed
preparations require a new ID. The reservation binds configuration, not dataset or
verifier contents: it contains no measurement or verification context. The consuming
run captures the currently selected inputs before execution. `--check-only` does not
reserve a namespace or establish an execution context. Neither operation makes an old
measurement with missing context reusable.

## Inspect and export the results

Point the Console at the same result root used by the command above:

```bash
export HG_CONSOLE_RUNS="$PWD/runs"
python console/server.py --port 4460
```

Open `http://127.0.0.1:4460/`. If the port is occupied, choose a different port;
do not stop an unrelated service. Observation requires neither a model nor a
launch budget. An empty list should first be checked against `HG_CONSOLE_RUNS`.

Use Runs, Version and Trace to inspect stored results. Evidence export downloads
filtered records from this configured root, including failed/unmeasured attempts;
it does not run experiments. The author supplies concerns, selection rules and
declared budgets. See [evidence export](evidence-export.md) for scope and privacy.

Launching from the Console or evaluating an edited candidate requires additional
deployment settings documented in [Console setup](../console/README.md).
Zero improvement rounds does not mean one task: a candidate trial can cover the
entire selected task pool. Inspect the task range and budgets before confirming.

## Common first-use failures

| Symptom | Next action |
| --- | --- |
| Python or Bubblewrap prerequisite fails | Fix the reported installation/user-namespace requirement; retain sandbox checks |
| Managed Harness runtime is missing | Run the selected Harness's preparation command; use `--fetch` only to permit downloads |
| Dataset root or loading environment is absent | Set `HG_DATA_ROOT`, fetch the selected data and prepare its declared profile |
| Editor configuration is missing | Configure the method/editor role; a solver endpoint does not supply it automatically |
| A run ID already exists | Choose a new ID, or explicitly resume compatible recorded work |
| Console shows no experiments | Point `HG_CONSOLE_RUNS` at the actual result root |
| Preflight passed but execution failed | Inspect the recorded stage and diagnostic; preflight is not an execution result |

Keep original failed attempts for diagnosis. This guide covers Linux source
installation; other distribution and platform routes need their own validation.
