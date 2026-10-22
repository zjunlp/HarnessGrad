# Producing a harness-evolution result

This page is the protocol for the one measurement this repository has never produced: an H0 → H1
improvement that survives comparison with a control. It is written because the machinery for it
exists and the *procedure* did not, and because two limits of that machinery are structural
rather than incidental and have to be known **before** a run is launched, not discovered from a
receipt afterwards.

It is not a plan for a paper and it authorizes nothing. A run still needs the operator's
authorization, an applicable split side, and a fresh run ID.

## 1. What has to be true before a delta means anything

Measured, 2026-10-05, on `terminal_bench` with `trials=3`, `jobs=4`, **five tasks**:

| run | edits | H0 → max |
| --- | --- | --- |
| `B2-noop` | **none** (`editable_surface_touched: []`) | 0.267 → 0.467 |
| `B2-rrsi` | **none** | 0.200 → 0.467 |
| `B2-llm` | `agent.py` | 0.267 → **0.000** |

The control arm moved **+0.2 with no edit at all**. On five tasks one task is worth 0.2, so that
band and one task are the same size and no improvement smaller than a task flip can be seen. The
same holds across the corpus: 96 runs have a curve, **8** have `max > H0`, and every one of the
eight is inside the band — `tau-a-r10` moved +0.018 (less than one task), `strong-model-1` used
**two** tasks, `flash-three-*` used three, and the two largest movers are the no-edit controls.

Two consequences, and neither is optional:

* **A reference that every method is measured against -- one, frozen, and measured well.** A
  per-run H0 gives each method its own axis, which is the confounding this platform exists to
  remove: two curve points belong on one axis only when their provenance agrees
  (`INTERFACE.md` §2.3). `--baseline <point.json>` declares a frozen reference; without it the
  baseline is round 0 of the same run, which is right for one method and wrong for a series.
  The price is that nothing re-measures it, so every run checks the fields that must be held
  fixed -- agent model, backend, harness family, framework version, and the task set -- and
  records the problems on `point["baseline"]["problems"]` rather than assuming they are empty.
* **The frozen reference is amortized over every method, so spend on its precision.**
  `B2-noop` moved +0.2 with no edit, at `temperature=0.0` and an unchanged `harness_sha`, so the
  noise is endpoint-side and per-call independent: it does not *bias* a frozen reference, it is
  baked into it permanently. Measure the reference once with `--trials 3` so its `per_task` is
  a majority over trials, and keep the method runs cheap at `--trials 1`. This inverts the
  earlier advice on this page, which was written when the baseline was per-run.
* **A no-op arm stops being the control and becomes the drift probe.** Re-run the unedited
  harness against the frozen reference: if it moves, the reference no longer describes today's
  platform. That is the one thing a frozen reference cannot detect by itself, and
  `point["baseline"]["problems"]` is where it has to surface.
* **Enough tasks that one task is small.** `full-a` used 51 and is the only run in the corpus at
  a scale where a two-task move does not dominate the number.

**The band is set by the task count, not by `--trials`.** An earlier draft of this page said
otherwise and the operator's own archived runs refute it. `score_ci95` is computed over *tasks*
(`harnessgrad/records.py:164,211`), so repeating a task does not shrink it. On `alfworld`'s 134
tasks, at `trials=1`:

| run | score | `score_ci95` |
| --- | --- | --- |
| `refix-alfworld-train` | 0.4146 | [0.307, 0.522] |
| `refix-alfworld-eval` | 0.5577 | [0.421, 0.694] |

One harness, one model, two halves of one pool — **0.143 apart with overlapping intervals**.
That is the marginal band, and it is why a marginal comparison cannot see a component-level
edit. `--trials` still matters, but for the *floor* guard rather than for this (see §2): the
paired test in §4 is what makes a 134-task pool enough.

**What the corpus's best actually is, in paired terms.** Applying `improvement` to every
archived run with a paired baseline — 63 runs, 145 candidate rounds — gives the sharpest
statement of the problem:

| discordant pairs (best → worst) | rounds |
| --- | --- |
| 3 gained / 0 lost (`p = 0.25`) | 4 |
| 2 / 1, 2 / 0, 1 / 1, 1 / 0 | 8 |
| 0 gained / 1 or 2 lost | 11 |
| **0 / 0 — nothing discordant** | **101** |

**Zero rounds are significant.** The largest one-directional movement anywhere in the corpus is
**three tasks**, and `B2-noop` — the no-edit control — produces exactly that (3 gained / 0 lost,
`p = 0.25`), as does `B2-rrsi`. So the corpus's best "improvement" is the same size as the
control's, which is the same conclusion §1's mean comparison reaches, now with a test behind it.

The 101 zero-discordance rounds are silent by *vacuity*, not evidence of no change, and the
record says so: `direction: "unchanged"` with `gained = lost = 0`. That is consistent with the
75-of-129 rounds that produced no edit at all.

The bar for the experiment is therefore explicit: **six discordant pairs in one direction**
(6-0, `p = 0.031`) is the smallest significant result, and eight against one (`p = 0.039`) is
the first that also tolerates a regression.

## 2. What each path can and cannot produce

Two limits are structural. Both were verified this round by running the code, not by reading it.

**The staged path cannot turn the *floor* guard on, but that is not what detects an
improvement.** Two separate things, and conflating them overstated the limit in an earlier
draft of this page. `staged_task_set` (`eval/policy/diagnostic_policy.py:101-102`) refuses
anything but `trials=1, jobs=1`:

    staged diagnostics and temporary pilots require trials=1, jobs=1

and the capability-floor guard (`harnessgrad/policies.py:112`,
`bool(candidate_std and baseline_std)`) needs a spread, so at `trials=1` it reports
`ci_available: false`. But that guard is a **disqualification** — it only asks whether a
candidate sits *below* the baseline. It was never the improvement detector.

The improvement detector is `improvement` on the curve point, and it is **paired**: the same
task compared with itself before and after, over the discordant pairs only, as a sign test
(McNemar's exact test when the per-task scores are binary). It needs no spread, so it works at
`trials=1`. `score_ci95` cannot substitute — it is computed over *tasks*, so it does not shrink
when a run repeats them.

**The staged path also withholds its score.** `_require_publication`
(`eval/policy/diagnostic_policy.py:160-170`) needs `gates.status == "passed"` plus `cleanup` and
`snapshot` receipts, and no dataset has a lifecycle receipt to bind. Measured: all three staged
diagnostics on disk (`aime`, `appworld`, `scienceworld`) report `score: None`,
`measured_tasks: 0`, `outcome: unmeasured, reason: policy_enforcement_not_observed`.

**The unstaged path publishes and grants nothing.** `driver.save_policy_receipt` returns `True`
immediately when `not staged`, which is how all 96 historical curves got their numbers. It
carries no production or H0 authorization, and that has to be said in the record rather than
implied by silence.

So the choice for the first evolution result is: run **unstaged**, record it as provisional, and
accept that the number is not production-authorized; or build the missing runtime-observation
producer first (`docs/runtime-observation-brief.md`) so a staged run can publish. The second is
the honest long path and it blocks everything else in the meantime.

## 3. The protocol

```sh
# 0. A files-environment dataset, not a container one: `aime` (60 tasks) or `gsm8k` rather
#    than `terminal_bench`, which needs Docker per task and costs an order of magnitude more.
#    Measured: one aime task ran end to end in 44.3 s with both GPUs saturated.

# 1. Measure the reference ONCE, well, and freeze it. `--split declared` (or omitting
#    `--split`) is the dataset's own division; `--split default` is the platform's hash third
#    and is a DIFFERENT task set -- measured: 82 tasks under split_sha 249d6aac against 81
#    under 919f17c8 for alfworld. Freeze the round-0 line of that curve as the reference.
python3 driver.py --harness loop --dataset <name> --split declared --side train \
  --sampling all --trials 3 --jobs <n> --rounds 0 --improver none \
  --run-id <fresh>-reference --runs-root <runs>
head -1 <runs>/<fresh>-reference/curve.jsonl > reference.json

# 2. Probe the reference for drift before trusting it: the unedited harness, today. It should
#    land where the reference says. If it does not, the reference is stale -- stop.
python3 driver.py --harness loop --dataset <name> --split declared --side train \
  --sampling all --trials 1 --jobs <n> --rounds 0 --improver none \
  --run-id <fresh>-driftprobe --runs-root <runs> --baseline reference.json

# 3. Every method run, cheap, against the same frozen reference.
python3 driver.py --harness loop --dataset <name> --split declared --side train \
  --sampling all --trials 1 --jobs <n> --rounds <n> --improver <name> \
  --method-entrypoint "<method>" --run-id <fresh>-method --runs-root <runs> \
  --baseline reference.json

# 4. Read the points. `score`, `score_std`, `n_trials`, `improvement`, `capability_floor`,
#    `editable_surface_touched`, `editable_surface_components` and `baseline` are all on the
#    point; `baseline.problems` being non-empty invalidates the comparison.
```

**`--split declared` and `--split default` are different task sets.** `declared` is the
adapter's own `SPLIT`; `default` is the platform's documented rule, a deterministic third by
hash of the task id. They agree in shape and differ at the boundary -- on alfworld, 82 against
81, `split_sha 249d6aac` against `919f17c8`. A frozen reference must name the same one the
analysis was made on, and `reference_problems` reports the difference when it does not, because
the paired test would otherwise silently shrink to the intersection.

`--side eval` is **not** "score the eval side now": in the driver it means *score a harness a
previous train run produced* and needs `--from-run`. A dataset whose whole pool is evaluation
material (`OFFICIAL_EVAL = None`, which is `alfworld`) therefore has no single-run path to a
full-pool number -- `--split official --side train` is empty and `--side eval` wants a source
run. The operator's fold of two runs is the way that exists today, and this page does not
pretend otherwise.

## 4. What counts as a result

A round is an improvement only when **all** of these hold, and the third is what most of the
corpus fails:

1. **`improvement.significant` is `true` and `improvement.direction` is `"better"`**, read from
   the point. This is the paired test over the same tasks, and it is the primary criterion: at
   `trials=1` on 134 tasks it separates 8 gained against 1 lost (p ≈ 0.039) from 5 against 1
   (p ≈ 0.22), while the marginal interval cannot separate either from the baseline. A
   significant `direction == "worse"` is a regression, not a result — the two fields are a
   conjunction on purpose and neither alone is a reading;
2. `score_std` is present and `capability_floor.ci_available` is `true`, so the *floor* guard
   could have fired — a `true` here means the comparison was available, not that it passed;
3. `editable_surface_touched` is non-empty **and** `editable_surface_components` names a
   component. A touched path reported as `unattributed` is an honest reading, but it is not a
   claim about a component;
4. it is monotone across rounds or, if it is not, the reversal is reported rather than dropped —
   `v9-dgm` went 0.1 → 0.2 → 0.1 and neither end of that is a result;
5. the control arm from steps 1-2, run at the same configuration, did not move in the same
   direction by a comparable amount. `improvement` compares two rounds of the *same* run, so it
   cannot see this; only the control can.

A null result that satisfies 1-5 is worth more than a positive one that does not. The corpus
already contains 88 flat curves and 8 false positives; one more of either adds nothing.

## 5. What is still weak

* **Attribution is close to meaningless on the base harness.** `base_harness/loop` is a single
  `agent.py`, so a `components` map would claim that one file as context *and* control *and*
  verification. The reading works and is tested, but a claim like "context handling improved"
  needs a harness whose files separate along the components first.
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

And the cap is **below the environment's own budget**: `MAX_STEPS` defaults to 32
(`base_harness/loop/agent.py:63`) and the adapter never raises it, while `STEP_BUDGET = 50`
(`data/datasets/alfworld/lib/gym_env.py:44`). So the harness ends the episode eighteen actions
before the environment would. Passing tasks need a median of fourteen, so the cap does not bind
them.

**The first experiment should therefore be a hand-authored H1, not a method run.** Raise the cap
to the environment's budget and measure. The reason is methodological: a method's edit may be
arbitrary, so a null result would leave "the instrument cannot see a real improvement" and "the
method proposed nothing useful" indistinguishable — and the corpus is full of exactly that
ambiguity (101 of 145 rounds have zero discordant pairs). A known-answer H1 costs two runs
instead of a method loop and it tests the *measurement chain*: if the paired test cannot see a
change that the failure structure predicts, the instrument is what is broken.

Prediction, stated before the run so it can be wrong: converting six or more of the
thirty-seven capped failures gives `6 gained / 0 lost`, `p = 0.031`, which is the smallest
significant result §4 accepts. `alfworld` is the dataset for this because its controls reach 1
and 0 (verified), its pool is small enough to run whole, and its failures are step-limited
rather than knowledge-limited.

Only after a known-answer H1 moves the number is a method run interpretable.
