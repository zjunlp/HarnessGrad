# CostBench static sequence-5

This adapter executes the released CostBench travel tools behind HarnessGrad's
selected-task relay and scores its sealed conversation with the pinned official
evaluator. The candidate harness owns planning and tool choice. The relay owns
environment state, costs and the 20-turn limit; the actor cannot reset it or submit
a replacement trajectory.

Source: [JiayuJeff/CostBench](https://github.com/JiayuJeff/CostBench/tree/6424069f3f773ade076bcedbed302ed47cfb1837),
revision `6424069f3f773ade076bcedbed302ed47cfb1837`.
The archive, executable source files, released query files and prepared manifest
are pinned by SHA-256. `source-files.json` lists executable source pins;
`requirements.lock` pins the isolated Python 3.12 environment. Large datasets and
generated databases live outside the repository.

## Scope and protocol

| Setting | Value |
| --- | --- |
| Released train / test | 1,912 / 381 tasks |
| HarnessGrad train / eval | Official train / official test, with split-namespaced IDs |
| Domains | Location, transportation, accommodation, attraction, dining, shopping |
| Atomic sequence | Five steps, including two refinement steps |
| Whole-chain composite tool | Disabled |
| Atomic cost range / composite noise | 15–25 / 0.1 |
| Global tool seed | 42, with the upstream SHA-256 derivation per query |
| Episode limit | 20 assistant turns, including final submission |
| Dynamic interventions | Disabled |

Tool prices are generated once per query and stay fixed. Different HarnessGrad
trial seeds identify runs without resampling the benchmark's pinned tool prices.
Only the first tool call in an assistant turn executes. A recognized final answer
takes precedence over tool calls in the same turn. Schema inspection and status
requests do not consume turns. These are environment limits; the adapter does not
choose a model, model endpoint, token limit or new wall-clock budget.

The released files contain 1,912 training tasks; the adapter uses that observed
inventory rather than the paper's 1,902 figure. Preparation verifies that execution
queries and the filtered query release differ only by the rendered `query.input`.

## Training compatibility

The published runtime's enums describe test vocabulary, while the training
configuration declares different legal values. Training IDs have seven digits;
the published runtime and answer extractor assume five. An unmodified audit of
all 1,912 training rows reproduces the enum failures and answer mismatch.

The training runtime therefore builds enums from the author's training YAML and
uses seven-digit IDs. Test execution retains its original vocabulary and ID
length. The official evaluator remains unchanged. Its raw training answer score
is preserved, and the repaired score is reported separately as `train_compat_tcr`
with identity `costbench.train.seven_digit_candidate_tcr.v1`. It is never applied
to test rows. The primary path metric does not depend on this answer repair.

## Metrics and cost meaning

The scalar reward is `evaluate_single_query()['tool_path_exact_match']`, named
`costbench.static.sequence5.per_query_path_exact_match.v1`. Its mean includes
**every measured task/trial**. A no-op scores zero. Runtime, integrity or scorer
failures are unmeasured and cannot silently become task zeros.

The separate official aggregate retains upstream eligibility masks and raw
results. In particular, upstream aggregate EMR excludes conversations with at
most three messages; its denominator can differ from the primary metric's.
Cost metrics require an eligible episode that reaches the goal state. When a
denominator is zero, presentation metrics are `null`, not an apparent zero cost.
Reports expose counts for accuracy, path, cost, tool use and total tool calls.

The official scorer filters invalid tool calls out of the path and its cost.
Consequently, a matching valid path can receive primary reward 1 despite extra
invalid calls or an incorrect final answer. Always inspect TCR and invalid-tool-use
ratio alongside path exact match. `cost_gap_raw_units` is mean agent cost minus
mean reference cost; `official_avg_delta_atomic` retains upstream's different,
atomic-cost-normalized quantity. `report.py` reruns the original batch evaluator
on full query records rather than averaging per-task ratios.

This exercises HarnessGrad's **cost-aware tool selection** surface. CostBench's
prices are simulated task costs, not measured model tokens, money, latency or a
hard spending cap. The adapter does not establish coverage of all meanings of
HarnessGrad cost, nor does its per-query reward claim to reproduce the paper's
filtered aggregate EMR.

## Preparation and controls

Run from the repository with a Python 3.12 platform environment and `uv` available:

```bash
export HG_COSTBENCH_DATA=/absolute/path/outside/repository/costbench
python3 data/datasets/costbench/prepare.py --root "$HG_COSTBENCH_DATA" --install
```

Preparation downloads the pinned archive, installs the isolated runtime,
regenerates the official lookup databases, freezes each query's tool prices and
optimal path, and executes **all train and test gold paths**. It writes
`prepared-v1/manifest.json`, `gold-audit.json` and `raw-train-audit.json`.
Use the default full split order (`train test`) for the pinned adapter manifest;
partial preparation is diagnostic only. The generated lookup database is
losslessly partitioned by location preference to avoid reloading a gigabyte per
tool call. Each accessed shard is digest-checked before the original lookup runs.

`HG_COSTBENCH_RUNTIME` optionally selects the installed runtime directory; by
default it is `$HG_COSTBENCH_DATA/runtime`. The driver/verifier also requires
Python 3.12 because it loads this runtime's compiled dependencies. Metadata
discovery remains lazy and does not need prepared data or those dependencies.

```bash
python3 data/datasets/costbench/control.py --oracle --platform \
  --task-id costbench/test/Query00001 --output /tmp/costbench-test-gold.json
python3 data/datasets/costbench/control.py --noop --platform \
  --task-id costbench/train/Query00003 --output /tmp/costbench-train-noop.json
python3 -m pytest -q tests/quality/test_costbench_dataset.py \
  tests/quality/test_costbench_official_scorer.py \
  tests/quality/test_costbench_preparation.py \
  --basetemp=/tmp/hg-costbench-tests
```

Omit `--platform` for the direct relay/verifier control. Oracle actions are
injected only into the control harness; normal task setup exposes only the
selected public query, official instructions, tool schemas and client. The
private case, lookup tables, reference path and scorer are outside the actor's
filesystem projection. The trusted relay seals its record after execution, and
verification checks task, source, case and seed bindings. Actor files cannot
replace the trusted record or scorer inputs. Model traffic follows the chosen
harness/platform configuration; this adapter makes no additional egress claim.

The full preparation audit passed 2,293/2,293 path matches, with exact reference
costs and zero normalized edit distance. Test answer correctness is 381/381 under
the original scorer; training answer correctness is 1,912/1,912 under the named
compatibility metric (the original training answer scorer remains 0/1,912).
These are no-model reachability controls, not model performance measurements.

## Aggregating an evaluation

Build `expected-cells.json` before evaluation as a JSON list of
`{"task_id": "...", "run_id": "...", "trial_id": "..."}` objects. Extract each
trusted verifier evidence object from the corresponding HarnessGrad result into
a separate file, then run:

```bash
python3 data/datasets/costbench/report.py \
  --bundle "$HG_COSTBENCH_DATA/bundle" \
  --runtime "$HG_COSTBENCH_DATA/runtime" \
  --expected-cells /path/to/expected-cells.json /path/to/evidence/*.json
```

The report refuses missing, duplicate, unexpected, unmeasured or unverified
cells. Use an expected manifest for the intended split; keep training diagnostics
and official test results separate. Preserve incomplete receipts and correct
infrastructure failures before making complete-split comparisons.

## Real API validation (2026-10-21)

The latest static diagnostic used `Ling-3.1-flash` with `base_harness/loop`
and the operator's API configuration. Run
`costbench-static-ling31-24-display50k-20261021-01` measured all 24 preselected
tasks: 12 train and 12 test, two per domain on each split, one trial and one job.
It ran adapter commit `d970a47da83d1f2845afe9e8a05ff2596593a48a`, with manifest
`7bcc4ba0bc653229b5536f625d797217b3b48f31764314d9d2a397e2e8332587`.

| Split | Selected / measured | Reference path matches | Correct answers across selected tasks | Official path / answer / cost denominator |
| --- | --- | --- | --- | --- |
| Train | 12 / 12 | 5 / 12 | 7 / 12 (training compatibility) | 7 / 7 / 7 |
| Test | 12 / 12 | 4 / 12 | 7 / 12 (official answer check) | 7 / 7 / 7 |

All tasks were measured, with no invalid or excluded attempts. Fourteen episodes
submitted answers; ten ended after parsing retries and remain measured zeros.
The primary means are 5/12 and 4/12, while the eligibility-filtered official EMR
is 5/7 and 4/7. Raw official training TCR remains zero; `train_compat_tcr` is 7/7
on eligible training episodes. These denominators must not be interchanged.

The display limit was temporarily set to `HG_AGENT_OUTPUT_LIMIT=50000` in child
processes. The loop source and `.env` were unchanged; this is not a measurement
of the default display limit. The remaining limits were 32 model calls, 2,048
output tokens per call, a 60,000-character history budget and the official
20-turn episode limit. No output clipping occurred. All 24 relay cleanups passed,
and temporary work directories were archived and removed. Provider usage is
recorded separately from simulated tool costs; no currency cost is claimed.

The summary, launch and source identities, train/test reports and cleanup receipt
are retained outside Git. This is a small
integration diagnostic, not a full-benchmark estimate. Earlier model/display
diagnostics remain preserved and are not pooled into these 24 measurements.

| Earlier diagnostic on the same 24 selected tasks | Measured | Reference path matches | Correct answers | Display clipping events |
| --- | --- | --- | --- | --- |
| Ling-3.0-flash, original display limit | 24 | 0 | 0 | 97 |
| Ling-3.0-flash, temporary 50,000-character display limit | 24 | 1 (train) | 0 | 0 |

The original-display report combines valid measurements from fresh recovery
runs; earlier mount, network and interrupted attempts are excluded and preserved
in that report. The 50,000-character Ling-3.0 run ended in parsing retry exhaustion
on all 24 tasks. These histories distinguish display coverage from model outcomes.

The evidence files below are retained only in the author's local workspace,
under `/mnt/40t/wangyenan/Projects/HarnessGrad-Workspace/runs/`. They are not
checked into this repository and are not accessible through GitHub links.
The validation results and their limitations are summarized above.

- `costbench-static-ling31-24-display50k-20261021-01/`: `summary.json`, `launch.json`, `official-train-report.json`, `official-test-report.json`, `cleanup.json`.
- `costbench-static-ling24-20261021-summary/`: `summary.json`.
- `costbench-static-ling24-display50k-20261021-01/`: `summary.json`.
