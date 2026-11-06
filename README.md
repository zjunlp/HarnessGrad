<p align="center">
  <img src=".github/assets/logo.svg" alt="" width="76">
</p>

<h1 align="center">HarnessGrad: Harness as A Continual Learner</h1>

<p align="center">An agent combines a model and a harness in an environment;<br>HarnessGrad studies how an editable harness can learn from feedback.</p>

<p align="center">🚀 <a href="#quick-start">Quick start</a> · 🧩 <a href="#what-you-can-run">Integrations</a> · 📚 <a href="DATASETS.md">Datasets</a> · 🔗 <a href="#related-work">Related work</a></p>

<p align="center"><img src=".github/assets/learning-loop.svg" width="720" alt="Learning loop: tasks lead to feedback, editing the harness and evaluation, which returns to tasks."></p>

<p align="center">
  <a href="https://zjunlp.github.io/HarnessGrad/"><img src="https://img.shields.io/badge/website-live-brightgreen.svg" alt="Website"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12 or newer">
  <img src="https://img.shields.io/badge/runtime%20dependencies-none-brightgreen.svg" alt="No third-party runtime dependencies">
  <a href="https://github.com/zjunlp/HarnessGrad/actions/workflows/quality.yml"><img src="https://github.com/zjunlp/HarnessGrad/actions/workflows/quality.yml/badge.svg" alt="Quality gate"></a>
</p>

<a id="what-you-can-run"></a>

## 🧩 What you can run

A run is one point in a **harness × method × benchmark** space: a *harness* solves tasks, a
*method* proposes edits to that harness between rounds, and a *benchmark* scores each
candidate. HarnessGrad provides tasks and scoring, isolated execution, and version control
for candidate harnesses, with a recorded curve for each experiment.

<details>
<summary>Full harness, method and benchmark inventory</summary>

The table records the inventory at this README revision; the benchmark row is checked against the declared adapters.

| axis | what ships | where |
| --- | --- | --- |
| **harnesses** | 8: `loop`, `codex`, `opencode`, `deepseek-harness`, `openclaw`, `miniagent`, `miniswe`, `oracle` | `base_harness/<name>/` |
| **RSI methods** | 19: `dgm`, `rrsi`, `adas`, `ahe`, `ace`, `skillopt`, `evoprompt`, `evo_harness`, `opro`, `tthe`, `dream_rsi`, `sica_ci`, `gepa`, `darwinx`, `arbor`, `hyperagents`, `meta_harness`, `harnessx`, `modular_rsi` | `experiments/method-<name>.json` |
| **benchmarks** | 56 declared, **56 runnable**, none refused — every one listed: `agentdojo`, `aime`, `alfworld`, `appworld`, `authored_example_fix_bug`, `bbeh`, `bfcl_v3`, `bird_minidev`, `browsecomp`, `chessbench_puzzles`, `cooperbench`, `costbench`, `costbench_dynamic`, `deepsearchqa`, `deepswe`, `ds1000`, `fever`, `gaia`, `gsm8k`, `harvey_lab`, `hle`, `hotpotqa`, `jericho`, `jobbench`, `kumo`, `livecodebench`, `livemathbench`, `locomo`, `math`, `math500`, `memoryagentbench`, `musique`, `nq`, `officeqa_pro`, `personamem_v2`, `physicsquestions`, `reasoning_gym`, `scienceworld`, `searchqa`, `sportsunderstanding`, `spreadsheetbench`, `spreadsheetbench2`, `strategyqa`, `strongreject`, `supergpqa`, `swe_bench`, `swe_bench_pro`, `tau2_bench`, `tau3_bench`, `tau_bench`, `terminal_bench`, `travelplanner`, `triviaqa`, `twowikimultihopqa`, `widesearch`, `workbench` | `data/datasets/`, detail in [`DATASETS.md`](DATASETS.md) |

</details>

### Where to start

- **Bring a harness:** supply a directory with `harness.json` and an entrypoint accepting `--task` and `--workdir`. See [bring your own harness](docs/bring-your-own-harness.md).
- **Bring a method:** supply a program that reads a trajectory and writes a candidate tree. See [bring your own method](docs/bring-your-own-method.md).
- **Bring a benchmark:** write an adapter that materialises tasks and runs its official scorer. See [dataset onboarding](docs/dataset-onboarding.md).

<a id="why-one-command-is-enough"></a>

## ⚙️ Why one command is enough

A harness supplies `harness.json` and an entrypoint accepting `--task` and `--workdir` ([interface](INTERFACE.md)). The platform runs dataset tasks and scoring, isolates execution, versions candidate harnesses, and records each run's outcomes and receipts. Dataset and harness prerequisites still need to be installed separately.

<a id="why-the-comparison-is-fair"></a>

## ⚖️ Why the comparison is fair

Each round records the candidate harness hash and its changes, alongside the run's model roles and measurement metadata. Compare results only when their task selection, scorer, models and other relevant conditions support it. Failed or incomplete measurements are not scores, and unsupported measurements must not be replaced with proxy scores under the benchmark's name. See the [measurement policy](docs/refusal-policy.md).

<a id="installation"></a>

## 📦 Installation

Linux with Python **3.12+**, git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and a working `bubblewrap`. Keep the virtual environment
outside the checkout and outside `/tmp`, so the sandbox can mount its runtime without exposing
your platform or task files. The base Python installation must also stay outside those
directories. Interpreter links used by tools such as uv are preserved inside the sandbox;
only the required runtime directories are mounted, read-only.

```bash
git clone https://github.com/zjunlp/HarnessGrad.git
cd HarnessGrad
uv venv --seed --python 3.12 ~/.venvs/harnessgrad
source ~/.venvs/harnessgrad/bin/activate
uv pip install -e .
./install.sh --check
```

`--seed` installs pip into the environment, which harness configuration tools need for
host dependency overlays; `uv pip install` alone does not seed pip. Seeding may download
pip if it is not already cached, so an offline setup requires an appropriate pre-populated
uv cache. Creating the environment or installing the project may also require downloading
Python or build tools if they are not already available locally.

The platform itself has **no third-party runtime dependencies** and imports only the standard
library; a dataset, a harness or a search provider may need more, and each recipe names what.
`./install.sh` installs missing system prerequisites and verifies the sandbox boundary.

```bash
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json --check-only
```

`--check-only` checks the invocation without starting a round. `--prepare-only` also creates the run directory without starting a round.

<a id="dataset-storage"></a>

## 🗂️ Dataset storage

Set the shared external dataset directory in your local `.env`:

```dotenv
HG_DATA_ROOT=/path/to/datasets
```

See [downloads](DOWNLOADS.md) for preparation and locations, and [datasets](DATASETS.md) for adapter details.

<a id="quick-start"></a>

## 🚀 Quick start

Configure the dataset and both model roles, then start a run. Use your own OpenAI-compatible endpoint URLs, model names and credentials; do not commit credentials.

```bash
# The provided configuration uses GSM8K.
python3 tools/configure_harness.py --harness base_harness/loop
python3 tools/datasets.py --fetch --dataset gsm8k
export HG_AGENT_BACKEND=openai
export HG_AGENT_BASE_URL='YOUR_SOLVER_ENDPOINT_URL'
export HG_AGENT_MODEL='YOUR_SOLVER_MODEL_NAME'
export HG_AGENT_API_KEY='YOUR_SOLVER_API_KEY'
export HG_METHOD_BASE_URL='YOUR_EDITOR_ENDPOINT_URL'
export HG_METHOD_MODEL='YOUR_EDITOR_MODEL_NAME'
export HG_METHOD_API_KEY='YOUR_EDITOR_API_KEY'
harnessgrad run --config experiments/method-dgm.json --check-only --check-net
harnessgrad run --config experiments/method-dgm.json
python3 tools/run_summary.py runs/dgm-quickstart
```

Replace the placeholders before running. The check probes endpoints but does not generate responses. The run uses five training tasks and one editing round; see the [recipe](experiments/method-dgm.json) and [experiment documentation](docs/rsi-experiments.md) for details.

<a id="contributors"></a>

## 🌱 Contributors

Thank you to everyone who contributes harnesses, methods, benchmarks, and improvements to HarnessGrad!
The three charts show the most recent 30 days; each line includes contributions made before that window.

Want to join? [Bring a harness](docs/bring-your-own-harness.md), [add a method](docs/bring-your-own-method.md), or [onboard a benchmark](docs/dataset-onboarding.md), then open a pull request. We will add your name to the relevant contributor chart.

<!-- BEGIN contributors (generated by tools/contributors.py --write) -->
![Harnesses: who contributed, by day](.github/assets/contributors-harness.svg)

![RSI methods: who contributed, by day](.github/assets/contributors-method.svg)

![Benchmarks: who contributed, by day](.github/assets/contributors-benchmark.svg)
<!-- END contributors -->

<a id="the-workbench"></a>

## 🖥️ The workbench

Explore experiments, inspect harness versions and trajectories, and edit a candidate in the Console.

```bash
export HG_CONSOLE_RUNS="$PWD/runs"  # existing run-record directory
python3 console/server.py --port 4460
```

Open [http://127.0.0.1:4460/](http://127.0.0.1:4460/). Run these commands from the repository root after an experiment has created `runs/`, or point `HG_CONSOLE_RUNS` to another existing run-record directory. The Console binds to localhost by default.

Candidate trials additionally require `HG_CONSOLE_TRIAL_RUNS` to name an existing **absolute**, symlink-free directory outside (and not an ancestor of) the platform checkout. An administrator must provision and protect this directory and all its ancestors against untrusted modification, and dedicate it to this service; for example, create a private directory under a suitable deployment data volume and export its absolute path before starting the Console. There is no shared `/tmp` default. The service reserves both `<root>/<run-id>` and `<root>/work/<run-id>` exclusively; existing or incomplete IDs cannot be reused, including after failures or timeouts. Choose a new ID rather than deleting historical data. Run IDs use 1–48 ASCII letters, digits, dot, underscore or hyphen, excluding `.`, `..`, and `work`. This changes compatibility for deployments relying on the former default or reusing IDs. The root must be exclusive to this service: direct driver CLI runs and other writers must not use either namespace. The reservation protects cooperating trial requests, not malicious processes running as the same OS user or administrators modifying the trusted root.

A candidate trial passes `--sampling all --rounds 0`: zero optimization rounds does **not** mean one task. It can evaluate the entire selected dataset and incur corresponding model, time, and resource costs. Review the dataset and deployment limits before launching.

<details>
<summary>🖥️ See the Console</summary>
<br>
<table>
  <tr>
    <td align="center"><a href=".github/assets/console-gallery/experiments.png"><img src=".github/assets/console-gallery/experiments.png" alt="Experiments view" width="360"></a><br><strong>Experiments</strong></td>
    <td align="center"><a href=".github/assets/console-gallery/versions.png"><img src=".github/assets/console-gallery/versions.png" alt="Harness versions view" width="360"></a><br><strong>Versions</strong></td>
  </tr>
  <tr>
    <td align="center"><a href=".github/assets/console-gallery/editor.png"><img src=".github/assets/console-gallery/editor.png" alt="Harness editor view" width="360"></a><br><strong>Editor</strong></td>
    <td align="center"><a href=".github/assets/console-gallery/traces.png"><img src=".github/assets/console-gallery/traces.png" alt="Task trajectories view" width="360"></a><br><strong>Trajectories</strong></td>
  </tr>
</table>
<sub>Click an image to view it at full size.</sub>
</details>

<a id="citation"></a>

## 📖 Citation

If the platform, the measurement protocol or a curve produced here is useful in your work:

```bibtex
@misc{xu2026harnessgrad,
  title  = {HarnessGrad: Harness as A Continual Learner},
  author = {Buqiang Xu and Yenan Wang and Yutong Yu and Rongyao Li and
            Jintian Zhang and Shuofei Qiao and Xuehai Wang and Zirui Xue and
            Yijun Chen and Ningyu Zhang},
  year   = {2026},
}
```

<a id="related-work"></a>

## 🔗 Related work

<div align="center">
  <table>
    <tr>
      <td align="center" width="150">
        <a href="https://github.com/RUC-NLPIR/FlashRSI">
          <img src="https://avatars.githubusercontent.com/u/139615285?v=4&s=200" width="80" style="border-radius:50%" alt="FlashRSI"/>
          <br />
          <sub><b>FlashRSI</b></sub>
        </a>
      </td>
      <td align="center" width="150">
        <a href="https://github.com/simple-agent-lab/RSIHub">
          <img src="https://avatars.githubusercontent.com/u/283423986?v=4&s=200" width="80" style="border-radius:50%" alt="RSIHub"/>
          <br />
          <sub><b>RSIHub</b></sub>
        </a>
      </td>
      <td align="center" width="150">
        <a href="https://github.com/Prism-Shadow/penguin-harness">
          <img src="https://avatars.githubusercontent.com/u/236323912?v=4&s=200" width="80" style="border-radius:50%" alt="PenguinHarness"/>
          <br />
          <sub><b>PenguinHarness</b></sub>
        </a>
      </td>
      <td align="center" width="150">
        <a href="https://github.com/quqxui/RSI-Flow">
          <img src="https://avatars.githubusercontent.com/u/41822324?v=4&s=200" width="80" style="border-radius:50%" alt="RSI-Flow"/>
          <br />
          <sub><b>RSI-Flow</b></sub>
        </a>
      </td>
    </tr>
  </table>
</div>

<a id="license"></a>

## 📜 License

MIT. Third-party benchmark code keeps its own licence and provenance next to the code that uses
it (`PROVENANCE.json`, `LICENSE.upstream`).
