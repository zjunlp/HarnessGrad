# Handover: onboarding a Harbor dataset into HarnessGrad

**Audience:** whoever adapts datasets. **Status:** process agreed, two platform-side
prerequisites outstanding (listed at the end).

This file is the **Harbor-specific overlay** on the dataset process. It deliberately does not
repeat the five gates or the ten steps — those live in [`dataset-onboarding.md`](dataset-onboarding.md)
and that file stays the single source of truth for them. What is here is only what changes when the
dataset comes from Harbor, plus the decision that has to be made before any work starts.

The container-side blocker is specified separately in
[`runtime-observation-brief.md`](runtime-observation-brief.md).

## 0. The decision that comes first: port, or wrap?

**Every Harbor adapter is container-first.** Each one declares `environment: type: docker` (or a
cloud container host). So "use Harbor's datasets" has a consequence that is easy to miss:

* **wrap** — call `harbor run` from a HarnessGrad adapter. Everything lands on the container path,
  so **every** dataset is refused at admission until the runtime-observation work lands. Including
  the ones whose tasks need no container at all.
* **port** — read Harbor's task definition and metric, and re-express them in our own native
  shape. Nothing calls Harbor. These run today.

Harbor's value to us is therefore **its task definitions and metric implementations**, not its
execution. Borrow those; do not inherit the execution model.

### The per-adapter table

Fill this in once per adapter before touching code. **Five** questions decide everything. The
fifth was added after the first port attempt, because it is the one that is easiest to skip and
it killed the obvious first candidate.

| adapter | container needed? | delivery | upstream open + pinnable? | `parity_experiment.json`? | Harbor is the upstream? | route | runnable today? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `aime` | no (text + integer match) | one-shot | **yes** — `GAIR-NLP/AIME-Preview` @ `7011a9ea…`, 3 JSONL, 162 KB | **no** | no | **port** | yes |
| `gpqa-diamond` | no | one-shot | **no** — upstream is HF `Idavidrein/gpqa`, **gated**; Harbor's generated tasks sit in a **4.2 GB** repo | yes (87.88 ± 0.58 vs 87.21 ± 0.34) | no | port, but see §2 | blocked on data |
| `locomo` | no | one-shot, whole transcript | ? | yes | no | **port** | yes |
| `simpleqa` | no | one-shot | ? | ? | no | port | yes |
| `swebench` | yes | environment | ? | ? | **no** — upstream is the SWE-bench package | wrap | no — blocked |
| `terminal-bench` | yes | environment | ? | ? | **yes** — Harbor is the official harness | wrap | no — blocked |

`?` means unverified. Run the checks described in §2 and §3 to fill it.

**Why the fifth question is decisive.** A gated upstream cannot be pinned by `downloads()`, and
Harbor's generated tasks are published in `harbor-framework/harbor-datasets`, which is **4.2 GB**
— too large to fetch for one dataset, and `downloads()` has no per-file kind. So an adapter whose
only reachable data source is either gated or inside that repository is **not** a first target,
however simple its metric is. `gpqa-diamond` is exactly this case: a one-line metric and a good
parity record, and no data we can pin. `aime` became the first target because its upstream is a
small open repository.

**Rule of thumb:** if the task is "read this text, answer this question, compare to gold", port it
and it runs today. If the task is "work inside an environment and leave artifacts for a verifier",
it is blocked on the admission work no matter how it is adapted.

## 1. The delivery shape decides the label, not the dataset's name

`tools/dataset_classes.py` defines `memory` as *"the Harness must maintain a representation of
information that arrives **over time and changes**"*, and contrasts it with `evidence`, which
*"asks it to select from a **static set**"*.

So before labelling anything, answer: **does the material arrive all at once, or incrementally?**

* **one-shot delivery** → the material is in context, the questions come with it, and retaining
  anything cannot change the score. This is `evidence` on a long-context surface. **Labelling it
  `memory` would be a claim the run does not support.**
* **incremental delivery** (material in sequence, later material withheld) → retention is required,
  and `memory` is the right demand, with `authored_store` if the Harness builds the store or
  `supplied_history` if the history is provided and must be preserved.

`locomo` is the worked example of the first case. Its name says long-term memory, and its
**upstream evaluation is a closed-book single call with the full transcript in context** — see
`adapters/locomo/README.md` ("the agent receives the full text-only transcript plus the question
list in its instruction") and `adapter_metadata.json` ("Upstream eval is a closed-book LLM call …
batch size 200 so all questions for a conversation are answered in one call"). Retention is not
exercised and the metric cannot see it, so it is not a `memory` dataset here.

That does not make it worthless. "A long-context task where a memory mechanism is available but
unnecessary — does the Harness use it anyway, and does that help or hurt?" is a real question. It
is a **platform capability**, not a labelling choice: answering it needs a recorded field for
whether the Harness wrote to a store. `.state/` is a per-task tmpfs and never reaches the curve,
so today that question has no answer in the data.

**Also check coverage when labelling, and expect a port to add none.** `exercises` still never uses
`delegation`, `reliability` or `cost` across all 29 datasets, and `demand: memory` has no dataset at
all. `aime` — the first port actually completed — landed on the existing `(reasoning, reasoning)`
cell with the existing `{context, verification}` exercise set, so it moved coverage by **zero**. That
is the expected result of porting a member of a family you already have, and it is the reason to
pick the next target from the gap list rather than from the list of adapters that look easiest. A new
dataset that fills one of those cells is worth more than one that duplicates an existing cell.

## 2. What a Harbor adapter is made of, and where the data is

**The layout is not uniform, and an earlier version of this file got it wrong by describing one
adapter's layout as if it were the convention.** `locomo` uses `src/<name>/{adapter,main,metric}.py`
plus `src/<name>/task-template/`; `gpqa-diamond` has no `src/` at all and puts its files at the
adapter root (`adapter.py`, `template/`, `run_adapter.py`); `aime` uses `src/aime/{adapter,main}.py`
plus `src/aime/task-template/`. **There is no guaranteed `metric.py`** — `locomo` has one,
`gpqa-diamond` and `aime` do not.

Where the metric actually lives, in the two shapes seen so far:

* **in the task template's verifier**, which is the Terminal-Bench heritage.
  `gpqa-diamond/template/tests/test.sh` is the scorer, and the adapter bakes the gold in by
  substituting it into the script:
  `test_sh.replace("{answer}", task.correct_letter)`. The generated task under
  `harbor-datasets` ships the same script with the answer already in it;
* **in `src/<name>/metric.py`**, for adapters written that way (`locomo`), where
  `adapter_metadata.json` states which upstream file it mirrors.

So the first move is always the same — **find the scorer** — but do not assume the filename.

### The data source is `harbor-framework/harbor-datasets`

This is the piece that makes a port practical, and it is easy to miss because the adapter's own
runner points somewhere else. `gpqa-diamond/run_adapter.py` loads
`load_dataset("Idavidrein/gpqa", "gpqa_diamond")` and fails with *"GPQA is a gated dataset"* when
the terms have not been accepted. That upstream is **gated**, which our `downloads()` contract
cannot pin.

`harbor-framework/harbor-datasets` publishes the **generated tasks** for 100+ benchmarks in a
public repository: `datasets/<name>/<task index>/` with `instruction.md`, `environment/`,
`solution/`, `task.toml` and `tests/` (the scorer, with the gold already substituted). For
`gpqa-diamond` that is 198 task directories. Its `.gitignore` excludes only large venvs, so the
data is committed.

**Pin that repository at a commit and digest, not the gated upstream.** Record the provenance
chain in the module docstring — generated tasks come from `harbor-datasets`, which came from the
upstream — and cite `parity_experiment.json` for the equivalence.

### `parity_experiment.json` is the equivalence evidence

Where it exists. For `gpqa-diamond` it records three trials on both sides —
original `87.88 +/- 0.58`, Harbor `87.21 +/- 0.34` — and names the upstream repository and the
pull requests. That is a recorded answer to "is our number the benchmark's number?", which is what
gate 3 needs. `locomo` has one too; `aime` does not, so check per adapter rather than assuming.


## 3. The flow

Same ten steps and five gates as [`dataset-onboarding.md`](dataset-onboarding.md), with these five
Harbor-specific changes.

| step | what changes for Harbor |
| --- | --- |
| **0. triage** | Answer the five questions in §0. Container + environment-shaped → **stop**, it is blocked. Also check for `parity_experiment.json`. |
| **1. read the source, not the README** | **Find the scorer**, wherever this adapter keeps it — a task-template verifier, or a `metric.py` (§2). Read **`parity_experiment.json`** if it exists. Also settle the **delivery shape** (§1) — it decides the label. |
| **2. choose the gate-3 form** | Transcribe the scorer's rule verbatim and pin its source; or report a published table column under its own name; or refuse. Where a parity record exists, cite it as the equivalence evidence. |
| **3. `downloads()`** | Pin **what the adapter actually reads**, which is not the same repository twice. If the adapter's own upstream is open and small, pin *that* at a commit and digest and leave `harbor-datasets` alone (AIME: `GAIR-NLP/AIME-Preview` @ `7011a9ea…`). Only when the upstream is gated or absent do you pin `harbor-framework/harbor-datasets` at a commit and digest — and the 4.2 GB figure is why that fallback usually ends the attempt instead of starting it (GPQA). Record the provenance chain in the docstring either way, and watch for adapters that fetch from a **branch** rather than a commit: Harbor's AIME `main.py` reads `raw.githubusercontent.com/.../main/...`, which moves, so the commit is the fix, not a nicety. |
| **5. the instruction** | Harbor's `instruction.md` is the task text and it does **not** state the metric. Carry the platform's interface facts — the answer path, and the `qa submit` sentence `tests/quality/test_qa_dataset_contract.py` requires — and **do not restate the comparison rule**. For a bare string equality, spelling out that `070` and `70.` score 0 removes a format trap the benchmark leaves in place, and our number stops being comparable to Harbor's; an instruction-level advantage is still an advantage. Eleven adapters here *do* restate their metrics, so this is a deliberate exception for ported tasks, not the house style. `aime` records the decision in `_goal`; `gaia` and `terminal_bench` take the same position. |
| **6. scorer into a test + drift test** | Quote the scorer's lines, pin the source you pinned in step 3, cite the parity record **if one exists** (AIME has none, so the transcription carries the whole claim), and add the drift test so a change upstream breaks our test rather than our number. Vendoring the files and re-deriving their **git blob digests** is stronger than a sha256 alone: a fixture edited by one byte cannot then pass as "the same file". |
| **7. label and register** | As in the base doc, **plus** a `source` field pointing at Harbor — which needs the schema change listed under prerequisites. The registry row is an exact set of seven fields today and is drift-checked against `tools/dataset_classes.py`. |

The three hard gates are unchanged: **step 0** (can it be done at all), **step 5** (oracle → 1 and
noop → 0, by hand *and* through the platform), **step 6** (metric identity).

Two of them are worth stating plainly because they are where a Harbor adaptation is most likely to
look finished while being wrong:

* **Step 5 cannot be satisfied by Harbor's own controls.** Harbor ships `oracle` and `nop` agents
  (`src/harbor/agents/oracle.py`, `nop.py`), and `locomo.yaml` runs with `agents: - name: oracle`.
  That proves the *task* is reachable inside Harbor. Our gate 5 asks whether *our platform*, driving
  *our harness*, gets 1 and 0 — a different question. It must be run through
  `tools/task_difficulty.py` and the `--platform` control path.
* **Step 6 is not satisfied by an upstream link in a README.** It is satisfied by a test that fails
  when the metric moves.

## 4. What the platform side owes (prerequisites, in order)

1. **Container admission** — the runtime observation producer, and the model-transport change for
   `exec` tasks. Nothing environment-shaped runs before this. Specified in
   [`runtime-observation-brief.md`](runtime-observation-brief.md).
2. **The `source` field.** Dataset rows are an exact set of seven fields
   (`tools/check_dataset_blockers.py`), so adding provenance is a schema change across the checker,
   the applier and their tests. Methods already carry `source_commit`, so the pattern exists.
3. **The shared harness-launch module.** The contract is already fixed and documented —
   `entrypoint --task <path> --workdir <path>` (`eval/run/runner.py:380`, INTERFACE.md §1.2) — and
   the pieces exist (`driver.py:1054 stage_workspace`, `runner.py:96 harness_env`,
   `runner.py:182 _run_one`, `eval/containers/harness_runtime.py`,
   `tools/configure_harness.py`). Extracting them into one reusable module is what keeps "any
   harness" implemented **once** rather than once natively and once inside a Harbor agent adapter.
   Without it the two paths will drift, and "the same harness, two executions" stops being
   comparable. 17 test files cover those functions; they must stay green.

## 5. What the dataset side owes, per adapter

* the §0 table filled in;
* the label (`demand`, `surface`, `exercises`) with the delivery shape as evidence;
* the three files touched together: `data/datasets/<name>.py`, `tools/dataset_classes.py`,
  `registry/dataset-blockers.json` — the last two are drift-checked against each other, so a
  mismatch fails the suite;
* the gate-3 test, quoting `metric.py` lines and citing the parity record;
* `REFUSED` rather than a nearby number, if the metric cannot be carried. A refusal must be reached
  by **measurement** — run the gold through the scorer and report what came back. "It looked hard"
  is not a refusal.

## 6. Definition of done

```sh
python3 data/datasets/<name>/control.py --oracle              # reward 1
python3 data/datasets/<name>/control.py --noop                # reward 0
python3 data/datasets/<name>/control.py --oracle --platform   # reward 1 through the platform
python3 data/datasets/<name>/control.py --noop   --platform   # reward 0 through the platform
python3 tools/dataset_lint.py                                 # ok <name>
python3 tools/datasets.py --write                             # DOWNLOADS.md regenerated
python3 tools/task_difficulty.py --dataset <name>             # rc 0
python3 -m pytest tests/quality -q                            # suite stays green
```

## 7. Open questions, stated rather than assumed

* **How many of the 85 adapters ship `parity_experiment.json`?** That number decides how many can
  clear gate 3 on recorded equivalence rather than on our own measurement. Unverified.
* **Does `metric.py` ever diverge from the upstream it mirrors?** The metadata claims it mirrors;
  the parity record is the evidence. Where neither exists, we measure.
* **Which adapters are genuinely text-only?** Harbor wraps everything in Docker, so the §0 table's
  second column is a claim about the *task*, not about the adapter's config, and it needs checking
  per adapter rather than inferring from the name.
