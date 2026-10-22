"""A candidate harness: does the method's edit sequence actually apply, and is the
result still a harness?

The gap this closes
-------------------
A method proposes edits; the platform used to copy whatever it produced into the
workspace and measure it. "Applied" was `write_text` and nothing else, so three
different outcomes were indistinguishable on the curve:

* the method decided not to change anything,
* the method's edit sequence named a path outside the harness, or a protected file, or
  a file it never wrote,
* the method produced a tree that is not a harness at all -- a manifest that no longer
  parses, an entrypoint that is not there, Python that does not compile.

All three came out as `score 0.000`, and the reader is left to guess which. Measured:
`build-pmars`, three rounds, `no edit` each time; and separately a failed round whose
step named the pristine base directory, which the old code could not tell apart from a
deliberate no-op.

Two steps, and the second one is verifiable
-------------------------------------------
    step 1  propose   the method returns an edit sequence -- `[{path, content}, ...]`
    step 2  apply     every edit is applied, then the *result* is validated

`apply` and `validate` live here rather than in each method because they are the
platform's definition of what it is about to measure, and a definition a method can
restate is a definition that will drift. `tools/validate_harness.py` is reused rather
than reimplemented: the run path and the CLI must agree on what a harness is, or the
door and the measurement disagree about the subject.

**Applying is not measuring.** A candidate that does not validate is not scored 0 -- it
is recorded as *unmeasured*, with the problems, because there is no measurement to
report. That is the same distinction `n_invalid` draws for a task the platform could not
measure (§2.5.7), applied one level up.
"""
from __future__ import annotations

import json
from pathlib import Path


def _validator():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import tools.validate_harness as harness_validator
    return harness_validator


def validate(repo: Path, *, base_manifest: dict | None = None) -> list[str]:
    """Problems that make `repo` not a harness, or not *this* harness.

    The tool's checks first -- manifest parses, required fields, entrypoint exists,
    backend supported -- because the door, the CLI and this must not disagree about what
    a harness is. Then two the tool has no reason to know about, because they are about a
    *candidate* rather than a fresh harness:

    * **`env_kinds` may not be widened.** The capability gate ran at the door on the base
      harness (§2.5.9). A candidate that adds a kind would be measured in an environment
      the gate never approved -- and mode A measures candidates the method produced, so
      the thing being measured would be the thing deciding it may run.
    * **`role` may not change.** It is how the platform classifies the artifact; a
      harness that could relabel itself `control` mid-run would leave the measurement
      surface without moving a number.
    """
    repo = Path(repo)
    problems = list(_validator().validate(repo, smoke=False))
    from eval.containers.bun_runtime import validate_source
    problems.extend(validate_source(repo))
    try:
        man = json.loads((repo / "harness.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return problems or ["harness.json is unreadable"]
    if not isinstance(man, dict):
        return problems + ["harness.json is not an object"]
    if base_manifest:
        base_kinds = set(base_manifest.get("env_kinds") or ["files"])
        new_kinds = set(man.get("env_kinds") or ["files"])
        widened = sorted(new_kinds - base_kinds)
        if widened:
            problems.append(
                f"the candidate widens `env_kinds` by {widened}; the capability gate ran "
                f"on the base ({sorted(base_kinds)}), so this would be measured in an "
                f"environment that was never approved")
        if man.get("role", "harness") != base_manifest.get("role", "harness"):
            problems.append(
                f"the candidate changes `role` from "
                f"{base_manifest.get('role', 'harness')!r} to "
                f"{man.get('role', 'harness')!r}")
    return problems
