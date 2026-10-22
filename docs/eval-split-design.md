# Eval-split design: what cross-validation can and cannot fix

This document records the split construction for the `score(N)` study, and why
cross-validation is **not** the instrument for the main claim. It exists because the
report's methodology section has to state how train/valid/eval were carved, and the
answer is dataset-specific rather than generic.

## 1. The three jobs, kept separate

Three different problems get conflated under "how do we split". They have three
different fixes, and only one of them is cross-validation.

| Job | What it protects | Correct instrument | CV's role |
| --- | --- | --- | --- |
| **Leakage** — a test task is reconstructible from a train task | the *point estimate* | **grouping** the split | none (see §3) |
| **Axis consistency** — every point on the `score(N)` curve is scored the same way | the *shape* of the curve | one **frozen** eval slice, same `split_sha` throughout | **anti-role**: rotating folds breaks it (§4) |
| **Variance** — how much of the gap is sampling noise | the *error bars* | repeated, group-disjoint fold assignments | this is the only legitimate use (§5) |

## 2. Measured leakage structure — MuSiQue dev

MuSiQue question ids encode their constituent single-hop questions
(`2hop__460946_294723`), and `question_decomposition` lists each source id, answer
and `paragraph_support_idx`. So the reuse graph is recoverable exactly, without
heuristics.

| Quantity | Value |
| --- | ---: |
| Tasks | 2417 |
| Distinct source single-hop questions | 2768 |
| Source questions reused by ≥2 tasks | 915 (33.1%) |
| Tasks sharing ≥1 source question with another task | 2173 (**89.9%**) |
| Task pairs sharing ≥1 source question | 22 889 (0.78% of all pairs) |
| **Connected components** over that relation | **544** |
| Largest component | 58 tasks (2.4%) |
| Singleton components | 244 |
| Components spanning >1 hop count | 34 |

The 0.78% figure is the misleading one. Converted to an actual split, a random
100-task eval slice leaves every eval task with about **18 train neighbours that
share a constituent question**; the number is stable at 17–19 for eval sizes 60–200.

### 2.1 The channel is shallow and diffuse, not duplication

| Overlap (shared sources ÷ union) | Pairs |
| --- | ---: |
| 0.1 | 1 089 |
| 0.2 | 3 471 |
| 0.3 | 2 902 |
| 0.4 | 503 |
| 0.5 | 3 831 |
| 0.6 | 1 819 |
| 0.8 | 6 |
| 1.0 (near-duplicate tasks) | **28** |

Literal near-duplicates are rare: only 28 task pairs (56 tasks) have an identical
source-question set — these are genuine reorderings such as
`3hop2__87184_90327_76291` versus `3hop2__90327_87184_76291`. The dominant channel is
the **shallow shared hop** (12 349 pairs at overlap 0.2–0.6). This matters for two
reasons:

* Grouping costs almost no data. The dataset is not a pile of clones, so cutting by
  group does not gut the corpus.
* But the confound is *pervasive*. It is not a handful of bad items to delete; it is
  90% of the corpus sharing one intermediate fact with something else. It cannot be
  cleaned item-wise, only partitioned group-wise.

This is the same mechanism that inflates capability claims on multi-hop data
generally: the constituent fact is memorised, so only the composition step remains
to be learned, and R1's staircase measures less than it appears to.

## 3. Why CV does not fix leakage

Plain k-fold splits the *tasks*, so each fold's test side still contains the leaked
neighbours of that fold's train side. Every fold is inflated in the same direction,
so averaging them reproduces the bias rather than cancelling it — and does so with
smaller variance, which makes the biased number look *more* trustworthy than a single
split would. Cross-validation estimates the variance of an estimator; it does not
redefine the unit of independence.

Group k-fold does fix it, but the grouping is what fixes it. CV is then just a way to
repeat the correction, not the correction itself.

The required group definition is the **transitive closure** of the shared-source
relation. If `A ~ s1 ~ B` and `B ~ s2 ~ C`, then `A`, `B` and `C` must land in one
group; a non-transitive grouping still lets leakage walk along a chain.

## 4. Why CV structurally conflicts with `score(N)`

The platform's rule is that two points belong on one axis only when `split_sha`
agrees. Rotating folds changes `split_sha` by construction, so point `N=8` and point
`N=64` would be scored on different eval sets. The curve would then move for two
reasons at once — more training data, and a different test set — and neither is
separable after the fact.

The `score(N)` curve therefore requires **one frozen eval slice for the entire
sweep**, not an averaged one.

## 5. The decision

1. **Freeze a group-disjoint eval slice.** Materialise `groups.json` from the
   connected components, then hold out whole groups until the slice reaches the
   target size. At ~4.4 tasks per group, 100 tasks is about 23 of 544 groups, so the
   remainder of the corpus is untouched. Record the resulting `split_sha`; the whole
   `N`-sweep shares it.
2. **Freeze a separate valid slice**, built the same way and disjoint from eval. It
   is used to choose configurations and checkpoints and is never an input to a
   reported number.
3. **Use CV only for variance.** Pre-register a small number (≈3) of *group-disjoint*
   fold assignments, run the full frozen pipeline under each, and report the spread
   across slices. This upgrades the existing per-point `ci95` from "within one slice"
   to "across slices". Each fold is an independent point with its own `split_sha`.
   Never select the fold, harness or checkpoint that scores best — that converts the
   slices from evidence back into a training set.
4. **Keep cross-dataset transfer (R4) on a separate axis.** No fold assignment
   addresses it; it is a different test with a different instrument.

The split mechanism already supports this: `--split declared|default|official|<file.json>`
records `split_source` and `split_sha`, so a committed `groups.json` is the only new
artifact required.

## 5b. Sizing: a floor, not a ratio

A fixed ratio cannot work here, because the eligible counts are bimodal. Measured by
enumerating each adapter's rows:

| Dataset | Eligible | 10% eval | Resolvable at 10% |
| --- | ---: | ---: | ---: |
| fever | 15 935 | 1 594 | 2.2 pp |
| twowikimultihopqa | 12 576 | 1 258 | 2.4 pp |
| chessbench_puzzles | 10 000 | 1 000 | 2.7 pp |
| gsm8k | 8 792 | 879 | 2.9 pp |
| hotpotqa | 7 405 | 740 | 3.2 pp |
| musique | 2 417 | 242 | 5.2 pp |
| strategyqa | 2 290 | 229 | 5.4 pp |
| reasoning_gym | 1 780 | 178 | 6.1 pp |
| scienceworld | 1 666 | 167 | 6.3 pp |
| bfcl_v3 | 1 040 | 104 | 8.0 pp |
| sportsunderstanding | 1 000 | 100 | 8.2 pp |
| appworld | 750 | 75 | 9.4 pp |
| bird_minidev | 500 | 50 | 11.6 pp |
| spreadsheetbench | 400 | 40 | 12.9 pp |
| tau2_bench | 278 | 28 | 15.4 pp |
| sotuqa | 74 | 7 | 30.8 pp |
| jericho | 56 | 6 | 33.3 pp |
| physicsquestions | 53 | 5 | 36.4 pp |

(Counts are unverified for `chessbench_puzzles`, whose eligible split is `test`, and
for `gsm8k`, where `train:7473 + test:1319` were summed; `bfcl_v3`'s 1040 is
`simple:400 + _answers:400 + irrelevance:240` and may overlap. `triviaqa` is large but
was not enumerated.)

### Required eval size

Per-task score sd `sigma ~= 0.291`, back-solved from the M2 measurement (sd 1.30
tasks over 20 tasks = 6.5 pp). Required `n` for a two-sided test at alpha 0.05 and
power 0.8 is `n = (2.802 * sigma / d)^2`:

| Step to resolve | Eval tasks needed |
| --- | ---: |
| 20 pp | 17 |
| 15 pp | 29 |
| 10 pp | 66 |
| 8 pp | 104 |
| 5 pp | 265 |
| 3 pp | 737 |

### The rule

```
eval  = clamp(ceil(0.10 * eligible),  EVAL_FLOOR = 150, EVAL_CAP = 500)
valid = clamp(ceil(0.10 * eligible),  VALID_FLOOR = 50,  VALID_CAP = 150)
train = eligible - eval - valid          # and whole groups only
```

* **Floor 150** — 10 pp needs 66 tasks and 8 pp needs 104, so 150 leaves margin and
  resolves about 6.7 pp. Below it, do not emit a point.
* **Cap 500** — 500 tasks already resolves 3.6 pp, and the sweep is about 12 `N`
  points x 3 trials x 2 harnesses, so 500 already means ~36k task-runs per dataset.
  Taking 10% of `fever` would be 3.2x that for no gain.
* **Valid 50–150** — valid only selects checkpoints and does not need a tenth of the
  corpus. What it does not take, train keeps.

Note that the current mechanism is not 10%: `data/scaffolds/qa/base.py::is_eval` takes a
deterministic **third** (`sha256(id)[0] % 3 == 0`). Moving to this rule cuts eval by
3.3x and raises MuSiQue's floor from 2.9 pp to 5.2 pp, in exchange for 23 pp more
train for the `N`-sweep. That is a deliberate trade of resolution for train range,
not a strict improvement.

### The declaration this forces: `curve_eligible`

A dataset below the floor must not silently emit a noisy point. It should declare
that it does not carry the R1/R2 curve, exactly as `UNCOVERED_DEMANDS` and
`UNCOVERED_FUNCTIONS` declare gaps, while remaining usable for other claims
(transfer, coverage). Two independent conditions:

1. `eligible - eval - valid >= MIN_TRAIN` (about 300), so the curve has range.
2. `largest_component_ratio` is small enough that the floor is reachable without one
   group dominating the corpus.

Under those conditions the curve is carried by 13 datasets (`fever` through
`bird_minidev`). `spreadsheetbench` (400) and `tau2_bench` (278) can support a
single-point harness comparison (resolving ~13 pp) but not a sweep. `jericho` (56)
and `physicsquestions` (53) cannot support either.

Finally, eval size and `--trials` reduce different noises — eval size reduces
task-sampling noise, trials reduce run-to-run noise. Neither substitutes for the
other, and `--trials >= 3` remains required.

### Small datasets: a coverage problem, not a splitting problem

Small datasets come in two kinds, and pooling does not rescue either.

**Small but in a populated cell.** `physicsquestions` (53) sits in
`reasoning/reasoning` with 13 915 tasks, so it is 0.38% of its cell. Even a 50%
improvement in it moves the cell mean by 0.19 pp, far below the noise floor, and
`gsm8k`'s 8792 tasks would drown it. Pooling therefore helps the *cell* and not the
dataset. If the claim is cell-level the dataset contributes nothing measurable; if
the claim is dataset-level the dataset can never carry it. **Pooling is not worth
doing.**

**Small and alone in its cell.** Measured singleton cells:

| Cell | Dataset | Eligible |
| --- | --- | ---: |
| `artifact/spreadsheet` | spreadsheetbench | 400 |
| `artifact/text_to_sql` | bird_minidev | 500 |
| `environment/interactive_fiction` | jericho | **56** |
| `environment/embodied_text` | alfworld | not enumerated |
| `environment/terminal` | terminal_bench | not enumerated |
| `environment/science_simulation` | scienceworld | 1666 |
| `reasoning/planning_and_search` | chessbench_puzzles | 10 000 |

`chessbench_puzzles` and `scienceworld` are singletons *and* large, and carry a curve
normally. Singleton-ness and smallness are independent properties; only their
intersection is a problem.

The key consequence: **`jericho` at 56 tasks is not a splitting problem.** It is the
sole member of `environment/interactive_fiction`, which violates
`MIN_MEMBERS_PER_DEMAND = 2`. The platform already has a mechanism for that — the
two-way below-floor declaration. So its handling is a coverage declaration, not a new
split rule.

### Tiering

| Tier | Condition | Split | Supports |
| --- | --- | --- | --- |
| **A** `curve_eligible` | eligible ≥ 800 | `clamp(10%, 150, 500)`, whole groups | the R1/R2 curve |
| **B** `point_eligible` | 150 ≤ eligible < 800 | same rule, one frozen `N` only | single-point comparison (~9–13 pp) |
| **C** `illustrative` | eligible < 150, or singleton and < 800 | **no split, use all tasks** | existence, end-to-end, coverage — no number |

Singleton status stacks on top of the tier: a singleton cell also carries a
coverage-gap declaration regardless of its tier.

**Do not split a Tier C dataset.** Splitting `jericho` 40/8/8 yields 8 eval tasks and
a 29 pp resolution floor; that number demonstrates nothing and invites citation. All
56 tasks used for an end-to-end existence claim and for coverage of the
`interactive_fiction` surface is a defensible asset, where the split would convert a
usable asset into an unusable number.

If a number is genuinely needed for such a dataset, the only honest form is an
explicitly labelled **in-sample diagnostic**: no split, all tasks, vary `N`, reported
as mechanism sensitivity and declared not to be generalisation evidence. The platform
already distinguishes valid, unmeasured and diagnostic, so the label is available —
but it must not enter the R1 table.

## 5c. Measured: what the pool actually is, and why six of seven datasets run as `full`

Everything above is *design* — how a split would be built. This section is the one
measurement that changes what we do with it.

The platform derives the split mode from a single adapter field, `OFFICIAL_EVAL`:

* `OFFICIAL_EVAL is None` — the adapter has no notion of "this part of the pool is
  study material". Every row is benchmark **evaluation** material, so there is nothing
  to hold out, and imposing a `train`/`eval` cut would manufacture a held-out set out
  of rows that were never trainable. Such a pool is archived as **`full`**.
* `OFFICIAL_EVAL` is a list — the adapter knows the released test set, and the pool
  contains both sides. The split is then the dataset's own, not ours, and it is kept.

Enumerated across the seven adapters actually run:

| Dataset | Source pool | `OFFICIAL_EVAL` | Mode | Why |
| --- | --- | --- | --- | --- |
| musique | `musique_ans_v1.0_dev` | `None` | `full` | the pool *is* the dev set |
| alfworld | `valid_unseen` | `None` | `full` | the pool *is* the valid set |
| jericho | evaluation games | `None` | `full` | no training games shipped |
| scienceworld | evaluation set | `None` | `full` | as above |
| agentdojo | benchmark suites | `None` | `full` | as above |
| terminal_bench | benchmark tasks | `None` | `full` | as above |
| gsm8k | released `train` 7473 + `test` 1319 | 1319-entry list | **`train`/`eval`** | genuine released split |

**Consequence, and it is not cosmetic.** For six of the seven, the platform's
`train`/`eval` is imposed by the platform for its own protocol, not inherited from the
dataset. The rows we would call "train" are benchmark evaluation rows. Two things
follow:

1. **Our "study set" is not a training distribution.** Statements that read like
   "training on N tasks improves the harness" are, for these six, statements about a
   *subset of an evaluation set*. The honest description is a learning-curve probe over
   a benchmark pool, and the report must say so.
2. **Our means are not comparable to published numbers.** A published MuSiQue-dev score
   is over all 2417 rows; ours is over a platform-chosen subset of them. Any table that
   places our mean beside a leaderboard number is comparing different test sets.

This is also the reason a **pooled / layered** analysis was considered and then
dropped. Pooling rows across these six does not turn them into training data; it
concatenates evaluation sets with different scoring contracts. The `demand × surface`
cell means from §5b are comparable *as cells*, and nothing more.

### 5c.1 Instance structure differs from row count

Two measured cases where "eligible count" is not the number of independent units:

* **alfworld — 134 evaluated episodes, clustered in 47 task directories.** The
  134 `valid_unseen` game files are the paper's whole unseen evaluation set, not a
  2.85x-diluted sample. Each `__trial_<n>` game file is an evaluated episode with its
  own initial state; the benchmark point estimate remains the mean over all 134
  episodes. The 47 task-directory groups matter for dependence and uncertainty:
  group by `task_id.split("__trial_")[0]` when constructing a study/eval split and
  use group-aware error bars. They do not justify dividing the success rate by
  134/47 or replacing the benchmark's episode mean with a 47-group macro mean.
  The archived 0.4254 was measured under a historical 30-action budget; the adapter
  now uses the paper's 50 (`data/datasets/alfworld/lib/gym_env.py:41-44`, commit
  `cdb43ac4`), so the protocol difference is repaired in code but the archived number
  was not re-measured and must not be read as a 50-action result.
* **musique — 2417 rows, 544 components** (§2), from a genuine re-use graph rather than
  from repeated trials. Same lesson, different mechanism.

Neither is fixed by any rule in §5. They are arguments for recording the *unit* a number
is computed over, alongside `split_sha`.

### 5c.2 `Σp(1−p)` does not rank datasets

An early internal statistic scored each dataset by `Σ p(1−p)` over its per-task scores
and read it as "how much room there is for a harness to improve". That reading is wrong,
and the measured census shows why:

* `Σ p(1−p)` is maximised by tasks at `p = 0.5`. It measures **scalar-outcome
  sensitivity** — how much a mean score moves when a single task flips. It says nothing
  about whether a *harness change* moves anything.
* A task at `p = 0` contributes zero to it, yet a task the harness currently scores zero
  on is precisely where an improvement has the most room. The statistic is therefore
  blind to the largest available gains, not a proxy for them.
* It is also a property of the **current** harness and model, not of the dataset, so it
  cannot be an instrument-selection criterion that outlives one configuration.

Measured spread, for the record — MuSiQue density 36.7% (1035 of 2417 rows interior,
`Σp(1−p)` 221.67 of 604.25), gsm8k density 2.3% (226 interior of 8792), alfworld 0
interior of 134. If this quantity is reported at all it must be labelled a descriptive
property of one (dataset, harness, model) triple, and it must not be used to order
datasets or to predict where a harness will help.

The real signal about where a harness can improve lives in the **trajectories**, not in
the scalar outcome distribution, and extracting it is the improver's job
(`propose_changes`), not the split's and not this document's.

## 6. Open items

* The same measurement has not been run for the other multi-hop adapters
  (`hotpotqa`, `2wiki`), where the group key would have to come from the corpus
  rather than the id. Whether their leakage magnitude is comparable is unknown.
* Whether the 34 hop-spanning components should be split by hop count is undecided;
  partitioning by component keeps them together, which is the conservative choice.
