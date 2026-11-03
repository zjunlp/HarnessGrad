# RSI experiments beyond arithmetic QA

Start with **BBEH × DGM** for a small, real method run without Docker or a judge.
Choose **Terminal-Bench 2** for terminal harness work, **DeepSearchQA** for research
agents, or **SWE-bench Pro V2** for long-horizon repository repair. They exercise
different harness capabilities; a combined mean is not a meaningful result.

These are executable experiment configurations, not new benchmark results. The
five-task settings bound the initial task count; they do not demonstrate a method's
quality. Downloading source bytes does not establish native runtime readiness.

| Route | Why an RSI/Harness researcher might choose it | Preparation beyond the platform | Headline score in this port |
| --- | --- | --- | --- |
| [BBEH](https://github.com/google-deepmind/bbeh) | Hard general reasoning; diagnose context, execution and answer delivery beyond school mathematics | Pinned JSON data; agent/editor model; no Docker, web service or model judge | Native per-answer correctness |
| [Terminal-Bench 2.0](https://github.com/harbor-framework/terminal-bench-2) | Terminal tools, environment repair, long workflows; a common experimental surface in RSI work | Docker, selected task images and harness dependency overlays | Native task reward; HG snapshot-service verification |
| [DeepSearchQA](https://huggingface.co/datasets/google/deepsearchqa) | Search coverage, stopping rules and complete set answers | Search/fetch service plus an independent declared judge | Native set-answer precision/recall/F1 with an explicitly identified compatible judge |
| [SWE-bench Pro V2](https://github.com/scaleapi/SWE-bench_Pro-os/tree/66f92766bba642462d4bbe5479e83f91f9211862/v2) | Newer, difficult repository repair; separates a promising chat response from a patch that survives fresh regrading | Harbor 0.23.0 in Python 3.12+, native task images, source-bound actor/replay/verifier declarations | Native resolved reward after official patch capture/replay |

BBEH is a 2025 benchmark, not a newly released 2026 dataset. Pro V2's pinned
release commit is dated **2026-09-22** and its upstream README states 642 tasks
and HARD-51. Google's [DeepSearchQA release announcement](https://blog.google/technology/developers/deep-research-agent-gemini-api/)
is dated **2025-12-11**; its published pool has 900 research questions. This guide
does not infer release dates from local download timestamps.
HG's Terminal-Bench adapter pins **2.0**; the fact that another framework also
supports 2.1 does not upgrade this adapter's task release.

## 1. Configure the solver and the editor separately

Use the Linux installation and zero-cost demo in the README first. Activate the
same Python environment for preparation, checking and running. Set model names
to names your own endpoint actually serves:

```bash
export HG_AGENT_BACKEND=openai
export HG_AGENT_BASE_URL=http://127.0.0.1:8001/v1
export HG_AGENT_MODEL=YOUR_SOLVER_MODEL
export HG_AGENT_API_KEY=local
export HG_METHOD_BASE_URL=http://127.0.0.1:8001/v1
export HG_METHOD_MODEL=YOUR_EDITOR_MODEL
export HG_METHOD_API_KEY=local
```

The endpoint above must already exist. This does not start or download a model.
The editor may use the same served model, but its calls and credentials are a
separate configuration. Run preflight checks the selected ordinary judge and
environment model declarations before H0; `--check-net` also probes their
required endpoints with GET requests. Custom dual-provider/native-container
scorers keep their separately documented readiness requirements.
`HG_AGENT_BACKEND=mock` affects the solver only; do not
leave it exported when you intend to measure a real model.

Prepare the reference harness dependencies once in an overlay, outside its
candidate source tree:

```bash
python3 tools/configure_harness.py --harness base_harness/loop
```

The checked-in recipes use the **DGM policy port**, which edits the harness and
consumes platform-measured study feedback. They do not use `echo_base` or a no-op
as a substitute for model-driven RSI. The port's deviations from the original
DGM evaluation/feedback protocol are documented in `methods/dgm/method.json`.
`--rounds 1` requests one measured candidate after H0; it does not promise a score
increase. `fail_on_incomplete` makes method failures visible to shell scripts.

## 2. BBEH: the shortest real RSI recipe

Use a fresh external data root. The preparation command downloads the complete
pinned native test file and writes its source manifest; it does not call a model:

```bash
python3 -m pip install huggingface_hub
export HG_DATA_BBEH="$HOME/.local/share/harnessgrad/datasets/bbeh"
python3 -m data.scaffolds.native_qa.prepare --dataset bbeh --root "$HG_DATA_BBEH" --duplicate-questions exclude
harnessgrad split create --dataset bbeh --source official-eval \
  --train-ratio 0.2 --seed 42 --output splits/bbeh.json
harnessgrad split check --dataset bbeh --file splits/bbeh.json
```

BBEH publishes evaluation material, so this is an explicit **custom** study /
held-out partition carved out of its test pool. It does not become the official
BBEH train split. Keep the frozen split file for every method you compare.
The pinned source has 4520 rows, including two repeated-question groups with
9 rows in total; one group has contradictory labels. The explicit upstream
`duplicate_questions=exclude` policy removes **all** rows of both groups, leaving
4511 experiment tasks. The source file and its 4520-row hash inventory are retained
unchanged. `source-manifest.json:selection` records the policy, counts and question
ID hashes; the loader recomputes that receipt before filtering. Default `error`
still refuses duplicate questions, and keeping an arbitrary answer is not offered.

```bash
harnessgrad run --config experiments/rsi-bbeh.json --improver none --check-only --check-net
harnessgrad run --config experiments/rsi-bbeh.json --improver none
harnessgrad run --config experiments/rsi-bbeh-eval.json --improver none
python3 tools/run_summary.py runs/bbeh-dgm-study
python3 tools/run_summary.py runs/bbeh-dgm-heldout
python3 tools/results_matrix.py --runs runs --dataset bbeh --side eval \
  --md results/bbeh-heldout.md --json results/bbeh-heldout.json
```

The study run measures **five selected train tasks at H0 and after one candidate**;
the eval run measures the saved **round-1 checkpoint on five held-out tasks**.
Thus the task-evaluation count is at most 15 for this example, before retries or
method-specific stopping. Solver calls per task and editor calls are additional
budgets; five tasks is not a token or money cap. This five-task held-out result is
a smoke experiment, not the complete custom evaluation set or the official score.

`seed: 42` is recorded on the experiment; split creation also records its own
seed. The current `random_N` sampler uses its documented fixed selection rule,
and actual selected task IDs are retained in each run. Do not claim that changing
the run seed alone creates a new split or an independently resampled test set.

For a larger study, edit the training sampling/rounds deliberately. To measure
the **entire** frozen held-out side, use
`--config experiments/rsi-bbeh-eval.json --set sampling=all` with a fresh run ID.
Do not use full official evaluation on a checkpoint that studied official test
rows. The paired custom-split guard refuses that accidental overlap.

## 3. DeepSearchQA: web-research harnesses

This route needs three independent facilities: solver, editor and verifier judge,
plus a working search/fetch provider. Reuse the solver/editor block above, then
prepare the pinned source with the declared judge model:

```bash
python3 -m pip install huggingface_hub
export HG_DATA_DEEPSEARCHQA="$HOME/.local/share/harnessgrad/datasets/deepsearchqa"
export HG_JUDGE_BASE_URL=http://127.0.0.1:8001/v1
export HG_JUDGE_MODEL=YOUR_JUDGE_MODEL
export HG_JUDGE_API_KEY=local
mkdir -p experiment-inputs
python3 -c 'from pathlib import Path; Path("experiment-inputs/deepsearchqa.json").write_text("{}\n")'
python3 tools/prepare_native_research.py deepsearchqa \
  --config experiment-inputs/deepsearchqa.json --fetch \
  --output "$HG_DATA_DEEPSEARCHQA" --judge-model "$HG_JUDGE_MODEL"
export HG_WEB_SEARCH=searxng
export HG_WEB_SEARXNG_URL=http://127.0.0.1:8888
export HG_WEB_FETCH=http
export HG_WEB_PROXY=
```

The SearXNG service must already be running with JSON search enabled. This recipe
does not quietly start one. The explicit empty `HG_WEB_PROXY` selects direct
host web access; on a host requiring a proxy, set it to that host's HTTP proxy
instead. This avoids inheriting a development-machine proxy address. The
public-target and redirect checks still apply. The host owns provider configuration, fetch
SSRF checks and judge credentials. A network-isolated harness receives only the
`web.py` capability: `python3 web.py call search '{"query":"..."}'` and
`python3 web.py call fetch '{"url":"https://..."}'` inside the selected task.
Raw judge gold and the source `answer_type` are not inference inputs.

```bash
harnessgrad split create --dataset deepsearchqa --source official-eval \
  --train-ratio 0.2 --seed 42 --output splits/deepsearchqa.json
harnessgrad run --config experiments/rsi-deepsearchqa.json --improver none --check-only --check-net
harnessgrad run --config experiments/rsi-deepsearchqa.json --improver none
harnessgrad run --config experiments/rsi-deepsearchqa-eval.json --improver none
python3 tools/run_summary.py runs/deepsearchqa-dgm-heldout
```

The native autorater prompt/counts/F1 are retained. This HG route uses a declared
OpenAI-compatible judge rather than silently claiming the original Google SDK /
Gemini profile. Record the actual judge model and web provider with your result.
The five-task settings do not bound the number of searches, pages or judge tokens.
Preflight is not proof that every external page or native scorer will work.

## 4. Terminal-Bench 2: terminal and tool harnesses

Use this after BBEH if Docker environments are already part of your work. Fetch
the exact source tree outside HarnessGrad, then freeze a custom partition:

```bash
export HG_DATA_TERMINAL_BENCH="$HOME/.local/share/harnessgrad/datasets/terminal-bench"
git clone --no-checkout https://github.com/harbor-framework/terminal-bench-2.git \
  "$HG_DATA_TERMINAL_BENCH"
git -C "$HG_DATA_TERMINAL_BENCH" checkout --detach 2fd12b88aafdd04a52c298e3940bcb189f9766d6
harnessgrad split create --dataset terminal_bench --source official-eval \
  --train-ratio 0.5 --seed 42 --output splits/terminal-bench2.json
```

The source checkout is not the task runtime. Explicitly import **only the task
images this smoke pair will use**, then configure the harness's dependencies for
those image interpreters. This preparation script reads public task metadata;
the `import_env` calls may download large images:

```bash
python3 - <<'PY'
import os, subprocess, sys
from data.scaffolds import registry, custom_split
from eval.run.metrics import select_task_set
tasks, _ = registry.load('terminal_bench')
split, _ = custom_split.read('terminal_bench', tasks, 'splits/terminal-bench2.json')
ids = sorted({t for side in ('train', 'eval')
              for t in select_task_set({}, 'random_5', within=split[side])})
envs = registry.load_envs('terminal_bench', task_ids=ids)
for image in sorted({env['image'] for env in envs.values()}):
    subprocess.run([sys.executable, 'tools/import_env.py', '--image', image], check=True)
subprocess.run([sys.executable, 'tools/configure_harness.py', '--harness',
                'base_harness/loop', '--dataset', 'terminal_bench'],
               env={**os.environ, 'HG_TB_TASKS': ','.join(ids)}, check=True)
PY
harnessgrad run --config experiments/rsi-terminal-bench2.json --improver none --check-only --check-net
harnessgrad run --config experiments/rsi-terminal-bench2.json --improver none
harnessgrad run --config experiments/rsi-terminal-bench2-eval.json --improver none
python3 tools/run_summary.py runs/terminal-bench2-dgm-heldout
```

Do not export `HG_TB_TASKS` after creating the split: changing the loaded pool
would invalidate its roster hash. The script sets it only on the overlay builder.
Native task timeouts can be long, and some individual images are many gigabytes.
The reference harness must also reach its selected model route from Docker.
The task's original internet policy is preserved; this is HG's snapshot-service
scorer topology, not the Pro fresh-patch regrading protocol or a general egress
closure claim. Five native task rewards are not the Terminal-Bench leaderboard.

## 5. SWE-bench Pro V2: a prepared-host recipe

This is the most demanding route, not a first-install smoke. Source preparation
is explicit and small compared with its instance images:

```bash
export HG_DATA_SWE_BENCH_PRO="$HOME/.local/share/harnessgrad/datasets/swe_bench_pro"
python3 data/datasets/swe_bench_pro/prepare.py --root "$HG_DATA_SWE_BENCH_PRO"
harnessgrad split create --dataset swe_bench_pro --source official-eval \
  --train-ratio 0.2 --seed 42 --output splits/swe-bench-pro-v2.json
```

Before launching, prepare `harbor==0.23.0` in a separate Python 3.12+ environment,
acquire the chosen instance images, and follow
[native Harbor runtime preparation](../data/scaffolds/native_harbor_runtime/README.md).
Each selected task needs a source-bound runtime entry with local immutable
actor/replay/verifier image IDs and the pinned `v2/tooling` files. The exact CLI is:

```bash
python3 data/scaffolds/native_harbor_runtime/prepare_runtime.py \
  --dataset swe_bench_pro --data-root "$HG_DATA_SWE_BENCH_PRO" \
  --task-id "$NATIVE_TASK_ID" --actor-image "$ACTOR_IMAGE" \
  --replay-actor-image "$REPLAY_IMAGE" --verifier-image "$VERIFIER_IMAGE" \
  --harbor-python "$HARBOR_PYTHON" --pro-tooling "$HG_DATA_SWE_BENCH_PRO/v2/tooling" \
  --manifest "$HG_DATA_SWE_BENCH_PRO/native-runtime.json" --workdir /app \
  --workspace-bytes "$WORKSPACE_BYTES" --workspace-inodes "$WORKSPACE_INODES" \
  --memory-mb "$MEMORY_MB" \
  --build-actor-derivative
export HG_SWE_BENCH_PRO_RUNTIME="$HG_DATA_SWE_BENCH_PRO/native-runtime.json"
```

The uppercase placeholders are task-specific preparation inputs, not runnable
defaults. Choose filesystem, inode and memory limits from the selected native task
and image footprint; there is no universal one-gigabyte workspace that fits every
repository. Repeat for the selected train/eval tasks; do not replace private test
execution with an answer-file match or an actor's self-reported resolved flag.
Run preflight and actual execution resolve only the tasks selected by this split
and sampling policy; unavailable runtime entries for other tasks do not block
this smoke. `tools/datasets_doc.py --host` is a separate full-pool inventory and
can still report the rest as unprovisioned. Source download alone does not supply
any selected task images or scorer dependencies.

```bash
harnessgrad run --config experiments/rsi-swe-bench-pro-v2.json --improver none --check-only --check-net
harnessgrad run --config experiments/rsi-swe-bench-pro-v2.json --improver none
harnessgrad run --config experiments/rsi-swe-bench-pro-v2-eval.json --improver none
python3 tools/run_summary.py runs/swe-bench-pro-v2-dgm-heldout
```

The normal-finish path captures the patch using the pinned upstream tooling and
replays it through the author's `PatchReplayAgent` in a fresh native trial.
Tool-command timeouts currently remain **unmeasured** under HG policy; this port
does not claim upstream timeout/partial-patch equivalence. Its native Harbor
transport does not carry HG pristine-offline image/ABI closure attestation.

## 6. From a smoke pair to a research result

Use the same frozen split, harness state, model identities and evaluation budget
for each method. Evaluate H0 and a **preselected** final checkpoint on held-out
tasks; do not repeatedly pick the best checkpoint using your final exam. A
separate model-selection/dev pool is not created by these two-side recipes.
For a full held-out report, change eval sampling to `all` deliberately and record
the larger task count and trials. Missing/provider/scorer/cleanup failures stay
separate from measured wrong answers. Five tasks and one trial are useful for
checking the pipeline, not a significance claim for rebuttal.

The output directories retain curve points, per-task evidence, harness states,
cost counters and model/split identities. Export each dataset separately:

```bash
python3 tools/results_matrix.py --runs runs --side eval \
  --md results/heldout.md --json results/heldout.json
```

## What each route's sources say

The release dates and pool sizes below are quoted from the upstream release, not inferred
from a local download timestamp:

- **BBEH's official README** distinguishes the full 4520-example suite from the 460-example
  mini version; this adapter prepares its pinned full-source projection, so sampling five
  tasks does not rename that projection the official mini benchmark.
- **SWE-bench Pro V2's** pinned release commit is dated **2026-09-22** and its upstream README
  states 642 tasks and HARD-51; the DGM port does not thereby become a reproduction of the
  original DGM paper's numerical results.
- **Google's DeepSearchQA release announcement** is dated **2025-12-11**; its published pool has
  900 research questions.
- **Terminal-Bench:** HG's adapter pins **2.0**; the fact that another framework also supports
  2.1 does not upgrade this adapter's task release.

The recipes are validated as configuration/preparation contracts with local protocol controls.
This release work did not run paid models, full benchmark sweeps or claim newly measured
leaderboard numbers.
