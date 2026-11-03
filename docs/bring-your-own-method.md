# Bring your own RSI method

A **harness** solves one task. A **method** edits a copy of a harness and returns
the candidate; HarnessGrad measures it. Your optimizer can use its own model,
archive, prompt search or code editing logic. It must not call the benchmark
evaluator or use the held-out results to select edits.

## Start with a working process boundary

```bash
method_dir=$(mktemp -d "/tmp/my-rsi-method.XXXXXXXX")
cp -R examples/method-template/. "$method_dir/"
python3 tools/validate_method.py "$method_dir" --harness base_harness/loop
method_entrypoint=$(python3 - "$method_dir/run.py" <<'PY'
import shlex
import sys
print(shlex.join([sys.executable, sys.argv[1]]))
PY
)
run_id="my-method-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json \
  --method-entrypoint "$method_entrypoint" \
  --work-root "/tmp/hg-method-work-$run_id" \
  --run-id "$run_id" --fail-on-incomplete
python3 tools/run_summary.py "runs/$run_id"
```

Run this from the repository root on Linux or WSL with Python 3.10+. The copied
template is validated outside the repository. Its absolute entrypoint is quoted
with `shlex.join` so interpreter and script paths containing spaces remain valid
when the engine starts it from a temporary workspace.

The validator invokes the method with a temporary request and checks stdout,
trajectory structure, and candidate manifest presence. `--no-call` checks only
entrypoint structure; `--smoke` runs the platform and can call models. A passing
validator is not a sandbox, candidate-correctness, or dataset-readiness certificate.

The unchanged template is a zero-cost control, so it ends early with no edit.
Replace `propose(candidate, study)` in `run.py` with your edit rule and return
whether it changed the candidate. Use fresh run IDs for subsequent attempts.
No model, benchmark download or optimizer dependency is required for this smoke.
Configure the solver model and your optimizer separately before using a
[real research recipe](rsi-experiments.md).

The platform sends one JSON request on stdin. Important fields are:

| Field | Use |
| --- | --- |
| `base_harness` | pristine candidate source, including its staged study channel |
| `workspace` | private writable directory for candidate copies and method files |
| `trajectory_out` | write the candidate sequence here |
| `round_index` | next platform measurement round |
| `epochs_total`, `epochs_measured` | total and already measured improvement states; budget is their difference |
| `improver` | optional resolved editor tool; you can also implement your own editor |

Read the study channel under `<base_harness>/_harnessgrad/` (the `base_harness`
entry in the request names the read-only input tree): `round.json`, `traces/`, `tasks/`, `history/` and
`states/` hold the evidence available to the method. The candidate's own task
execution does not receive that optimizer channel. Custom-split held-out task IDs
and the private manifest are not included in method feedback.

Write a JSON object at `trajectory_out` with a nonempty `steps` array, each
naming an absolute `harness_dir` containing a complete harness. Emit only one
JSON response on stdout. Even `changed: false` requires a valid trajectory and
complete harness step, as the unchanged template demonstrates. A single-step
submission stops by default when `changed` is false; a batch stops by default
after its submitted steps. An explicit `stop: false` allows another call within
the remaining budget; `stop: true` asks to stop after this submission.

Send diagnostics to stderr or `workspace/*.log`, which the platform preserves
before deleting the workspace. Missing or empty trajectories and steps declaring
`error` or `method_reported.error` are failures, not no-change results. An error is an error;
do not return the unchanged base as a successful optimized candidate when your
provider or optimizer crashed. Report your actual generation cost; the template's
zero is correct only while it calls no model.

The platform validates each candidate, runs it, and returns its measured study
history on subsequent calls. It owns scoring, held-out evaluation and receipts.
Use `--fail-on-incomplete` in batch scripts so optimizer failures cannot turn an
H0-only curve into a successful job. A method that deliberately finishes early
with a valid no-change response is reported as `early_stop`.
Keep the method directory while developing it, then remove it and the temporary
work root when you no longer need them; keep the run receipts for review.

## From a smoke to an experiment

1. Select and prepare a benchmark from the research recipes.
2. Freeze a custom study/held-out manifest once.
3. Point `--method-entrypoint` at your method, using the same initial harness and
   manifest as the baselines. Configure any private optimizer model in your
   method process, separately from `HG_AGENT_*`.
4. Train on the study side, then measure a fixed checkpoint on the held-out side.
5. Export the run summary and matrix. Check completion, invalid/unmeasured tasks,
   dataset/split identity and cost alongside scores.

For the complete versioned protocol, helper functions and multi-candidate
selection fields, read [methods/protocol.py](../methods/protocol.py) and
[INTERFACE.md §4](../INTERFACE.md#4-the-method-interface).
