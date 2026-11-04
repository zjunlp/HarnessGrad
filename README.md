<p align="center">
  <img src=".github/assets/logo.svg" alt="" width="76">
</p>

<h1 align="center">HarnessGrad: Harness as a Continual Learner</h1>

HarnessGrad runs experiments in which an agent's **harness changes between rounds,
while its solver model stays fixed**. A harness is the code around a model: it
organizes the model's actions, tool calls and working context to solve a task.
Here, “continual learner” means updating that harness from experience, not training
model weights.

[Website](https://zjunlp.github.io/HarnessGrad-Hub/) · [MIT license](LICENSE) · [Interface](INTERFACE.md) · [Experiment recipes](docs/rsi-experiments.md)

## How a round works

1. A **harness** uses the fixed solver model to attempt selected tasks.
2. The task **environments** return tool outputs and feedback; dataset scorers
   measure the resulting answers or artifacts.
3. An **optimizer** (called a method in the CLI) reads permitted study feedback
   and proposes changes to the harness's editable files, such as its control loop
   or context handling. Its editor model is configured separately from the solver.
4. HarnessGrad versions the candidate, evaluates it and records scores, coverage,
   traces and changes. The next round can use that experience to propose another edit.

HarnessGrad supplies task adapters, sandboxed execution, scoring and run records.
The optimizer supplies the editing strategy; the platform keeps the execution and
measurement record so you can inspect what changed and what happened next.

## Install

Use **Linux, Python 3.12+, git and working bubblewrap user namespaces**. Docker
is additionally required for `exec` task environments, but not the GSM8K example
below. Run commands from the checkout. Keep the virtual environment outside the
checkout and outside `/tmp` so the sandbox can mount its runtime safely.

```bash
git clone https://github.com/zjunlp/HarnessGrad.git
cd HarnessGrad
python3 -m venv ~/.venvs/harnessgrad
source ~/.venvs/harnessgrad/bin/activate
python3 -m pip install -e .
./install.sh --check
```

`--check` verifies prerequisites without installing them; `./install.sh` can
install missing system prerequisites. The platform has no third-party Python
runtime dependencies. Harnesses and datasets can have their own dependencies;
prepare the reference harness's dependency overlay before a real model run:

```bash
python3 tools/configure_harness.py --harness base_harness/loop
```

## Check the plumbing without a model

```bash
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json --check-only
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json \
  --set run_id=demo-smoke-001 --improver none --fail-on-incomplete
python3 tools/run_summary.py runs/demo-smoke-001
```

This uses the `demo` fixture and a scripted `echo_base` edit. It is an **interface
smoke test, not measured learning or a benchmark result**. It needs no model
endpoint or GPU. The mock setting applies only to each command above. Use a fresh
run ID for each attempt; do not overwrite an old receipt.

## Configure a local solver and editor

Start your own OpenAI-compatible server separately; HarnessGrad does not download
or serve a model. Replace the model names with IDs your server actually serves.
The solver and editor may share an endpoint/model, but have separate roles:

```bash
export HG_AGENT_BACKEND=openai
export HG_AGENT_BASE_URL=http://127.0.0.1:8001/v1
export HG_AGENT_MODEL=your-served-solver-model
export HG_AGENT_API_KEY=local
export HG_METHOD_BASE_URL=http://127.0.0.1:8001/v1
export HG_METHOD_MODEL=your-served-editor-model
export HG_METHOD_API_KEY=local
```

`local` is a nonempty placeholder for a server that does not require credentials;
use the server's actual key if it does. Keep credentials out of experiment files.
Check that the served model supports the harness's tool behavior, then use the
preflight below to check this pairing. Keep solver settings fixed across revisions.

## Run one real experiment

The checked-in [DGM configuration](experiments/method-dgm.json) selects **GSM8K ×
`loop` × `dgm`**: five training tasks, one trial, one job and one candidate round
after the initial harness evaluation (H0). It needs data and solver/editor calls,
but no container images or model judge. This is a bounded integration experiment,
not a full benchmark evaluation or a token/time budget.

```bash
export HG_DATA_ROOT="$HOME/harnessgrad-datasets"
python3 tools/datasets.py --fetch --dataset gsm8k
harnessgrad run --config experiments/method-dgm.json \
  --set run_id=gsm8k-dgm-001 --improver none --check-only --check-net
```

Preflight checks the selected configuration. `--check-only` starts no rounds;
`--check-net` additionally probes required endpoints with GET requests, without
generating model responses. Fix any required failures before continuing.

**The next command calls your configured local solver/editor**; run it only when
you are ready to spend their compute. The DGM policy port and its deviations
from the original method are documented in [method.json](methods/dgm/method.json).

```bash
harnessgrad run --config experiments/method-dgm.json \
  --set run_id=gsm8k-dgm-001 --improver none --fail-on-incomplete
python3 tools/run_summary.py runs/gsm8k-dgm-001
```

Read the summary's score **and coverage**, then inspect `runs/gsm8k-dgm-001/`:
`curve.jsonl` records round measurements, `diffs/` records edits, `snapshots/`
identifies candidates, and `run_meta.json` records configuration and provenance.
See [experiment recipes](docs/rsi-experiments.md) for explicit study/held-out
workflows and more demanding environments.

## Bring your own component

| You have | Start here |
| --- | --- |
| A harness | [Harness onboarding](docs/bring-your-own-harness.md): manifest, entrypoint and supported environment kinds |
| An optimizer | [Method onboarding](docs/bring-your-own-method.md): read feedback and write a candidate tree |
| A dataset and scorer | [Dataset onboarding](docs/dataset-onboarding.md): task materialisation, scoring and controls |

Each guide defines the supported interface, environment requirements and checks
for admitting your component.

## Documentation and reference

- [Interface](INTERFACE.md): task, harness and receipt contracts.
- [Datasets](DATASETS.md) and [downloads](DOWNLOADS.md): sources, metrics and preparation.
- [Dataset storage](docs/readme-reference.md#dataset-storage): `HG_DATA_ROOT`, overrides and `.env` resolution.
- [Technical and historical reference](docs/readme-reference.md): catalogue, run records, console and archived curve notes.
- [Contributors](docs/contributors.md), [leaderboard rules](docs/leaderboards.md) and [release validation](docs/release-validation.md).

<details>
<summary>Moved reference sections (legacy links)</summary>

<!-- Legacy README anchors: keep incoming links useful after moving the detail. -->
<a id="what-you-can-run"></a><a id="where-to-start-if-you-already-have-something"></a>
[Catalogue and existing-component routes](docs/readme-reference.md#what-you-can-run).
<a id="why-one-command-is-enough"></a><a id="why-the-comparison-is-fair"></a>
[Execution contracts and comparison requirements](docs/readme-reference.md#why-one-command-is-enough).
<a id="installation"></a><a id="what-each-task-environment-needs"></a>
[Installation above](#install); [environment requirements](docs/readme-reference.md#what-each-task-environment-needs).
<a id="dataset-storage"></a><a id="quick-start-one-real-experiment"></a>
[Storage details](docs/readme-reference.md#dataset-storage); [real experiment above](#run-one-real-experiment).
<a id="contributors"></a><a id="measured-results"></a>
[Contribution record and measured-results admission](docs/readme-reference.md#contributors).
<a id="the-workbench"></a><a id="what-the-curves-look-like"></a>
[Console](docs/readme-reference.md#the-workbench) and [historical curves](docs/readme-reference.md#what-the-curves-look-like).
<a id="documentation"></a>
[Documentation index above](#documentation-and-reference).

</details>

## Tests

```bash
python3 tools/run_tests.py --fast
```

Omit `--fast` for the full suite. The runner uses per-file processes and an
external temporary root; see [test lanes](docs/test-lanes.md).

## Status and limits

Version 0.1: interfaces may change. HarnessGrad is the measurement platform, not a
new optimization algorithm. Dataset prerequisites and scorer limitations still
apply; [refusal policy](docs/refusal-policy.md) records unsupported measurements
and historical removals. Compare scores only with compatible task sets, metrics,
model settings and receipts. Incomplete measurements are not ordinary zeros;
accepted edits need not improve scores, and study gains do not establish held-out
gains or retention. The quickstarts do not establish method quality.

## Citation

```bibtex
@misc{xu2026harnessgrad,
  title  = {HarnessGrad: Harness as a Continual Learner},
  author = {Buqiang Xu and Yenan Wang and Yutong Yu and Rongyao Li and
            Jintian Zhang and Shuofei Qiao and Xuehai Wang and Zirui Xue and
            Yijun Chen and Ningyu Zhang},
  year   = {2026},
}
```

For a recorded result, also cite its run ID and receipt.
## License

MIT. Third-party benchmark code retains its own license and provenance alongside
its adapter (`PROVENANCE.json`, `LICENSE.upstream`).
