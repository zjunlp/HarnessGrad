# CostBench dynamic sequence-5

This adapter adds four **separate one-intervention protocols** to the existing
CostBench static integration. The static dataset and metric remain available as
`costbench`; dynamic tasks are registered as `costbench_dynamic`.

The implementation reuses the pinned upstream `DynamicBlocker`, tool generator,
reference solver and evaluator at revision
`6424069f3f773ade076bcedbed302ed47cfb1837`. There are 1,912 released training and
381 released test queries per mode, or 9,172 mode/query cases in total. IDs are
`costbench_dynamic/<mode>/<train|test>/QueryNNNNN`. Replacement preferences are
drawn only from the same released split.

## Protocol

| Setting | Value |
| --- | --- |
| Modes | `cost_change`, `steplen_change`, `ban_tool`, `preference_change` |
| Events per episode | One, using the upstream deterministic trigger schedule |
| Atomic sequence | Five steps (`refinement_level=2`) |
| Whole-chain composite tool | Disabled |
| Costs and seeds | Atomic range 15–25; composite noise 0.1; global seed 42 and upstream per-query derivation |
| Episode limit | 20 assistant turns, including final submission |
| Mixed/multiple events | Not registered by this adapter version |

Cost and tool-length changes occur after a tool result; a scheduled ban occurs
before execution. Preference changes append the upstream user message and reset
reachable types. `steplen_change` follows the pinned implementation: it removes
composites whose component count equals the range's lower endpoint; it does not
filter all tools outside the named range. A banned tool remains visible and its
failed invocation is charged and retained in the benchmark's valid tool path.
The actor and reference may ban different tools because their paths differ.

`python3 costbench.py show` reads the relay's **current** query and schemas.
Intervention responses return updated schemas or a new user message as relevant.
Inspection consumes no turn and cannot reset or reconfigure the environment.
Future schedules, replacement preferences, random seeds, reference plans and
cost-optimal paths remain private. The relay seals the sole scoring record.

## Metrics and compatibility

Each mode has its own primary metric:
`costbench.dynamic.sequence5.<mode>.block1.per_query_path_exact_match.v1`.
The primary mean includes every measured task/trial. An actor that stops before
its required event scores zero; the same episode may be excluded by upstream's
aggregate eligibility filters. Reports retain those original results, explicit
denominators and an `event_incomplete_count`. Missing reference data, service
failures and scorer failures remain unmeasured errors.

All four modes use the static adapter's [measurement-validity policy](../costbench/README.md#metrics-and-cost-meaning):
native sandboxed execution with `network=none`, an observed agent model route,
and unmeasured outcomes for inference HTTP 429/5xx even after recovery.
Raw scorer evidence is preserved and
must not be reported as a valid measurement when the platform result is `invalid`.

The evaluator compares against its segmented dynamic reference, including
charged failed ban attempts. A correct final answer need not have an optimal
path. Tool prices are simulated benchmark units, not API spending or model-token
cost. Static and dynamic CostBench count as one benchmark family for HarnessGrad
coverage; dynamic adds replanning/recovery behavior, not a new cost definition.

Training retains the static adapter's declared enum and seven-digit ID repairs.
Its answer metric is separately named
`costbench.dynamic.train.seven_digit_candidate_tcr.v1`, selecting the reference
scenario according to the actual effective intervention count. Raw official
training scores are preserved. The dynamic blocker's hardcoded relative test
lookup path is routed to the selected split's digest-checked database so reference
recalculation works for both splits. No upstream source file is changed.

## Preparation and controls

First prepare the existing static dataset. Then, using the pinned Python 3.12
runtime:

```bash
export HG_COSTBENCH_DATA=/absolute/path/outside/repository/costbench
"$HG_COSTBENCH_DATA/runtime/bin/python" data/datasets/costbench_dynamic/prepare.py \
  --root "$HG_COSTBENCH_DATA"
```

Preparation verifies the static manifest and released queries, freezes private
plans, executes every dynamic gold trajectory, and writes
`dynamic-prepared-v1/{manifest.json,gold-audit.json,tasks/}`. Each audit checks
reference path equality, goal attainment, answer correctness and exact reference
cost. `--smoke` instead writes a separate 48-case diagnostic across all modes,
splits and domains. Neither preparation calls a model or changes static artifacts.

```bash
python3 data/datasets/costbench_dynamic/control.py --oracle --platform \
  --task-id costbench_dynamic/cost_change/test/Query00001
python3 data/datasets/costbench_dynamic/control.py --noop --platform \
  --task-id costbench_dynamic/ban_tool/test/Query00001
python3 -m pytest -q tests/quality/test_costbench_dynamic_dataset.py \
  tests/quality/test_costbench_dynamic_runtime.py \
  tests/quality/test_costbench_dynamic_scoring.py \
  --basetemp=/tmp/hg-costbench-dynamic-tests
```

Controls inject private oracle scripts only into the control harness. Normal
actors receive no oracle. Platform tests cover both splits and every mode with
oracle and no-op arms, current-schema inspection, denied reconfiguration and
forged actor records. These are no-model controls, not model performance results;
the previous static API runs do not constitute a dynamic API evaluation.

Aggregate sealed evidence with `data/datasets/costbench/report.py --mode MODE`
and an explicit `--expected-cells` manifest, plus the same `--bundle`, `--runtime`
and evidence arguments used by the static report. A report accepts one mode and
rejects static/dynamic or cross-mode mixing; compare train and test separately.

## Validation (2026-10-21)

All 9,172 gold trajectories passed the complete audit. The frozen manifest SHA256
is `a424801c3d156bfc9527534e715e40ed02cd812889ae0d1c711c9d8c792e4c14`.
CostBench regression/runtime/scoring tests passed 104 cases; the prepared-data
integration suite passed 54, including both splits, all modes, and non-location
preference notifications. Both dataset declarations pass lint. Sixteen direct
oracle/no-op controls and eight report CLI checks preserve measured zeros and
the upstream cost/answer denominators. Cost equality uses tolerance `1e-8` for
ordinary floating-point summation differences.

The broader source/catalog checks have four existing failures reproduced on the
static base: unrelated import edges, unavailable adapter dependencies, and
missing aggregation declarations/inventory entries for other datasets. This
branch supplies the missing static CostBench aggregation declaration as well as
the dynamic one. It does not claim a green repository-wide suite.
Local logs, source hashes and report receipts are retained outside Git.

## Real API validation and event supplement (2026-10-21)

The real API diagnostic used `Ling-3.1-flash`, `base_harness/loop` and the
operator's API configuration at adapter commit
`2f88e25e891dc7a60b835308e9a246fc3d2fad05`, with the dynamic manifest above.
It preselected three tasks per mode/split cell: 24 tasks, one trial and one job.
The temporary child-process setting `HG_AGENT_OUTPUT_LIMIT=50000` applies to
both the original cohort and the supplement; loop source and `.env` were
unchanged. Other limits remained 32 model calls, 2,048 output tokens per call,
60,000 history characters and 20 environment turns. These are small integration
diagnostics, not default-display performance or a paired comparison of modes.

The accepted-cohort report
(`costbench-dynamic-ling31-coverage-20261021-01`) retains 24 measured tasks from
37 attempts. Thirteen initial API 429 failures were excluded and rerun, with
their original receipts preserved. There were no other invalid attempts.
At that adapter revision the shared runner could store a raw zero for terminal
429 failures; this historical report explicitly treated those attempts as
unmeasured. Current platform execution uses the HTTP validity policy above;
historical receipts are unchanged. Nine normal parsing-retry endings remain
measured zeros and were not rerun to improve scores.

| Mode | Split | Accepted | Actual / effective event tasks | Reference path matches | Correct answers |
| --- | --- | --- | --- | --- | --- |
| `cost_change` | train | 3 | 2 / 2 | 1 | 0 |
| `cost_change` | test | 3 | 1 / 1 | 1 | 1 |
| `steplen_change` | train | 3 | 3 / 3 | 2 | 2 |
| `steplen_change` | test | 3 | 0 / 0 | 0 | 0 |
| `ban_tool` | train | 3 | 2 / 2 | 1 | 2 |
| `ban_tool` | test | 3 | 2 / 2 | 1 | 2 |
| `preference_change` | train | 3 | 3 / 3 | 0 | 2 |
| `preference_change` | test | 3 | 2 / 2 | 1 | 2 |

Counts above use all accepted tasks in each cell. Training answer counts use the
named compatibility metric; raw official training TCR is still zero. Official
aggregate metrics use their own eligibility denominators, retained in the
report. In the original `steplen_change/test` cell, no event occurred and all
official aggregate denominators were zero, so those metrics are `null`.
All accepted trajectories had zero output clipping; all 37 attempts completed
relay cleanup. Integrity checks and the independent final review passed. Source
identities, resource limits and cleanup are retained in provenance and the cleanup
receipt. Thirteen failed attempts lack final provider usage, so recorded token
totals do not establish the full API bill.

The separate three-task supplement
(`costbench-dynamic-steplen-test-ling31-3-20261021-01`) preselected fresh
`steplen_change/test` tasks `Query00002`, `Query00401` and `Query00798`, spanning
location, accommodation and dining. All three were measured, triggered an actual
and effective step-length change, matched the reference path and answered
correctly. There were no provider failures, other invalid attempts, API retries
or output clipping. Each trajectory's cost matched its reference within `1e-8`.
The supplement audit and cleanup checks passed.

The coverage closure receipt therefore establishes real event execution in
**all eight mode/split cells**, up from seven in the original
cohort. The supplement remains a separate diagnostic: the original 24-task
results and receipts are unchanged, and these three successes are not pooled
into the original scores. A separate cost/reference review
recomputed representative official and adapter results without finding a
discrepancy; the segmented reference is not a globally minimum-cost episode.
