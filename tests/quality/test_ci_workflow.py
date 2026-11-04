"""CI is one file, and it may not rot: every path it names has to exist.

A workflow is the only code in this repository that nobody runs before merging it. It runs
on someone else's machine, after the change, and its failure mode is not a crash -- it is a
red badge that everybody learns to ignore. So the file is checked here, textually, against
the tree it describes: every `tests/quality/test_*.py` it lists must be a real file, every
`tools/*.py` it invokes must be real, and the interpreter it asks for must be one
`pyproject.toml` claims to support.

It is textual on purpose. PyYAML is not a dependency of this repository -- the platform is
standard library only -- so a test that imports it would fail on exactly the machine this
workflow is for. The properties below are the ones a regex can settle honestly: presence,
pinning, permissions, timeouts, trigger coverage, and the rule that the suite is selected by
`tools/run_tests.py` rather than by a second list of files that would drift from the first.
"""
from __future__ import annotations

import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = (ROOT / ".github" / "workflows" / "quality.yml",
             ROOT / ".github" / "workflows" / "contributors.yml")
WORKFLOW = WORKFLOWS[0]

#: Paths inside the repository that the workflow may name. The pattern deliberately stops
#: at a quoted or whitespace boundary, so a shell flag after a path is not swallowed.
PATH_PATTERN = re.compile(r"(?:tests/quality|tools)/[A-Za-z0-9_./-]+\.py")
ACTION_PATTERN = re.compile(r"uses:\s*([^\s#]+)")
PYTHON_PATTERN = re.compile(r"python-version:\s*['\"]?([0-9]+(?:\.[0-9]+)*)")
FLOOR_PATTERN = re.compile(r'requires-python\s*=\s*"[>=!~ ]*([0-9]+(?:\.[0-9]+)*)')


def _text(workflow: pathlib.Path | None = None) -> str:
    return (workflow or WORKFLOW).read_text(encoding="utf-8")


def test_the_workflow_exists_and_is_yaml_safe() -> None:
    assert WORKFLOW.is_file(), WORKFLOW
    text = _text()
    assert not re.search(r"^\t", text, flags=re.MULTILINE), (
        "a tab-indented line is invalid YAML indentation and GitHub rejects the whole file")
    assert "jobs:" in text and "steps:" in text, "not a job definition"


def test_every_test_file_it_names_exists() -> None:
    for workflow in WORKFLOWS:
        named = sorted({match.group(0) for match in PATH_PATTERN.finditer(_text(workflow))})
        assert named, f"{workflow.name} names no test file at all"
        missing = [path for path in named if not (ROOT / path).is_file()]
        assert not missing, f"{workflow.name} runs files that do not exist: {missing}"


def test_every_script_it_runs_exists() -> None:
    for workflow in WORKFLOWS:
        invoked = sorted({match.group(0) for match in PATH_PATTERN.finditer(_text(workflow))})
        scripts = [path for path in invoked if path.startswith("tools/")]
        assert scripts, f"{workflow.name} invokes no tool"
        missing = [path for path in scripts if not (ROOT / path).is_file()]
        assert not missing, f"{workflow.name} invokes scripts that do not exist: {missing}"


def test_the_suite_job_goes_through_the_lane_runner() -> None:
    """The selection belongs to `tools/run_tests.py`, not to the workflow.

    A workflow that lists test files for the suite is a second copy of the suite: the copy
    that is not updated when a file is renamed, and whose removals nobody reviews. The lane
    runner already owns selection, the per-file temporary root and the run-alone recheck.
    """
    text = _text()
    assert "tools/run_tests.py" in text, (
        "the suite must run through tools/run_tests.py")
    assert "tools/run_selected.py --plan plan.json --jobs" in text, (
        "the suite job must execute the validated plan with explicit concurrency")
    assert "--fast" not in text, "CI must not silently prune explicitly planned slow tests"


def test_actions_are_pinned_to_a_major_version() -> None:
    """`@main` moves under the workflow; a major tag does not (much).

    Full commit pinning would be stronger and is the wrong trade here: the repository has
    no bot that bumps them, and a stale SHA keeps a security fix out. A major tag is the
    promise GitHub Actions documentation makes, and a floating branch is not.
    """
    for workflow in WORKFLOWS:
        refs = [match.group(1) for match in ACTION_PATTERN.finditer(_text(workflow))]
        assert refs, f"{workflow.name} uses no action"
        floating = [ref for ref in set(refs) if "@" in ref
                    and not re.fullmatch(r"[^@]+@v[0-9]+", ref)]
        assert not floating, (
            f"{workflow.name}: actions must be pinned to a major tag: {sorted(floating)}")


def test_jobs_declare_a_timeout() -> None:
    """A hung job holds a runner for six hours by default, and a suite can hang.

    The job headers are the two-space keys *inside* `jobs:`; the trigger names above it use
    the same indentation, which is why the section is sliced first rather than searching the
    whole file.
    """
    for workflow in WORKFLOWS:
        text = _text(workflow)
        assert "\njobs:" in text, f"{workflow.name} declares no jobs section"
        section = text.split("\njobs:", 1)[1]
        headers = list(re.finditer(r"(?m)^  ([a-z][a-z0-9_-]*):\s*$", section))
        assert headers, f"{workflow.name} declares no job"
        for index, match in enumerate(headers):
            end = headers[index + 1].start() if index + 1 < len(headers) else len(section)
            assert "timeout-minutes:" in section[match.end():end], (
                f"{workflow.name}: job {match.group(1)} declares no timeout")


def test_permissions_are_read_only() -> None:
    """Nothing here publishes, tags or comments, so nothing may ask for write scope."""
    text = _text()
    assert re.search(r"(?m)^permissions:\s*$", text), "permissions must be declared"
    assert "contents: read" in text
    for granted in ("contents: write", "packages: write", "pages: write",
                    "id-token: write"):
        assert granted not in text, f"the quality gate does not need {granted}"


def test_a_pull_request_is_covered() -> None:
    """The gate is for changes from outside: a branch-only trigger never sees them."""
    text = _text()
    triggers = text.split("permissions:", 1)[0]
    assert re.search(r"pull_request", triggers), "pull requests are not covered"
    assert re.search(r"workflow_dispatch", triggers), "the workflow cannot be run by hand"
    assert re.search(r"schedule", triggers), (
        "the full suite is deferred to the nightly run, so the workflow needs a schedule")
    assert re.search(r"branches:\s*\[\s*main\s*\]", triggers), (
        "a push trigger must name main; an unnamed push doubles the queue for nothing")


def test_the_interpreter_it_uses_is_one_the_project_supports() -> None:
    """CI on a version the project does not claim would find bugs that cannot be shipped."""
    declared = FLOOR_PATTERN.search((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert declared, "pyproject.toml declares no requires-python"
    floor = tuple(int(part) for part in declared.group(1).split("."))
    versions = {match.group(1) for match in PYTHON_PATTERN.finditer(_text())}
    assert versions, "the workflow pins no interpreter"
    for version in versions:
        parsed = tuple(int(part) for part in version.split("."))
        assert parsed[:len(floor)] >= floor, (
            f"CI runs python {version}, below the declared floor {declared.group(1)}")

def test_the_report_job_fetches_the_commit_it_compares_against() -> None:
    """`actions/checkout` is one commit deep, so a diff against the base yields nothing and the
    selector has to read that as "everything changed". The report job fetched nothing, so every
    push deferred the whole suite and the per-change selection never ran. Fetching the one base
    commit is what makes the selection real; the fallback stays for a force-push."""
    text = _text()
    assert "git fetch" in text and "--depth=1" in text, (
        "the report job must fetch the comparison base, or every push selects everything")
    assert "github.event.pull_request.base.sha || github.event.before" in text, (
        "the base is the PR base or, for a push, the previous tip")
    assert "could not be fetched" in text, "an unfetchable base must say so, not pass silently"


def test_a_manual_dispatch_runs_the_full_suite() -> None:
    """The full suite is deferred off the push path, not skipped: the nightly run and a manual
    dispatch ask for it. Keying only on `schedule` made a dispatch run the deferred plan, which
    is an empty plan, which is nothing at all."""
    text = _text()
    assert "schedule|workflow_dispatch" in text, "both events must request the full suite"
    assert "github.event_name != 'schedule' && github.event_name != 'workflow_dispatch'" in text
    plan = text.split("- name: Plan the tests this change can reach", 1)[1].split(
        "- name: Suite", 1)[0]
    assert "--force-full --plan plan.json" in plan
    assert "printf" not in plan, "all plan states must be produced by the selector"
    suite = text.split("- name: Suite", 1)[1].split(
        "- name: Full suite deferred", 1)[0]
    assert re.search(r"schedule\|workflow_dispatch\)\s*"
                     r"python3 tools/run_selected.py --plan plan.json --jobs 4 --full ;;", suite)
    assert re.search(r"\*\)\s*python3 tools/run_selected.py --plan plan.json --jobs 4 ;;", suite)
    assert "--full-if-empty" not in suite, "empty push selections must never expand to full"
