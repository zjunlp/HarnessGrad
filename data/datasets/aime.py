r"""AIME as a dataset: integer answers whose metric is exact string equality.

    python3 tools/datasets.py --fetch --dataset aime
    python3 data/scaffolds/qa/control.py --dataset aime --oracle
    driver.py --dataset aime ...

Where the tasks come from
-------------------------
`GAIR-NLP/AIME-Preview` at commit `7011a9eae7c4b78071f27b6f77f618d65608cfb0`, files
`eval/data/aime/test2024.jsonl` (30 tasks), `test2025-I.jsonl` (15) and `test2025-II.jsonl`
(15) -- 60 tasks, ~162 KB of JSONL inside a 2.0 MB tarball.

**Why this table, and not the one Harbor's adapter reads.** Harbor's AIME adapter is the
source of this port, and its `main.py` fetches the same three files from

    https://raw.githubusercontent.com/GAIR-NLP/AIME-Preview/main/eval/data/aime/...

-- from the **branch `main`**, which moves. A `downloads()` entry has to pin an immutable
artifact, so this adapter pins the commit those files were at when it was written and
records the digest of the whole repository tarball below. The three files are not fetched
individually because `downloads()` has no per-file kind; the tarball is small enough that
this costs nothing.

The provenance chain is therefore: Harbor's `adapters/aime` (task shape and metric) ->
`GAIR-NLP/AIME-Preview` (the problems and answers) -> this module. Harbor publishes no
`parity_experiment.json` for `aime` (unlike `gpqa-diamond` and `locomo`), so gate 3 rests on
the transcription below rather than on a recorded comparison, and the drift test pins the
pinned commit.

The metric, verbatim
--------------------
Harbor's `adapters/aime/src/aime/task-template/tests/test_outputs.py`:

    ANSWER_FILE = "/app/answer.txt"
    EXPECTED_ANSWER = "{answer}"          # the adapter substitutes the gold in
    ...
    answer = f.read().strip()
    if answer == EXPECTED_ANSWER: return True

The comparison is **`.strip()` and then exact string equality** -- it is *not* an integer
parse. So `70`, ` 70 ` and `70\n` score, while `070`, `70.` and `70 is the answer` do not.
That is the benchmark's rule and it is transcribed in the test rather than paraphrased.

Two consequences worth stating because they are easy to lose in a rewrite:

* a wrong *format* scores **0**, not "unmeasured" -- `test.sh` writes `0` to the reward file
  when `test_outputs.py` exits non-zero, and that includes a missing `/app/answer.txt`. This
  adapter keeps that: an unparseable answer is a failed task. It is not the same thing as an
  unmeasured task, and the platform records the two differently -- a format failure lands in
  the per-task record as a harness failure *and* scores 0;
* the answer space is stated in the instruction as "a single integer from 0 to 999", which
  is a property of the competition, not something this metric enforces. The metric enforces
  string equality and nothing else.

What this adapter does not compute
----------------------------------
Harbor's template carries no normalisation beyond `.strip()`, so neither does this one. No
integer parsing, no leading-zero stripping, no LaTeX or unit stripping: each of those would
accept an answer Harbor scores as wrong, which is the one thing a port must not do.
"""
from __future__ import annotations

import json

from data.scaffolds.qa import base

KIND = "dataset"

#: The oracle control submits the benchmark's own released answer; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"


DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/aime")

#: `GAIR-NLP/AIME-Preview` at the commit this adapter pins. See the module docstring for why
#: it is a commit and not the branch Harbor's own adapter reads.
AIME_PREVIEW_COMMIT = "7011a9eae7c4b78071f27b6f77f618d65608cfb0"
AIME_PREVIEW_TARBALL = (
    "https://codeload.github.com/GAIR-NLP/AIME-Preview/tar.gz/"
    "7011a9eae7c4b78071f27b6f77f618d65608cfb0"
)
AIME_PREVIEW_SHA256 = "1cb676694f93ca7cfbcc4f160ace9176262f4822fcfa1acecd47ebfd9d6863c9"

#: Where the three JSONL files land after the tarball is extracted.
EXTRACT_PREFIX = f"AIME-Preview-{AIME_PREVIEW_COMMIT}"
JSONL_MEMBERS = (
    "eval/data/aime/test2024.jsonl",
    "eval/data/aime/test2025-I.jsonl",
    "eval/data/aime/test2025-II.jsonl",
)
N_TOTAL = 60
N_BY_MEMBER = {"test2024.jsonl": 30, "test2025-I.jsonl": 15, "test2025-II.jsonl": 15}

#: The competition states this range in the instruction. It is recorded here so the goal can
#: repeat it, not because the metric enforces it -- the metric compares strings and nothing
#: else. Measured range of the 60 released golds: 16..907.
ANSWER_LO, ANSWER_HI = 0, 999


# ------------------------------------------------------------------ the scorer ---


def score(prediction: str, gold: dict) -> dict:
    """Harbor's rule: `prediction.strip() == gold["answer"]`, and nothing else.

    Not an integer comparison. `"070"` fails here exactly as it fails upstream.
    """
    want = str(gold.get("answer", ""))
    got = (prediction or "").strip()
    hit = got == want
    return {
        "reward": 1.0 if hit else 0.0,
        "correct": hit,
        "official": {"metric": "exact string equality after .strip()",
                     "answer": want,
                     "submitted": got},
        "why": (f"{'matches' if hit else 'does not match'} {got!r} vs {want!r}; "
                f"the comparison is a string comparison after .strip(), so a leading zero, "
                f"a trailing period or any surrounding words score 0"),
    }


# ---------------------------------------------------------------- the adapter ---


def _rows() -> list[dict]:
    rows: list[dict] = []
    for member in JSONL_MEMBERS:
        path = DATASET_ROOT / EXTRACT_PREFIX / member
        if not path.is_file():
            raise FileNotFoundError(f"no {member} at {path}")
        with path.open(encoding="utf-8") as handle:
            chunk = [json.loads(line) for line in handle if line.strip()]
        expect = N_BY_MEMBER[path.name]
        if len(chunk) != expect:
            raise ValueError(f"aime: {path.name} has {len(chunk)} rows, expected {expect}")
        rows.extend(chunk)
    if len(rows) != N_TOTAL:
        raise ValueError(f"aime: {len(rows)} rows, expected {N_TOTAL}")
    return rows


def _gold(row: dict) -> dict:
    """The released answer, kept as the string the metric compares against.

    Measured at the pinned commit: all 60 rows carry `answer` as a **`str`** of pure digits
    (`'204'`, `'70'`), never an `int` -- so there is no coercion here, and the value is
    returned exactly as released.

    The shape is asserted rather than assumed because the metric substitutes the gold into
    `EXPECTED_ANSWER = "{answer}"` and then compares strings. A gold carrying a leading zero
    would mean the released canonical form is not the plain integer, so an agent that answers
    the mathematically equal `70` scores 0 for a reason nothing in the task states -- a trap
    worth stopping on rather than transcribing silently. A non-digit gold would mean this is
    not the integer-answer benchmark the module describes. Measured across the three files:
    16 two-digit and 44 three-digit answers, no leading zero, no surrounding whitespace,
    integer range 16..907.
    """
    raw = row["answer"]
    if not isinstance(raw, str) or not raw.isdigit():
        raise ValueError(f"aime: answer {raw!r} is not a string of digits")
    if len(raw) > 1 and raw.startswith("0"):
        raise ValueError(
            f"aime: answer {raw!r} has a leading zero. The metric compares strings, so this is "
            f"the gold as released and must not be stripped -- but an agent answering the equal "
            f"integer would score 0 with nothing in the task saying so. Stop and decide how the "
            f"goal should state the canonical form rather than transcribing this silently.")
    if not ANSWER_LO <= int(raw) <= ANSWER_HI:
        raise ValueError(f"aime: answer {raw!r} is outside {ANSWER_LO}..{ANSWER_HI}")
    return {"answer": raw}


def _goal(problem: str, gold: dict) -> str:
    """The instruction, carrying upstream's information and no more.

    Harbor's `instruction.md` is two sentences and the problem, and no more than that:

        Solve the following problem. Reason step by step, create the file
        `/app/answer.txt`, and put your final answer there.
        Your answer should be a single integer from 0 to 999 inclusive.

    This says the same thing. Two changes are forced and are the only ones:

    * the answer path, because `test_outputs.py`'s `ANSWER_FILE` is the container path
      `/app/answer.txt` and there is no `/app` on this platform;
    * the `qa submit` sentence, which `tests/quality/test_qa_dataset_contract.py` requires of
      every QA goal.

    The metric is deliberately **not** restated, although eleven adapters here do restate
    theirs. The comparison is a bare string equality, so spelling out that `070` and `70.`
    score 0 would remove a format trap the benchmark leaves in place, and this adapter's number
    would then not be comparable to Harbor's -- an instruction-level advantage is still an
    advantage. `gaia` and `terminal_bench`, the two other adapters whose task text arrives from
    outside, take the same position. `docs/harbor-dataset-handover.md` §3 records the rule.
    """
    return (
        "# Task\n"
        "\n"
        "Solve the following problem. Reason step by step, create the file\n"
        f"`{base.ANSWER_FILE}` in your working directory, and put your final answer there.\n"
        f"Your answer should be a single integer from {ANSWER_LO} to {ANSWER_HI} inclusive.\n"
        "\n"
        f"{problem}\n"
        "\n"
        "## How to answer\n"
        "\n"
        f"Write the integer to `{base.ANSWER_FILE}`, or submit it with\n"
        "\n"
        f"    python3 {base.CLI_FILE} submit \"<the integer>\"\n"
    )


def _downloads() -> list[dict]:
    return [
        {
            "kind": "http",
            "name": "GAIR-NLP/AIME-Preview tarball (the three AIME JSONL files)",
            "version": AIME_PREVIEW_COMMIT,
            "url": AIME_PREVIEW_TARBALL,
            "size": "2.0 MB tarball, ~162 KB of JSONL (60 tasks)",
            "sha256": AIME_PREVIEW_SHA256,
            "dst": "aime-preview.tar.gz",
            "extract_to": ".",
            "note": "Trap: Harbor's own adapter reads these files from the branch `main` "
                    "(`raw.githubusercontent.com/GAIR-NLP/AIME-Preview/main/...`), which "
                    "moves. A download entry must pin an immutable artifact, so this pins the "
                    "commit and the tarball digest instead. Measured at that commit: "
                    "test2024.jsonl 146,233 B / test2025-I.jsonl 6,226 B / "
                    "test2025-II.jsonl 9,426 B, 30+15+15 = 60 tasks.",
        },
    ]


_SPEC = base.QASpec(
    name="aime",
    root=DATASET_ROOT,
    rows=lambda: {"test": _rows()},
    question=lambda row: str(row["problem"]),
    gold=_gold,
    goal=_goal,
    score=score,
    downloads=_downloads,
    split="test",
    source="harbor-framework/harbor adapters/aime -> "
           "GAIR-NLP/AIME-Preview@7011a9eae7c4b78071f27b6f77f618d65608cfb0 "
           "eval/data/aime/{test2024,test2025-I,test2025-II}.jsonl",
)

_BUILT = _SPEC.build()

DEFAULT_ENV = _BUILT["DEFAULT_ENV"]
ENV = _BUILT["ENV"]
SPLIT = _BUILT["SPLIT"]

#: The benchmark's own evaluation set, within this adapter's pool.
#:
#: Read from `QASpec.division()`, the one place that decides what this pool's division is:
#: `None` here because AIME-Preview released no study split, so the pool **is** the scored
#: split and every task in it belongs to the evaluation set. `--split official --side eval`
#: scores the whole pool, which is the comparable run.
OFFICIAL_EVAL = _BUILT["OFFICIAL_EVAL"]

#: `"none"`: the benchmark states no division for this pool, so this adapter declares none
#: and the run covers every task. It used to fill the slot with the platform's hash third
#: (`"hash"`), an invented division removed by the operator on 2026-10-24; `--split default`
#: still names that rule at run time. See `data/scaffolds/qa/base.py::division`.
SPLIT_SOURCE = _BUILT["SPLIT_SOURCE"]


def __getattr__(name: str):
    return base.module_getattr(_SPEC, name)


def load() -> tuple[list[dict], dict[str, str]]:
    return _SPEC.load()


def downloads() -> list[dict]:
    return _SPEC._downloads()


def provision_status() -> dict:
    return _SPEC.provision_status()


__all__ = ["KIND", "DATASET_ROOT", "N_TOTAL", "load", "downloads", "score",
           "AIME_PREVIEW_COMMIT", "SPLIT"]
