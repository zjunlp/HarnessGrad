"""AIME: Harbor's scorer, vendored byte-for-byte, plus the transcription differential.

Gate 3 of `docs/dataset-onboarding.md` wants the official scorer copied verbatim next to a
drift test that pins where it came from. `aime` carries no `parity_experiment.json` -- measured
against Harbor `main`, `gpqa-diamond` and `locomo` ship one and this adapter does not -- so the
transcription *is* the equivalence evidence, and the pins below are the whole of what keeps it
honest. Nothing here reaches the network: the four files are on disk and their digests are
checked, not re-fetched.

Provenance, measured at Harbor `main = 2de4605b9404eada7d341388291e6e469c90330b`
(2026-10-16T00:32:52Z). Each vendored file was read through the git blobs API, and the test
re-derives the **git blob digest** rather than only a sha256, so a fixture edited by one byte
cannot pass as "the same file":

| vendored path | upstream path | git blob |
| --- | --- | --- |
| `adapter.py` | `adapters/aime/src/aime/adapter.py` | `391ba994ee995802e887613ec988070cc9c1d8dc` |
| `task-template/instruction.md` | `.../task-template/instruction.md` | `5a6ac47827d0d8b8dd1c4c32f8506bd8b71ba816` |
| `task-template/tests/test.sh` | `.../task-template/tests/test.sh` | `47a75fa828bec6f855212b2064eb71298f3bbb49` |
| `task-template/tests/test_outputs.py` | `.../task-template/tests/test_outputs.py` | `7174f68d371ddf63ba04e6affbf592617274c6f9` |

Harbor is Apache-2.0 and these four files are the only ones this port reads. **No benchmark
payload is vendored**: every answer and gold below is fabricated, and no test opens the
released AIME table.

What the scorer actually does, and the two readings this file is written to hold
-------------------------------------------------------------------------------
`test_outputs.py` reads `/app/answer.txt`, applies `.strip()`, and compares the result to the
gold with `==` on **strings**. It is not an integer parse, and the difference is observable:
`"070"` is the integer 70 and still scores 0. `test.sh` then writes `1` or `0` to the reward
file from that script's *exit code*, and `test.sh` itself always exits 0 -- so a missing or
unreadable answer file is a **failed task, not an unmeasured one**. Both readings are asserted
below, and both are transcribed into `data/datasets/aime.py`.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import data.datasets.aime as dataset

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/aime_official"
SCORER = "task-template/tests/test_outputs.py"
INSTRUCTION = "task-template/instruction.md"
TEST_SH = "task-template/tests/test.sh"

HARBOR_COMMIT = "2de4605b9404eada7d341388291e6e469c90330b"

#: vendored path -> (git blob digest, sha256 of the same bytes)
VENDORED = {
    "adapter.py": (
        "391ba994ee995802e887613ec988070cc9c1d8dc",
        "59f6d2825bc4503d67e267e4bb15096ce28e41a94af87491e507299552f5865d",
    ),
    INSTRUCTION: (
        "5a6ac47827d0d8b8dd1c4c32f8506bd8b71ba816",
        "c084e0c4c059fa0ee8ac0f231b9a55936b4e00c0c5b1be30b5371aea8f9be129",
    ),
    TEST_SH: (
        "47a75fa828bec6f855212b2064eb71298f3bbb49",
        "cf923c7e4cc7a535435523cd2f9e65e45c48bdd13502b59440c4383681bc0fcd",
    ),
    SCORER: (
        "7174f68d371ddf63ba04e6affbf592617274c6f9",
        "0f7070c0ab90f07bc3b7872c3a0ba4fdec1c1fe02fee31cb8198770d99495b79",
    ),
}

#: The lines this port transcribes, by upstream line number. A re-generated upstream file that
#: moves or rewords any of them has to be re-read rather than silently re-pinned.
ANCHORS = [
    (SCORER, 4, 'ANSWER_FILE = "/app/answer.txt"'),
    (SCORER, 7, 'EXPECTED_ANSWER = "{answer}"'),
    (SCORER, 20, "            answer = f.read().strip()"),
    (SCORER, 23, "        if answer == EXPECTED_ANSWER:"),
    (SCORER, 38, "    success = test_answer()"),
    (TEST_SH, 21, "    echo 1 > /logs/verifier/reward.txt"),
    (TEST_SH, 24, "    echo 0 > /logs/verifier/reward.txt"),
    ("adapter.py", 60, '            content = content.replace("{answer}", str(task.answer))'),
    ("adapter.py", 79, '            content = content.replace("{problem_text}", problem_text)'),
    (INSTRUCTION, 2, "  Your answer should be a single integer from 0 to 999 inclusive."),
]


def _git_blob(path: Path) -> str:
    """The digest git itself would name these bytes by."""
    body = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()


@pytest.mark.parametrize("rel", sorted(VENDORED))
def test_vendored_files_are_the_upstream_bytes(rel: str) -> None:
    blob, sha256 = VENDORED[rel]
    path = FIXTURES / rel
    assert path.is_file(), f"{rel} is not vendored"
    assert _git_blob(path) == blob, (
        f"{rel} is no longer the file Harbor had at {HARBOR_COMMIT}. Re-read the upstream "
        f"scorer and re-derive the transcription before updating this digest.")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256


@pytest.mark.parametrize("rel,lineno,text", ANCHORS)
def test_the_transcribed_lines_are_where_they_were(rel: str, lineno: int, text: str) -> None:
    lines = (FIXTURES / rel).read_text(encoding="utf-8").splitlines()
    assert lines[lineno - 1] == text, (
        f"{rel}:{lineno} is no longer {text!r} -- the transcription in data/datasets/aime.py "
        f"cites this line and has to be re-read.")


def _official_scorer(gold: str, answer_file: Path | None):
    """Harbor's scorer, generated the way the adapter generates it, run against a real file.

    Three upstream steps are reproduced rather than paraphrased:

    1. `adapter.py:60` substitutes the gold with `str(task.answer)`. This test performs that
       exact replacement on the vendored template, so `EXPECTED_ANSWER` here is derived, not
       hand-written.
    2. `test.sh:11` runs the file as a **script**, so the reward comes from the `__main__`
       branch and its exit code. The file is exec'd with `__name__` left off `"__main__"` so
       the definitions bind first, and the exit code is then taken from the same expression
       line 38 uses -- asserted as an anchor above, not assumed.
    3. `test.sh:19-25` maps that exit code to 1 or 0.

    The **one** redirect is `ANSWER_FILE`: upstream hard-codes the container path
    `/app/answer.txt` and there is no `/app` here, so the module-level constant is rebound to a
    temporary file after binding. Nothing in the comparison is touched, and
    `test_vendored_files_are_the_upstream_bytes` is what proves the rest is verbatim.

    Returns the reward `test.sh` would have written.
    """
    source = (FIXTURES / SCORER).read_text(encoding="utf-8")
    assert 'EXPECTED_ANSWER = "{answer}"' in source, "the gold placeholder moved"
    generated = source.replace("{answer}", str(gold))
    namespace: dict = {"__file__": str(FIXTURES / SCORER)}
    exec(compile(generated, "test_outputs.py", "exec"), namespace)
    namespace["ANSWER_FILE"] = str(answer_file)
    success = namespace["test_answer"]()
    exit_code = 0 if success else 1                      # line 38, asserted above
    return 1 if exit_code == 0 else 0                    # test.sh:19-25, asserted above


#: (written answer, gold, what Harbor scores). The golds are fabricated; `"204"` and `"371"`
#: are real released answers used only because they are two- and three-digit respectively.
CASES = [
    ("70", "70", 1),
    (" 70 ", "70", 1),                     # `.strip()` before the comparison
    ("70\n", "70", 1),
    ("\t70 ", "70", 1),
    ("70.", "70", 0),                      # not an integer parse
    ("70 is the answer", "70", 0),
    ("070", "70", 0),                      # the integer is equal; the string is not
    ("70.0", "70", 0),
    ("", "70", 0),
    ("71", "70", 0),
    ("204", "204", 1),
    ("371", "371", 1),
    ("  371  ", "371", 1),
    ("0371", "371", 0),
]


@pytest.mark.parametrize("written,gold,expected", CASES)
def test_the_transcription_agrees_with_the_upstream_scorer(
        written: str, gold: str, expected: int, tmp_path: Path) -> None:
    """The differential: our `score()` and Harbor's own code on the same input."""
    answer_file = tmp_path / "answer.txt"
    answer_file.write_text(written, encoding="utf-8")
    upstream = _official_scorer(gold, answer_file)
    ours = dataset.score(written, {"answer": gold})
    assert upstream == expected, (
        f"Harbor scores {written!r} against {gold!r} as {expected}, not {upstream}")
    assert ours["reward"] == float(expected)
    assert ours["correct"] is bool(expected)


@pytest.mark.parametrize("gold", ["70", "204", "371"])
def test_a_missing_answer_file_is_a_zero_and_not_an_absence(gold: str, tmp_path: Path) -> None:
    """`test.sh` writes 0 when the scorer fails, and a missing file makes the scorer fail.

    This is the reading that is easiest to lose: the task was attempted and the answer was not
    there, which is a failed task. It is *not* "no measurement", and the platform records the
    two differently -- so it must never become `None` here.
    """
    absent = tmp_path / "not-written.txt"
    assert _official_scorer(gold, absent) == 0
    ours = dataset.score(None, {"answer": gold})
    assert ours["reward"] == 0.0
    assert ours["correct"] is False
    assert ours["reward"] is not None


def test_the_gold_is_the_released_string_and_never_an_int() -> None:
    """`adapter.py:60` is `str(task.answer)`, and the released values are already strings.

    Measured on all 60 rows at the pinned upstream commit: `answer` is a `str` of pure digits.
    So there is no coercion to get wrong -- but there is a coercion to *detect* if the release
    ever changes shape, which is what the two failing cases below are for.
    """
    assert dataset._gold({"answer": "204"}) == {"answer": "204"}
    assert dataset._gold({"answer": "70"}) == {"answer": "70"}
    assert isinstance(dataset._gold({"answer": "70"})["answer"], str)
    # Not a number: an int gold is a shape change, not something to stringify silently.
    with pytest.raises(ValueError, match="string of digits"):
        dataset._gold({"answer": 70})
    # A leading zero would make the instruction this adapter writes untrue, so it stops.
    with pytest.raises(ValueError, match="leading zero"):
        dataset._gold({"answer": "070"})
    with pytest.raises(ValueError, match="string of digits"):
        dataset._gold({"answer": "70."})


def test_the_goal_carries_the_upstream_instruction_and_not_the_metric() -> None:
    """The instruction-fidelity decision, pinned so it cannot drift back.

    Harbor's `instruction.md` is two sentences and the problem. This port says the same thing
    with this platform's answer path and the `qa submit` sentence the QA contract requires --
    and deliberately does **not** restate the metric. The comparison is a bare string equality,
    so spelling out that `070` and `70.` score 0 would remove a format trap the benchmark
    leaves in place and make our number incomparable to Harbor's.
    """
    goal = dataset._goal("A fabricated problem statement.", {"answer": "70"})
    instruction = (FIXTURES / INSTRUCTION).read_text(encoding="utf-8")

    # Verbatim upstream content, modulo the forced path change.
    assert "Solve the following problem. Reason step by step" in goal
    assert "Your answer should be a single integer from 0 to 999 inclusive." in goal
    assert "A fabricated problem statement." in goal
    assert "a single integer from 0 to 999 inclusive" in instruction

    # The two strings `tests/quality/test_qa_dataset_contract.py` requires.
    assert "answer.txt" in goal
    assert "qa submit" in goal

    # The metric's failure modes are not handed over, and neither is a real answer.
    for leak in ("leading zero", "070", "70.", "How it is scored", "exactly", "strip"):
        assert leak not in goal, f"the goal hands over {leak!r}"
    assert '"70"' not in goal, "the goal uses a released answer as its example"


def test_the_goal_is_the_same_for_every_task() -> None:
    """No task gets extra help: the only task-varying text is the problem itself."""
    golds = [{"answer": "70"}, {"answer": "204"}]
    goals = [dataset._goal("Problem A.", golds[0]), dataset._goal("Problem B.", golds[1])]
    assert goals[0].replace("Problem A.", "X") == goals[1].replace("Problem B.", "X")
