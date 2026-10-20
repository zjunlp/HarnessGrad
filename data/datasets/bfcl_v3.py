r"""BFCL-v3 as a dataset: tool selection, graded on the **function name**.

    python3 tools/datasets.py --fetch --dataset bfcl-v3
    python3 data/scaffolds/qa/control.py --dataset bfcl_v3 --oracle
    driver.py --dataset bfcl_v3 ...

Where the tasks come from
-------------------------
The Berkeley Function Calling Leaderboard, **v3**, from the official repository
`ShishirPatil/gorilla` at tag **`v1.3`** -- not from the HuggingFace dataset, and that is a
measured decision rather than a preference:

    the HF dataset's `BFCL_v3_simple.json` at its current revision is **answer-only**
    (`{"id", "ground_truth"}`), and `possible_answer/BFCL_v3_simple.json` is the same
    60,809 bytes. The question files with `question` + `function` exist only under
    `/resolve/main/`, which is a moving ref. The tag `v1.3` has both, 400 questions with
    their schemas and a separate answer file -- verified by parsing them.

Two subsets, 640 tasks:

| subset | n | what it tests | gold |
| --- | --- | --- | --- |
| `simple` | 400 | call the right function for a one-shot request | `possible_answer/` |
| `irrelevance` | 240 | **do not call anything** -- the request needs no tool | the empty set |

`irrelevance` needs no answer file at all: its gold is "nothing", which makes it the cheapest
per-item label in the dataset and the one that tests the decision rather than the arguments.

The metric, and what it is **not**
----------------------------------
`reward = 1` iff the set of function names the agent names equals the gold set. For
`irrelevance` the gold set is empty, so a harness that calls anything scores 0.

**This is not the official BFCL score, and the adapter says so in every record.** The official
metric compares the *full call*, arguments included, and BFCL's answer file expresses an
argument as a list of acceptable values:

    {"calculate_triangle_area": {"base": [10], "height": [5], "unit": ["units", ""]}}

That shape is free-form -- `unit` accepts two different strings, `z` accepts `""` or `0` -- so
there is no enumerable candidate set to pick from. Scoring names only is the part that **is**
decidable from the data, and it is also the part the leaderboard's own `simple`/`irrelevance`
subsets are named for. The evidence carries `metric: "function-name set equality (not the
official BFCL score)"` so no reader can mistake one for the other.

The trap JevLoop recorded and this adapter inherits
---------------------------------------------------
`parameters.type` is BFCL's own vocabulary, not JSON Schema: measured across the first 200
questions, the values include **`dict`**, **`float`** and **`tuple`**. The schemas are rendered
for the agent with those normalised (`dict`->`object`, `float`->`number`), because a JSON
Schema a model is asked to read should be a JSON Schema.

How the agent answers
---------------------
One function name per line in `answer.txt`, or `NONE` when no tool is needed. The shared QA
check (`data/scaffolds/qa/verify/check.py`) reads the file and calls this module's `score()`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from contextlib import ExitStack
from pathlib import Path

from data.scaffolds.qa import base

KIND = "dataset"

#: The oracle control submits the benchmark's own released reference; see
#: `data/dataset.schema.json` (`module.oracle_about`) for what the other value means.
ORACLE_SOURCE = "released"
# The shared verifier uses this marker to avoid echoing trusted BFCL gold in evidence.
REDACT_EVIDENCE = True


def NOOP_EXPECTED(record: dict) -> float:
    """Submitting nothing is correct exactly when no function is expected."""
    return 1.0 if not record["gold"].get("functions") else 0.0


#: The benchmark's own evaluation set, within this adapter's pool: **every task here belongs
#: to it**. The pool is the `simple` and `irrelevance` categories, both official BFCL evaluation categories. The categories this platform does not host are absent from the pool as well.
#:
#: `None` is the accurate value only because nothing in the pool is study material -- it does
#: not claim the pool is the whole of the benchmark's evaluation set (see `ds1000`, which is a
#: measured subset). `--split official --side eval` is the comparable run: the whole pool, with
#: no study material in it. `--split declared` keeps this adapter's own division.
OFFICIAL_EVAL = None

DATASET_ROOT = base.Path("/mnt/20t/xubuqiang/datasets/bfcl-v3")

#: The official repository tarball at a **tag**, with the digest of the download.
REPO_TAG = "v1.3"
TARBALL_URL = "https://codeload.github.com/ShishirPatil/gorilla/tar.gz/refs/tags/v1.3"
TARBALL_SHA256 = "57854157e82aed5723a1458d2a4ac426e3256959ded4a20ddc4d627c22edb7fe"
TARBALL_NAME = "gorilla-v1.3.tar.gz"

#: Where the tag puts the data, inside the extracted tarball.
DATA_PREFIX = "gorilla-1.3/berkeley-function-call-leaderboard/bfcl_eval/data"
SIMPLE = f"{DATA_PREFIX}/BFCL_v3_simple.json"
IRRELEVANCE = f"{DATA_PREFIX}/BFCL_v3_irrelevance.json"
SIMPLE_ANSWERS = f"{DATA_PREFIX}/possible_answer/BFCL_v3_simple.json"

SUBSETS = {"simple": SIMPLE, "irrelevance": IRRELEVANCE}
N_SIMPLE = 400
N_IRRELEVANCE = 240
SELECTED_INDEX = "selected-index.v1.json"
# Local read limits, not measured release maxima or custody evidence.
MAX_SELECTED_ROW_BYTES = 1 << 20
_MAX_SELECTED_POSITION = min(sys.maxsize, (1 << 63) - 1)
DEFAULT_ENV = {"kind": "files"}
_TASK_ID_RE = re.compile(r"(simple|irrelevance)/(0|[1-9][0-9]*)")

#: BFCL's type vocabulary -> JSON Schema's. Only the entries that are actually wrong are here;
#: everything else is passed through unchanged.
_TYPE_FIXES = {"dict": "object", "float": "number", "tuple": "array", "any": "object"}


def _read_jsonl(path: Path) -> list[dict]:
    """BFCL ships **JSONL**, not a JSON array -- the HF README says so explicitly."""
    if not path.is_file():
        raise FileNotFoundError(f"no BFCL file at {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _rows(subsets: set[str] | None = None) -> dict[str, list[dict]]:
    """Read only the requested BFCL categories and their needed answer rows."""
    wanted = set(SUBSETS) if subsets is None else set(subsets)
    unknown = wanted - set(SUBSETS)
    if unknown:
        raise ValueError(f"bfcl-v3: unknown subset(s): {sorted(unknown)}")
    tables: dict[str, list[dict]] = {}
    if "simple" in wanted:
        tables["simple"] = _read_jsonl(DATASET_ROOT / SIMPLE)
        tables["_answers"] = {
            r["id"]: r["ground_truth"]
            for r in _read_jsonl(DATASET_ROOT / SIMPLE_ANSWERS)
        }
    if "irrelevance" in wanted:
        tables["irrelevance"] = _read_jsonl(DATASET_ROOT / IRRELEVANCE)
    return tables


def normalize_schema(parameters: dict) -> dict:
    """BFCL's `parameters` as a JSON Schema, with its type vocabulary corrected.

    Measured: `dict`, `float`, `tuple` and `any` appear as `type` values in the v3 data. They
    are BFCL's own words; a model asked to read a JSON Schema should get one. The mapping is
    total (`any` -> `object`) rather than partial, so no value is left unrecognised.
    """
    out = dict(parameters or {})
    if "type" in out:
        out["type"] = _TYPE_FIXES.get(str(out["type"]), out["type"])
    properties = {}
    for name, spec in (out.get("properties") or {}).items():
        spec = dict(spec or {})
        if "type" in spec:
            spec["type"] = _TYPE_FIXES.get(str(spec["type"]), spec["type"])
        properties[name] = spec
    if properties:
        out["properties"] = properties
    return out


def bare_name(name: str) -> str:
    """A function name without BFCL's namespace prefix.

    Measured: one row in 400 (`simple_363`) has gold `find_closest` while its schema is
    `restaurant_search.find_closest` -- the answer file drops the namespace the question file
    carries. The comparison takes the last dotted segment on **both** sides, so that row grades
    the function the agent can actually see, and a nested name still compares exactly.
    Only valid identifiers are normalized; call syntax and prose stay invalid.
    """
    # parse_prediction retains call-shaped lines so they fail even on irrelevance.
    # Never let a dot in prose/arguments turn such a line into a valid tool name.
    name = str(name)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", name):
        return name
    return name.rsplit(".", 1)[-1]


def gold_names(ground_truth) -> list[str]:
    """The function names in a gold call list, in order.

    The answer file expresses each gold call as `{"<function>": {<arg>: [...]}}`, so the names
    are the keys. Duplicates matter for `parallel` subsets, which this dataset does not use --
    but they are kept rather than deduplicated so the comparison is over the call list.
    """
    names = []
    if isinstance(ground_truth, list):
        for call in ground_truth:
            if isinstance(call, dict):
                names.extend(str(k) for k in call)
    return names


def parse_prediction(text: str) -> list[str]:
    """Accept bare/dotted ASCII function identifiers, one per line.

    Whitespace and surrounding commas/semicolons are stripped. Blank lines
    and case-insensitive NONE/no tool/no function markers (optional space, underscore or
    hyphen) mean no call. Prose without an identifier followed by '(' is ignored, as are
    invalid identifiers. A line containing a call-shaped identifier is retained as-is:
    call syntax is rejected by name-only equality and still fails irrelevance. Arguments
    are neither parsed nor executed.
    Names keep their case, order and duplicates; no available-function allowlist is used.
    """
    identifier = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    names = []
    for line in str(text or "").splitlines():
        line = line.strip().strip(",;")
        if not line:
            continue
        if re.fullmatch(r"(?i)none|no[ _-]?tool|no[ _-]?function", line):
            continue
        if (re.fullmatch(identifier, line)
                or re.search(rf"(?<![\w.]){identifier}\s*\(", line)):
            names.append(line)
    return names


def score(prediction: str, gold: dict) -> dict:
    """The dataset's grade: set equality over function names.

    `simple` gold has one or more names; `irrelevance` gold is empty, so *any* named function
    is a miss and the only way to score is to name none.
    """
    want = [bare_name(n) for n in (gold.get("functions") or [])]
    got = [bare_name(n) for n in parse_prediction(prediction)]
    hit = sorted(got) == sorted(want)
    return {
        "reward": 1.0 if hit else 0.0,
        "correct": hit,
        "official": {
            "metric": "function-name set equality (not the official BFCL score)",
            "official_metric": ("BFCL compares the full call including arguments; the answer "
                                "file expresses each argument as a free-form list of "
                                "acceptable values, which no scorer can enumerate. Only the "
                                "names are decidable from the data."),
            "subset": gold.get("subset"),
            "category": gold.get("subset"),
            "expected_functions": "<redacted>",
            "predicted_functions": got,
            "no_tool_needed": not want,
            "evidence_redacted": True,
        },
        "why": "function-name set matches" if hit else "function-name set differs",
    }


def _render_functions(functions: list[dict]) -> str:
    """The available functions, as JSON Schemas the agent can read."""
    rendered = []
    for fn in functions[:64]:
        rendered.append({
            "name": fn.get("name"),
            "description": fn.get("description"),
            "parameters": normalize_schema(fn.get("parameters") or {}),
        })
    return json.dumps(rendered, indent=1, ensure_ascii=False)


def _goal(record: dict) -> str:
    """The instruction: the user request, the available functions, and the answer format.

    Both subsets get the same shape. For `irrelevance` the request is answerable without any
    function, and the goal says the answer format allows that (`NONE`) -- but it does not say
    whether any particular request needs a tool.
    """
    return (
        "# Task\n"
        "\n"
        "A user has sent a request. Decide which of the available functions answers it,\n"
        "and name them.\n"
        "\n"
        "## The request\n"
        "\n"
        f"{record['user_request']}\n"
        "\n"
        "## Available functions\n"
        "\n"
        "```json\n"
        f"{record['functions_json']}\n"
        "```\n"
        "\n"
        "## How to answer\n"
        "\n"
        f"Write the name of each function that answers the request -- one per line -- to\n"
        f"`{base.ANSWER_FILE}` in your working directory, or submit them with\n"
        "\n"
        f"    python3 {base.CLI_FILE} submit \"<function_name>\"\n"
        "\n"
        "If **no** function is needed, submit `NONE` (or write `NONE` in the file).\n"
        "\n"
        "## How it is scored\n"
        "\n"
        "- The set of function names you name must equal the set the request needs. Only the\n"
        "  **names** are graded -- not the arguments, and not the call syntax.\n"
        "- Naming a function for a request that needs none scores 0, and so does naming none\n"
        "  when one is needed.\n"
        "- Nothing else you print is read.\n"
    )


def _downloads() -> list[dict]:
    return [
        {
            "kind": "http",
            "name": "gorilla BFCL-v3 data (tag v1.3)",
            "version": REPO_TAG,
            "url": TARBALL_URL,
            "size": "~45 MB tarball; the three BFCL files read here are ~500 KB",
            "sha256": TARBALL_SHA256,
            "dst": "_src/" + TARBALL_NAME,
            "extract_to": ".",
            "note": "Trap 1: the **HuggingFace** dataset's `BFCL_v3_simple.json` is "
                    "answer-only at the current revision (`{id, ground_truth}`), and its "
                    "`possible_answer/` copy is byte-identical to it -- the question files "
                    "with `question`/`function` survive only under the moving `main` ref. The "
                    "tag `v1.3` is authoritative and immutable, so the pin is the tag plus the "
                    "download's sha256. Trap 2: BFCL files are **JSONL**, not JSON arrays (the "
                    "dataset's own README says not to use `load_datasets`). Trap 3: "
                    "`parameters.type` uses BFCL's vocabulary (`dict`, `float`, `tuple`, "
                    "`any`), not JSON Schema's; `normalize_schema()` corrects it.",
        },
        {
            "kind": "pip",
            "name": "pyarrow",
            "package": "pyarrow",
            "version": "23.0.0",
            "size": "~40 MB",
            "note": "Declared for consistency with the other QA datasets; BFCL is JSONL, so "
                    "the reader is `json`, not pyarrow.",
        },
    ]


# ---------------------------------------------------------------- records ---


def _selected_records(task_ids: list[str] | set[str]) -> list[dict]:
    """Validate selection and metadata before reading any question or answer bytes.

    The release index requires schema_version=1, repo_tag, tarball_sha256, and
    tasks keyed by canonical task ID. Each task has question_id and a question
    byte span (offset, length, sha256); simple tasks
    also have an answer span. Paths are fixed here, never supplied by the index.
    No scan/build fallback is permitted. This JSON is NOT authenticated: its
    release fields and row hashes are caller-authorable assertions, not approval.
    External index custody, release provisioning, and immutable source storage
    remain separate production gates. Digest checks do not secure confidentiality.
    """
    if task_ids is None:
        raise ValueError("bfcl-v3: selection must not be empty")
    ids = list(dict.fromkeys(task_ids))
    if not ids:
        raise ValueError("bfcl-v3: selection must not be empty")
    selections = {}
    for task_id in ids:
        match = _TASK_ID_RE.fullmatch(task_id) if isinstance(task_id, str) else None
        if match is None:
            raise ValueError(f"bfcl-v3: invalid task id {task_id!r}")
        subset, raw_index = match.groups()
        index = int(raw_index)
        if index >= (N_SIMPLE if subset == "simple" else N_IRRELEVANCE):
            raise ValueError(f"bfcl-v3: invalid task id {task_id!r}")
        selections[task_id] = (subset, index)

    index_path = DATASET_ROOT / SELECTED_INDEX
    try:
        manifest = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("bfcl-v3: selected index is unavailable or unreadable") from exc
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or manifest.get("repo_tag") != REPO_TAG
            or manifest.get("tarball_sha256") != TARBALL_SHA256
            or not isinstance(manifest.get("tasks"), dict)):
        raise ValueError("bfcl-v3: selected index has invalid release binding")

    entries = {}
    for task_id, (subset, _) in selections.items():
        entry = manifest["tasks"].get(task_id)
        if (not isinstance(entry, dict) or not isinstance(entry.get("question_id"), str)
                or not entry["question_id"]):
            raise ValueError(f"bfcl-v3: selected index is missing identity for {task_id}")
        for kind in ("question", "answer") if subset == "simple" else ("question",):
            span = entry.get(kind)
            if (not isinstance(span, dict)
                    or type(span.get("offset")) is not int or span["offset"] < 0
                    or type(span.get("length")) is not int or span["length"] <= 0
                    or span["length"] > MAX_SELECTED_ROW_BYTES
                    or span["offset"] > _MAX_SELECTED_POSITION - span["length"]
                    or not isinstance(span.get("sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", span["sha256"]) is None):
                raise ValueError(f"bfcl-v3: invalid selected {kind} span for {task_id}")
        entries[task_id] = entry

    def fingerprint(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    def read_span(handle, span: dict, question_id: str) -> dict:
        handle.seek(span["offset"])
        payload = handle.read(span["length"])
        if (len(payload) != span["length"]
                or hashlib.sha256(payload).hexdigest() != span["sha256"]):
            raise ValueError("bfcl-v3: selected row digest mismatch")
        row = json.loads(payload)
        if not isinstance(row, dict) or row.get("id") != question_id:
            raise ValueError("bfcl-v3: selected row identity mismatch")
        return row

    # Open each required source once. Bounds and reads use the same descriptor;
    # checking Path.stat() then reopening would race with path replacement.
    with ExitStack() as stack:
        sources = {}
        for task_id, (subset, _) in selections.items():
            paths = {"question": SUBSETS[subset]}
            if subset == "simple":
                paths["answer"] = SIMPLE_ANSWERS
            for kind, relative in paths.items():
                if relative not in sources:
                    handle = stack.enter_context((DATASET_ROOT / relative).open("rb", buffering=0))
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("bfcl-v3: selected source must be a regular file")
                    sources[relative] = (handle, info)
                _, info = sources[relative]
                span = entries[task_id][kind]
                # Subtraction keeps both the file-position and EOF checks bounded.
                if span["length"] > info.st_size or span["offset"] > info.st_size - span["length"]:
                    raise ValueError("bfcl-v3: selected span exceeds source bounds")

        # Preflight every selected span before reading any question or answer byte.
        records = []
        for task_id, (subset, index) in selections.items():
            entry = entries[task_id]
            row = read_span(sources[SUBSETS[subset]][0], entry["question"], entry["question_id"])
            truth = None
            if subset == "simple":
                answer = read_span(sources[SIMPLE_ANSWERS][0], entry["answer"], entry["question_id"])
                truth = answer.get("ground_truth")
            records.append(_record_from_row(subset, index, row, truth))
        for handle, before in sources.values():
            if fingerprint(os.fstat(handle.fileno())) != fingerprint(before):
                raise ValueError("bfcl-v3: selected source changed during loading")
        # Stat comparisons detect ordinary writes, not an authenticated snapshot.
        return records


def _records(task_ids: list[str] | set[str] | None = None) -> list[dict]:
    """Build records for exactly the selected task identities.

    A selected control must not read the other category: task IDs encode the category and file
    index, so the loader can select rows without first materializing the mixed pool.
    """
    if task_ids is not None:
        return _selected_records(task_ids)
    tables = _rows()
    answers = tables.get("_answers", {})
    records = []
    for subset, key in (("simple", "simple"), ("irrelevance", "irrelevance")):
        rows = tables[key]
        expected = N_SIMPLE if subset == "simple" else N_IRRELEVANCE
        if len(rows) != expected:
            raise ValueError(f"bfcl-v3: {subset} has {len(rows)} rows, expected {expected}")
        for index, row in enumerate(rows):
            records.append(_record_from_row(subset, index, row, answers.get(str(row["id"]))))
    return records


def _record_from_row(subset: str, index: int, row: dict, ground_truth) -> dict:
    # `question` is a list of **turn groups**, and each group is a list of messages:
    # measured, `question[0][0] == {"role": "user", "content": ...}`. A single-turn
    # subset has one group; the flattening below is what makes a future multi-turn
    # subset load without a second code path.
    request = ""
    for group in (row.get("question") or []):
        turns = group if isinstance(group, list) else [group]
        for turn in turns:
            if isinstance(turn, dict) and str(turn.get("role")) == "user":
                content = str(turn.get("content") or "").strip()
                if content:
                    request = content
                    break
        if request:
            break
    if not request:
        raise ValueError(
            f"bfcl-v3: {row.get('id')} has no user message (shape: "
            f"{type(row.get('question')).__name__} of "
            f"{type((row.get('question') or [None])[0]).__name__})")
    functions = list(row.get("function") or [])
    names = [str(f.get("name")) for f in functions]
    gold = gold_names(ground_truth) if subset == "simple" else []
    if subset == "simple" and not gold:
        raise ValueError(f"bfcl-v3: no gold for {row.get('id')}")
    # The gold is bare-named while the schema may be namespaced (measured on
    # `simple_363`); the check compares bare names, so both sides are normalised here.
    gold = [bare_name(n) for n in gold]
    return {
        "task_id": f"{subset}/{index}",
        "subset": subset,
        "question_id": str(row["id"]),
        "user_request": request,
        "functions_json": _render_functions(functions),
        "function_names": names,
        "gold": {"answer": "\n".join(gold) if gold else "NONE",
                 "functions": gold, "subset": subset},
    }


class _BFCLSpec(base.QASpec):
    """`QASpec` with its own `records()`, because this dataset has two subsets.

    The scaffold's `records()` reads one split with one question/gold pair per row; BFCL has
    two subsets, a tool set per question, and a gold that lives in a different file. Overriding
    the one method is smaller than teaching the scaffold about multi-file joins, and the rest
    of the scaffold (the split, the goal rendering, `SETUP`, `VERIFY`, the check) is unchanged.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows = lambda: {"all": []}

    def records(self) -> list[dict]:
        records = _records()
        if not records:
            raise ValueError("bfcl-v3: no records")
        return records


_SPEC = _BFCLSpec(
    name="bfcl_v3",
    root=DATASET_ROOT,
    rows=lambda: {"all": []},
    question=lambda row: "",
    gold=lambda row: {},
    goal=lambda question, gold: "",
    score=score,
    downloads=_downloads,
    split="all",
    source="ShishirPatil/gorilla@v1.3 berkeley-function-call-leaderboard/bfcl_eval/data",
)

# Records are deliberately populated only by an explicit full-pool request. Importing the scorer
# must remain cheap and must not read mixed train/eval data.
_RECORDS: list[dict] = []
_LOAD_ERROR: str | None = None


def _ensure(task_ids: list[str] | set[str] | None = None) -> list[dict]:
    global _RECORDS, _LOAD_ERROR
    if task_ids is not None:
        return _records(task_ids)
    if _RECORDS:
        return _RECORDS
    try:
        _RECORDS = _SPEC.records()
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        raise
    return _RECORDS


def load_selected(task_ids: list[str] | set[str]) -> tuple[list[dict], dict[str, str]]:
    """Load only the explicitly selected BFCL task identities."""
    records = _selected_records(task_ids)
    return ([task_for(r) for r in records],
            {r["task_id"]: r["gold"]["answer"] for r in records})


def control_task(task_id: str | None) -> dict:
    return _selected_records(["simple/0" if task_id is None else task_id])[0]


def task_for(record: dict) -> dict:
    """Render an already loaded selected record without a second payload read."""
    return {"task_id": record["task_id"], "goal": _goal(record), "inputs": []}


def setup_for(record: dict) -> dict:
    return _SPEC.setup(record)


def verify_for(record: dict) -> dict:
    verifier = _SPEC.verifier(record)
    verifier["argv"].append("--redact-gold")
    return verifier


def load() -> tuple[list[dict], dict[str, str]]:
    if _LOAD_ERROR is not None:
        raise ValueError(
            f"bfcl-v3: cannot read the data ({_LOAD_ERROR}). Provision it with "
            f"`python3 tools/datasets.py --fetch --dataset bfcl-v3`.")
    records = _ensure()
    tasks = [{"task_id": r["task_id"], "goal": _goal(r), "inputs": []} for r in records]
    return tasks, {r["task_id"]: r["gold"]["answer"] for r in records}


def _is_eval(task_id: str) -> bool:
    import hashlib
    return hashlib.sha256(task_id.encode()).digest()[0] % 3 == 0


def __getattr__(name: str):
    if name == "SETUP":
        return {r["task_id"]: _SPEC.setup(r) for r in _ensure()}
    if name == "VERIFY":
        return {r["task_id"]: verify_for(r) for r in _ensure()}
    if name == "ENV":
        return {r["task_id"]: {"kind": "files"} for r in _ensure()}
    if name == "SPLIT":
        import os
        ids = [r["task_id"] for r in _ensure()]
        if (os.environ.get("HG_BFCL_V3_SPLIT") or "hash").lower() == "none":
            return {"train": [], "eval": ids}
        return {"train": [t for t in ids if not _is_eval(t)],
                "eval": [t for t in ids if _is_eval(t)]}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def downloads() -> list[dict]:
    return _downloads()


def provision_status() -> dict:
    tarball = DATASET_ROOT / "_src" / TARBALL_NAME
    # `SELECTED_INDEX` is part of "am I provisioned", not an optional extra: the
    # selected-only route and the control refuse without it, and a status that said
    # present while its own control raises "selected index is unavailable" is the
    # wrong answer to the question the lint asks.
    present = all((DATASET_ROOT / p).is_file()
                  for p in (SIMPLE, IRRELEVANCE, SIMPLE_ANSWERS, SELECTED_INDEX))
    return {
        "root": str(DATASET_ROOT),
        "present": present and tarball.is_file(),
        "split_dir": str(DATASET_ROOT / DATA_PREFIX),
        "tarball": str(tarball) if tarball.is_file() else None,
        "tasks": len(_RECORDS),
    }


__all__ = ["KIND", "DATASET_ROOT", "NOOP_EXPECTED", "load", "downloads", "provision_status", "score",
           "normalize_schema", "gold_names", "parse_prediction", "bare_name",
           "load_selected", "control_task", "task_for", "setup_for", "verify_for", "DEFAULT_ENV"]
