#!/usr/bin/env python3
"""One readiness table for every published dataset, generated from the adapters.

Why this exists
---------------
A reader deciding whether a dataset is usable had to open four files per dataset and hold
39 answers in their head: the adapter for the metric, `ORACLE_SOURCE` and `downloads()`,
`tests/quality/test_dataset_controls.py:ROWS` for the control arm and the test that pins
the metric, and `DOWNLOADS.md` for the pinned release. Every one of those facts is
*declared* by the adapter or by `ROWS`, so this tool copies none of them: it renders them,
and `--check` fails the moment the rendered document stops matching the declarations. The
fix is always `--write`, never an edit.

No cell is a fact about this machine
------------------------------------
The document is committed and read on machines that are not the one that rendered it, so
every cell describes the *repository*: the metric, the deviation, the oracle, and the
command that fetches the pinned bytes. "Are those bytes already on this disk?" is a fact
about the host, so it is not a column -- it is each adapter's `provision_status()`, and
only `--host` prints it. `--write` refuses `--host`, and `--check` renders without it,
which is what makes `--check` host-independent: on a machine with no dataset bytes at all
it fails only when the committed file really drifts from the adapters.

Usage
-----
    python3 tools/datasets_doc.py                  # print the document
    python3 tools/datasets_doc.py --write          # rewrite DATASETS.md
    python3 tools/datasets_doc.py --check          # fail if DATASETS.md is stale
    python3 tools/datasets_doc.py --host           # ... plus what is on THIS host
    python3 tools/datasets_doc.py --path <file>    # the same, against another copy

`--path` compares whatever copy it is pointed at; `tests/quality/test_datasets_doc.py`
writes a fresh one into `tmp_path` instead of mutating the checked-in file.

Catalog metadata before runtime installation
--------------------------------------------
Source declarations keep every dataset in the table even before its optional runtime
or task pool is installed. When importing an adapter fails, reuse the download catalog's
trusted-source reader for provisioning and literal declarations for metadata. This view
cannot load tasks or report a locally provisioned runtime. If the source metadata also
cannot be read, the row carries the exception and an `unknown` provisioning command.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import data.scaffolds.registry as registry  # noqa: E402
from tools.check_dataset_blockers import published_declarations  # noqa: E402

DOC = ROOT / "DATASETS.md"
CONTROLS = ROOT / "tests" / "quality" / "test_dataset_controls.py"

#: Every dataset whose **scored reward is not the metric the benchmark publishes**, verified
#: by reading the adapter -- not by pattern-matching its prose. Each line says what the
#: adapter reports and what the benchmark reports, in that order.
#:
#: This mapping is the whole story: a reader who sees `no` in the `known deviation` column
#: is being told that a person read the adapter and found no deviation, and a row that is
#: not in here or in `CLEARED` and whose prose hints at one says `undeclared deviation
#: (review)` instead. Adding an entry is a claim about a benchmark, so it belongs beside
#: the reading that supports it, which is why it is a literal dict and not a grep at
#: render time.
DEVIATIONS: dict[str, str] = {
    'locomo': 'per-conversation Harbor rewards are preserved; the platform curve is conversation-macro mean, while report.py computes the question-weighted Harbor aggregate',
    'nq': 'upstream short-answer-text EM/F1 on the any-short-answer-or-yes-no subset; not the full official NQ long/short-answer leaderboard metric',
    'supergpqa': 'upstream final_letter_v1 sample-mean accuracy; not official discipline-macro/freeform evaluation',
    'browsecomp': 'upstream corrects simple-evals regex group(0) to group(1); judge model profile is explicit and not universally comparable',
    'deepsearchqa': 'native set-answer F1 prompt/counts; configured OpenAI-compatible judge differs from the official Gemini route unless explicitly reproduced',
    'livemathbench': 'curve is single-sample-label accuracy; native hypergeometric G-Pass/mG-Pass is a separate complete-trial report',
    # `data/scaffolds/native_qa/scoring.py` normalizes with NFKC + casefold before
    # EM/F1, which is upstream's normalizer rather than the SearchQA release's own exact
    # match. The review record calls this out ("text_em_f1 with upstream NFKC/casefold
    # normalization") and the cell said `no`; the reading is a deviation from the
    # benchmark's string comparison, not a proxy metric under its name.
    'searchqa': 'EM/F1 after upstream NFKC + casefold normalization, not the SearchQA release exact string match; tokenization is the platform scorer, not the paper implementation',

    # `score()` returns function-name set equality while the docstring says, in the
    # adapter's own words, "This is not the official BFCL score": BFCL compares the full
    # call (arguments included), and the answer file expresses each argument as a
    # free-form list of acceptable values that no scorer can enumerate.
    "bfcl_v3": ("reward is function-name set equality; the official BFCL score compares "
                "the full call, arguments included, which the released answer file does "
                "not make decidable"),
    # The adapter's title is "FEVER exported-row conjunctive strict@5, not classification
    # Macro-F1"; the paper metric is computed from the per-label counts as a diagnostic.
    "fever": ("reward is exported-row conjunctive strict@5 (label plus one evidence group "
              "in the first five pairs); FEVER's paper metric, classification Macro-F1 "
              "over LABELS, is reported as diagnostic counts only"),
    # Docstring: "This is an adapter metric, distinct from upstream exact match."
    "gsm8k": ("decimal-aware extraction with a 1e-6 absolute tolerance, an adapter metric "
              "distinct from upstream exact match"),
    # `score_prediction()` rewards answer F1 and records joint F1 as diagnostic only; the
    # docstring opens "reporting answer F1, not official joint F1".
    "hotpotqa": ("reward is answer F1/EM; the official HotpotQA metric is joint F1 over "
                 "the answer and its supporting facts, which this question-only "
                 "configuration records as diagnostics"),
    # `_score()`'s default reward is `answer_f1 * support_f1` and marks the policy "not an
    # official metric"; MuSiQue's own `evaluate_v1.0.py` reports answer and support
    # separately and defines no product.
    "musique": ("default reward is the platform policy answer_f1 * support_f1; MuSiQue's "
                "own evaluator reports answer and support separately and defines no "
                "product, so the adapter marks the policy 'not an official metric'"),
    # `score()` rewards alias-max EM for **one row** (`data/datasets/triviaqa.py:164`, over
    # that row's aliases, `:271`), and a run's mean is over rows. TriviaQA does not score
    # rows: the pinned upstream evaluator
    # (`tests/fixtures/triviaqa_upstream/evaluation/triviaqa_evaluation.py:102-133`, sha256
    # `51785a3b...`) divides by the keys of `get_key_to_ground_truth`
    # (`utils/dataset_utils.py:8-13`) -- `QuestionId` for the Wikipedia domain,
    # `QuestionId--Filename` for the Web domain -- and the paper states the same rule (Table
    # 6: "The Wikipedia domain is evaluated over questions while the web domain is evaluated
    # over documents"). Measured on the pinned `rc.nocontext` parquet: the config is the
    # union of the two domain files, row for row with the paper's per-domain counts
    # (train 138,384 = 61,888 + 76,496; validation 17,944 = 7,993 + 9,951; test
    # 17,210 = 7,701 + 9,509), so the 17,944 validation rows are 9,960 question ids and
    # 7,982 of the rows are byte-identical duplicates. Upstream also reports token F1.
    "triviaqa": ("reward is per-row alias-max exact match over the config's 17,944 rows; "
                 "TriviaQA scores its Wikipedia domain over 7,993 dev questions and its "
                 "Web domain over 68,621 question-document pairs, and this config unions "
                 "both (9,960 question ids, 7,982 duplicate rows); upstream also reports F1"),
    # `verify/check.py:259` is upstream `calculate_reward` (`tau_bench/envs/base.py:124-164`:
    # database-hash equality, every `outputs` string, gated on `done`), but `load()` drops 32
    # tasks that cannot discriminate (`data/datasets/tau_bench.py:210-220`), 26 of them from
    # the official test files, so the exam is 139 of the 165 official test tasks. There is
    # no user simulator: the user id is in the goal and the request is the instruction.
    "tau_bench": ("reward is upstream `calculate_reward` (database-hash equality, the "
                  "task's `outputs`, gated on `done`) on 139 of the 165 official test "
                  "tasks; 26 tasks whose ground truth leaves the database unchanged and "
                  "which require no output are excluded as non-discriminating, and the "
                  "user side is the task instruction, not an LLM user simulator"),
}

#: Every dataset whose adapter prose warns that a number is not official and whose **reward
#: a person read and found to be the official metric**, on the rows the row claims. An entry
#: is the reading, not a verdict: it is rendered under "Review flags a reading cleared", so
#: the flag leaves the queue without the reading leaving the document, and a later reader
#: can disagree with it. Deleting an entry puts the row back in the queue: an entry here is
#: the only way a warned-about row is allowed to say `no`.
#:
#: `no` and `yes:` are both claims a person made; the difference is what they found.
CLEARED: dict[str, str] = {
    # The flagged sentence (`data/datasets/appworld.py:274`) is about `HG_APPWORLD_POOL`
    # rejecting a split name, not about the metric: `verify/check.py` runs the released
    # `ground_truth/evaluation.py` through `AppWorld.evaluate()` and rewards
    # `tracker.success` (`check.py:58-73`), and upstream's `TestTracker.success` is
    # `pass_count == num_tests` -- the paper's TGC unit ("the percentage tasks for which the
    # agent passed all evaluation tests", arXiv 2407.18901 §3.2). Every official test task is
    # scored: 585 = 168 `test_normal` + 417 `test_challenge`, no exclusion. The paper's
    # second metric, SGC, is not reported.
    "appworld": ("reward is the released evaluator's own per-task verdict "
                 "(`AppWorld.evaluate()` -> `TestTracker.success`, every test passed) on "
                 "all 585 official test tasks -- the TGC unit of arXiv 2407.18901 -- and "
                 "`HG_APPWORLD_POOL` only chooses which official split is scored. The "
                 "paper's second metric, SGC over 3-task scenarios, is not reported"),
}

#: A phrase that means "the adapter is telling a reader its number is not the benchmark's".
#: It is deliberately prose-level: the point is to catch a sentence a person wrote and a
#: mapping nobody updated, and to make that visible instead of reading as `no`.
DEVIATION_HINT = re.compile(
    r"not\s+(?:the\s+|an\s+)?(?:benchmark'?s?\s+)?official", re.IGNORECASE)

#: What an `official metric` cell says when the adapter declares no machine-readable metric.
UNDECLARED = "\u2014 undeclared"

#: `unknown` / `—` are not zeros: they are "this was not declared" and "this was not asked".
#: A measured zero is a number in a run receipt, never a cell in this table.
DASH = "\u2014"

#: A refusal's first line is a paragraph, not a sentence; the cell keeps it readable and the
#: adapter keeps the whole text (`REFUSED`). Nothing is dropped that the cell does not say
#: it dropped.
REFUSAL_LIMIT = 150


@dataclass(frozen=True)
class Row:
    """One dataset's line in the document.

    Every rendered cell is a non-empty string: `DASH` and `UNDECLARED` are spellings, not
    absences, so a blank cell can never be mistaken for a measured zero.
    """

    dataset: str
    kind: str
    metric: str
    deviation: str
    oracle: str
    provision: str
    control: str
    metric_tests: str
    refused: str
    #: `will not import: ImportError: ...`, when the adapter could not be imported at all.
    import_error: str = ""
    #: `(location, source line)` when the adapter's prose hints at a deviation and
    #: `DEVIATIONS` records none -- the review queue's line.
    review_hint: tuple[str, str] | None = None
    #: The `CLEARED` reading when the adapter's prose hints at a deviation and a person
    #: found the reward *is* the official metric. The cell says `no`; this says why.
    cleared_review: str = ""


# --------------------------------------------------------------- the seams the test uses ---
# The functions below are the only ways this module touches the adapters. The test for an
# unimportable adapter monkeypatches `_published`/`_import_adapter`, so it exercises the
# real renderer without needing a broken file in `data/datasets/`. `_provisioned` is the
# only reader of `provision_status()` and is reached only by `host_status()`/`--host`, so
# the host-independence test can monkeypatch those hooks and watch `--check` not care.


def _published() -> tuple[str, ...]:
    """Every dataset a reader can choose, in name order."""
    return tuple(published_declarations(ROOT))


def _import_adapter(name: str):
    """Metadata remains readable before optional runtimes or task pools are installed."""
    provider = registry.provider(name)
    if provider is not None:
        view = _source_adapter(name)
        # Only the explicit --host report calls this hook. Static documentation
        # must remain independent of both data and prepared runtime availability.
        view.provision_status = provider.check
        return view
    try:
        return importlib.import_module(registry.module_name(name))
    except Exception:
        return _source_adapter(name)


def _source_adapter(name: str):
    """Read owned metadata without importing a provider's loading dependencies."""
    from tools.datasets import _source_downloads

    path = ROOT / "data/datasets" / f"{name}.py"
    constants = _module_constants(ast.parse(path.read_text(encoding="utf-8")))
    downloads = _source_downloads(path, resolve_root=False)
    if constants.get("KIND") != "dataset" or not callable(downloads):
        raise ValueError(f"{name}: no readable dataset metadata")
    return SimpleNamespace(__file__=str(path), downloads=downloads, **constants)


# ------------------------------------------------------------------------ declarations ---


def _source_text(module) -> str | None:
    """The adapter's own source, for the metric scan. `None` when it cannot be read."""
    path = getattr(module, "__file__", None)
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "..."` string constants, by name.

    They are how an adapter spells a metric it uses in more than one place
    (`METRIC`, `REWARD_METRIC`, `STRICT_METRIC`), so a `metric` entry that names one can be
    resolved to the string instead of being reported as the identifier.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        pairs = []
        if isinstance(node, ast.Assign):
            pairs = [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = [(node.target, node.value)]
        for target, value in pairs:
            if not isinstance(target, ast.Name):
                continue
            try:
                resolved = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                continue
            if isinstance(resolved, str) and resolved.strip():
                out[target.id] = resolved
    return out


def _key_is(node, name: str) -> bool:
    """Is this dict key exactly the string `name`? (`**spread` has no key, skipped.)

    Exact, not case-insensitive: `data/datasets/strongreject.py` carries a
    `{"JUDGE_MODEL": "model", "METRIC": "metric", ...}` lookup table, and a case-folding
    match would read that table's *value* -- the literal string `metric` -- as the metric.
    Every adapter spells these keys lowercase, and a future `Metric` key that this misses
    reads as `undeclared` rather than as something invented.
    """
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value == name)


def _official_dicts(tree: ast.Module) -> set[int]:
    """Ids of the dict literals a reader would call the adapter's `official` block.

    Two spellings, both used in `data/datasets/`: `{"official": {"metric": ...}}` inside a
    returned record, and `official = {"metric": ...}` assigned to a name. A metric named in
    one of these is the metric the adapter reports; a `metric` key anywhere else (a
    diagnostic, a trial protocol) is a fallback, not the answer.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if _key_is(key, "official") and isinstance(value, ast.Dict):
                    found.add(id(value))
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (isinstance(target, ast.Name) and target.id.lower() == "official"
                    and isinstance(node.value, ast.Dict)):
                found.add(id(node.value))
    return found


def _resolve_metric_value(node, module, constants: dict[str, str]) -> str | None:
    """The string a `"metric": <value>` entry names, or None when it does not name one."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) and node.value.strip() else None
    if isinstance(node, ast.Name):
        # A module with `__getattr__` (`strongreject`) resolves names lazily, and resolving
        # one can raise; an unresolved value is skipped, never propagated.
        try:
            from_module = getattr(module, node.id, None)
        except Exception:  # noqa: BLE001 - a lazy attribute is not a metric declaration
            from_module = None
        if isinstance(from_module, str) and from_module.strip():
            return from_module
        return constants.get(node.id)
    # An f-string or a call is a *template* for a metric, not one: reporting it as the
    # metric would be a paraphrase dressed as a declaration.
    return None


def _metric_from_source(module, source: str) -> str:
    """The metric string the adapter declares in data, scanning its source in this order.

    1. the metric inside an `official` block -- what the adapter itself calls the metric;
    2. else a module-level `*METRIC*` string constant (`METRIC = 'kumo...v1'` is the value
       the verifier compares against, so it *is* the declaration);
    3. else any `"metric": "..."` entry in the module -- a scoring function's evidence or a
       trial protocol still names the metric, it is just not the adapter's headline;
    4. else nothing, and the cell says `undeclared` rather than guessing from prose.

    Values are de-duplicated in source order; an adapter that reports two metrics in two
    modes (`twowikimultihopqa`'s legacy and structured scorers) shows both, because picking
    one would be an opinion this tool is not entitled to.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    constants = _module_constants(tree)
    official = _official_dicts(tree)
    in_official: list[tuple[int, str]] = []
    anywhere: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not _key_is(key, "metric"):
                continue
            resolved = _resolve_metric_value(value, module, constants)
            if resolved is None:
                continue
            position = getattr(value, "lineno", 0)
            anywhere.append((position, resolved))
            if id(node) in official:
                in_official.append((position, resolved))
    for candidates in (in_official,
                       [(0, constants[name]) for name in sorted(constants)
                        if "METRIC" in name.upper() and constants[name].strip()],
                       anywhere):
        chosen = _dedupe(value for _, value in sorted(candidates, key=lambda pair: pair[0]))
        if chosen:
            return "; ".join(chosen)
    return ""


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _declared_metric(module, source: str | None) -> str:
    """`METRIC_NAME` first (the field an adapter can set without a source scan), else data."""
    declared = getattr(module, "METRIC_NAME", None)
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    if source is not None:
        metric = _metric_from_source(module, source)
        if metric:
            return metric
    return UNDECLARED


def _deviation_hint(module) -> tuple[str, str] | None:
    """`(file:line, source line)` of the first prose hint that a number is not official."""
    source = _source_text(module)
    path = getattr(module, "__file__", None)
    if source is None or not path:
        return None
    for number, line in enumerate(source.splitlines(), 1):
        if DEVIATION_HINT.search(line):
            try:
                location = str(Path(path).resolve().relative_to(ROOT))
            except ValueError:
                location = str(path)
            return f"`{location}:{number}`", line.strip()
    return None


def _provisioned(module) -> str:
    """`yes`/`no` from the dataset's own `provision_status()`, else `unknown`.

    A dataset **without** the hook is `unknown`, not `yes`: `tools/dataset_lint.py` assumes
    provisioned in that case only because `load()` is about to decide, and this table has
    no `load()` to ask. A hook that raises is `unknown (<Exception>)` -- still not a `no`,
    because a status this tool could not read is not evidence that the bytes are absent.

    Only `host_status()` (that is, `--host`) calls this. The committed document never does:
    the answer is a fact about the host, and a rendered cell must not be.
    """
    status = getattr(module, "provision_status", None)
    if not callable(status):
        return "unknown"
    try:
        return "yes" if bool(status().get("present")) else "no"
    except Exception as exc:  # noqa: BLE001 - an unreadable status is not a "no"
        return f"unknown ({type(exc).__name__})"


def _provision(name: str, module, *, refused: bool) -> str:
    """The command a reader runs for this dataset -- a fact about the repository.

    It comes from the adapter's own `downloads()`, the same declaration `DOWNLOADS.md` is
    rendered from, and it never reads the data root: a declaration says what *would* be
    fetched, not what is already there, so the cell is identical on a machine with every
    byte and on one with none. Four spellings:

    * `` `python3 tools/datasets.py --fetch --dataset <name>` `` -- the adapter declares
      `http` / `hf-dataset` / `hf-file` artifacts, so a fetch is the command;
    * `` `—` `` -- there is nothing to fetch: the bytes ship with the repository
      (`authored`), or the adapter declares only a `pip` dependency, which an install
      (pinned in `DOWNLOADS.md`), not a fetch, provides;
    * `` `—` no fetch lifts this refusal `` -- downloading a refused benchmark does not
      make it usable; its `refused` cell says what is actually missing;
    * `unknown (...)` -- the adapter has no `downloads()` hook, or it raised when read.
      Not `—`: nobody asked successfully, which is different from "nothing to fetch".
    """
    if refused:
        return f"{DASH} no fetch lifts this refusal; its `refused` cell says why"
    declared = getattr(module, "downloads", None)
    if not callable(declared):
        return "unknown (the adapter declares no `downloads()`)"
    try:
        entries = list(declared() or [])
    except Exception as exc:  # noqa: BLE001 - an unreadable declaration is not a "none"
        return f"unknown ({type(exc).__name__})"
    kinds = {entry.get("kind") for entry in entries if isinstance(entry, dict)}
    if not entries:
        return "unknown (the adapter declares nothing to fetch)"
    if "http" in kinds or "hf-dataset" in kinds or "hf-file" in kinds:
        command = f"`python3 tools/datasets.py --fetch --dataset {name}`"
        if registry.provider(name) is not None:
            command += f"; then `python tools/prepare_dataset.py --dataset {name}`"
        return command
    if "authored" in kinds:
        return f"{DASH} the bytes ship with the repository; nothing to fetch"
    if "pip" in kinds:
        return (f"{DASH} no bytes to fetch; `pip install` the package pinned in "
                f"`DOWNLOADS.md`")
    return f"{DASH} no fetchable artifact is declared"


def host_status(rows: list[Row]) -> dict[str, str]:
    """`dataset -> provisioned here` on *this* host, for `--host` only.

    `build_rows()` and `render()` never call this, which is the property the tests pin: a
    committed render must not be able to depend on which bytes this machine has. A dataset
    whose adapter would not import is `unknown`, not `no` -- nobody asked it successfully.
    """
    status: dict[str, str] = {}
    for row in rows:
        if row.import_error:
            status[row.dataset] = "unknown"
            continue
        try:
            module = _import_adapter(row.dataset)
        except Exception as exc:  # noqa: BLE001 - a reportable value, not a crash
            status[row.dataset] = f"unknown ({type(exc).__name__})"
            continue
        status[row.dataset] = _provisioned(module)
    return status


def _refusal(module) -> str:
    """The first line of the dataset's own refusal, bounded so the table stays a table."""
    refused = getattr(module, "REFUSED", None)
    if not isinstance(refused, str) or not refused.strip():
        return DASH
    first = refused.strip().splitlines()[0].strip()
    if len(first) <= REFUSAL_LIMIT:
        return _cell(first)
    cut = first[:REFUSAL_LIMIT].rsplit(" ", 1)[0]
    return _cell(f"{cut}\u2026")


def _controls(path: Path = CONTROLS) -> dict[str, dict]:
    """`ROWS` from the control test, read as data.

    Read with `ast`, not imported: the tool must not execute a test module (and must not
    need pytest) to render the document, and `ROWS` is a literal, so there is nothing to
    execute. A future row that stops being a literal fails loudly here rather than being
    reported as `—`, which would read as "this dataset has no control".
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        if target == "ROWS" and value is not None:
            try:
                rows = ast.literal_eval(value)
            except (ValueError, SyntaxError) as exc:
                raise ValueError(
                    f"{path.name}: ROWS is no longer a literal table ({exc}); "
                    f"tools/datasets_doc.py reads it as data and cannot render the "
                    f"`control arm` and `metric test(s)` columns without it") from exc
            if not isinstance(rows, dict):
                raise ValueError(f"{path.name}: ROWS must be a dict of rows")
            return rows
    raise ValueError(f"{path.name}: no `ROWS` declaration found; the document's control "
                     f"columns come from it")


def _control_cells(row: dict | None) -> tuple[str, str]:
    """`(control arm, metric test(s))` for one `ROWS` entry, or `(—, —)` when absent."""
    if row is None:
        return DASH, DASH
    if row.get("control"):
        arm = "dataset"
    elif row.get("exempt"):
        arm = "exempt"
    else:
        # A refusal has no control by design, and `refused` says so; calling that
        # "exempt" would read as a debt when nothing is owed.
        arm = DASH
    tests = ", ".join(str(name) for name in (row.get("metric_tests") or [])) or DASH
    return arm, tests


# ---------------------------------------------------------------------------- the rows ---


def row_for(name: str, controls: dict[str, dict]) -> Row:
    """One row, from the adapter and `ROWS`. Never raises for a dataset's own failure."""
    arm, tests = _control_cells(controls.get(name))
    try:
        module = _import_adapter(name)
    except Exception as exc:  # noqa: BLE001 - one broken adapter is a row, not a crash
        return Row(dataset=name, kind=f"unimportable ({type(exc).__name__})",
                   metric=UNDECLARED, deviation=DASH, oracle=DASH, provision="unknown",
                   control=arm, metric_tests=tests, refused=DASH,
                   import_error=f"will not import: {type(exc).__name__}: {exc}")
    source = _source_text(module)
    refused = _refusal(module)
    deviation = DASH
    hint = None
    cleared = ""
    if refused == DASH:
        recorded = DEVIATIONS.get(name)
        if recorded:
            deviation = f"yes: {recorded}"
        else:
            hint = _deviation_hint(module)
            if hint is None:
                deviation = "no"
            elif name in CLEARED:
                # The prose warns and a person read the code: the reading is the reason
                # the cell says `no`, so it is a row fact and not a note-only aside.
                cleared = CLEARED[name]
                hint = None
                deviation = "no"
            else:
                deviation = "undeclared deviation (review)"
    oracle = getattr(module, "ORACLE_SOURCE", None)
    return Row(
        dataset=name,
        kind=_cell(getattr(module, "KIND", DASH)),
        metric=_cell(_declared_metric(module, source)),
        deviation=_cell(deviation),
        oracle=_cell(oracle if isinstance(oracle, str) and oracle else DASH),
        provision=_cell(_provision(name, module, refused=refused != DASH)),
        control=arm,
        metric_tests=tests,
        refused=refused,
        review_hint=hint,
        cleared_review=_cell(cleared),
    )


def build_rows() -> list[Row]:
    """Every published dataset's row, sorted by dataset name."""
    controls = _controls()
    return [row_for(name, controls) for name in sorted(_published())]


def runtime_support_rows() -> dict[str, dict]:
    """Read reviewed role boundaries without loading datasets or probing a host."""
    inventory = json.loads((ROOT / 'data/runtime-support.json').read_text())
    if inventory.get('schema') != 'harnessgrad.runtime-support-inventory.v1':
        raise ValueError('unsupported runtime support inventory')
    result = {}
    fields = {'datasets', 'loading_and_preparation', 'trusted_execution', 'verification_scope'}
    for group in inventory['groups']:
        if (set(group) != fields or not isinstance(group['datasets'], list)
                or any(not isinstance(group[key], str) or not group[key]
                       for key in fields - {'datasets'})):
            raise ValueError('invalid runtime support group')
        for name in group['datasets']:
            if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', name) or name in result:
                raise ValueError('invalid or duplicate runtime support dataset')
            result[name] = group
    return result


def _runtime_section(rows: list[Row]) -> list[str]:
    inventory = runtime_support_rows()
    lines = ['', '## Dependency boundaries and validation scope', '',
        'This maintained source review separates loading/preparation from trusted execution. '
        'It is generated from `data/runtime-support.json`, not from imports succeeding or '
        'runtime manifests merely existing. A synthetic or mock control covers only its named '
        'path; it does not prove every task, external service, wheel resource or host platform '
        'is supported. Unknown and pending work remain explicit. Use the selected component\'s '
        'preparation/check command to inspect your own installation.', '',
        '| dataset | Loading and preparation | Trusted execution | Verification scope and remaining work |',
        '| --- | --- | --- | --- |']
    for row in rows:
        entry = inventory.get(row.dataset, {})
        cells = [_cell(entry.get(key, 'Not yet reviewed.')) for key in
                 ('loading_and_preparation', 'trusted_execution', 'verification_scope')]
        lines.append(f'| [{row.dataset}](data/datasets/{row.dataset}.py) | ' + ' | '.join(cells) + ' |')
    return lines + ['']


# ------------------------------------------------------------------------ the document ---


def _cell(text: str) -> str:
    """One table cell: no pipes, no line breaks, no trailing space."""
    return " ".join(str(text).replace("|", "\\|").split())


def _code_span(text: str) -> str:
    """`` `text` `` with a fence long enough for any backticks already inside it.

    The review queue quotes adapter source lines verbatim, and those lines contain
    prologues, f-strings and backticked words -- a fixed one-backtick span would end early
    and turn the rest of the line into prose.
    """
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _glance(rows: list[Row]) -> str:
    """The one-sentence summary: the reason a reader opens this file at all.

    Host-independent by construction -- every count below is a declaration. The count of
    datasets whose bytes are present is what `--host` prints instead, because it is a fact
    about one machine.
    """
    refused = sum(1 for row in rows if row.refused != DASH)
    deviations = sum(1 for row in rows if row.deviation.startswith("yes:"))
    cleared = sum(1 for row in rows if row.cleared_review)
    review = sum(1 for row in rows if row.review_hint)
    undeclared = sum(1 for row in rows if row.metric == UNDECLARED)
    return (f"**At a glance.** {len(rows)} published datasets: {refused} refused by "
            f"design; {deviations} with a recorded metric deviation; {cleared} whose "
            f"adapter mentions one that a reading cleared; {review} whose adapter mentions "
            f"one that nobody has recorded; {undeclared} that declare no machine-readable "
            f"metric (`{UNDECLARED}`). Every row says how to get its bytes; `--host` "
            f"answers the separate question of what is already here.")


_HEADER = """\
<!-- GENERATED BY tools/datasets_doc.py --write. DO NOT EDIT BY HAND. -->

# Dataset readiness

Every published dataset has a readiness row: what its grade is, whether that grade is the
benchmark's own metric, where the oracle's reference came from, how to fetch its bytes,
and which control and which metric test prove the grade is reachable. It is rendered from
the adapters and from `tests/quality/test_dataset_controls.py:ROWS`, so `python3
tools/datasets_doc.py --check` fails as soon as the file stops matching them; the fix is
always `--write`, never an edit. No cell is a fact about the machine that rendered it:
what is already on a given disk is a separate question, answered by `--host`.

## How a row is computed

`tools/datasets_doc.py:build_rows()` builds every cell; nothing here is hand-written.

| column | comes from |
| --- | --- |
| `dataset` | `tools/check_dataset_blockers.py:published_declarations()` -- adapters declaring `KIND = "dataset"`, sorted by name, including those awaiting local data or optional dependencies |
| `kind` | the adapter's own `KIND` (the source inventory already filters out fixtures) |
| `official metric` | `METRIC_NAME` when the adapter declares it; else the first `metric` string found in the adapter's source -- an `official` block first, then a `METRIC*` constant, then any scoring function's `metric` entry (identifiers resolved against the module); else `— undeclared` |
| `known deviation` | the `DEVIATIONS` table in `tools/datasets_doc.py`, each line verified by reading the adapter; `no` means a person read it and found the reward *is* the official metric, either with no warning in the prose or after clearing one into `CLEARED`; `undeclared deviation (review)` means the adapter's prose hints at one and neither table has recorded the reading |
| `oracle source` | the adapter's `ORACLE_SOURCE`: `released` (the benchmark's own reference artifact) or `authored` (written here, validated by the released tests) |
| `provision` | the adapter's own `downloads()`, the same declaration `DOWNLOADS.md` is rendered from: `python3 tools/datasets.py --fetch --dataset <name>` when it declares `http` / `hf-dataset` / `hf-file` artifacts, `—` when there is nothing to fetch (bytes that ship with the repository, or a `pip`-only dependency installed separately), and `unknown (<Exception>)` when the declaration could not be read. It is a fact about the repository, not about this machine |
| `control arm` | `tests/quality/test_dataset_controls.py:ROWS`: `dataset` when the row names a control to run, `exempt` when it records why there is none, `—` when the dataset has no row |
| `metric test(s)` | `ROWS[...]["metric_tests"]` -- the files under `tests/quality/` that pin the official metric |
| `refused` | the adapter's `REFUSED`, first line (truncated at 150 characters; the whole sentence is the adapter's `REFUSED`): this benchmark is refused by design and `load()` raises it |

**`unknown` and `—` mean "not known here", never a measured zero.** `unknown` is a value
the adapter does not declare, or one this tool could not read; `—` is a question that does
not apply -- a refused dataset reports no number at all, so it has no control arm, no
deviation and nothing to fetch. A zero is a score, and a score only ever appears in a run
receipt.

## How to provision a dataset

The bytes live outside the tree and are never committed, under each dataset's own root:
`HG_DATA_<NAME>` when it is set, else `<HG_DATA_ROOT>/<dataset-directory>`.
There is no implicit storage directory. Set `HG_DATA_ROOT` before accessing external
data, or supply the dataset's explicit override; otherwise the command asks for a path.
`harnessgrad.paths.dataset_root()` is the common resolver for adapters and preparation.
Relative settings use the main checkout as their base. Worktrees share its `.env`.
Set variables in the shell or
`.env`; see [dataset storage](README.md#dataset-storage) for configuration precedence.
Directory aliases and shared directories are listed in `DOWNLOADS.md`.
Catalog declarations can be read from trusted source before optional runtimes or task
pools are installed; this metadata does not attest local execution. A dataset's own
`downloads()` is the authority on what it needs, and the `provision` cell is that command --
the same on every machine:

    python3 tools/datasets.py --fetch --dataset <name>

`--fetch` downloads the `http`, `hf-dataset` and `hf-file` entries into that root, creating it on
demand -- which is what makes a fresh clone work -- and reports a `pip` entry rather than
installing it, so install the package version pinned in `DOWNLOADS.md`. That file also
carries the pinned version, sha256 and exact files for `<name>`, and `python3
tools/datasets.py --write` renders it from the same functions -- read it before fetching,
because a version that is not pinned is not a benchmark. A refused dataset's cell is `—`:
downloading it does not make it usable, and its `refused` cell says what is actually
missing.

## Is it already on this machine?

That answer differs per host, so it is not a column in this file. Ask it explicitly:

    python3 tools/datasets_doc.py --host

`--host` appends a local-only table to stdout -- `yes` / `no` / `unknown`, straight from
each adapter's `provision_status()` -- and `--write` refuses to include it. `--check`
therefore compares a render that cannot read the local data root, so it fails only on real
drift: on a machine with no dataset bytes at all it passes the same file this machine does.
`python3 tools/datasets.py --check` is the per-entry version of the same local question.

## How to verify a dataset

A dataset whose grade is reachable has a control that scores 1 with the benchmark's
reference and 0 with a do-nothing arm. Most share the QA scaffold:

    python3 data/scaffolds/qa/control.py --dataset <name> --oracle --platform

A dataset with its own control uses the argv in its `control arm` column instead -- read
the row, do not guess, because the control often needs a `--task-id`. `--platform` puts the
arm through `run_one` rather than scoring by hand; drop it for the faster by-hand arm. The
files in `metric test(s)` pin the metric itself, and `tests/quality/test_dataset_controls.py`
runs gates 1 and 2 for every declared control. A refused dataset has no control by design:
its refusal *is* the result.
"""


def render(rows: list[Row], host: dict[str, str] | None = None) -> str:
    """The whole document: header, table, notes -- and, only when asked, the local section.

    Deterministic and host-*independent*: rows are sorted by dataset, nothing in the text
    is a timestamp, a duration or an absolute path, and no cell reads the local data root.
    `host` is the one host-local input and it is opt-in (`--host`), so `--write` and
    `--check` -- which pass none -- can never see it.
    """
    lines = [_HEADER.rstrip("\n"), ""]
    lines += _runtime_section(rows)
    lines.append(_glance(rows))
    lines += [
        "",
        "## Readiness",
        "",
        "| dataset | kind | official metric | known deviation | oracle source | "
        "provision | control arm | metric test(s) | refused |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row.dataset}` | {row.kind} | {row.metric} | {row.deviation} | "
            f"{row.oracle} | {row.provision} | {row.control} | {row.metric_tests} | "
            f"{row.refused} |")
    lines += _notes(rows)
    if host is not None:
        while lines and lines[-1] == "":
            lines.pop()
        lines.append("")
        lines += _host_section(rows, host)
    return "\n".join(lines).rstrip("\n") + "\n"


def _host_section(rows: list[Row], status: dict[str, str]) -> list[str]:
    """The local answer, rendered only for `--host`.

    A section, not a column, so that the committed table cannot carry it: `render()` sees
    it only when the caller passes it, and `main()` rejects `--host` with `--write`/`--check`.
    """
    here = sum(1 for row in rows if status.get(row.dataset) == "yes")
    absent = sum(1 for row in rows if status.get(row.dataset) == "no")
    unknown = len(rows) - here - absent
    lines = [
        "## Provisioned on this host (local only)",
        "",
        "`python3 tools/datasets_doc.py --host` appends this section to stdout and never to "
        "`DATASETS.md`: it answers a question about the machine that ran the command. "
        "`--check` does not read it, so a change here can never make the committed file "
        "look stale.",
        "",
        f"**On this host.** {here} of {len(rows)} datasets have their declared bytes, "
        f"{absent} do not, and {unknown} did not say (`unknown`: no `provision_status()` "
        f"hook, or one that raised).",
        "",
        "| dataset | provisioned here |",
        "| --- | --- |",
    ]
    lines += [f"| `{row.dataset}` | {status.get(row.dataset, 'unknown')} |"
              for row in rows]
    return lines + [""]


def _notes(rows: list[Row]) -> list[str]:
    """The three things a table cell cannot say: why an adapter is unimportable, which
    prose sentence landed a dataset in the review queue, and the reading that cleared one."""
    broken = [row for row in rows if row.import_error]
    review = [row for row in rows if row.review_hint]
    cleared = [row for row in rows if row.cleared_review]
    if not broken and not review and not cleared:
        return []
    lines = ["", "## Notes"]
    if broken:
        lines += [
            "",
            "### Adapters that will not import",
            "",
            "The row above is still a row: the exception is reported, not raised, so one "
            "broken adapter cannot hide the others. Nothing else is known about it -- "
            "`unknown` and `—` there mean exactly that, not a zero.",
            "",
        ]
        lines += [f"* `{row.dataset}` -- {row.import_error}" for row in broken]
    if review:
        lines += [
            "",
            "### Deviations to review",
            "",
            "These adapters say in their own words that a number is not the official one, "
            "and neither `DEVIATIONS` nor `CLEARED` in `tools/datasets_doc.py` records the "
            "reading -- so their `known deviation` cell is `undeclared deviation (review)` "
            "rather than `no`. Read the quoted sentence: if it is about the metric, record "
            "it in `DEVIATIONS`; if it is about something else (the pool, a split, a path "
            "the adapter does not take), the flag is a false alarm, and the reading that "
            "clears it goes in `CLEARED` so the cell can say `no` without trusting a `no` "
            "nobody checked. Either way this list empties.",
            "",
        ]
        for row in review:
            assert row.review_hint is not None
            location, text = row.review_hint
            lines.append(f"* `{row.dataset}` -- {location}: {_code_span(text)}")
    if cleared:
        lines += [
            "",
            "### Review flags a reading cleared",
            "",
            "These adapters' prose warns that a number is not the official one and "
            "`DEVIATIONS` records nothing, so their `known deviation` cell used to say "
            "`undeclared deviation (review)`. A person then read the adapter and the "
            "benchmark's own metric and found the reward *is* the official metric on the "
            "rows the row claims -- `CLEARED` in `tools/datasets_doc.py` is that reading, "
            "and it is here so a later reader can disagree with it. Deleting the entry "
            "puts the row back in the review queue.",
            "",
        ]
        lines += [f"* `{row.dataset}` -- {row.cleared_review}" for row in cleared]
    return lines + [""]


def _display(path: Path) -> str:
    """A path as a reader typed it: relative when it is inside the repository."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def render_document() -> str:
    """The committed document: header, table, notes -- and no host fact."""
    return render(build_rows())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite the document")
    mode.add_argument("--check", action="store_true",
                      help="fail if the document is stale (exit 1)")
    parser.add_argument("--host", action="store_true",
                        help="append the local provisioning section; print mode only, "
                             "because the committed document carries no host fact")
    parser.add_argument("--path", default=None,
                        help=f"the document to print/write/check (default "
                             f"{DOC.relative_to(ROOT)})")
    args = parser.parse_args(argv)
    if args.host and (args.write or args.check):
        parser.error("--host answers a question about this machine, so it is printed "
                     "only: neither --write nor --check may see it, or the committed "
                     "document would start describing one host")
    target = Path(args.path) if args.path else DOC
    rows = build_rows()
    rendered = render(rows, host_status(rows) if args.host else None)

    if args.write:
        target.write_text(rendered, encoding="utf-8")
        print(f"wrote {_display(target)} ({len(rows)} dataset(s))")
        return 0
    if args.check:
        try:
            current = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"{_display(target)} does not exist; run tools/datasets_doc.py --write",
                  file=sys.stderr)
            return 1
        if current != rendered:
            print(f"{_display(target)} is stale; run tools/datasets_doc.py --write",
                  file=sys.stderr)
            return 1
        print(f"{_display(target)} matches the adapters")
        return 0
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
