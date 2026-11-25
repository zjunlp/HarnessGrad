<p align="center">
  <img src=".github/assets/logo.svg" alt="" width="76">
</p>

<h1 align="center">HarnessGrad: Harness as A Continual Learner</h1>

<p align="center"><strong>Build agent harnesses that improve with feedback.</strong><br>Run reproducible experiments, build for a new domain, or turn corrections into changes to your own harness.</p>

<p align="center">🧭 <a href="#table-of-contents">Contents</a> · 🧪 <a href="#run-experiments">Experiments</a> · 🛠️ <a href="#customize-harness">Customize</a> · 🌱 <a href="#evolve-personal-harness">Evolve</a> · 📚 <a href="docs/README.md">Docs</a> · 💬 <a href="https://github.com/Xubqpanda/HarnessGrad-Community">Community</a> · 📖 <a href="#citation">Citation</a></p>

<p align="center">
  <a href="https://github.com/zjunlp/HarnessGrad/actions/workflows/quality.yml"><img src="https://github.com/zjunlp/HarnessGrad/actions/workflows/quality.yml/badge.svg" alt="Quality checks"></a>
  <a href="https://github.com/zjunlp/HarnessGrad/actions/workflows/deploy.yml"><img src="https://github.com/zjunlp/HarnessGrad/actions/workflows/deploy.yml/badge.svg" alt="Website deployment"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2f855a.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-3776ab.svg?logo=python&amp;logoColor=white" alt="Python 3.12 or newer">
</p>

<p align="center">
  <a href="https://zjunlp.github.io/HarnessGrad/"><img src="https://img.shields.io/badge/Website-HarnessGrad-285f50?logo=githubpages&amp;logoColor=white" alt="Project website"></a>
  <a href="docs/README.md"><img src="https://img.shields.io/badge/Docs-Explore-385fc4?logo=readthedocs&amp;logoColor=white" alt="Documentation"></a>
  <a href=".github/CONTRIBUTING.md"><img src="https://img.shields.io/badge/Contribute-Get%20involved-bb6346?logo=github&amp;logoColor=white" alt="Contributing guide"></a>
  <a href="https://github.com/Xubqpanda/HarnessGrad-Community#wechat"><img src="https://img.shields.io/badge/Community-WeChat-07c160?logo=wechat&amp;logoColor=white" alt="Join the WeChat community"></a>
</p>

<a id="choose-your-path"></a>

## ✨ Choose your path

<table>
  <tr>
    <td align="center" width="33%">
      <a href="#run-experiments"><img src=".github/assets/showcase/experiments-preview.png" alt="ScienceWorld experiment curve" width="245"></a>
      <br><strong>🧪 Run experiments</strong>
      <br>Follow how a harness changes across rounds.
      <br><a href="#run-experiments">Open experiments →</a>
    </td>
    <td align="center" width="33%">
      <a href="#customize-harness"><img src=".github/assets/showcase/ocean-preview.png" alt="Ocean Harness showing research evidence" width="245"></a>
      <br><strong>🛠️ Customize a harness</strong>
      <br>See a domain harness collect evidence and answer.
      <br><a href="#customize-harness">Open customization →</a>
    </td>
    <td align="center" width="33%">
      <a href="#evolve-personal-harness"><img src=".github/assets/showcase/xiaolv-preview.png" alt="Xiao Lü desktop assistant alongside a chat" width="245"></a>
      <br><strong>🌱 Evolve your harness</strong>
      <br>Turn a correction into a proposed change.
      <br><a href="#evolve-personal-harness">Open personal evolution →</a>
    </td>
  </tr>
</table>

<a id="table-of-contents"></a>

## 🧭 Table of Contents

- [🧪 Run experiments](#run-experiments)
  - [Quick start](#quick-start)
  - [Experiment integrations](#what-you-can-run) · [Where to start](#where-to-start)
  - [Execution and comparison](#why-one-command-is-enough) · [Installation](#installation)
  - [Dataset storage](#dataset-storage)
  - [Experiment workbench](#the-workbench) · [Contributors](#contributors)
- [🛠️ Customize a harness](#customize-harness)
  - [Installation](#customize-installation) · [Quick start](#customize-quick-start)
  - [Customization workbench](#customize-workbench)
- [🌱 Evolve your personal harness](#evolve-personal-harness)
  - [Installation](#personal-installation) · [Quick start](#personal-quick-start)
- [💬 Community](https://github.com/Xubqpanda/HarnessGrad-Community)
- [📚 Documentation](docs/README.md)
- [📖 Citation](#citation)
- [🔗 Related work](#related-work)
- [📜 License](#license)

> [!TIP]
> Expand a workflow to see what you can build with HarnessGrad and how to get started.

<details>
<summary><strong>🧪 Run experiments</strong> — compare harnesses and RSI methods under recorded conditions</summary>

<a id="run-experiments"></a>

Track how a harness performs as an improvement method edits it over successive rounds. The charts below show runs on three benchmarks.

<table>
  <tr>
    <th>ALFWorld</th>
    <th>AppWorld</th>
    <th>ScienceWorld</th>
  </tr>
  <tr>
    <td><a href=".github/assets/showcase/alfworld-panel-1.png"><img src=".github/assets/showcase/alfworld-panel-1.png" width="266" alt="alfworld panel-1 curve"></a><br><a href=".github/assets/showcase/alfworld-panel-2.png"><img src=".github/assets/showcase/alfworld-panel-2.png" width="266" alt="alfworld panel-2 curve"></a></td>
    <td><a href=".github/assets/showcase/appworld-panel-1.png"><img src=".github/assets/showcase/appworld-panel-1.png" width="266" alt="appworld panel-1 curve"></a><br><a href=".github/assets/showcase/appworld-panel-2.png"><img src=".github/assets/showcase/appworld-panel-2.png" width="266" alt="appworld panel-2 curve"></a></td>
    <td><a href=".github/assets/showcase/scienceworld-training.png"><img src=".github/assets/showcase/scienceworld-training.png" width="266" alt="scienceworld training curve"></a><br><a href=".github/assets/showcase/scienceworld-validation.png"><img src=".github/assets/showcase/scienceworld-validation.png" width="266" alt="scienceworld validation curve"></a></td>
  </tr>
</table>

[PersonaMem-v2 training results](https://huggingface.co/datasets/Xubqpanda/HarnessGrad/tree/main/Experiments/PersonaMem-v2/qwen3.5-9b_loop/dsh-deepseek-v4.1-flash-1017) include H0–R20 curves and text-free task traces for seven methods.

<a id="quick-start"></a>

### 🚀 Quick start

**How does one round of DGM change Loop’s performance on math word problems?** The task agent uses **Loop 0.2.0 (study source) with Qwen3.5-9B** to solve five GSM8K training questions. DGM uses **DeepSeek Harness (headless) with DeepSeek-V4.1-Flash** to edit Loop, then HarnessGrad scores the candidate on the same questions. [Set up the two agents and GSM8K](docs/getting-started.md#run-the-readme-experiment).

```bash
harnessgrad run --preset gsm8k-dgm-study
```

Open `runs/gsm8k-dgm-study/end-summary.md` to compare the two rounds. For a repeat run, add `--run-id` with a new name.

### 🔬 From a question to an experiment

**Choose a harness → Select an RSI method → Run tasks → Inspect the evidence.** Use the same recorded conditions when comparing results, then explore the learning curve, versions and task trajectories in the Console.

<p align="center"><img src=".github/assets/learning-loop.svg" width="720" alt="Learning loop: tasks lead to feedback, editing the harness and evaluation, which returns to tasks."></p>

**PTC-Bench** covers single-turn tool use, recovery from scheduled API failures, and follow-up requests over shared simulated state. The [adapter guide](data/datasets/ptc_bench/README.md) covers preparation and the [80/80/80 research split](experiments/splits/ptc-bench/README.md) defines checkpoint selection and held-out evaluation.

**WTM-Bench** covers 150 Level 5 spreadsheet tasks. The [adapter guide](data/datasets/wtm_bench/README.md) explains preparation and the released workbook score used by HarnessGrad.

<a id="what-you-can-run"></a>

### 🧩 Experiment integrations

A research run is one point in a **harness × method × benchmark** space: a *harness* solves tasks, a *method* proposes edits to that harness between rounds, and a *benchmark* scores each candidate. HarnessGrad provides tasks and scoring, isolated execution, and version control for candidate harnesses, with a recorded curve for each experiment.

> [!NOTE]
> The inventory lists declared integrations; each harness and benchmark has its own preparation and runtime requirements.

<details>
<summary>Full harness, method and benchmark inventory</summary>

The table records the inventory at this README revision; the benchmark row is checked against the declared adapters.

| axis | what ships | where |
| --- | --- | --- |
| **harnesses** | 8: `loop`, `codex`, `opencode`, `deepseek-harness`, `openclaw`, `miniagent`, `miniswe`, `oracle` | `base_harness/<name>/` |
| **RSI methods** | 19: `dgm`, `rrsi`, `adas`, `ahe`, `ace`, `skillopt`, `evoprompt`, `evo_harness`, `opro`, `tthe`, `dream_rsi`, `sica_ci`, `gepa`, `darwinx`, `arbor`, `hyperagents`, `meta_harness`, `harnessx`, `modular_rsi` | `experiments/method-<name>.json` |
| **benchmarks** | 58 declared adapters; preparation and runtime prerequisites vary — every one listed: `agentdojo`, `aime`, `alfworld`, `appworld`, `authored_example_fix_bug`, `bbeh`, `bfcl_v3`, `bird_minidev`, `browsecomp`, `chessbench_puzzles`, `cooperbench`, `costbench`, `costbench_dynamic`, `deepsearchqa`, `deepswe`, `ds1000`, `fever`, `gaia`, `gsm8k`, `harvey_lab`, `hle`, `hotpotqa`, `jericho`, `jobbench`, `kumo`, `livecodebench`, `livemathbench`, `locomo`, `math`, `math500`, `memoryagentbench`, `musique`, `nq`, `officeqa_pro`, `personamem_v2`, `physicsquestions`, `ptc_bench`, `reasoning_gym`, `scienceworld`, `searchqa`, `sportsunderstanding`, `spreadsheetbench`, `spreadsheetbench2`, `strategyqa`, `strongreject`, `supergpqa`, `swe_bench`, `swe_bench_pro`, `tau2_bench`, `tau3_bench`, `tau_bench`, `terminal_bench`, `travelplanner`, `triviaqa`, `twowikimultihopqa`, `widesearch`, `workbench`, `wtm_bench` | `data/datasets/`, detail in [`DATASETS.md`](DATASETS.md) |

</details>

<a id="where-to-start"></a>

### Where to start

- **Bring a harness:** supply a directory with `harness.json` and an entrypoint accepting `--task` and `--workdir`. See [bring your own harness](docs/bring-your-own-harness.md).
- **Bring a method:** supply a program that reads a trajectory and writes a candidate tree. See [bring your own method](docs/bring-your-own-method.md).
- **Bring a benchmark:** write an adapter that materialises tasks and runs its official scorer. See [dataset onboarding](docs/dataset-onboarding.md).

<a id="why-one-command-is-enough"></a>

### ⚙️ Why one command is enough

A harness supplies `harness.json` and an entrypoint accepting `--task` and `--workdir` ([interface](INTERFACE.md)). The platform runs dataset tasks and scoring, isolates execution, versions candidate harnesses, and records each run's outcomes and receipts. Dataset and harness prerequisites still need to be installed separately.

<a id="why-the-comparison-is-fair"></a>

### ⚖️ Why the comparison is fair

Each round records the candidate harness hash and its changes, alongside the run's model roles and measurement metadata.

> [!IMPORTANT]
> Compare results only when the task selection, scorer, models and other relevant conditions support it. Failed or incomplete measurements are not scores; do not substitute proxy scores under a benchmark name. See the [measurement policy](docs/refusal-policy.md).

<a id="installation"></a>

### 📦 Installation

Requires Linux, Python 3.12+, Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and a working `bubblewrap`. See the [first-use guide](docs/getting-started.md) for setup details.

```bash
git clone https://github.com/zjunlp/HarnessGrad.git
cd HarnessGrad
uv venv --seed --python 3.12 ~/.venvs/harnessgrad
source ~/.venvs/harnessgrad/bin/activate
uv pip install -e .
./install.sh --check
```

<a id="dataset-storage"></a>

### 🗂️ Dataset storage

Set the shared external dataset directory in your local `.env`:

```dotenv
HG_DATA_ROOT=/path/to/datasets
```

See [downloads](DOWNLOADS.md) for preparation and locations, and [datasets](DATASETS.md) for adapter details.

> [!TIP]
> New to HarnessGrad? The [first-use guide](docs/getting-started.md) includes an installation control that does not need a model call or dataset download.

<a id="the-workbench"></a>

### 🖥️ Experiment workbench

Use the shared Console to inspect experiments, harness versions and task trajectories.

```bash
export HG_CONSOLE_RUNS="$PWD/runs"  # existing run-record directory
python console/server.py --port 4460
```

Open [http://127.0.0.1:4460/](http://127.0.0.1:4460/) from the repository root, or point `HG_CONSOLE_RUNS` to another existing run-record directory. The Console binds to localhost by default.

<table>
  <tr>
    <td align="center"><a href=".github/assets/console-gallery/experiments.png"><img src=".github/assets/console-gallery/experiments.png" alt="Experiments view" width="360"></a><br><strong>Experiments</strong></td>
    <td align="center"><a href=".github/assets/console-gallery/versions.png"><img src=".github/assets/console-gallery/versions.png" alt="Harness versions view" width="360"></a><br><strong>Versions</strong></td>
  </tr>
  <tr>
    <td align="center"><a href=".github/assets/console-gallery/traces.png"><img src=".github/assets/console-gallery/traces.png" alt="Task trajectories view" width="360"></a><br><strong>Trajectories</strong></td>
  </tr>
</table>

<a id="contributors"></a>

### 🌱 Contributors

Thank you to everyone who contributes harnesses, methods, benchmarks, and improvements to HarnessGrad! The three charts show the most recent 30 days; each line includes contributions made before that window.

Want to join? [Bring a harness](docs/bring-your-own-harness.md), [add a method](docs/bring-your-own-method.md), or [onboard a benchmark](docs/dataset-onboarding.md), then open a pull request. We will add your name to the relevant contributor chart.

<!-- BEGIN contributors (generated by tools/contributors.py --write) -->
![Harnesses: who contributed, by day](.github/assets/contributors-harness.svg)

![RSI methods: who contributed, by day](.github/assets/contributors-method.svg)

![Benchmarks: who contributed, by day](.github/assets/contributors-benchmark.svg)
<!-- END contributors -->

</details>

<details>
<summary><strong>🛠️ Customize a harness</strong> — build for a model, environment or task</summary>

<a id="customize-harness"></a>

<p align="center"><a href=".github/assets/showcase/ocean.gif"><img src=".github/assets/showcase/ocean.gif" alt="Ocean Harness assembles research evidence and returns a sourced answer" width="800"></a></p>

Ocean Harness is one example built with HarnessGrad. It combines domain sources, research tools, and an answer workflow to answer ocean questions with citations. Build a harness for your own domain using the workflow below.

<a id="customize-installation"></a>

### 📦 Installation

Use Python 3.12+ and install from the HarnessGrad checkout. For model-generated edits, configure an editor endpoint in the [customization guide](docs/customization.md).

```bash
python -m pip install .
```

<a id="customize-current"></a>

### Current capabilities

**Choose a starting harness → Edit a candidate → Inspect the diff → Try it on tasks.**

Start from a base or saved candidate, edit its files with an assistant and evaluate the result. See [bring your own harness](docs/bring-your-own-harness.md). The [customization proposal command](docs/customization.md) can also generate an unevaluated candidate from explicitly supplied feedback and text, with options to refine instructions from failure feedback, propose memory-backed skills and export a source-checked patch for review.

> [!TIP]
> Already have a harness? Run `harnessgrad customize inspect --root ./my-harness` to see the components declared in its manifest, or see [bring your own harness](docs/bring-your-own-harness.md) for the entrypoint and manifest format.

<a id="customize-quick-start"></a>

### 🚀 Quick start

[Install HarnessGrad](docs/getting-started.md) and configure an OpenAI-compatible editor endpoint as described in the [customization guide](docs/customization.md). Choose the files and feedback you want to share with that endpoint; for example, put the following in `feedback.json`:

```json
[{"id": "review-1", "kind": "failure", "text": "The task ended without running the project tests."}]
```

```bash
harnessgrad customize collect --root ./my-harness --skill skills/main.md --feedback ./feedback.json > request.json
harnessgrad customize propose --request request.json --max-calls 8 > proposal.json
harnessgrad customize review --request request.json --proposal proposal.json --root ./my-harness
```

Replace `./my-harness` and `skills/main.md` with your own selected paths. The proposal includes candidate text and a diff for review; use the [guide](docs/customization.md#export-a-patch) to export a patch and test it on a copy.

<a id="customize-workbench"></a>

### Customization workbench

The same Console used to inspect experiments also provides a workspace for editing harness candidates. See the [workbench guide](console/WORKBENCH.md) for setup and sessions.

<p align="center"><a href=".github/assets/console-gallery/editor.png"><img src=".github/assets/console-gallery/editor.png" alt="Harness editor view" width="720"></a><br><strong>Harness editor</strong></p>

</details>

<details>
<summary><strong>🌱 Evolve your harness</strong> — turn feedback into improvements to your own harness</summary>

<a id="evolve-personal-harness"></a>

<p align="center"><a href=".github/assets/showcase/xiaolv.gif"><img src=".github/assets/showcase/xiaolv.gif" alt="Xiao Lü demonstrates turning personal feedback into a proposed change" width="760"></a></p>

Xiao Lü is one example of personal Harness evolution built with HarnessGrad. The desktop assistant shows how a correction from a session can become a scoped change proposal for review.

<a id="personal-installation"></a>

### 📦 Installation

Use Python 3.12+ and install from the HarnessGrad checkout. To build the native desktop shell, follow the [desktop setup guide](desktop/personal/README.md).

```bash
python -m pip install .
```

<a id="personal-quick-start"></a>

### 🚀 Quick start

Open the local personal workspace with a synthetic session. Choose an existing project directory and a separate state directory outside the checkout and project.

```bash
harnessgrad personal demo --state-root /path/to/state --project /path/to/project
harnessgrad personal serve --state-root /path/to/state
```

Open the printed local URL and select the service's `connection.json` in the browser. This example previews the workspace without model calls. For the desktop workflow shown above, see the [personal evolution guide](docs/personal-evolution.md) and the [proposal preview in PR #64](https://github.com/zjunlp/HarnessGrad/pull/64).

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
    </tr>
  </table>
</div>

<a id="license"></a>

## 📜 License

MIT. Third-party benchmark code keeps its own licence and provenance next to the code that uses it (`PROVENANCE.json`, `LICENSE.upstream`).
