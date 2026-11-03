# Bring your own method

A method is an external program that receives a harness, produces complete
candidate harness directories, and lets HarnessGrad measure them. It does not
need to live in this repository or register a new Python class. The entrypoint
reads one JSON object from stdin and writes one JSON object to stdout; see
[the protocol](../methods/protocol.py) for the fields.

## Start with a working external control

Run these commands from the repository root on Linux or WSL, with Python 3.10+
available. This copies the existing [`noop`](../methods/noop/run.py) control and
its two standard-library helpers into a fresh directory outside the repository.
Keep all three files together so the external method also works in the sandbox.

```bash
method_dir=$(mktemp -d /tmp/hg-external-method-XXXXXXXX)
cp methods/noop/run.py methods/_siblings.py methods/protocol.py "$method_dir/"
python3 tools/validate_method.py "$method_dir" --harness base_harness/loop
```

The validator invokes that external program with a temporary workspace, checks
its JSON response and trajectory, then deletes its temporary output. The control
returns an unchanged harness with `changed: false`, so this check needs no model,
credentials, or dependency installation. Exit status 0 means these contract
checks passed; warnings identify optional declarations. `--no-call` checks only
the entrypoint structure. `--smoke` runs the platform and can call models.

The method directory remains yours to edit. Replace the copy-and-return logic in
`run.py` with your algorithm. [`echo_base`](../methods/echo_base/run.py) shows the
same protocol for a scripted edit and multiple candidates; it is a reference
example, not evidence of a general improvement method.

## Produce a submission the engine can measure

The request supplies absolute `base_harness`, `workspace`, and `trajectory_out`
paths, task IDs, and `platform_api_version`. Validate the API with `require_api`.
Read staged round evidence from `base_harness/_harnessgrad/`, including
`round.json`, `tasks/`, `traces/`, `history/`, `states/`, and `SKILL.md`. The base is
read-only; put edits and candidate directories under `workspace`. The platform
hides its private tree from the method.

Write a JSON object at the requested `trajectory_out` path with a **nonempty**
`steps` array. Each step names an absolute `harness_dir` containing a complete
harness satisfying [the harness contract](writing_a_harness.md), with its label,
edit kind, claimed costs, and optional method-reported results. The platform
measures each submitted step in order, within the run's round budget. Your
reported scores do not replace that measurement.

Write a single JSON response to stdout, for example
`{"steps": 1, "changed": false, "generation_tokens": 0}`. `emit` supplies
`method_model` and defaults `changed` to false; set `changed` explicitly to reflect
your submission. Diagnostics go to stderr or `workspace/*.log`; the platform
preserves those log files in the run receipt before deleting the workspace.

No change still requires a trajectory containing a complete harness step, as the
control demonstrates. `changed: false` stops after a single-step submission by
default; explicit `stop: false` permits another call. A batch defaults to stopping
after its steps unless `stop: false` is supplied. Missing or empty trajectories,
unreadable harness directories, and steps with `error` or
`method_reported.error` are failures, not ordinary no-change results. These rules
apply to both legacy modes because they share the unified engine.

## Connect the method to a run

Use an absolute, shell-quoted entrypoint: the engine runs the method from a
temporary workspace, so a repository-relative script path will fail. Construct
the command with Python's shell quoting to preserve paths containing spaces:

```bash
method_entrypoint=$(python3 - "$method_dir/run.py" <<'PY'
import shlex
import sys
print(shlex.join([sys.executable, sys.argv[1]]))
PY
)
python3 driver.py --harness loop --dataset demo --side train --sampling all \
  --mode A --rounds 1 --trials 1 --jobs 1 --improver none \
  --method-entrypoint "$method_entrypoint" \
  --work-root /tmp/hg-external-method-work --check-only
```

`--check-only` verifies launch prerequisites without running the method, harness,
or model. A measurement requires a configured agent model and a fresh run ID;
after those are ready, remove `--check-only` and supply `--run-id`. Method model
configuration, when your algorithm needs one, uses the separate
`HG_METHOD_BASE_URL`, `HG_METHOD_API_KEY`, and `HG_METHOD_MODEL` settings. Re-run
the validator after edits, then follow the dataset gates and staged pilot policy
before benchmarking. Clean up the external method directory and work root when
you no longer need them.

The validator checks the process/trajectory interface and candidate manifest
presence. It does not certify sandbox behavior, scientific comparability,
candidate correctness, or measurement readiness for every dataset.
