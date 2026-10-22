#!/usr/bin/env python3
"""The check every plain-text QA dataset in this repository shares.

    python3 -m data.scaffolds.qa.verify.check --dataset gsm8k --module data.datasets.gsm8k \
        --answer answer.txt --gold gold.json \
        --reward-file verifier/reward.txt --evidence-file verifier/evidence.json

The rule
--------
    reward = the dataset's **official scorer**, applied to the bytes in `answer.txt`
             and the gold the platform re-materialized into `gold.json`.

Nothing else is read. Not stdout, not a trace, not a summary the harness wrote beside
the answer: an interactive or tool-using harness can make all of those say anything,
and `answer.txt` is the platform's own answer channel (`eval/run/runner.py`), so it is the
one a harness has to mean.

Why the gold is a file and not an argument
------------------------------------------
`VERIFY.inputs` re-materializes it immediately before this check runs
(`eval/verification/verifier.py::_verify`), so the `gold.json` read here is the platform's copy. If
the gold travelled as an argv value the check would be identical, but a check that reads
a *path* is the shape ALFWorld already uses for its trusted game file, and one shape for
"trusted input" is easier to keep trustworthy than two.

`measured: false` is not a zero
-------------------------------
A check that cannot run -- no `gold.json`, a dataset module that will not import, an
official scorer that refuses the contract -- writes **no reward file**, writes evidence
saying `measured: false` and why, and exits 3. `eval/verification/verifier.py` turns that into an
`invalid` task. A bare 0 would be indistinguishable from "the agent answered wrongly",
and that distinction is the whole reason the evidence file exists.

A missing or empty `answer.txt` is a **measured zero**: the task ran and the agent did
not answer. That is a real result and it is reported as one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import traceback
from pathlib import Path

#: This file lands in `<workdir>/qa/verify/check.py` when the platform materializes a
#: `VERIFY.inputs` entry, and it is also runnable in place from the platform checkout.
#: In both layouts the module it imports -- `read`, the parquet reader -- sits **one
#: directory up**, so that directory goes on `sys.path` first. Nothing here may import
#: `data.*`: the verification namespace hides the platform tree, and a check that
#: reached back into it would either fail or read whatever the subject left there.
HERE = Path(__file__).resolve().parent
for candidate in ((HERE,) if HERE == Path('/scorer') else (HERE.parent, Path.cwd())):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

DEFAULT_REWARD_FILE = "/verifier/reward.txt"
DEFAULT_EVIDENCE_FILE = "/verifier/evidence.json"


#: Dataset names are a strict subset of what a module path may be, so `--dataset` is
#: validated before it is used to build an import name. Validated rather than escaped: a
#: dynamic import of `data.` + an unchecked value is a route into any importable module,
#: and `tests/quality/test_boundaries.py::R1` refuses the unvalidated spelling for the
#: same reason. With this pattern the argument can only name a module under `data/`,
#: which is what the adapter is.
DATASET_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: The package the adapter is materialized into. The check imports `data.datasets.<name>` -- the
#: adapter's real module name, so the scorer that grades is the same file the platform
#: loaded, re-materialized into the workdir by `VERIFY.inputs`.
DATASET_PACKAGE = "data.datasets"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--answer", default="answer.txt")
    parser.add_argument("--prediction", default="prediction.json")
    parser.add_argument("--gold", default="gold.json")
    parser.add_argument("--task-id", help="trusted selected task identity")
    parser.add_argument("--gold-sha256", help="trusted SHA-256 of serialized gold bytes")
    parser.add_argument("--score-mode", help="trusted adapter scoring policy")
    parser.add_argument("--redact-gold", action="store_true",
                        help="omit trusted gold from all verifier evidence paths")
    parser.add_argument("--reward-file", default=DEFAULT_REWARD_FILE)
    parser.add_argument("--evidence-file", default=DEFAULT_EVIDENCE_FILE)
    return parser.parse_args(argv)


def write_reward(path: Path, score: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{score:g}\n")


def write_evidence(path: Path, evidence: dict) -> None:
    """Evidence is for a reader, never for the score: failing to write it is not fatal."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=1, sort_keys=True,
                                   ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"could not write evidence: {exc}", file=sys.stderr)


def unmeasured(args, evidence: dict, why: str) -> int:
    """The one path that must not write a reward file. See the module docstring."""
    evidence.update(measured=False, error=why)
    write_evidence(args.evidence_file, evidence)
    print(f"check cannot measure: {why}", file=sys.stderr)
    return 3


def error_detail(args, exc: Exception) -> str:
    """Exception messages and traceback source lines can contain trusted labels."""
    return type(exc).__name__ if args.redact_gold else f"{type(exc).__name__}: {exc}"


def record_traceback(args, evidence: dict) -> None:
    if not args.redact_gold:
        evidence["traceback"] = traceback.format_exc()[-2000:]


#: A quoted answer is unwrapped before scoring. Measured on the first smoke run of
#: `sportsunderstanding`: the oracle's own submission came back as `'plausible'` in one
#: trial because a shell snippet quoted it, and an official scorer that does a substring
#: test then *passes* for the wrong reason while an exact-match one fails for the wrong
#: reason. Unwrapping one layer of matching quotes is a formatting fix, not a scoring
#: concession: it changes no dataset's rule, and it is stated here because a silent
#: normalisation is how a scorer stops being the official one.
_QUOTED = re.compile(r"^\s*(?P<q>['\"`])(?P<body>.*)(?P=q)\s*$", re.S)


def unwrap(answer: str) -> str:
    match = _QUOTED.match(answer)
    return match.group("body") if match else answer


def main(argv) -> int:
    args = parse_args(argv)
    # Known private adapters must redact before gold parsing, prediction validation,
    # or scorer import. Adapter flags are too late for those failure paths.
    args.redact_gold = args.redact_gold or args.dataset in {"chessbench_puzzles", "bfcl_v3"}
    args.reward_file = Path(args.reward_file)
    args.evidence_file = Path(args.evidence_file)
    answer_path = Path(args.answer)
    prediction_path = Path(args.prediction)
    gold_path = Path(args.gold)

    evidence: dict = {
        "dataset": args.dataset,
        "answer_file": str(answer_path),
        "gold_file": str(gold_path),
        "reward_file": str(args.reward_file),
    }
    if args.redact_gold:
        evidence["gold"] = {"redacted": True}

    if not gold_path.is_file():
        # The check's own trusted input is missing. On a real run the platform writes
        # it; on a by-hand run the caller forgot. Either way this is not a measurement,
        # and it is emphatically not the agent's zero.
        return unmeasured(args, evidence,
                          f"no gold file at {gold_path} -- the platform's "
                          f"re-materialization did not happen")

    try:
        # Hash and parse one buffer: a later path substitution cannot change the
        # object supplied to the scorer while leaving a receipt for earlier bytes.
        gold_bytes = gold_path.read_bytes()
        digest = hashlib.sha256(gold_bytes).hexdigest()
        bound = (bool(args.task_id) and bool(args.gold_sha256)
                 and re.fullmatch(r"[0-9a-f]{64}", args.gold_sha256) is not None
                 and digest == args.gold_sha256)
        evidence["scorer_input"] = {
            "task_id": args.task_id, "path": str(gold_path), "sha256": digest,
            "expected_sha256": args.gold_sha256, "verified": bound,
        }
        if (args.task_id is not None or args.gold_sha256 is not None) and not bound:
            return unmeasured(args, evidence, "scorer input task/digest binding mismatch")
        # Legacy by-hand calls without either argument remain explicitly unbound.
        # Native bound dispatch refuses such a receipt, even if it has a reward.
        gold = json.loads(gold_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
        return unmeasured(args, evidence,
                          f"gold file {gold_path} is unreadable: {error_detail(args, exc)}")
    if not isinstance(gold, dict):
        return unmeasured(args, evidence,
                          f"gold file {gold_path} holds {type(gold).__name__}, not an "
                          f"object")

    try:
        answer = answer_path.read_text(encoding="utf-8") if answer_path.is_file() else ""
    except (OSError, ValueError) as exc:
        return unmeasured(args, evidence,
                          f"answer file {answer_path} is unreadable: {error_detail(args, exc)}")
    evidence.update(answer_present=answer_path.is_file(),
                    answer_chars=len(answer),
                    answer=answer[:2000],
                    gold={"redacted": True} if args.redact_gold else gold)

    structured = None
    if prediction_path.is_file():
        try:
            from data.scaffolds.qa.prediction import validate_prediction
            structured, prediction_error = validate_prediction(
                json.loads(prediction_path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            return unmeasured(args, evidence,
                              f"prediction file {prediction_path} is malformed: {error_detail(args, exc)}")
        except Exception as exc:  # noqa: BLE001 - unavailable validation is unmeasured
            record_traceback(args, evidence)
            return unmeasured(args, evidence,
                              f"could not validate prediction: {error_detail(args, exc)}")
        if prediction_error:
            return unmeasured(args, evidence,
                              f"prediction file {prediction_path} is invalid: "
                              f"{prediction_error}")
        evidence.update(prediction_file=str(prediction_path), prediction=structured)

    if not DATASET_NAME_RE.match(args.dataset):
        return unmeasured(args, evidence,
                          f"{args.dataset!r} is not a dataset name; this check imports "
                          f"`data.datasets.<name>` and will not be handed an arbitrary module")
    module_name = f"{DATASET_PACKAGE}.{args.dataset}"
    evidence["scorer_module"] = module_name
    try:
        # `__import__` rather than an importlib call with a variable argument: the
        # boundary rule (`tests/quality/test_boundaries.py::R1`) reads the argument of
        # every dynamic import call and refuses one that is not a literal, because a
        # non-literal is how the platform would reach an arbitrary module. The name is
        # already validated to `data.datasets.<name>` above, and the rule this file must not break
        # is about *reaching an arbitrary module*, not about which builtin does it.
        adapter = __import__(module_name, fromlist=["score"])
        if getattr(adapter, "REDACT_EVIDENCE", False) and not args.redact_gold:
            evidence["gold"] = {"subset": gold.get("subset"),
                                  "category": gold.get("subset"),
                                  "redacted": True}
            args.redact_gold = True
        scorer = getattr(adapter, "score", None)
        if not callable(scorer):
            return unmeasured(args, evidence,
                              f"{module_name} has no callable `score(prediction, gold)`; "
                              f"the adapter was materialized without its scoring half")
    except Exception as exc:  # noqa: BLE001 - materializing the scorer is the platform's job
        record_traceback(args, evidence)
        return unmeasured(args, evidence, f"could not import {module_name}: "
                                          f"{error_detail(args, exc)}")

    prediction = (str(structured["answer"]) if structured is not None
                  else unwrap(answer).strip())
    try:
        evidence_scorer = getattr(adapter, "score_prediction", None)
        score_options = {"mode": args.score_mode} if args.score_mode is not None else {}
        result = (evidence_scorer(structured, gold, **score_options)
                  if structured is not None
                  and getattr(adapter, "EVIDENCE_AWARE", False)
                  and callable(evidence_scorer)
                  else scorer(prediction, gold, **score_options))
    except Exception as exc:  # noqa: BLE001 - a scorer that refuses is not a zero
        record_traceback(args, evidence)
        return unmeasured(args, evidence,
                          f"the official scorer refused to score this item: "
                          f"{error_detail(args, exc)}")

    if not isinstance(result, dict) or "reward" not in result:
        return unmeasured(args, evidence,
                          f"{module_name}.score() returned "
                          f"{'an invalid result' if args.redact_gold else repr(result)}; it must "
                          f"return a dict with a numeric `reward`")
    try:
        reward = float(result["reward"])
    except (TypeError, ValueError):
        return unmeasured(args, evidence,
                          f"{module_name}.score() returned a non-numeric reward "
                          f"{'<redacted>' if args.redact_gold else repr(result['reward'])}")

    evidence.update(measured=True, reward=reward,
                    prediction=(structured if structured is not None else prediction[:2000]),
                    correct=bool(result.get("correct", reward >= 1.0)),
                    why=str(result.get("why") or ""),
                    official=result.get("official"))
    write_evidence(args.evidence_file, evidence)
    write_reward(args.reward_file, reward)
    print(f"reward {reward:g}: {evidence['why']}")
    return 0 if reward >= 1.0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - a crash must not look like a measured zero
        traceback.print_exc()
        raise SystemExit(4)
