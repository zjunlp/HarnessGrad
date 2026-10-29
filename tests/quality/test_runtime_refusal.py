"""A candidate's runtime declaration must be refused, not raised, whatever the method did.

The strict copier parses `harness.json` and raises on a malformed runtime block, and that
raise used to arrive before the platform's refusal record existed: the round died and the
candidate was lost, which is the defect `tests/quality/test_candidate_apply.py` pins from
the other side. `harnessgrad/loops/mode_a.py:_runtime_declaration_problems` is the check
that runs first so the refusal becomes a record.

The path it reads is supplied by the method, so the failure it must survive is not only
malformed JSON. A symlink to an unreadable target, or a file removed between the manifest
check and the read, raises `OSError` -- and a path the method controls must not be able to
end a run with an exception. That is the case this file exists for; malformed JSON is here
so the two halves stay visible together.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harnessgrad.loops.mode_a import _runtime_declaration_problems


def _candidate(tmp_path: Path, manifest) -> Path:
    root = tmp_path / "candidate"
    root.mkdir()
    text = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (root / "harness.json").write_text(text, encoding="utf-8")
    return root


def test_a_plain_harness_declares_no_runtime(tmp_path):
    root = _candidate(tmp_path, {"name": "h", "version": "0.1.0", "entrypoint": "agent.py"})
    assert _runtime_declaration_problems(root) == []


def test_malformed_json_is_a_refusal(tmp_path):
    root = _candidate(tmp_path, "{not json")
    problems = _runtime_declaration_problems(root)
    assert problems, "a manifest that does not parse must be refused"
    assert "Invalid harness manifest" in problems[0]


def test_an_unsupported_runtime_is_a_refusal(tmp_path):
    root = _candidate(tmp_path, {"name": "h", "version": "0.1.0", "entrypoint": "agent.py",
                                 "runtime": {"kind": "deno", "source": "a", "entrypoint": "b",
                                             "package": "c"}})
    problems = _runtime_declaration_problems(root)
    assert problems and "supported kind" in problems[0]


def test_an_unreadable_manifest_is_a_refusal_not_an_exception(tmp_path, monkeypatch):
    """The path is method-supplied: `OSError` must land on the record, not end the run."""
    root = _candidate(tmp_path, {"name": "h", "version": "0.1.0", "entrypoint": "agent.py"})
    real = Path.read_text

    def deny(self, *args, **kwargs):
        if self.name == "harness.json":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny)
    problems = _runtime_declaration_problems(root)
    assert problems, "an unreadable manifest must be refused"
    assert "could not be read" in problems[0]
    assert "Permission denied" in problems[0]


def test_a_missing_manifest_is_not_a_problem(tmp_path):
    """No `harness.json` at all is a different fact: `candidate.validate` names it."""
    root = tmp_path / "empty"
    root.mkdir()
    assert _runtime_declaration_problems(root) == []


def test_an_eval_run_refuses_a_round_the_platform_rejected(tmp_path):
    """A rejected round has a state and no measurement; naming it must say so.

    Without this the eval run stages an unadmitted tree and fails somewhere inside
    admission, which reads as a platform fault rather than as a round that was refused.
    """
    runs = tmp_path / "runs"
    run = runs / "train-x"
    (run / "state_after_round0").mkdir(parents=True)
    (run / "state_after_round0" / "harness.json").write_text('{"name": "h"}')
    (run / "state_after_round1").mkdir(parents=True)
    (run / "state_after_round1" / "harness.json").write_text("{not json")
    (run / "curve.jsonl").write_text("\n".join([
        json.dumps({"round": 0, "score": 0.5, "per_task": {"t": 1.0}}),
        json.dumps({"round": 1, "score": None, "per_task": {},
                    "unmeasured": {"reason": "not a harness", "problems": ["bad manifest"]}}),
    ]) + "\n")

    from harnessgrad.loops.eval_side import _eval_source
    import argparse

    with pytest.raises(SystemExit) as refused:
        _eval_source(argparse.Namespace(from_run="train-x", from_round=1), runs)
    assert "was rejected" in str(refused.value)
    assert "nothing to evaluate" in str(refused.value)

    # Round 0 is measurable and must still be offered.
    state, round_no = _eval_source(argparse.Namespace(from_run="train-x", from_round=0), runs)
    assert round_no == 0 and state.name == "state_after_round0"
