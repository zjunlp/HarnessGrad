# AIME source-only journal review

**Filing note.** This is the review for a dataset that arrived by **porting a Harbor adapter**
rather than by reading an upstream release directly, so it is also the record of what that
route costs. It was written after the five gates were run, not before: every number below is a
measurement this review made or a command it ran, and every line citation was read. Where
something was **not** done, it says so and names what doing it would take. Nothing here
authorizes a diagnostic, a pilot or production.

## Metric identity

Harbor's scorer is `adapters/aime/src/aime/task-template/tests/test_outputs.py`, vendored
byte-for-byte at `tests/fixtures/aime_official/task-template/tests/test_outputs.py`. Its rule,
by line:

* line 4 — `ANSWER_FILE = "/app/answer.txt"`;
* line 7 — `EXPECTED_ANSWER = "{answer}"`, a placeholder the adapter substitutes into, with the
  substitution at `adapters/aime/src/aime/adapter.py:60`:
  `content = content.replace("{answer}", str(task.answer))`;
* line 20 — `answer = f.read().strip()`;
* line 23 — `if answer == EXPECTED_ANSWER:`;
* line 38 — `sys.exit(0 if success else 1)`, and `test.sh:19-25` maps that exit code to
  `1` or `0` in `/logs/verifier/reward.txt`; `test.sh:33` itself always exits 0.

The comparison is therefore **`.strip()` and then string equality**. It is *not* an integer
parse, and the difference is observable rather than theoretical: `070` is the integer 70 and
scores 0. `data/datasets/aime.py:104` transcribes this and `data/datasets/aime.py:34-38` quotes
the upstream lines verbatim.

Two consequences are load-bearing and both are transcribed:

1. **A wrong format scores 0, not "unmeasured".** `test.sh` writes `0` when the scorer exits
   non-zero, and a missing `/app/answer.txt` takes exactly that path. The local reading agrees:
   `data/scaffolds/qa/verify/check.py:34` records that a missing or empty `answer.txt` is a
   **measured zero**, distinct from `check.py:107-109`'s `measured: false` path which writes no
   reward file at all.
2. **The gold is a string and never an int.** Measured over all 60 released rows: `answer` is a
   `str` of pure digits in every one, so `str(task.answer)` at `adapter.py:60` is the identity
   and this port has no coercion to get wrong. `data/datasets/aime.py:144` still asserts the
   shape, and raises rather than silently stringifying an int or accepting a leading zero —
   because the promise of a canonical form is in the instruction, which is what would have to
   change.

Harbor ships **no `parity_experiment.json` for `aime`** (measured: `gpqa-diamond` and `locomo`
ship one and this adapter does not). Gate 3 therefore rests on the transcription and on the
digests in `tests/quality/test_aime_official_scorer.py`, not on a recorded comparison. Harbor
is Apache-2.0 and four files are vendored — `adapter.py`, `task-template/instruction.md`,
`task-template/tests/test.sh`, `task-template/tests/test_outputs.py` — each re-checked by its
**git blob digest**, so a one-byte edit to a fixture cannot pass as "the same file".

## Task and split provenance

Harbor's own `main.py:23-27` fetches the three JSONL files from

    https://raw.githubusercontent.com/GAIR-NLP/AIME-Preview/main/eval/data/aime/…

— from the **branch `main`**, which moves. A `downloads()` entry has to pin an immutable
artifact, so this adapter pins the commit instead:
`GAIR-NLP/AIME-Preview` @ `7011a9eae7c4b78071f27b6f77f618d65608cfb0`
(`data/datasets/aime.py:78`), repository tarball
`data/datasets/aime.py:79-83`, sha256
`1cb676694f93ca7cfbcc4f160ace9176262f4822fcfa1acecd47ebfd9d6863c9`.

Measured: `python3 tools/datasets.py --fetch --dataset aime` downloaded **2,079,990 bytes**,
reported `sha256 ok` and extracted to `/mnt/20t/xubuqiang/datasets/aime`. The three members
landed at 146,233 / 6,226 / 9,426 bytes — matching the counts recorded in the adapter's own
`downloads()` note — and `data/datasets/aime.py:127` re-checks 30/15/15 against `N_TOTAL = 60`
on every load.

**The payload was read by this review, and the release is less uniform than the field list
suggests.** All three facts are measured over the extracted files:

* `test2024.jsonl` and `test2025-I.jsonl` carry `answer, id, problem, solution, url`;
  `test2025-II.jsonl` carries `answer, id, problem` — **no `solution` and no `url`**;
* `id` is an `int` (60…89) in `test2024.jsonl` and a `str` (`"I-1"`…, `"II-1"`…) in the other
  two;
* `answer` is a digit string in all 60 rows: 16 two-digit and 44 three-digit, no leading zero,
  no surrounding whitespace, integer range **16…907**, and **55 distinct values** — so the
  answer string is not a task key and a join on it would be wrong.

The platform assigns `aime/test/<row-index>` IDs and imposes the division, because AIME-Preview
released no study split: `SPLIT_SOURCE = "hash"` (`data/datasets/aime.py:263`),
`OFFICIAL_EVAL = None` (`data/datasets/aime.py:259`), and the split is **37 train / 23 eval**
over the 60 tasks. A hash split is the platform's own rule and is **not** the benchmark's
division; a run on `--side eval` scores those 23 tasks, and nothing here establishes that they
correspond to any original AIME partition.

## Runtime requirements and verification boundary

The environment is `files` (`data/datasets/aime.py:249-250`): text in, one integer out, no
container and no service. That is the whole reason this adapter was the first Harbor port —
Harbor's task template builds
`ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624` and its `task.toml` declares
`[verifier] network_mode = "public"` and `[agent] network_mode = "public"`, none of which this
platform's `files` kind needs or grants.

Gates, all run at this revision and none of them a model run:

| gate | command | result |
| --- | --- | --- |
| 1 oracle | `data/scaffolds/qa/control.py --dataset aime --oracle` | `reward 1`, `measured: true` |
| 2 noop | `… --noop` | `reward 0`, `measured: true` |
| 1+2 platform | `… --oracle/--noop --platform` | `1.0` / `0.0`, `sandbox=True`, read from `/verifier/reward.txt` |
| 3 transcription | `pytest tests/quality/test_aime_official_scorer.py` | **34 passed** |
| 4 contract | `python3 tools/dataset_lint.py --dataset aime` | `ok aime`, rc 0 |
| shared QA contract | `pytest tests/quality/test_qa_dataset_contract.py -k aime` | **12 passed** |
| 5 difficulty | `python3 tools/task_difficulty.py --dataset aime` | rc 0 — `no measured state -- skipped` |

The last row is the honest shape of gate 5 for a new adapter: it passes because there is
nothing to measure yet, not because a difficulty was established.

**The instruction-fidelity decision is part of the metric and is recorded here.** Harbor's
`instruction.md` is two sentences and the problem, and does not state the comparison rule. This
port says the same thing with two forced changes — this platform's answer path, and the
`qa submit` sentence `tests/quality/test_qa_dataset_contract.py:127` requires — and
**deliberately does not restate the metric**. The comparison is a bare string equality, so
spelling out that `070` and `70.` score 0 would remove a format trap the benchmark leaves in
place and make this adapter's number not comparable to Harbor's. An instruction-level advantage
is still an advantage. `data/datasets/aime.py:171` carries the reasoning and
`tests/quality/test_aime_official_scorer.py` pins it against drift and against a released
answer leaking into the example.

## Gate disposition

**CLOSED:** the metric transcription and its provenance
(gate 3, git-blob pinned vendored files and an upstream-line drift test); the oracle and
do-nothing controls by hand and through `--platform` (gates 1 and 2); the contract lint (gate
4); gate 5's rc; the shared QA contract over the adapter; the data pin (commit, tarball digest,
byte counts, row counts) and the payload census over all 60 rows. Every one of those closed with
the measurement named above, and none of them is closed by reading a document.

**OPEN:** four items, and the registry carries them as one `journal-coverage` blocker,
`aime / dataset-journal-gap`, rather than as measurements this review claims.

* **the operator-side bounded first-five selected-only custody packet**, which the 2026-10-15
  closures treated as a required clause. This review cannot produce it: it is an operator
  artefact about selection and privacy, and inventing one would be fabricating the record;
* **an independent metric-comparability statement** in the sense the closed datasets carry in
  `registry/w-metriccmp-comparability.md`. This port's statement is local and weak by
  construction: our scorer is Harbor's transcribed, with no `parity_experiment.json` to confirm
  that Harbor's number and ours are the same number;
* **measured coverage.** AIME has **never been run here** — zero tasks, zero trials, no run ID.
  Every coverage count is therefore absent rather than zero, and per the operator's 2026-10-15
  closures a coverage obligation is a precondition on a future, separately authorized
  diagnostic rather than a condition on this review;
* **side-by-side parity with Harbor's own published number**, which needs a run on both sides
  and is not authorized by this document.

This review closes **no** measurement, claims **no** score, and authorizes **no** diagnostic.
`registry/dataset-blockers.json`'s `production_authorized` stays false.

**One consequence this registration has, stated plainly.** `aime` is the first non-refusal
blocker this registry has carried since the 2026-10-15 closures, so the sentence "No non-refusal
blocker remains; the registry now carries only the four permanent refusals" in
`docs/evidence-status-decision.md` (operator decision 2026-10-16) is superseded from this
registration onward. That operator decision is not edited; the supersession is recorded as a
dated note in `registry/dataset-blockers.json`'s `review.notes`, which is where this repository
keeps registry-level records. Nothing else in that decision changes, and the four permanent
refusals are untouched.

## What this route taught, in one paragraph

Porting a Harbor adapter is **cheaper than the survey route for the scorer and dearer for the
provenance**, and the split is not where it looks. The scorer was the easy half: 39 lines with
an obvious rule, and three of Harbor's own files pinned by git blob make gate 3 stronger than
the convention asks for. The dear half is everything the adapter does *not* tell you — Harbor
fetches from a moving branch, publishes no parity record for this adapter, ships a
container-shaped task template the `files` kind cannot use, and states no metric in the
instruction, so the port has to decide for itself whether restating the metric is a
transcription or an advantage. Two of the three findings that changed this adapter came from
reading the payload rather than the adapter: `answer` is a string where the adapter's own
`str()` makes it look like a number, and `test2025-II.jsonl` carries two fewer fields than its
siblings. The reusable rule is in `docs/harbor-dataset-handover.md`: read the release, not the
adapter, and treat every field the scorer touches as unverified until counted.
