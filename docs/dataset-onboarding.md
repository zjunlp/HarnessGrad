# Onboarding a dataset: the procedure
## Step 0: measure the baseline once, and share it

Every run measures round 0 as the baseline harness's score, so a batch that starts N runs has N
baselines unless one is shared. Those are not interchangeable: the cross-method comparison is
paired, and "method A beat method B" computed against two different baselines answers two
different questions.

```bash
# once per (dataset, study set, agent model, endpoint)
python3 tools/prepare_h0.py --dataset appworld --side train \
    --study-ids /tmp/appworld-study-train90.txt --jobs 32
# -> H0 measured: h0-appworld-<stamp>   score 0.0111 on 90 tasks
#    --h0-from h0-appworld-<stamp>

# then every study run for that dataset passes it
driver.py ... --h0-from h0-appworld-<stamp>
```

The tool refuses instead of measuring when a valid baseline for the same configuration is already
on disk (two measurements of one state are two numbers, and picking either is a choice no record
can defend), and it refuses to *publish* a baseline with holes: fewer tasks than the study set, any
invalid task, or no `h0_sha` is not a baseline.

**What counts as the same configuration** is the dataset, the side, the study-id set (by content,
not by path), the agent model and the endpoint. A different model or a different study set is a
different H0, and the tool measures one rather than reusing.

`--h0-from` copies the donor's point **and its `round0_traces`**, so every method's first input
traces are the same traces too -- which matters for methods that read the traces rather than the
score.

**This page is the procedure. `docs/adapter-integration-journal.md` is the in-repository onboarding log and evidence index.**
Every adapter entry must also update `docs/platform-improvement-log.md` when it teaches the shared platform a reusable capability.
If the dataset is new to you, read `docs/dataset-first-day.md` first: it is the
reconnaissance — three measurements that decide whether this dataset is runnable, refused, or
runnable with a measured exclusion — and its whole point is that **no adapter is written until
the gold has been run through the official scorer**.
The skill doc is more than 1,500 lines of measured lessons and is titled *"the judgement, not
the checklist"* on purpose — read it when a decision is not obvious. This page is what you
follow so you do not have to rediscover which decisions there are.

**Precedence, in order:** the programs win over this page, and this page wins over the
skill doc. If this page and `data/dataset.schema.json` / `tools/dataset_lint.py` disagree
about something a program can decide, the program is right — fix this page.

Three rules that produce most of the failures this page exists to prevent:

1. **Read the function that computes the number**, not the paper. Dropping a requirement is
   a downgrade; adding one is an invention; both produce a plausible number.
2. **Everything the check needs must travel in `VERIFY.inputs`.** The verification namespace
   hides the platform tree (`eval/containers/sandbox.py`), so a check that imports from `data/` works
   by hand and fails under `--platform`.
3. **The gold never reaches the agent.** `SETUP` is trimmed of reference solutions,
   walkthroughs and expected outputs; `VERIFY.inputs` is re-materialised after the agent
   stops. If the answer is readable from the task workdir, the oracle scores 1 for the wrong
   reason.

---

## 1. The five gates

A dataset is not a dataset until all five hold. They are cheap; a curve cannot tell an
unreachable check from a weak harness, and these are what separate them.

| # | gate | how you prove it |
| --- | --- | --- |
| 1 | **oracle control scores 1** | `python3 data/datasets/<name>/control.py --oracle` |
| 2 | **do-nothing control scores 0** | `python3 data/datasets/<name>/control.py --noop` |
| 3 | **the official scorer is copied verbatim into a test, with a differential test** | `tests/quality/test_<name>_official_scorer.py` |
| 4 | **`KIND = "dataset"`** | `python3 tools/dataset_lint.py` |
| 5 | **it runs** | `python3 tools/task_difficulty.py --dataset <name>` returns 0 |

Gates 1 and 2 must pass **twice**: by hand, and through the platform
(`… --oracle --platform`, which drives `SETUP → harness → VERIFY` via
`eval.run.runner.run_one`). The QA family's control is shared —
`python3 data/scaffolds/qa/control.py --dataset <name> --oracle` — not a per-dataset script. Gate 3
means *transcribe their function*, quote its upstream line numbers, and add a drift test that
fails when the upstream lines move. Importing their package and calling it "the same" is not
gate 3 — the point is that the number you publish is the number they publish, provably.
Where the metric *is* the upstream package's own function (Jericho's normalised score), the
test pins the table and the game set instead, and says so.

Gate 3 has three admissible forms, and which one applies is decided by what the benchmark
publishes, not by which is cheapest:

* the metric **is a function in the upstream package** (Reasoning Gym's
  `Dataset.score_answer`) — call it, pin the package version, and compare the call against the
  library called independently in the test;
* the metric is **a column of a table the benchmark publishes** (MuSiQue's `answer_f1`,
  2WikiMultihopQA's `Ans F1`), while other columns of the same evaluator need evidence this
  contract cannot carry — report that column **under its own name**, transcribe the columns
  that cannot be carried, and have the evidence say which are missing;
* **nothing this contract can compute appears in that table** (HotpotQA, whose leaderboard
  number is `joint`) — that is **§5 (a refusal)**, not a reason to report a nearby number.

The distinction is not cosmetic: a number that is near a published one and shares its name
makes a curve point indistinguishable from a wrong metric, while a column reported under its
own column name is the benchmark's number.

`tests/quality/test_dataset_controls.py` enforces gates 1–3 over every published dataset: a
dataset with no row there fails the suite, which is what stops the next one from being
onboarded without a way to show its grade is reachable.

---

## 2. Where everything lives

| what | where |
| --- | --- |
| the dataset declaration | `data/datasets/<name>.py` — `KIND`, `load()`, `downloads()`, and optionally `provision_status()`, `SPLIT`, `ENV`, `VERIFY`, `SETUP` |
| **which tasks are study material** | `SPLIT` (the dataset's default division) and `OFFICIAL_EVAL` (the ids of the benchmark's own evaluation set, or `None` when the pool holds no study material). A run may replace the division with `--split default\|official\|<file.json>`; it records `split_source` and `split_sha`, and two curve points belong on one axis only when the sha agrees — INTERFACE.md §2.3 |
| the data bytes | `/mnt/20t/xubuqiang/datasets/<name>/` — **never in git**; `DATASET_ROOT` must point outside the platform tree (lint rule: `lint_paths`) |
| dataset-private code | `data/datasets/<name>/{lib,cli,verify,control,oracle_harness}/` |
| the machine-checkable contract | `data/dataset.schema.json` |
| the checker | `tools/dataset_lint.py` |
| the generator | `tools/new_dataset.py --kind qa\|command\|files` — see §3 |
| the check that runs over *every* published dataset | `tests/quality/test_dataset_lint.py` |
| the check that every dataset declares a control and a metric test, and runs the controls | `tests/quality/test_dataset_controls.py` |
| the classification: derived axes plus the one judged axis, as a `(demand, surface)` pair | `tools/dataset_classes.py` |
| current blocker families, work status, evidence and next actions; preserved historical incompletes | `registry/dataset-blockers.json`; validate with `python3 tools/check_dataset_blockers.py` without importing adapters. **The registry moved here from `docs/dataset-blockers.json`**, which no longer exists; citations of the old path elsewhere in this repository are historical records and are deliberately left alone |
| the declared coverage gap (a demand the vocabulary names with no adapter yet) | `tools/dataset_classes.py::UNCOVERED_DEMANDS` — `memory` today, with its two planned surfaces and the five screened candidates |
| which Harness function a dataset makes score-relevant, and which functions no dataset covers | `tools/dataset_classes.py::EXERCISES` / `FUNCTIONS` / `UNCOVERED_FUNCTIONS`; the reasoning is in `docs/harness-functional-coverage.md` |
| the shared control for `command` datasets | `data/scaffolds/command_control.py` + `data/scaffolds/command_oracle_harness/` |
| the download index (generated) | `DOWNLOADS.md` — written by `tools/datasets.py --write`; **never edit by hand** |
| "which datasets exist" | `data/scaffolds/registry.py::published()` — a module appears iff `KIND == "dataset"` |
| the judgement half | the internal onboarding log (kept outside this repository) |

Anything that is not a dataset for a reader to run declares `KIND = "fixture"` (the
default). That is why the platform's own scaffolding (`demo`, `probe_set`,
`broken_service`) still exists in `data/` but is not offered in the chooser.

### The contract, in full

A dataset declares an environment and a verifier per task. There are exactly four names to
know, and spelling one of them differently is the most common way to write a dataset that
silently is not one:

| position | legal values | meaning |
| --- | --- | --- |
| `ENV[<task>]["kind"]` | `files` | a directory tree is materialised and the agent runs a command inside it |
| | `exec` | a container; this machine has no working Docker, so nothing here uses it |
| `VERIFY[<task>]["kind"]` | `answer` | the agent's answer is compared as a string; no check program runs |
| | `command` | an argv runs against the task workdir and writes a reward file |

Everything the earlier sixteen datasets used is one of those four. The schema is the
authority; this table is a reading of it.

### The download contract

`downloads()` returns one entry per artefact. Every entry needs `kind`, `name`, `version`,
`size` and a `note` of at least 20 characters; `version` may not be
`""`/`main`/`master`/`head`/`latest`/`trunk`/`dev`/`nightly`. `http` adds `url` + `dst`,
`hf-file` adds `repo` + `file` + `dst`, `hf-dataset` adds `repo` + `dst`, `pip` adds
`package`. Write the traps into `note` — a trap recorded only in a commit message is a trap
the next person pays for again.

```
python3 tools/datasets.py --check   --dataset <name>    # are the bytes here?
python3 tools/datasets.py --fetch   --dataset <name>    # get them (sha256 verified)
python3 tools/datasets.py --write                        # regenerate DOWNLOADS.md
```

`--fetch` verifies a declared `sha256` and deletes the file when it does not match, so a
`downloads()` entry without a hash is a fetch that trusts the network. Pin one.

**Hugging Face note (measured on this machine): `huggingface.co` is unreachable;
`hf-mirror.com` works and serves the same repo ids and commit SHAs.** `--endpoint` defaults
to the mirror. `raw.githubusercontent.com` is also blocked; use `codeload.github.com` or
`github.com/…/raw/…`.

---

## 3. The two shapes

Pick the shape first; it decides which files you write.

**Start from the generator, not from a copy:**

```sh
python3 tools/new_dataset.py --kind qa      <name> --source-url … --source-version … \
                                                  --source-size … --source-note "the trap"
python3 tools/new_dataset.py --kind command <name> --source-url … --source-version … \
                                                  --source-size … --source-note "the trap"
python3 tools/new_dataset.py --kind files   <name> --source-url … --source-version … \
                                                  --source-size … --source-note "the trap"
```

It writes a scaffold that **already passes the five gates** — a small working dataset whose
table is generated in memory, so `--oracle` reaches 1 and `--noop` reaches 0 before any real
data is on disk. You replace the table and the metric; you inherit the wiring. The provenance
flags are required, because step 3 of the order below is "write `downloads()` first" and the
lint refuses a dataset that names none.

`--kind files` generates the same three files as `command`, and the control it emits is the
same one: `data/scaffolds/command_control.py` turns out to be **kind-agnostic** — it needs `load()`,
`SPLIT`, `SETUP`, `VERIFY` and `oracle_files(task)` and nothing else. What the `files` scaffold
adds is the shape: a runtime tree built by `SETUP`, a check that inspects the state the agent
left, and the three traps that shape has already paid for — the check cannot see the dataset
root (everything it needs must travel in `VERIFY.inputs`), a runtime tree is often read-only,
and the sandbox copies those inputs per task. They are written into the generated module,
where the author will read them. Read skill doc §16–§17 for the two datasets that taught them.

### QA shape — "read a row, answer, compare"

Use it when the task is a question with a checkable answer and the environment is just a
place to write an answer. The shared scaffold is `data/scaffolds/qa/`; seven datasets ride it
(`gsm8k`, `triviaqa`, `strategyqa`, `sportsunderstanding`, `physicsquestions`,
`reasoning_gym`, `twowikimultihopqa`, plus `bfcl_v3` with its own `records()`).

```
data/datasets/<name>.py     declares a base.QASpec(...): root, rows, question, gold, goal, score,
                   downloads, split, source — and `study=("train",)` when the benchmark
                   publishes a study split too — then _SPEC.build() and thin wrappers.
data/scaffolds/qa/cli/qa     the agent's command (submit / show)
data/scaffolds/qa/verify/check.py   the check, shared by every QA dataset
data/scaffolds/qa/control.py --dataset <name> --oracle|--noop [--platform]
```

`_SPEC.build()` is also what decides the division: with no `study` split the platform's
hash rule divides the pool (`SPLIT_SOURCE == "hash"`, `OFFICIAL_EVAL is None`), and with one
the benchmark's own split labels do (`SPLIT_SOURCE == "declared"`, `OFFICIAL_EVAL` is the
scored split's ids). Both are read from `_BUILT`, so an adapter declares them in exactly one
place. `triviaqa` is the case where the study split is a declared **sample** rather than the
whole thing (`STUDY_ROWS`); `parquet_rows(sample=…)` is the reader's half of that, and it
tags each kept row with its position in the file so the task id does not move when the
sample size does.

The adapter is materialised *by itself* inside the verification namespace, together with
`data/{__init__,datasets/__init__,scaffolds/__init__}.py`,
`data/scaffolds/qa/{__init__,read,base}.py`. Two consequences:

* an adapter that imports another dataset's module (e.g. `data.datasets.another_adapter`) fails
  inside the namespace — declare shared constants inline, three lines are cheaper than a
  fourth file in every `VERIFY.inputs`;
* `data/scaffolds/qa/verify/check.py` may not import a name that arrived in `argv` — validate the
  dataset name against `^[a-z][a-z0-9_]{0,63}$` and import the adapter with
  `__import__(module, fromlist=["score"])`.

### `files` shape — "ship a world and a command"

Use it when the agent needs a runtime: an interpreter, a game, a database, a CLI of its own.

```
data/datasets/<name>.py             ENV = {"kind": "files"}; SETUP, VERIFY (one entry per task)
data/datasets/<name>/lib/*.py       shared by the agent's world and the scored world — one library,
                           two thin programs, so both build the same world
data/datasets/<name>/cli/<cmd>      the command the agent runs
data/datasets/<name>/verify/check.py  reads the workdir, writes the reward file, exits 0
data/datasets/<name>/control.py     --oracle / --noop / --platform
data/datasets/<name>/oracle_harness/{agent.py,harness.json}   the scripted oracle as a real harness
```

Three measured traps:

* **`files` environments may carry only `kind`.** `data/scaffolds/registry.py::_check_env` refuses
  `agent_timeout_s` and friends — the platform defaults apply, so a task that needs longer
  must be sized down or dropped, and the drop must be recorded.
* **A directory in `VERIFY.inputs` must be re-materialisable.** The platform clears the
  target before copying (`eval/observe/tracing.py::_clear_tree`), because a read-only input tree
  (a JRE, a game suite) cannot be overwritten otherwise.
* **A dataset-private helper must be named per dataset.** Two datasets that both ship a
  module called `control` collide in `sys.modules`; this only shows in a full-suite run.

---

## 4. The order

Copied from the skill doc §10 and paired with the command that proves each step.

1. **Read the upstream scorer**, not the paper. Find the function that computes the number;
   every requirement in it must be in your check, and nothing that is not.
2. **Find the reader of every field you declare**: `grep -rn "<field>" eval/ harnessgrad/`.
   If nothing reads it on your `env.kind`, it is not a channel.
3. **Write `downloads()` first**, with pinned versions and a plain-language note per trap.
   Then `python3 tools/datasets.py --check`.
4. **Write the CLI and the check as one shared library plus two thin programs.**
5. **Strip the answer** from everything the agent receives.
6. **Write the oracle and the do-nothing control and run both** — `--oracle` 1, `--noop` 0,
   by hand and through `--platform` — **before writing any prose**.
7. **Copy the official scorer into a test**, with upstream line numbers and a drift test.
8. **Name the reason in the evidence**, and keep "unmeasured" distinct from "failed": no
   reward file, exit 3, `evidence["measured"] is False` → the task is `unmeasured`, not 0.
9. **Run `python3 tools/dataset_lint.py` and `python3 tools/datasets.py --write`.**
10. **Record the noise and the task count** in this repository, where the next person
    comparing two runs will see it.

Then **append what this dataset taught you to the internal onboarding log (kept outside this repository)**, with the
measurement that produced it, before starting the next one. Lessons written in a batch lose
the measurement.

---

## 5. When the contract cannot carry the metric: a refusal

Some benchmarks cannot be scored here without reporting a different number. The honest
outcome is a **first-class refusal**, not a nearby metric:

* the module sets `REFUSED` to a sentence naming exactly what is missing and what was
  measured;
* `load()` raises that same sentence;
* `tools/dataset_lint.py` prints `REFUSED <name>: …`, returns **rc 0**, and still checks
  `KIND`, the required callables, the data-root path and the `downloads()` entries;
* `tests/quality/test_dataset_refusals.py` recognises **two kinds**:
  * `contract_gap` — the metric needs a channel the contract does not have
    (`fever`'s `strict_score` needs predicted evidence; `hotpotqa`'s `joint` needs
    supporting facts; `sotuqa`'s headline is an unpinned LLM judge);
  * `unreproducible` — the metric cannot be reproduced from the released samples at all
    (`api_bank`'s gold call reproduces its own recorded result 262/508 = 51.6%;
    `tau2_bench` is dual-control and 55/278 tasks carry `requestor: "user"` gold actions).

**A refusal must be reached by measurement, not by reading the README.** Run the gold
through the official scorer and report the number you got. "It looked hard" is not a
refusal.

---

## 6. Definition of done

```sh
python3 data/datasets/<name>/control.py --oracle              # expect reward 1
python3 data/datasets/<name>/control.py --noop                # expect reward 0
python3 data/datasets/<name>/control.py --oracle --platform   # expect reward 1 again
python3 data/datasets/<name>/control.py --noop   --platform   # expect reward 0 again
python3 tools/dataset_lint.py                        # expect: ok <name>, rc 0
python3 tools/datasets.py --write                    # DOWNLOADS.md, generated
python3 tools/task_difficulty.py --dataset <name>    # expect rc 0
python3 -m pytest tests/quality -q                   # the suite stays green
```

plus: the official scorer is transcribed with a differential test, the dataset appears in
`data.scaffolds.registry.published()`, no data byte is in git, and the lesson is appended to the
skill doc.

`tests/quality/test_dataset_controls.py` enforces the shape of this section over every
published dataset: each one must declare a control entry point and a scorer test, and the
controls are actually run. Datasets that predate the convention carry a reasoned exemption
in that file rather than a weakened assertion.
