"""Validate this synthetic evidence packet; no BFCL/runtime source is executed."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    inventory = json.loads((HERE / "source-inventory.json").read_text())
    sinks = [json.loads(line) for line in (HERE / "sinks.jsonl").read_text().splitlines()]
    reviews = [json.loads(line) for line in (HERE / "boundary-review.jsonl").read_text().splitlines()]
    observations = [json.loads(line) for line in (HERE / "observations.jsonl").read_text().splitlines()]
    callers = Counter({"convert_value": 0, "_convert_value": 0})
    for row in inventory:
        raw = (ROOT / row["path"]).read_bytes()
        assert len(raw) == row["bytes"]
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        for node in ast.walk(ast.parse(raw)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in callers:
                callers[node.func.id] += 1
    assert len(reviews) == len(sinks)
    for index, (sink, review) in enumerate(zip(sinks, reviews), 1):
        assert review["site"] == index and review["source"] == f"{sink['path']}:{sink['line']}"
        assert review["source_state"] == "FOUND"
    diff = [row for row in observations if row["kind"] == "differential"]
    boundary = [row for row in observations if row["kind"] == "boundary"]
    assert boundary[-1]["probe_events"] == ["entered"]
    assert all(row["probe_events"] == [] for row in boundary[:-1])
    assert observations[-1]["source_gate_satisfied"] is False
    assert observations[-1]["official_scorer_executed"] is False
    report = (ROOT / "registry/w-bfclcontain.md").read_text()
    citations = re.findall(r"(?<![\w/])((?:tests|registry|data)/[\w./-]+):(\d+)", report)
    for path, number in citations:
        assert 1 <= int(number) <= len((ROOT / path).read_text().splitlines()), (path, number)
    changed_existing = subprocess.check_output(
        ["git", "diff", "HEAD", "--name-only", "--diff-filter=MDRT"], cwd=ROOT, text=True).splitlines()
    assert not changed_existing, changed_existing
    sizes = {p.relative_to(ROOT).as_posix(): p.stat().st_size for p in HERE.iterdir() if p.is_file()}
    assert all(size < 1_000_000 for size in sizes.values())
    assert not any(Path(p).exists() for p in ("/tmp/bfclcontain-tmp", "/tmp/bfclcontain-pytest"))
    print(json.dumps(dict(valid=True, source_files=len(inventory), sink_sites=len(sinks),
                          site_reviews=len(reviews), differential_cases=len(diff),
                          boundary_controls=len(boundary), citations_checked=len(citations),
                          helper_calls_in_enumerated_scope=dict(callers),
                          comparison_counts=dict(Counter(row["comparison"] for row in diff)),
                          existing_tracked_files_changed=changed_existing,
                          task_tmp_remaining=0, python=sys.version), sort_keys=True))


if __name__ == "__main__":
    main()
