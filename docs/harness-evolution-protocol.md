# Producing a harness-evolution result

Use this protocol to compare candidate Harness states with a frozen H0 and an
unedited control. The numerical examples and corpus counts below describe the
October 2026 study snapshot. For current setup and evidence requirements, see
[experiment recipes](rsi-experiments.md), [H0 reuse](h0-content-comparability.md)
and [evidence export](evidence-export.md).

## 1. What has to be true before a delta means anything

Measured, 2026-10-05, on `terminal_bench` with `trials=3`, `jobs=4`, **five tasks**:

| run | edits | H0 → max |
| --- | --- | --- |
| `B2-noop` | **none** (`editable_surface_touched: []`) | 0.267 → 0.467 |
| `B2-rrsi` | **none** | 0.200 → 0.467 |
| `B2-llm` | `agent.py` | 0.267 → **0.000** |

The control arm moved **+0.2 with no edit at all**. That is observed drift.
With five equally weighted tasks and three binary trials per task, one trial
flip changes the mean by `1/15 ≈ 0.0667`. A delta of that size is visible in
the score and needs the control comparison for attribution. In the historical
corpus, 96 runs had a curve and **8** had `max > H0`. The original review put
all eight rises within the observed drift band. `tau-a-r10` moved +0.018,
`strong-model-1` used two tasks, `flash-three-*` used three, and the two
largest movers were the no-edit controls.

Use the same measurement conditions across arms.

* **Use one frozen comparison point.** `--baseline <point.json>` chooses the
  reference for candidate comparisons. Without it, the candidate is compared
  with its own run's H0. Check `baseline.problems` for reference mismatches,
  including agent model, backend, Harness family, framework version, task
  set, trials and jobs. Apply the [measurement contract](../INTERFACE.md)
  to the rest of the provenance.
* **Match trials and jobs across arms.** The historical B2 no-edit control
  moved +0.2 at `temperature=0.0` with an unchanged Harness hash. Use the same
  trial count and concurrency for H0, the unedited control and candidates.
  For each point in the three-trial recipe below, check that the scored IDs
  match the planned cohort and that every task has exactly three finite,
  measured scores in `per_task_trials`.
  The runner can keep an earlier score when a later trial fails, so a task
  with a recorded score and spread can still have incomplete trial coverage.
* **Use the unedited control to check drift.** Remeasure the base Harness
  independently and compare its per-task scores with the reference.
  `baseline.problems` reports mismatched measurement conditions. Score
  variation under matching conditions is evidence from the control itself.
* **Enough tasks that one task is small.** `full-a` used 51 and is the only run in the corpus at
  a scale where a two-task move does not dominate the number.

`score_ci95` is computed over per-task scores
([record construction](../harnessgrad/experiment/records.py)). Its interpretation
as a confidence interval assumes independent task observations. Repeated trials
change the estimate for a task and leave the number of task observations fixed.
The historical ALFWorld comparison covered 134 tasks at `trials=1`:

| run | score | `score_ci95` |
| --- | --- | --- |
| `refix-alfworld-train` | 0.4146 | [0.307, 0.522] |
| `refix-alfworld-eval` | 0.5577 | [0.421, 0.694] |

One harness, one model, two halves of one pool — **0.143 apart with overlapping intervals**.
That is the marginal band, and it is why a marginal comparison cannot see a component-level
edit. Repeated trials help estimate per-task spread for the *floor* guard.
Pairing by task exposes the direction of changes on the shared cohort.

The original paired-baseline summary at `06477915` reports 63 runs and
145 candidate rounds. Its table accounts for **124 rounds**, and the source
does not explain the difference of 21. The table supports conclusions about
the tabulated rounds only.

| discordant pairs (best → worst) | rounds |
| --- | --- |
| 3 gained / 0 lost (`p = 0.25`) | 4 |
| 2 / 1, 2 / 0, 1 / 1, 1 / 0 | 8 |
| 0 gained / 1 or 2 lost | 11 |
| **0 / 0 — nothing discordant** | **101** |

**None of the tabulated rounds passed this task-level sign test.** The
largest tabulated gain was three tasks. The unedited `B2-noop` and `B2-rrsi`
each recorded 3 gains and 0 losses (`p = 0.25`).

The 101 zero-discordance rounds had no evidence of a directional difference
in this test. The records label them `direction: "unchanged"` with
`gained = lost = 0`. The original summary separately reports 75 no-edit
rounds out of 129.

For the two-sided sign test at `alpha = 0.05`, six independent discordant
pairs in one direction give `p = 0.031`. Eight against one give `p = 0.039`.
See [§4](#4-what-counts-as-a-result) for the unit of analysis and the
interpretation of these probabilities.

## 2. What each path can and cannot produce

The [staged policy](../eval/policy/diagnostic_policy.py) limits diagnostics and
temporary pilots to H0 with one trial, one job and zero improvement rounds.
The capability-floor check needs per-task spread on both sides. The paired
improvement test can use single-trial per-task scores for a compatible
comparison.

Staged publication requires passing gates and verified cleanup and snapshot
receipts. The historical diagnostics for AIME, AppWorld and ScienceWorld recorded
`score: null`, `measured_tasks: 0`, `outcome: unmeasured` and
`reason: policy_enforcement_not_observed`. See the
[runtime observation history](runtime-observation-brief.md) and
[dataset runtime interfaces](dataset-runtime-interfaces.md) for supported routes.

Use ordinary `--sampling all` for the training comparison below. Check the
selected dataset's gates and report the scope established by its receipts.

## 3. The protocol

Freeze a [custom split manifest](custom-dataset-splits.md) before launching any
arm. It must define nonempty, disjoint study and held-out task sets. Reuse the
same file for the reference, drift probe, method run and held-out evaluation.
An adapter with no declared division can expose its whole task pool on either
side. A `--tasks` filter restricts only the invocation that receives it.

For a new two-way partition, set `DATASET`, `TRAIN_COUNT`, `SPLIT_SEED` and a
new `SPLIT_FILE`, then freeze the manifest:

```sh
harnessgrad split create --dataset "$DATASET" --source all \
  --train-count "$TRAIN_COUNT" --seed "$SPLIT_SEED" --output "$SPLIT_FILE"
```

For the released research partitions, reuse the checked-in legacy `{train, eval}`
file. Its eval side contains dev and test together. The final comparison below
selects the checked-in `test.txt` from the dataset name before launching either
run.

| `DATASET` | `SPLIT_FILE` | Frozen final cohort |
| --- | --- | --- |
| [`ptc_bench`](../experiments/splits/ptc-bench/README.md) | `experiments/splits/ptc-bench/study.json` | `experiments/splits/ptc-bench/test.txt` |
| [`wtm_bench`](../experiments/splits/wtm-bench/README.md) | `experiments/splits/wtm-bench/study.json` | `experiments/splits/wtm-bench/test.txt` |

Leave `STUDY_IDS_FILE` unset to use the full train side. For a smaller cohort,
set it to a file containing one train ID per line. The block reads the file
once and passes the same comma-separated IDs to all three runs. A failed read,
an empty selection or a failed manifest check stops the block.

```sh
# Set DATASET and SPLIT_FILE for the prepared benchmark.
# Set RUNS_ROOT, WORK_ROOT, RUN_PREFIX, RUN_SEED and METHOD_ENTRYPOINT for the run.
# Use external results/work roots and a fresh prefix.
(
  set -eu

  set --
  if [ "${STUDY_IDS_FILE+x}" = x ]; then
    study_ids=$(paste -sd , < "$STUDY_IDS_FILE")
    case "$study_ids" in
      *[![:space:],]*) set -- --tasks "$study_ids" ;;
      *) printf '%s\n' 'STUDY_IDS_FILE contains no task IDs.' >&2; exit 1 ;;
    esac
  fi

  split_receipt=$(harnessgrad split check --dataset "$DATASET" --file "$SPLIT_FILE")
  printf '%s\n' "$split_receipt" | python3 -c '
import json
import sys

receipt = json.load(sys.stdin)
if receipt["train"] <= 0 or receipt["eval"] <= 0:
    raise SystemExit("The frozen split must have nonempty train and eval sides.")
print(json.dumps(receipt, indent=2))
'

  python3 driver.py --harness loop --dataset "$DATASET" --split "$SPLIT_FILE" --side train \
    --sampling all "$@" --trials 3 --jobs 4 --seed "$RUN_SEED" --rounds 0 --improver none \
    --run-id "$RUN_PREFIX-reference" --runs-root "$RUNS_ROOT" --work-root "$WORK_ROOT" \
    --fail-on-incomplete
  head -n 1 "$RUNS_ROOT/$RUN_PREFIX-reference/curve.jsonl" > "$RUNS_ROOT/$RUN_PREFIX-reference.json"

  # Independently remeasure the unedited Harness to check for drift.
  python3 driver.py --harness loop --dataset "$DATASET" --split "$SPLIT_FILE" --side train \
    --sampling all "$@" --trials 3 --jobs 4 --seed "$RUN_SEED" --rounds 0 --improver none \
    --run-id "$RUN_PREFIX-driftprobe" --runs-root "$RUNS_ROOT" --work-root "$WORK_ROOT" \
    --fail-on-incomplete

  # Run a self-contained method against the same frozen reference.
  python3 driver.py --harness loop --dataset "$DATASET" --split "$SPLIT_FILE" --side train \
    --sampling all "$@" --trials 3 --jobs 4 --seed "$RUN_SEED" --rounds 3 --improver none \
    --method-entrypoint "$METHOD_ENTRYPOINT" --run-id "$RUN_PREFIX-method" \
    --runs-root "$RUNS_ROOT" --work-root "$WORK_ROOT" \
    --baseline "$RUNS_ROOT/$RUN_PREFIX-reference.json" --fail-on-incomplete
)
```

Compare the two round-zero points from the reference and drift-probe runs.
Check their task IDs, identities and coverage, then inspect the per-task
differences. Before reporting a gain for any candidate, check coverage and
require `baseline.problems` to be present and empty. A nonempty list invalidates
the comparison even when `improvement.significant` is true. For shared
round-zero measurements and traces, see [H0 reuse](h0-content-comparability.md).

`--split declared` selects the adapter's declared division. `--split default`
selects the platform's deterministic hash division. In the historical ALFWorld
example they selected 82 tasks under `249d6aac` and 81 under `919f17c8`,
respectively. See the [split guide](dataset-splits.md) for current declarations.

For a two-way split, choose `CHECKPOINT_ROUND` using study-side evidence and
measure the entire eval side in the final comparison. For PTC-Bench or
WTM-Bench, use `dev.txt` with separate run IDs for checkpoint selection, then
freeze that choice before the final runs. For either released partition the
example checks the supplied manifest against the released `study.json` and
reads the matching `test.txt` once. It uses those frozen IDs for both launches
and checks both saved points against them.

Exam points contain `per_task` scores and reference diagnostics. Compute their
paired statistics separately from the training curve's `improvement`. Before
computing a paired delta or p-value, check complete coverage and
`baseline.problems`, then call
[`isolation.require_comparable`](../eval/verification/isolation.py) on the two
exam points. It requires a known, matching scorer isolation class.

The example measures both arms under the same conditions, uses the H0 exam
point as the checkpoint's reference, and reports the descriptive paired delta
and gain/loss counts. Set `TASK_LEVEL_TEST=1` only for a versioned split with
independent task pairs. Use group-level inference for the PTC-Bench and
WTM-Bench partitions.

```sh
python3 - "$DATASET" "$SPLIT_FILE" "$RUNS_ROOT" "$WORK_ROOT" \
  "$RUN_PREFIX" "$RUN_SEED" "$CHECKPOINT_ROUND" "${TASK_LEVEL_TEST:-0}" <<'PYTHON'
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from statistics import mean

from eval.verification import isolation
from harnessgrad.experiment import policies

dataset, split_file, runs_root, work_root, prefix, seed, checkpoint, task_level_test = sys.argv[1:]
split_bytes = Path(split_file).read_bytes()
manifest = json.loads(split_bytes)
sides = manifest.get("split", manifest)
expected_ids = set(sides["eval"])
if not sides["train"] or not expected_ids:
    raise SystemExit("The frozen split must have nonempty train and eval sides.")

released = {"ptc_bench": "ptc-bench", "wtm_bench": "wtm-bench"}.get(dataset)
selection = []
if released:
    partition = Path("experiments/splits") / released
    if split_bytes != (partition / "study.json").read_bytes():
        raise SystemExit(f"{dataset}: use the released study.json for the final comparison.")
    test_ids = (partition / "test.txt").read_text(encoding="utf-8").splitlines()
    test_set = set(test_ids)
    if not test_set or len(test_set) != len(test_ids) or not test_set <= expected_ids:
        raise SystemExit(f"{dataset}: the released test.txt must contain unique IDs from eval.")
    expected_ids = test_set
    selection = ["--tasks", ",".join(test_ids)]

if task_level_test == "1" and ("schema" not in manifest or manifest["split_groups_sha256"] is not None):
    raise SystemExit("Task-level inference requires an independent versioned split.")

common = [
    sys.executable, "driver.py", "--dataset", dataset, "--split", split_file,
    "--side", "eval", "--sampling", "all", *selection, "--trials", "3",
    "--jobs", "4", "--seed", seed, "--rounds", "0", "--improver", "none",
    "--runs-root", runs_root, "--work-root", work_root, "--fail-on-incomplete",
]
h0_id = f"{prefix}-heldout-h0"
candidate_id = f"{prefix}-heldout"
h0_file = Path(runs_root) / h0_id / "curve.jsonl"
candidate_file = Path(runs_root) / candidate_id / "curve.jsonl"
subprocess.run([*common, "--from-run", f"{prefix}-reference", "--from-round", "0",
                "--run-id", h0_id], check=True, stdout=sys.stderr)
subprocess.run([*common, "--from-run", f"{prefix}-method", "--from-round", checkpoint,
                "--baseline", str(h0_file), "--run-id", candidate_id],
               check=True, stdout=sys.stderr)

h0 = json.loads(h0_file.read_text(encoding="utf-8"))
candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
split_hash = hashlib.sha256(split_bytes).hexdigest()
for point in (h0, candidate):
    if point["side"] != "eval" or point["split_protocol"]["file_sha256"] != split_hash:
        raise SystemExit(f"{point['run_id']}: expected an eval point on the frozen split")
    trial_scores = point.get("per_task_trials", {})
    if set(point["per_task"]) != expected_ids or set(trial_scores) != expected_ids:
        raise SystemExit(f"{point['run_id']}: incomplete held-out task coverage")
    if any(
        len(scores) != 3 or not all(map(math.isfinite, scores))
        for scores in trial_scores.values()
    ):
        raise SystemExit(f"{point['run_id']}: expected three measured scores per task")
baseline = candidate.get("baseline", {})
if baseline.get("run_id") != h0["run_id"] or baseline.get("problems") != []:
    raise SystemExit("The checkpoint has no compatible reference to this H0 exam.")
isolation.require_comparable(h0, candidate)

if task_level_test == "1":
    comparison = policies.improvement(candidate["per_task"], h0["per_task"])
else:
    deltas = [candidate["per_task"][tid] - h0["per_task"][tid] for tid in sorted(expected_ids)]
    comparison = {
        "checked": len(deltas),
        "net_rate": mean(deltas),
        "gained": sum(delta > 0 for delta in deltas),
        "lost": sum(delta < 0 for delta in deltas),
    }
print(json.dumps(comparison, indent=2))
PYTHON
```

For grouped data, use the validated exam pairs for the group-level comparison
in [§4](#4-what-counts-as-a-result). Label the paired delta as a custom
held-out comparison and identify the unit of any direction test.
Report the available absolute exam scores if you evaluated one arm or the
isolation check refused the comparison.

To evaluate an explicit Harness directory, use
[direct evaluation](bring-your-own-harness.md) with the same manifest.

## 4. What counts as a result

Apply the same coverage and pairing checks to study and held-out comparisons.
Report gains and nominal p-values from study tasks used for Harness edits or
dev tasks used for checkpoint selection as exploratory. The nominal p-value
from a selected round does not account for that selection. For a confirmatory
claim, fix the checkpoint, comparison rule and unit of analysis before
independently measuring the final held-out cohort.

For independent task pairs, the [sign test](../harnessgrad/experiment/policies.py)
tests the balance of gains and losses among tasks that changed.
`improvement.significant == true` and `improvement.direction == "better"`
identify a directional gain at the test's nominal threshold. The historical
task-level comparison gave `p ≈ 0.039` for 8 gains against 1 loss and
`p ≈ 0.22` for 5 gains against 1 loss.

For grouped tasks, treat the task-level gain and loss counts and mean delta as
descriptive. Use the groups declared by
[`SPLIT_GROUPS`](custom-dataset-splits.md#membership-grouping-and-evidence) or
those defined by the split recipe. For PTC-Bench, use the upstream ID families
from the [adapter](../data/datasets/ptc_bench.py). For WTM-Bench, reconstruct the
workbook families with `families()` in the
[split builder](../experiments/splits/wtm-bench/build.py) on the pinned prepared
workbooks. Both legacy split files record only membership.

Before evaluation, verify whole-group coverage for the selected partition and
choose a within-group aggregation rule. Reduce the matched task differences to
one difference per independent group. With `group_deltas` mapping group IDs to
those differences, use
`policies.improvement(group_deltas, dict.fromkeys(group_deltas, 0.0))`
for the group-level sign test. Report its p-value, group count and aggregation
rule alongside the descriptive task-level delta.

A mean-score gain requires `improvement.net_rate > 0`, where `net_rate`
is the mean paired score difference. Report that difference alongside any
applicable sign-test p-value, identifying the p-value as evidence about
direction. A significance claim about the mean requires an inference method
for the paired mean that accounts for any grouping.

For a study-side gain attributed to a Harness edit, also check:

1. `score_std` is present and `capability_floor.ci_available` is `true`.
   Inspect the floor check for violations.
2. `editable_surface_touched` is nonempty and `editable_surface_components`
   names a component. Report unclassified changes as `unattributed`.
3. Report reversals as well as gains. For example, `v9-dgm` went
   0.1 → 0.2 → 0.1.
4. Check that the unedited control under the same conditions did not move in
   the same direction by a comparable amount.

Report null results and reversals with the same provenance and control evidence.
The October 2026 corpus contained 88 flat curves and 8 apparent gains within
the noise band.

## 5. What is still weak

* **Component coverage in the historical study.** The reviewed Loop was one
  `agent.py` and a manifest, and its declaration therefore claims both as `control` and
  nothing else — `context`, `tools`, `verification`, `memory`, `cost` and `safety` are
  deliberately unclaimed rather than split by guess. The widest declarations among the seven
  are `miniagent` (seven components) and `openclaw` (five); the rest name two or three. The
  reading works and is tested; a claim like "context handling improved" needs a harness whose
  files separate along the components first.
  Historical runs may predate component declarations; missing attribution is not evidence
  that no component changed.
* **A clean decline is still a round with no child.** The `_ask_cli` fix makes a *crashed*
  improver an outage instead of a fabricated `no_change`; a round where the improver genuinely
  declines stays a no-child round, which is a fact about the method rather than about the
  platform.
* **The candidate preflight is a guarantee, not a new check.** `eval/run/candidate.py` already
  re-validates every candidate before measurement without executing it;
  `tests/quality/test_candidate_apply.py` now pins the non-execution property, which previously
  held only by an argument.

## 6. The first experiment: validate the instrument before attributing anything to a method

The failure structure on `alfworld` is measured, not guessed, and it points at one specific
change. From the 134 archived trajectories (`refix-alfworld-train` + `-eval`, 63 pass / 71 fail):

| | pass (n=63) | fail (n=71) |
| --- | --- | --- |
| loop iterations | median **14**, min 7, max 32 | median **32**, min 7, max 32 |
| hit the 32-iteration cap | **1 / 63** | **37 / 71** |
| parse errors | 0 | 0 |
| context trims | 0 | 0 |

**The failures are not formatting failures and not context overflow — they stop at the cap.**
(The `step` key appears on four auxiliary record types besides `reply`; counting records rather
than iterations inflates the figure, which is how an earlier reading of this data produced
"66 steps". The table counts `reply` records, one per iteration.)

In that study the cap was **below the environment budget**. `MAX_STEPS` defaulted to 32
(`base_harness/loop/agent.py:63`) and the adapter never raises it, while `STEP_BUDGET = 50`
(`data/datasets/alfworld/lib/gym_env.py:44`). So the harness ends the episode eighteen actions
before the environment would. Passing tasks need a median of fourteen, so the cap does not bind
them.

**The first experiment should therefore be a hand-authored H1, not a method run.** Raise the cap
to the environment's budget and measure. The reason is methodological: a method's edit may be
arbitrary, so a null result would leave "the instrument cannot see a real improvement" and "the
method proposed nothing useful" indistinguishable — and the corpus is full of exactly that
ambiguity (101 tabulated rounds have zero discordant pairs). A known-answer H1 costs two runs
instead of a method loop and it tests the *measurement chain*: if the paired test cannot see a
change that the failure structure predicts, the instrument is what is broken.

The proposed control targeted at least six of the thirty-seven capped
failures. With six gains and no losses, the task-level sign test gives
`p = 0.031` under the independence conditions in §4. The recorded ALFWorld
controls reached 1 and 0. The pool was small enough to run in full, and many
of the observed failures hit the step cap.

This was a proposed positive control for those archived trajectories.
The historical study notes also report 44.3 s for one AIME task with both
GPUs saturated, a timing specific to that host and configuration.
