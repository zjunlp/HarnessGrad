# Onboarding a dataset: the procedure

## Step 0: measure the baseline once, and share it

Once the adapter is ready, prepare H0 for the study configuration before comparing
methods. A shared H0 gives each method the same initial measurement and traces.

```bash
# Set STUDY_IDS_FILE to a file with one AppWorld train ID per line.
# Set RUNS_ROOT to the external directory for H0 and its recipient runs.
HG_APPWORLD_STUDY_IDS="$STUDY_IDS_FILE" python3 tools/prepare_h0.py \
    --dataset appworld --harness loop --side train --split declared \
    --study-ids "$STUDY_IDS_FILE" --sampling all --trials 1 --jobs 32 \
    --runs-root "$RUNS_ROOT"
```

The tool returns success and reuses an existing compatible measurement.
Publication requires the complete selected cohort, valid task measurements and
an H0 hash. Before reusing the donor, review its round-zero point in
`curve.jsonl`. Check that `per_task` covers exactly the study IDs, all task
scores are finite, and `n_invalid` is zero. For the single-trial run above,
the curve omits `n_trials` and `per_task_trials`. For a multi-trial run, also
check that `n_trials` matches the requested count, `per_task_trials` has the
same study IDs, and each ID has exactly that many finite scores. An earlier
trial's score can survive a later failure, and the publication check can
accept that partial record.
Also check that `identity.jobs` matches the recipient's `--jobs`, since
concurrency is outside the preparation tool's reuse key. If either check fails
or the required fields are missing, remeasure with the intended settings in a
fresh external `--runs-root` and use that root for the donor and its recipients.

Pass the donor's cohort explicitly to each recipient. The command below
verifies strict reuse before starting any method rounds. Use the same AppWorld
study file for the adapter's declared train side.

```bash
# Set H0_RUN to the printed --h0-from ID, WORK_ROOT to an external work
# directory, and RUN_ID to a fresh recipient ID.
(
    set -eu
    task_ids=$(paste -sd , < "$STUDY_IDS_FILE")
    HG_APPWORLD_STUDY_IDS="$STUDY_IDS_FILE" python3 driver.py \
        --dataset appworld --harness loop --side train --split declared \
        --sampling all --trials 1 --jobs 32 --tasks "$task_ids" \
        --runs-root "$RUNS_ROOT" --work-root "$WORK_ROOT" --h0-from "$H0_RUN" \
        --run-id "$RUN_ID" --rounds 0 --improver none --fail-on-incomplete
)
```

`--study-ids` is forwarded to the driver's `--tasks` selector. Empty or duplicate
IDs are refused. The default split is `declared`; choose another with `--split`.
The configuration binds task IDs and custom split files by content. It also
binds the dataset, side, agent model/backend/endpoint, trials, sampling,
Harness source and applicable scoring, input and runtime identities. See the
[preparation implementation](../tools/prepare_h0.py) and
[H0 reuse guide](h0-content-comparability.md) for strict reuse, content-attested
donors and the published historical baseline.

Recipient runs use the donor's round-zero point and `round0_traces`. Namespaced
IDs and ordered trial traces are preserved in the method evidence channel.

The previous walkthrough illustrated the tool output with 0.0111 on a
90-task AppWorld cohort. Keep that value with its original example.

**This page is the procedure.** Document reusable adapter contracts in the adapter's own public documentation; preserve historical evaluation evidence separately and link only to material actually distributed with the project.
If the dataset is new to you, read `docs/dataset-first-day.md` first: it is the
reconnaissance — three measurements that decide whether this dataset is runnable, refused, or
runnable with a measured exclusion — and its whole point is that **no adapter is written until
the gold has been run through the official scorer**.

**Precedence:** executable contracts take precedence over this guide. If this page and
`data/dataset.schema.json` / `tools/dataset_lint.py` disagree about something a program
can decide, the program is right — fix this page.

Three rules that produce most of the failures this page exists to prevent:

1. **Read the function that computes the number**, not the paper. Dropping a requirement is
   a downgrade; adding one is an invention; both produce a plausible number.
2. **Declare the verifier's dependencies.** Use `VERIFY.inputs` for private
   task inputs and [verifier runtime declarations](dataset-runtime-interfaces.md) for
   supported read-only runtime mounts. The verifier namespace hides the platform
   tree, so test imports and paths through `--platform`.
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
| 2 | **do-nothing control matches the adapter's expected outcome** | `python3 data/datasets/<name>/control.py --noop` |
| 3 | **the scorer is checked against the pinned official metric** | `tests/quality/test_<name>_official_scorer.py` |
| 4 | **`KIND = "dataset"`** | `python3 tools/dataset_lint.py` |
| 5 | **the intended route produces a measurement** | Inspect the platform run's `run_meta.json`, `curve.jsonl` and per-task verdicts for the planned cohort. |

Gates 1 and 2 must pass **twice**: by hand, and through the platform
(`… --oracle --platform`, which drives `SETUP → harness → VERIFY` via
`eval.run.runner.run_one`). Check the expectations and any scoped exemptions
in [the dataset controls](../tests/quality/test_dataset_controls.py).
PersonaMem-v2 and MemoryAgentBench expect a `null` no-op reward. Preserve
that outcome and its recorded reason. The QA family's control is shared —
`python3 data/scaffolds/qa/control.py --dataset <name> --oracle`. For a
transcribed scorer, cite the pinned upstream function and compare the local
implementation against it. For an upstream engine or package scorer, pin the
engine and inputs and verify the claimed metric with independent controls.

Gate 3 has three admissible forms, and which one applies is decided by what the benchmark
publishes, not by which is cheapest:

* the metric **is a function in the upstream package** (Reasoning Gym's
  `Dataset.score_answer`) — call it, pin the package version, and compare the call against the
  library called independently in the test;
* the adapter reports **a published component under its own name**. HotpotQA
  uses answer F1 as reward and reports supporting-fact and joint metrics
  separately as diagnostics. The [MuSiQue adapter](../data/datasets/musique.py)
  reports official answer and support components through
  `data.datasets.musique.aggregate` and labels its scalar reward as a platform
  product.
* a required metric has **no faithfully supported execution or evidence route**.
  Use the refusal procedure in §5 and name the missing requirement.

MuSiQue copies its [output contract](musique-output-contract.md) verbatim into
each task as `qa/output-contract.md`. Keep its bytes stable to preserve
existing verification contexts. The adapter serves MuSiQue-Ans v1.0 dev and
reads the full dev pool when asked for task or split maps. It declares no
train/eval partition. Use `--split default` for the platform's hash division.

The distinction is not cosmetic: a number that is near a published one and shares its name
makes a curve point indistinguishable from a wrong metric, while a column reported under its
own column name is the benchmark's number.

`tests/quality/test_dataset_controls.py` enforces gates 1–3 over every published dataset: a
dataset with no row there fails the suite, which is what stops the next one from being
onboarded without a way to show its grade is reachable.

For Gate 5, record the dataset, runtime route and task IDs from an actual
platform measurement, using the applicable [execution policy](../eval/policy/diagnostic_policy.py).
Check that the curve and verdicts cover the intended measurement cohort with
finite scores. Record the reasons for exclusions and any invalid or unmeasured
outcomes. `task_difficulty.py` summarizes existing runs and exits successfully
when it skips a dataset with no measured state.

---

## 2. Where everything lives

| what | where |
| --- | --- |
| the dataset declaration | `data/datasets/<name>.py` — `KIND`, `load()`, `downloads()`, and optionally `provision_status()`, `SPLIT`, `ENV`, `VERIFY`, `SETUP` |
| **which tasks are study material** | `SPLIT` (the dataset's division -- the benchmark's own, or `None`) and `OFFICIAL_EVAL` (the ids of the benchmark's own evaluation set, or `None` when the pool holds no study material). A run may replace the division with `--split default\|official\|<file.json>`; it records `split_source` and `split_sha`, and two curve points belong on one axis only when the sha agrees — INTERFACE.md §2.3 |
| the data bytes | `<HG_DATA_ROOT>/<dataset-directory>/` — **never in git**; use `harnessgrad.paths.dataset_root()` to keep `DATASET_ROOT` portable and outside the platform tree (lint rule: `lint_paths`) |
| dataset-private code | `data/datasets/<name>/{lib,cli,verify,control,oracle_harness}/` |
| the machine-checkable contract | `data/dataset.schema.json` |
| the checker | `tools/dataset_lint.py` |
| the generator | `tools/new_dataset.py --kind qa\|command\|files` — see §3 |
| the check that runs over *every* published dataset | `tests/quality/test_dataset_lint.py` |
| the check that every dataset declares a control and a metric test, and runs the controls | `tests/quality/test_dataset_controls.py` |
| the classification: derived axes plus the one judged axis, as a `(demand, surface)` pair | `tools/dataset_maintenance/dataset_classes.py`. For a new adapter, also add its dataset row to `registry/dataset-blockers.json` and run `python3 tools/check_dataset_blockers.py` to check catalogue consistency |
| dataset-specific dependencies, limitations, and validation instructions | the adapter's public documentation, for example the [AIME README](../data/datasets/aime/README.md) |
| declared coverage gaps (demands the vocabulary names with no adapter yet) | `tools/dataset_maintenance/dataset_classes.py::UNCOVERED_DEMANDS`; currently empty because all six demands have adapters |
| which Harness function a dataset makes score-relevant, and which functions no dataset covers | `tools/dataset_maintenance/dataset_classes.py::EXERCISES` / `FUNCTIONS` / `UNCOVERED_FUNCTIONS` |
| the shared control for `command` datasets | `data/scaffolds/command_control.py` + `data/scaffolds/command_oracle_harness/` |
| the download index (generated) | `DOWNLOADS.md` — written by `tools/datasets.py --write`; **never edit by hand** |
| "which datasets exist" | `data/scaffolds/registry.py::published()` — a module appears iff `KIND == "dataset"` |

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
| | `exec` | a container; requires a working Docker runtime and the declared runtime admission |
| `VERIFY[<task>]["kind"]` | `answer` | the agent's answer is compared as a string; no check program runs |
| | `command` | an argv runs against the task workdir and writes a reward file |

The schema is the authority; this table summarizes these environment and verifier kinds.

### The download contract

`downloads()` returns one entry per artefact. Every entry needs `kind`, `name`, `version`,
`size` and a `note` of at least 20 characters; `version` may not be
`""`/`main`/`master`/`head`/`latest`/`trunk`/`dev`/`nightly`. `http` adds `url` + `dst`,
`hf-file` adds `repo` + `file` + `dst`, `hf-dataset` adds `repo` + `dst`, `pip` adds
`package`. Write the traps into `note` — a trap recorded only in a commit message is a trap
the next person pays for again.

An entry may also declare `manifest`, a repository-relative path to an existing file
containing its full source inventory and checksums. This is a documentation link;
fetching and verification still use `files` and `sha256`. The generated index summarizes
lists exceeding eight files or 600 filename characters and links to the manifest.
Without a manifest it provides the command to inspect the full JSON declaration.
Small per-file checksum maps are rendered as tables.
Download listing, checking and fetching do not require provisioned task pools:
if an adapter cannot import, the tool evaluates its `downloads()` source and referenced
definitions without executing unrelated pool initialization. A declaration that itself
cannot be evaluated is an error rather than an omitted dataset. A source-only check
reports the adapter import error separately from the resource-presence result.

Resolve external storage with `from harnessgrad.paths import dataset_root` and
`DATASET_ROOT = dataset_root("my_dataset")`. This honors `HG_DATA_MY_DATASET`, then
`HG_DATA_ROOT` in the environment or `.env`. There is no implicit storage directory;
unconfigured declarations remain importable and require configuration on filesystem
access. Retired spellings are not aliases and are no longer read.
Preparation scripts use the same function; they must not invent a separate default.
See [dataset storage](../README.md#dataset-storage) for configuration precedence and
linked-worktree behavior. Machine-specific absolute defaults are rejected by lint.
The index displays logical paths derived from the `dataset_root()` declaration and
each download's `dst`/`extract_to`, including directory aliases and shared roots.
It names the per-dataset override separately. `--check` continues to report the
configured storage's actual absolute paths; local `.env` settings do not enter the index.

```
python3 tools/datasets.py --check   --dataset <name>    # are the bytes here?
python3 tools/datasets.py --fetch   --dataset <name>    # get them (sha256 verified)
python3 tools/datasets.py --write                        # regenerate DOWNLOADS.md
```

`--fetch` verifies a declared `sha256` and deletes the file when it does not match, so a
`downloads()` entry without a hash is a fetch that trusts the network. Pin one.

**Download endpoints:** `--endpoint` defaults to `https://hf-mirror.com`; set
`HG_HF_ENDPOINT` or pass `--endpoint` to select another Hugging Face endpoint.
Network access depends on the host. If a pinned raw GitHub URL is inaccessible,
`HG_GH_RAW_MIRROR=<proxy prefix>` makes `--fetch` request
`<mirror>/https://raw.githubusercontent.com/<path>`. This opt-in setting rewrites
only that host; declared checksums still apply. The header of `DOWNLOADS.md`
documents these endpoint settings.

Transport and preparation can fail independently. See
[MemoryAgentBench preparation](../data/datasets/memoryagentbench/README.md#preparation)
for the pinned tokenizer dependencies, PyArrow, resource preparation and
`HG_MAB_PYTHON` setup. The historical 2026-11-02 and 2026-11-03 checks saw a
connection reset on raw GitHub without a mirror.

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

`_SPEC.build()` is also what decides the division, and an adapter never invents one
(`docs/dataset-splits.md`, 2026-10-24):

* the benchmark publishes **both** sides (`gsm8k`, `triviaqa`): pass `study=("train",)`.
  `SPLIT_SOURCE == "declared"` and `OFFICIAL_EVAL` is the scored split's ids;
* the table is **one** side of it (`aime`, `musique`, most of them): pass nothing, or say
  what the pool is with `pool_is="study"` when it is not the benchmark's evaluation set.
  `SPLIT` is then `None`, `SPLIT_SOURCE == "none"`, `OFFICIAL_EVAL` is `None` (the pool is
  the scored split) or `[]` (not an evaluation set), and a run covers every task.

A hash third is **not** an adapter's division: `--split default` gives a run the platform's
documented hash rule, recorded as `split_source: default`. Both declarations above are read
from `_BUILT`, so an adapter states them in exactly one place. `triviaqa` is the case where the study split is a declared **sample** rather than the
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

* **Declare only supported `files` fields.** In addition to `kind`, the registry
  accepts `agent_timeout_s`, `verify_timeout_s`, `network: "none"`, `agent_runtime`
  and `task_python`. Use the [runtime interfaces](dataset-runtime-interfaces.md)
  and the [dataset schema](../data/dataset.schema.json) to check the field shapes.
* **A directory in `VERIFY.inputs` must be re-materialisable.** The platform clears the
  target before copying (`eval/observe/tracing.py::_clear_tree`), because a read-only input tree
  (a JRE, a game suite) cannot be overwritten otherwise.
* **A dataset-private helper must be named per dataset.** Two datasets that both ship a
  module called `control` collide in `sys.modules`; this only shows in a full-suite run.

---

## 4. The order

1. **Read the upstream scorer**, not the paper. Find the function that computes the number;
   every requirement in it must be in your check, and nothing that is not.
2. **Find the reader of every field you declare**: `grep -rn "<field>" eval/ harnessgrad/`.
   If nothing reads it on your `env.kind`, it is not a channel.
3. **Write `downloads()` first**, with pinned versions and a plain-language note per trap.
   Then `python3 tools/datasets.py --check`.
4. **Write the CLI and the check as one shared library plus two thin programs.**
5. **Strip the answer** from everything the agent receives.
6. **Write and run the oracle and do-nothing controls** by hand and through
   `--platform`. Check the declared outcomes before writing the integration notes.
7. **Record the scoring evidence required by [Gate 3](#1-the-five-gates).**
   Cite the pinned metric and the tests or controls that verify it.
8. **Name the reason in the evidence**, and keep "unmeasured" distinct from "failed": no
   reward file, exit 3, `evidence["measured"] is False` → the task is `unmeasured`, not 0.
9. **Run `python3 tools/dataset_lint.py` and `python3 tools/datasets.py --write`.**
10. **Document the validation scope and limitations** in the adapter's public documentation.
    Include the commands, task counts, observed variation, and any limits on comparison with upstream results.

---

## 5. When the contract cannot carry the metric: a refusal

Some benchmarks cannot be scored here without reporting a different number. The honest
outcome is a **first-class refusal**, not a nearby metric:

* the module sets `REFUSED` to a sentence naming exactly what is missing and what was
  measured;
* `load()` raises that same sentence;
* `tools/dataset_lint.py` prints `REFUSED <name>: …`, returns **rc 0**, and still checks
  `KIND`, the required callables, the data-root path and the `downloads()` entries;
* `tests/quality/test_refusal_contract.py` checks two kinds using a synthetic
  adapter. Use the [blocker registry](../registry/dataset-blockers.json) for
  current dataset dispositions.
  * `contract_gap` — the metric needs an unsupported channel. Past examples
    included FEVER's evidence, HotpotQA's supporting facts and SOTUQA's
    unpinned judge. Current FEVER and HotpotQA adapters accept structured
    evidence. Their own documentation defines the reward and diagnostic metrics.
  * `unreproducible` — the metric cannot be reproduced from the released samples at all
    (`api_bank`'s gold call reproduced its own recorded result on only 262/508 = 51.6%).

  τ² was the interesting case and it is instructive rather than hypothetical: it is dual-control
  and 94 of its 278 tasks carry `requestor: "user"` gold actions, which read like a contract gap
  until the platform owned the simulator. What lifted it was platform code, not a weaker claim.

**A refusal must be reached by measurement, not by reading the README.** Run the gold
through the official scorer and report the number you got. "It looked hard" is not a
refusal.

---

## 6. Definition of done

```sh
python3 data/datasets/<name>/control.py --oracle              # expect reward 1
python3 data/datasets/<name>/control.py --noop                # check the declared outcome
python3 data/datasets/<name>/control.py --oracle --platform   # expect reward 1 again
python3 data/datasets/<name>/control.py --noop   --platform   # check the declared outcome again
python3 tools/dataset_lint.py                        # expect: ok <name>, rc 0
python3 tools/datasets.py --write                    # DOWNLOADS.md, generated
python3 -m pytest tests/quality -q                   # the suite stays green
```

Record the applicable [Gate 3 and Gate 5](#1-the-five-gates) scoring and run evidence. Confirm
the dataset appears in `data.scaffolds.registry.published()`. Keep dataset
payloads outside Git and record the integration lesson in the skill doc.

`tests/quality/test_dataset_controls.py` enforces the shape of this section over every
published dataset: each one must declare a control entry point and a scorer test, and the
controls are actually run. Datasets that predate the convention carry a reasoned exemption
in that file rather than a weakened assertion.
