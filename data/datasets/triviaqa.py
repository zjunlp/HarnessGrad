r"""TriviaQA as a dataset: open-domain trivia questions graded against an alias set.

    python3 tools/datasets.py --fetch --dataset triviaqa
    python3 data/scaffolds/qa/control.py --dataset triviaqa --oracle
    python3 data/scaffolds/qa/control.py --dataset triviaqa --oracle --platform
    driver.py --dataset triviaqa ...

Where the tasks come from
-------------------------
Joshi, Choi, Weld, Zettlemoyer, *TriviaQA* (arXiv:1705.03551) -- `mandarjoshi/trivia_qa`,
config **`rc.nocontext`**, split **`validation`**: 17,944 questions, 7.3 MB. That split is
the exam side.

The **study side** is the benchmark's own `train` split, sampled to 2,000 rows by an even
stride across the file (`STUDY_ROWS`, and the reason is measured in `_rows()`: the file is
ordered by question source, so a prefix would be one source). The division is therefore
TriviaQA's own -- `--split declared` and `--split official` agree -- and the other 136,384
training rows are on disk and unread by design; `--split <file.json>` is how a run asks for
a different study set.

**Two choices, both forced, both worth stating.**

1. **`validation`, not `test`.** The benchmark's own `test` split has its gold withheld: in
   the mirror copy measured here every row's `answer["value"]` is `"<unk>"` and its
   `normalized_aliases` is empty. Scoring on `test` would report the fraction of systems that
   answer `"<unk>"`. ReWOO's `run_eval.py` never overrides its loader's `split="test"` default,
   so a TriviaQA number attributed to ReWOO has to come from `validation` or from `<unk>`; this
   adapter uses `validation` and says so.
2. **`rc.nocontext`, not `rc`.** `nocontext` is the config without retrieved documents, which
   is the one a parameterised agent can be scored on without a retrieval tool. It is also the
   one ReWOO used (`utils/DataLoader.py::load_trivia_qa`). The full `rc` config is 8 GB.

The metric and scorer identity
------------------------------
The official scorer is `evaluation/triviaqa_evaluation.py` in the dataset's own repository:
normalise both sides the same way, and count a hit if the prediction matches **any** alias --
`metric_max_over_ground_truths` takes the `max` over `get_ground_truths(answer)`.

New evidence records `SCORER_IDENTITY` and the pinned upstream EM reference. This
adapter reports per-row alias-max EM only; it does not implement upstream F1 or
question/document grouping. Historical scores keep their recorded scorer identity.

`get_ground_truths` is `answer['NormalizedAliases'] + [normalize_answer(a) for a in
answer.get('HumanAnswers', [])]`. Two things about it matter:

* the official key names are **capitalised** (`NormalizedAliases`, `HumanAnswers`); the
  HuggingFace copy lower-cases them and ships **no human answers** for this config, so the
  gold set here is the normalised aliases. The code below reads both spellings rather than
  one, because a silent `KeyError`-turned-empty-set would score every answer 0.
* the gold set is the **normalised** aliases. Normalising the raw `aliases` instead gives a
  different string whenever an alias carries punctuation or an article -- measured on the
  first row's aliases, where the two happen to agree, and the rule is what the upstream code
  does, not what one row shows.

What is deliberately not added
------------------------------
No retrieval tools and no context: `rc.nocontext` is the config that has none, so a score
here measures what the model knows, not what it can look up. A harness that wants to search
is free to, but the dataset does not hand it documents, because the config it pins does not
have any.
"""
from __future__ import annotations

import re
import string

from data.scaffolds.qa import base

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"

DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/triviaqa")

#: The pinned revision of `mandarjoshi/trivia_qa`. A commit sha: `main` is not a version.
REVISION = "0f7faf33a3908546c6fd5b73a660e0f8ff173c2f"
CONFIG = "rc.nocontext"
VALIDATION = f"{CONFIG}/validation-00000-of-00001.parquet"
TRAIN = f"{CONFIG}/train-00000-of-00001.parquet"
TEST = f"{CONFIG}/test-00000-of-00001.parquet"


# ------------------------------------------------------------------ the scorer ---
#
# EM semantics are compared with independently acquired, byte-pinned source in
# `tests/fixtures/triviaqa_upstream`, not a second local transcription. Local
# extensions are string coercion, Hub key spellings, float rewards and empty max=0.

SCORER_IDENTITY = "triviaqa-alias-max-em-upstream-normalization-all-aliases-v2"
SCORER_REFERENCE = {
    "repository": "https://github.com/mandarjoshi90/triviaqa",
    "commit": "ca43b5820b107f3970cf4b7d67f7db7a98117b79",
    "source_path": "evaluation/triviaqa_evaluation.py",
    "source_sha256": "51785a3bc2e02a8f1ebf9db3aa18834c6a5daa5089ee5d435c99e0babf4be682",
}


def normalize_answer(s: str) -> str:
    """Pinned upstream normalization semantics, with local string coercion.

    Replace underscores, lowercase, replace punctuation with spaces, remove English
    articles, then collapse whitespace. For example, `"The A-Team"` becomes `"team"`.
    """

    def remove_articles(text: str) -> str:
        return re.sub(r"\b(a|an|the)\b", " ", text)

    def white_space_fix(text: str) -> str:
        return " ".join(text.split())

    def remove_punc(text: str) -> str:
        exclude = set(string.punctuation + "‘’´`")
        return "".join(" " if ch in exclude else ch for ch in text)

    def lower(text: str) -> str:
        return text.lower()

    def replace_underscore(text: str) -> str:
        return text.replace("_", " ")

    return white_space_fix(remove_articles(
        remove_punc(lower(replace_underscore(str(s)))))).strip()


def get_ground_truths(answer: dict) -> list[str]:
    """Upstream alias selection with official and Hub key spellings.

        answer['NormalizedAliases'] + [normalize_answer(a) for a in answer.get('HumanAnswers', [])]

    The HuggingFace columns are `normalized_aliases` / `human_answers` and this config has
    no human answers at all, so the second term is empty here. Both spellings are read
    because the official JSON and the Hub copy differ, and a missing key must not silently
    become an empty gold set.
    """
    truths = list(answer.get("NormalizedAliases")
                  or answer.get("normalized_aliases") or [])
    human = answer.get("HumanAnswers") or answer.get("human_answers") or []
    return [str(t) for t in truths] + [normalize_answer(h) for h in human]


def exact_match_score(prediction: str, ground_truth: str) -> float:
    """Upstream normalized exact match, returned as a float reward."""
    return float(normalize_answer(prediction) == normalize_answer(ground_truth))


def metric_max_over_ground_truths(metric_fn, prediction: str,
                                  ground_truths: list[str]) -> float:
    """Upstream alias maximum, with a local zero for an empty alias list.

    This `max` is the official rule: one alias is enough. Scoring against `answer["value"]`
    alone is a **different, harsher** metric -- the `rewoo` filter, not the official one.
    """
    if not ground_truths:
        return 0.0
    return max(metric_fn(prediction, gt) for gt in ground_truths)


def score(prediction: str, gold: dict) -> dict:
    """The dataset's grade. Called by the shared check (`data/scaffolds/qa/verify/check.py`)."""
    accepted = list(gold.get("accepted") or [])
    hit = metric_max_over_ground_truths(exact_match_score, prediction or "", accepted) == 1.0
    return {
        "reward": 1.0 if hit else 0.0,
        "correct": hit,
        "official": {
            "metric": "exact_match over the alias set (metric_max_over_ground_truths)",
            "scorer_identity": SCORER_IDENTITY,
            "reference": dict(SCORER_REFERENCE),
            "accepted": accepted[:20],
            "normalized_prediction": normalize_answer(prediction or ""),
            "canonical_value": gold.get("value"),
        },
        "why": (
            f"{'matches' if hit else 'no match for'} "
            f"{normalize_answer(prediction or '')[:60]!r} among {len(accepted)} alias(es)"
            + (f"; canonical value {gold.get('value')!r}"
               if gold.get("value") else "")
        ),
    }


# ---------------------------------------------------------------- the adapter ---


#: The files this adapter knows about. The scored split is read on demand; `train` is read
#: only to build the study side, and only as a sample (see `STUDY_ROWS`).
SPLIT_FILES = {"validation": VALIDATION, "train": TRAIN, "test": TEST}

#: The two fields this adapter reads. TriviaQA ships `entity_pages` and `search_results`
#: beside them -- the retrieved Wikipedia pages and search results -- which no part of this
#: adapter or its scorer touches, and which are what the decode time is spent on: measured
#: on `train`, **9.46 s** for every column against **0.34 s** for these two.
COLUMNS = ("question", "answer")

#: How many rows of the official `train` split this adapter loads as study material.
#:
#: The split is 138,384 rows, and loading all of it is not a matter of taste. Measured:
#: `to_pylist()` over every row costs **3.55 s and ~850 MB** of nested `answer` dicts, and
#: a record keeps its whole row, so `records()` for `train` + `validation` (156,328
#: records) is 7.6 s / 1.1 GB and `load()` -- which renders every goal -- did not finish
#: inside 60 s. A study side also costs one harness run per task, so 138k of them is not a
#: round anybody runs.
#:
#: 2,000 is a declared sample, not a truncation of convenience: the rule is in
#: `_rows()`/`data/scaffolds/qa/read.py::parquet_rows(sample=…)` (every `total//2000`-th row in file
#: order), it is deterministic, and the file it samples is pinned by sha256 in
#: `DOWNLOADS.md`.
STUDY_ROWS = 2000


def _rows():
    """A lazy mapping: reading a split costs a parquet decode, so only the splits that are
    actually asked for are read. `records()` asks for `self.split` and, here, `train`.

    Reading `train` in full used to be refused outright for cost. With the projection and
    the arrow-side sample that is 0.33 s and a few thousand Python dicts, so what remains
    is the size of the *pool*, which is why the train side is sampled rather than whole.

    **Why an even spread and not the first 2,000 rows.** TriviaQA's parquet is ordered by
    question source, measured: row group 0 (the first 1,000 rows) is 100% `tc_*`, while
    rows spread across the file carry `qw`, `qg`, `tc`, `bt`, `odql` and more. A plain
    prefix would study one question writer's style and then be scored on a validation
    split that has all of them -- a study side that cannot transfer, chosen silently by a
    file layout. Measured on the two rules: the first 2,000 rows are 2,000 `tc` questions,
    the evenly spread 2,000 carry 8 sources with `sfq` the largest at 25%.
    """
    return _LazySplits()


class _LazySplits:
    def __init__(self) -> None:
        self._loaded: dict[str, list[dict]] = {}

    def get(self, split, default=None):
        if split not in SPLIT_FILES:
            return default
        if split not in self._loaded:
            self._loaded[split] = base.parquet_rows(
                DATASET_ROOT / SPLIT_FILES[split],
                columns=COLUMNS,
                sample=STUDY_ROWS if split == "train" else None,
            )
        return self._loaded[split]


def _question(row: dict) -> str:
    return str(row["question"]).strip()


def _gold(row: dict) -> dict:
    """The alias set, plus the dataset's own canonical `value`.

    `value` is what the control submits: it is the alias the dataset itself calls the
    answer, so it is in the accepted set by construction, and the control therefore tests
    the scorer rather than the control.
    """
    answer = {k: base.normalize_missing(v) for k, v in dict(row.get("answer") or {}).items()}
    accepted = get_ground_truths(answer)
    value = str(answer.get("value") or "").strip()
    if not accepted:
        raise ValueError(
            "triviaqa: a row has no answer aliases. Measured cause: the `test` split of "
            "`rc.nocontext` withholds its gold (`value` is `<unk>`, aliases empty), and "
            "scoring it would report the fraction of systems answering `<unk>`. Use "
            "`validation`.")
    return {"answer": value or accepted[0], "value": value or accepted[0],
            # The max must consider every alias; a size cap can discard the only hit.
            "accepted": accepted, "accepted_total": len(accepted)}


def _goal(question: str, gold: dict) -> str:
    return (
        "# Task\n"
        "\n"
        "Answer this trivia question.\n"
        "\n"
        f"{question}\n"
        "\n"
        "## How to answer\n"
        "\n"
        "Write the answer -- the name, title or phrase alone -- to "
        f"`{base.ANSWER_FILE}` in your working directory, or submit it with\n"
        "\n"
        f"    python3 {base.CLI_PATH} submit \"<the answer>\"\n"
        "\n"
        "## How it is scored\n"
        "\n"
        "- Your answer is compared with every known alias of the answer, after both sides\n"
        "  are lower-cased, punctuation is replaced with spaces, articles removed, and\n"
        "  white-space normalised (TriviaQA's own `normalize_answer`).\n"
        "- Matching **any one** alias scores 1; this is the benchmark's\n"
        "  `metric_max_over_ground_truths`, not a comparison with a single string.\n"
        "- Nothing else you print is read.\n"
    )


def _downloads() -> list[dict]:
    return [
        {
            "kind": "hf-dataset",
            "name": "mandarjoshi/trivia_qa (rc.nocontext)",
            "repo": "mandarjoshi/trivia_qa",
            "version": REVISION,
            "size": "~64 MB (rc.nocontext: 55 MB train + 7.3 MB validation + 1.2 MB test)",
            "files": [VALIDATION, TRAIN, TEST],
            "sha256": {
                VALIDATION: "48a5005c0eb4f8a4ae5b9868644297fd5bf1e694aeb3dc9c8ab958cac0d5b201",
                TRAIN: "455223e4a3ba6977611914d96b1b812fe19a6cd4ea3aef4df381f093b297670c",
                TEST: "35718c1b37e786c823a5b1e810bf97cd085a1536396143a153b4cb30ba41e4c5",
            },
            "dst": ".",
            "note": "Trap 1: the benchmark's `test` split has its gold withheld -- every "
                    "`answer['value']` is `<unk>` and the alias list is empty -- so the "
                    "scored split is `validation`, which is the choice ReWOO's own "
                    "`run_eval.py` never makes (it leaves the loader's `split='test'`). "
                    "Trap 2: only the `*.nocontext` configs are small; the full `rc` "
                    "config is ~8 GB because it carries the retrieved Wikipedia pages, "
                    "and `unfiltered` is larger still. The scored config has no documents "
                    "by construction, so a score here measures knowledge, not retrieval. "
                    "Trap 3: `train` is 138,384 rows and is ordered by question source, so "
                    "this adapter loads a 2,000-row even-stride sample of it as the study "
                    "side rather than a prefix -- see `STUDY_ROWS`.",
        },
        {
            "kind": "pip",
            "name": "pyarrow",
            "package": "pyarrow",
            "version": "23.0.0",
            "size": "~40 MB",
            "note": "Reads the parquet files the pinned revision ships. The check runs "
                    "inside the verification namespace and must not need the HuggingFace "
                    "`datasets` package.",
        },
    ]


_SPEC = base.QASpec(
    name="triviaqa",
    root=DATASET_ROOT,
    rows=_rows,
    question=_question,
    gold=_gold,
    goal=_goal,
    score=score,
    downloads=_downloads,
    split="validation",
    # The benchmark publishes both sides, so the platform takes TriviaQA's division. The
    # study side is a **declared sample** of the official `train` split -- 2,000 of 138,384
    # rows, by `_even_stride`; see `STUDY_ROWS` for the measured reason. The exam side is
    # the whole official `validation` split, untouched.
    study=("train",),
    source="mandarjoshi/trivia_qa@0f7faf33a3908546c6fd5b73a660e0f8ff173c2f "
           "(config `rc.nocontext`; split `validation` scored whole, `train` sampled to "
           "2,000 rows by even stride)",
)

_BUILT = _SPEC.build()

DEFAULT_ENV = _BUILT["DEFAULT_ENV"]
ENV = _BUILT["ENV"]
SPLIT = _BUILT["SPLIT"]

#: The benchmark's own evaluation set, within this adapter's pool: the whole official
#: `rc.nocontext`/`validation` split, which is the split TriviaQA numbers are computed on
#: (the `test` split withholds its gold). A list and not `None`, because the pool also holds
#: study material -- 2,000 rows of the official `train` split, sampled by `_even_stride`.
#:
#: Read from `QASpec.division()`, the one place that decides what this pool's division is.
#: So `--split declared` and `--split official` are the same division here: study the
#: sampled train rows, report on all 17,944 validation rows. `--split default` is the
#: platform's hash third of the same 19,944 tasks and is a different experiment
#: (`split_sha` is what keeps the two apart on a curve).
#:
#: **What `--side train` does not give you:** the other 136,384 training rows. They are on
#: disk and unread by design; `STUDY_ROWS` carries the measurement, and a run that wants
#: them can say so with `--split <file.json>`.
OFFICIAL_EVAL = _BUILT["OFFICIAL_EVAL"]

#: Which rule produced `SPLIT`: `"declared"` here -- the benchmark publishes both sides.
#: See `data/scaffolds/qa/base.py::division`.
SPLIT_SOURCE = _BUILT["SPLIT_SOURCE"]


def __getattr__(name: str):
    """`SETUP`, `VERIFY`, `_RECORDS`, `_LOAD_ERROR`, built on first use.

    Measured: building them at import cost **18 s** for this dataset's 17,944 tasks, and
    every control run paid it again. See `data/scaffolds/qa/base.py::module_getattr`.
    """
    return base.module_getattr(_SPEC, name)


def load() -> tuple[list[dict], dict[str, str]]:
    return _SPEC.load()


def downloads() -> list[dict]:
    return _SPEC._downloads()


def provision_status() -> dict:
    return _SPEC.provision_status()


__all__ = ["KIND", "DATASET_ROOT", "REVISION", "load", "downloads", "score",
           "normalize_answer", "get_ground_truths", "exact_match_score",
           "metric_max_over_ground_truths", "SPLIT", "OFFICIAL_EVAL", "SPLIT_SOURCE",
           "STUDY_ROWS"]
