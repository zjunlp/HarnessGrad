# Runtime observation aggregation review - w-obs

Correction: [w-fix](w-fix-unknown-isolation-records.md) supersedes this review's
unknown-only write-path refusal. Preserve complete explicitly noncomparable
records; keep unknown comparison and mixed-class refusals. The implementation
and validation below describe the original w-obs change.

Reviewed and repaired from `bd25f5a` in registered worktree `/tmp/hg-obs`,
2026-10-15. Controls use fabricated results, fabricated ChessBench puzzles and a
Docker double. No live dataset, Docker daemon, model, GPU or endpoint execution;
no existing runs or frozen receipts were opened or changed.

## Starting requirement

The `runtime-observations` object was read before editing. Its observed finding:

> The runner refusal hook blocks declared-policy paths; trusted effective backend observations are still absent.

Its own next action:

> Review backend observation integration against the existing refusal contract; keep unsupported modes blocked.

The authoritative [w-shared review](w-shared-verifier-review.md), section
`## OPEN: runtime-observations - unknown-only aggregation is allowed`, identifies
the residual mechanism gap: scored unknown-only inputs survive the trial, cache,
task, progress and curve boundaries, and stepwise ChessBench omits an observation.
This follow-up supersedes that open disposition for this source-review action.

## CLOSED: runtime-observations - observed topology required before aggregation

Both concrete defects are fixed:

- [eval/run/chess_stepwise.py:64](../eval/run/chess_stepwise.py#L64) explains the
  trust boundary, and line 67 records `scorer_isolation_class="in-process"` on
  both measured and incomplete verdicts. `finish` scores the trusted controller's
  relay receipt inside the platform process after a separate actor exits; this
  existing vocabulary value does not claim actor-process scoring or pristine
  runtime isolation. Fabricated runner controls check pass, measured failure,
  incomplete outcomes, cached evaluations and saved verdict propagation.
- [eval/verification/isolation.py:29](../eval/verification/isolation.py#L29)
  raises `IsolationMismatch` for any nonempty all-unknown input, including absent,
  unsupported and malformed class values. Mixed-class rejection remains at line 27;
  every uniform known class still works. Refusal is preferable to emitting a score
  with an optional warning: every existing aggregation caller must enforce it.
  The error says `scorer isolation class unknown: aggregation invalid`; no
  declaration supplies a missing observation. `require_comparable` at line 34
  shares the refusal, so the previous aggregation/comparison asymmetry is gone.

The shared guard applies before scored output at all named boundaries:

- Cached trials: [eval/run/runner.py:1152](../eval/run/runner.py#L1152).
- Fresh trial mean: [eval/run/runner.py:1200](../eval/run/runner.py#L1200), before
  averaging at line 1202. Run/task classes: line 1327.
- Progress: [eval/observe/tracing.py:396](../eval/observe/tracing.py#L396), before
  writing at line 408. This best-effort writer catches `ValueError` at line 428,
  emits an explicit warning and writes no scored progress record. The runner
  still raises at its own aggregation guard; the warning cannot admit the run.
- Curve construction: [harnessgrad/records.py:163](../harnessgrad/records.py#L163),
  before score statistics. Curve history: line 618, before opening for writing at
  line 619, preserving any existing file on refusal.

Legacy records remain readable: normalization is unchanged at
[eval/verification/isolation.py:15](../eval/verification/isolation.py#L15) and
[harnessgrad/store/schema.py:213](../harnessgrad/store/schema.py#L213) still loads
historical JSON without rewriting it. Synthetic legacy controls load records
with no class, explicit unknown and unsupported values, then prove aggregation,
comparison and curve rewriting refuse while the original bytes remain intact.
Legacy cached dictionaries are likewise unchanged on refusal. Empty inputs retain
the `unknown` sentinel because they contain no measured inputs; an empty runner
result has an empty `per_task`, never a newly accepted unknown scored task.

Unsupported policy-bearing production paths still refuse through
[eval/run/runner.py:38](../eval/run/runner.py#L38), including before cache lookup.
The diagnostic exception and admission validator are unchanged. A topology value
records the executed route; it does not attest every resource or security limit.

## Registry scope and validation

The registry is active-only. Remove only `runtime-observations` and its 23 dataset
references; retain both code-solvable shared blockers, dataset-local requirements,
all historical records and `production_authorized=false`. Append the closure note
without rewriting earlier review history. No validator changes.

Pinned expectations change with explanatory comments in the same commit: unknown
curve creation now refuses, a missing-observation legacy cache is readable but
cannot supply an aggregate, and registry sets lose completed source-review gates.
The AppWorld set also drops its stale `pristine-verifier` expectation, whose
closure was already recorded by w-shared; both remaining real gates stay pinned.
Existing synthetic trial, parallel-task, provider and curve fixtures now explicitly
model their known command-scorer route; their numerical and evidence assertions
are retained. This changes authored test inputs, never historical observations.

Focused commands and complete fresh outputs are in
[reports/w-obs-validation.txt](../reports/w-obs-validation.txt). The runtime tests
cover mixed classes, unknown-only inputs at each boundary, uniform known classes,
legacy reading without promotion, and ChessBench's actual controller route.
Admission controls retain unsupported-mode refusal; registry controls retain
history and the remaining gates. Results: **68 runtime/ChessBench tests**, **376
registry/admission/writer tests**, and **27 existing synthetic aggregation tests**
passed (**471 total**). Six driver-based tests were deliberately outside these
focused selections; no whole-suite result is claimed. The unchanged checker
returns `valid=true`, 28 datasets, two shared blockers, 11 historical records and
`production_authorized=false`.

This is source/test closure only. Dataset-specific
offline dependency closure, state export safety, live lifecycle/cleanup and measured
coverage remain subject to their existing requirements; no pilot is authorized.
