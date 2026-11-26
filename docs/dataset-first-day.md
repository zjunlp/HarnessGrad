# A new dataset, the first day

Use this page to check the data and scorer before following the [onboarding procedure](dataset-onboarding.md).

Precedence, as everywhere in this repository: the programs win over this page. If a claim
here and `data/dataset.schema.json` / `tools/dataset_lint.py` disagree about something a
program can decide, the program is right.

This page exists because of one measured fact from the October 2026 batch of five datasets:
**the expensive mistake is writing an adapter before measuring the metric.** Two of the five
would have been wrong in opposite directions. `nestful` looked easy — gold call sequences and
an official scorer shipped in the repository — and only running the gold through that scorer
showed the benchmark cannot reproduce its own recorded answers, which is what turned it into a
refusal. `ds1000` looked like a refusal — the official tests need a package this host lacks and
one group of them fetches data over the network — and measuring showed it was two different
things, neither of them a metric problem. That October 2026 sweep offered
926 of 1000 rows. See the [current DS-1000 runtime guide](../data/datasets/ds1000/README.md)
for the managed actor and verifier setup.

So: **do not write `data/datasets/<name>.py` until §1.3 is done.**

---

## 1. Three measurements, in this order

### 1.1 Count the bytes, and pin them

```sh
sha256sum <downloaded-file>
wc -l -c <downloaded-file>          # the count is a claim you can falsify
python3 -c "import csv,sys;print(sum(1 for _ in csv.DictReader(open(sys.argv[1]))))" <file>
```

If the task count differs from the published count, record the observed count, the pinned source
it came from, and any confirmed reason for the difference.

Check access to each upstream source in your own environment. A pin verified only
through a mirror is **mirror-verified**; record the mirror rather than claiming the
canonical host was checked.

**Choose a shared directory for the data.** Adapters and preparation tools use
`harnessgrad.paths.dataset_root()`. Set `HG_DATA_ROOT` for the whole collection
(e.g. `export HG_DATA_ROOT=/srv/harnessgrad/datasets`), or a per-dataset variable such
as `HG_DATA_GSM8K` to override it. Only the `HG_DATA_` spellings are read.
See [dataset storage](../README.md#dataset-storage) for defaults, `.env` precedence,
directory naming and linked-worktree behavior.
`python3 tools/datasets.py --check --dataset gsm8k` reports the resolved local paths
and resource status; `DOWNLOADS.md` displays portable logical paths.

### 1.2 Find the function that computes the number

Read the scorer, not the paper. The paper describes the headline; the scorer decides what is
computed and what is discarded. The questions that actually matter:

* Which evidence fields and execution channels does the scorer need? FEVER
  and HotpotQA accept structured evidence. Consult the selected adapter for its
  reward and any diagnostic metrics.
* Which components does the official evaluator report separately? Record each
  supported component by name. MuSiQue reports answer and support components and
  labels its combined scalar as a platform reward.
* Does the scorer call a service or accept a model identifier? Record the
  dependency and check whether the platform can bind and observe that route.

### 1.3 Run the gold through the official scorer — over the whole suite

This is the single most valuable hour in the process. Not a sample: the whole suite, because
the failures cluster.

```sh
# shape: for every released row, execute the gold solution and score it with the official code
```

Three outcomes, and they route everything that follows (§2). Two habits while doing it:

* **Record the per-row results outside git**, under the dataset root, with the count and the date.
* If the gold does not reach 1, **run it twice**. `nestful` gave 1857/4 on the first sweep and
  1854/7 on a repeat: a metric that moves under the same code is not reproducible, and one
  sweep would have hidden that.

---

## 2. Route: what §1.3 tells you

| what you measured | outcome | where to go |
| --- | --- | --- |
| gold reaches 1 on every row | **runnable** | §3.1, then the procedure page |
| gold reaches 1 on most rows, the rest fail for *environmental* reasons | **runnable, with a measured exclusion** | §3.3 |
| gold reaches 1, but the metric needs a channel the contract lacks | **refused** (`contract_gap`) | §3.2 |
| gold reaches 1, but the required simulator, judge or service lacks an admissible route | **blocked** for missing provisioning, **refused** for a demonstrated contract gap | §4 or §3.2 |
| the released data cannot reproduce the official metric at all | **refused** (`unreproducible`) | §3.2 |
| the environment cannot be provisioned without changing the shared interpreter | **blocked**, not refused | §4.1 |

The distinction in the last two rows is the one that cost the most time in the batch, and §4
is about drawing it correctly.

---

## 3. The three outcomes

### 3.1 Runnable

Go to `docs/dataset-onboarding.md`. Start from the generator rather than a copy:

```sh
python3 tools/new_dataset.py --kind qa <name> --source-url … \
    --source-version … --source-size … --source-note "the trap"
```

The scaffold already passes the five gates against a toy table, so what you replace is the
data and the metric, not the wiring. `--kind` takes one of `qa`, `command` or `files`: a
`files` dataset ships a runtime tree the agent acts inside, and its check inspects the state
rather than one answer string.

### 3.2 Refused

A refusal is a result with the same standing as an onboarding, and it has to be **first
class**:

* the module sets `REFUSED` to a sentence naming what is missing and what was measured;
* `load()` raises that same sentence;
* `tools/dataset_lint.py` prints `REFUSED <name>: …` and returns **rc 0**, still checking
  `KIND`, the callables, the data root and the downloads;
* `tests/quality/test_refusal_contract.py` recognises the two kinds — `contract_gap` and
  `unreproducible` — and the sentence must match one of them (it tests the mechanism against a
  synthetic module, since 2026-11-04 the repository has no refusing adapter);
* the transcribed upstream scorer stays in the tests, so the refusal is backed by code that
  runs rather than by prose.

"A refusal must be reached by measurement, not by reading the README." If the gold has not
been executed, you do not have a refusal; you have a suspicion.

### 3.3 Runnable, with a measured exclusion

Report every excluded row and its measured cause. The October 2026 DS-1000
sweep illustrates how it recorded its frozen cohort:

* the excluded rows are enumerated, per row, with the **cause that row actually reported**;
* the largest group is a *criterion* recomputed from the released data (rows whose official
  test code imports `tensorflow`), not a hand-written list, so it cannot drift;
* a test re-runs one row per cause **through the real check**, so a frozen reason cannot
  become a stale claim;
* the full sweep artifact is kept outside git and the frozen table is asserted against it;
* the module docstring states the coverage up front — "926 of the 1000 released rows are
  offered; 74 are excluded, and every exclusion is a measurement" — and every report says
  "926 of 1000", never "the DS-1000 score".

---

## 4. Environment questions that look like refusals and are not

### 4.1 What the sandbox can actually see

In the October 2026 `--platform` probe, `HOME` was `/tmp`. An undeclared
absolute path under the dataset root and a neighbouring adapter's runtime were
both absent. Those observations apply to that configuration.

For current native command verifiers, use `VERIFY.inputs` for task-private
inputs and declare supported read-only `VERIFY.runtime_paths` for runtime
dependencies outside the work root. See the [dataset runtime interfaces](dataset-runtime-interfaces.md)
for admission and the [DS-1000 runtime guide](../data/datasets/ds1000/README.md)
for a managed example. The managed runtime controls have a separate scope
from the historical 926/74 sweep.

### 4.2 A missing package is a coverage fact, not a refusal

A refusal says the *metric* cannot be computed here. "I did not install the dependency" is a
different sentence, and writing the first when you mean the second is how a benchmark gets
lost. The correct criterion is also not always the obvious field: DS-1000's tensorflow rows are
47, not the 45 the `library` label suggests, because two rows labelled `Numpy` ask how to
convert a tensorflow tensor.

### 4.3 Fixed data versus a live service

14 official DS-1000 rows call `sns.load_dataset(...)`, which seaborn downloads from GitHub at
test time. The fix was to pin `mwaskom/seaborn-data`, ship the four CSVs it needs in
`VERIFY.inputs`, and point `SEABORN_DATA` at them: the official code path is unchanged and the
rows are *included*. The line to draw is **what the fetch is for**:

* A metric that needs a live service requires an admissible, identified route.
  See the [runtime interfaces](dataset-runtime-interfaces.md) for supported trusted
  judges and environment simulators.
* Declare fixed, pinnable inputs through `downloads()` and retain the row when
  the pinned assets reproduce the intended execution.

### 4.4 Version drift is per-row and must be measured

The released gold targets the versions the benchmark pinned; this host has others. DS-1000's
27 non-tensorflow failures are entirely that: pandas 1.x APIs removed in pandas 2.2.2
(`DataFrame.append`, positional `str.split`/`str.rsplit`/`drop`, `set_axis(inplace=)`, …),
scipy 1.13 removals, one absent optional package, one network fetch. None of them is a metric
problem, and none of them is diagnosed by reading the paper's environment section — they are
diagnosed by running the gold and grouping the errors.

---

## 5. First-glance misjudgements, from the record

| it looked like | what measurement said | dataset |
| --- | --- | --- |
| "10,000 puzzles, gold in a column, done" | the first `Moves` token is the **opponent's** setup move; only odd indices are the solver's, and an alternative immediate mate also counts | `chessbench_puzzles` |
| "multi-hop QA with supporting facts requires a refusal" | the official scorer reports answer and support metrics separately. The current adapter accepts evidence and reports both components. | `musique` |
| "needs tensorflow and network → refuse" | a missing package is a coverage fact; the network fetch was fixed, pinnable data | `ds1000` |
| "gold execution failing is an environment problem" | 4 stable failures, and a repeat sweep that moved — the release cannot reproduce its own recorded results | `nestful` |
| "0 attacks succeeded, so the agent is safe" | that is one fixed attack template; and a do-nothing agent also has 0 attack successes, so `not ASR` is not a reward | `agentdojo` |

---

## 6. Copy-paste reconnaissance

```sh
# 1. pin the bytes
sha256sum <file>; wc -l -c <file>

# 2. is the data source already declared and present?
python3 tools/datasets.py --check --dataset <name>

# 3. lint only after an adapter exists
python3 tools/dataset_lint.py --dataset <name>
```

Everything else in the first day is reading and running upstream code, which no command in
this repository can do for you.

---

## 7. Before you write the adapter, have these answers written down

1. The **pin** (tag, commit sha, or dataset revision) and the byte count.
2. The **exact function** that computes the headline number, and whether it is one number or a
   product, and whether any sub-score is published separately.
3. The **gold result**: how many rows reach 1, how many do not, and the cause per failure.
4. The **kind** (`qa` or `command`) and why the other does not fit.
5. Whether the agent-visible world can be built from released data **without** the answer in it.
6. What would make this dataset **reachable** if you end up refusing.
