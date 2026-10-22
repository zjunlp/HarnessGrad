"""A task id with a slash in it is a **nested path**, and every record writer must know that.

`data/scaffolds/qa/base.py` states the convention: `task_id` is `<dataset>/<split>/<n>`, "a slash is
fine in a platform task id (it is a JSON value and a path component under the run
directory)". The second half of that sentence was not true. `dest.mkdir(parents=True)` for
`verdicts/round-0/` leaves `verdicts/round-0/gsm8k/train/` missing, so `<dest>/<tid>.json`
has no parent.

Measured, and it is why this file exists: the first real run of a QA dataset
(`driver.py --dataset gsm8k --side train --split official --tasks gsm8k/train/0,…`) died
with `FileNotFoundError: .../verdicts/round-0/gsm8k/train/0.json`. The progress record for
the same task was lost more quietly -- `_write_progress` never raises by design, so the
only sign was a warning line nobody read, and the run looked like it had recorded nothing
because it had.

The fix is `eval/observe/tracing.py::task_file`, used by all five writers. These tests use ids with
slashes and assert the file **is on disk**, which is the assertion the old code would fail
at four of the five sites.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.observe.tracing import _write_progress, task_file                    # noqa: E402
from harnessgrad import channel                                        # noqa: E402
from harnessgrad.records import _save_traces, _save_verdicts           # noqa: E402

NESTED = "gsm8k/train/0"


def test_task_file_creates_the_directories_the_id_implies(tmp_path):
    path = task_file(tmp_path / "verdicts", f"{NESTED}.json")
    path.write_text("{}")
    assert path == tmp_path / "verdicts" / "gsm8k" / "train" / "0.json"
    assert path.is_file()


def test_a_verdict_for_a_nested_id_is_written(tmp_path):
    """`verdicts/round-0/<dataset>/<split>/<n>.json`, which is what a reader looks for."""
    _save_verdicts(tmp_path, 0, {"per_task": {NESTED: 0.0},
                                 "verdicts": {NESTED: {"kind": "answer",
                                                       "passed": False,
                                                       "detail": "no answer"}}},
                   [NESTED])
    written = tmp_path / "verdicts" / "round-0" / "gsm8k" / "train" / "0.json"
    assert written.is_file()
    entry = json.loads(written.read_text())
    assert entry["task_id"] == NESTED and entry["detail"] == "no answer"


def test_a_trace_for_a_nested_id_is_written_both_ways(tmp_path):
    """The last trial's trace and the per-trial traces, both nested."""
    _save_traces(tmp_path, 0, {NESTED: '{"a": 1}\n'}, [NESTED],
                 {"train": [NESTED], "eval": []},
                 trial_traces={NESTED: ['{"a": 1}\n', '{"a": 2}\n']})
    base = tmp_path / "round0_traces" / "gsm8k" / "train"
    assert (base / "0.jsonl").is_file()
    assert (base / "0.trial1.jsonl").is_file() and (base / "0.trial2.jsonl").is_file()


def test_a_progress_record_for_a_nested_id_is_written(tmp_path, capsys):
    """The writer that swallows its own errors, so a missing file is the only signal."""
    _write_progress(tmp_path, 0, NESTED,
                    [{"verdict": {"passed": True, "kind": "answer"}, "trace": '{"t": 1}\n'}],
                    None)
    written = tmp_path / "progress" / "round-0" / "gsm8k" / "train" / "0.json"
    assert written.is_file(), capsys.readouterr().err
    assert "could not write the progress record" not in capsys.readouterr().err
    entry = json.loads(written.read_text())
    assert entry["task_id"] == NESTED and entry["complete"] is True
    assert (tmp_path / "progress" / "round-0" / "traces" / "gsm8k"
            / "train" / "0.trial1.jsonl").is_file()


def test_the_channel_stages_a_nested_trace_and_its_index(tmp_path):
    """`_write_trace_with_index` writes the trace *and* the table of contents beside it."""
    channel._write_trace_with_index(tmp_path / "traces", f"{NESTED}.jsonl", '{"x": 1}\n')
    base = tmp_path / "traces" / "gsm8k" / "train"
    assert (base / "0.jsonl").is_file()
    assert (base / "0.index.json").is_file()
