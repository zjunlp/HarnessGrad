# Shared scorer topology and pristine verifier review - w-shared

Reviewed source at `0b470b6` (including option B merged in `8da84f8`),
2026-10-15, in registered worktree `/tmp/hg-shared`. This review answers two
review actions; it does not attest a live runtime or authorize production/H0.
No real dataset scorer, Docker daemon, model, GPU or endpoint was executed.
The Python integration controls execute a fabricated scorer locally behind a
Docker double; their observations are synthetic.

## Starting requirements, read before disposition

`runtime-observations`, status `runtime`, originally observed:

> The runner refusal hook blocks declared-policy paths; trusted effective backend observations are still absent.

Its own next action:

> Review backend observation integration against the existing refusal contract; keep unsupported modes blocked.

`pristine-verifier`, status `runtime`, originally observed:

> Image/input rematerialization does not establish a fresh independently identified trusted scoring runtime.

Its own next action:

> Review pristine verifier identity and isolation requirements independently of actor-produced state.

The other two shared blockers were also read. `scorer-offline-closure` requires
dataset-specific pinned closures, complete transitive image/ABI dependencies,
official provenance/execution and class-specific live lifecycle evidence.
`artifact-only-state-export` requires authoritative task/private-input contracts,
ownership, quiescence/trial binding, equivalence and live lifecycle/cleanup.
This review supplies neither kind of dataset evidence. Both objects and their
dataset references remain unchanged. Their full current observations and next
actions remain in `docs/dataset-blockers.json`.

## OPEN: runtime-observations - unknown-only aggregation is allowed

Follow-up 2026-10-15: [w-obs](w-obs-runtime-review.md) repairs both defects and
closes this source-review action. The original findings below are retained as
the record of the reviewed source, rather than rewritten as passing evidence.

There is a platform-written topology channel, but the requested refusal across
unknown observations is incomplete. The blocker stays open, with its original
registry object and references intact.

The channel is substantive:

- [eval/verification/isolation.py:15](../eval/verification/isolation.py#L15)
  normalizes absent/unsupported observations to `unknown`.
  [Line 34](../eval/verification/isolation.py#L34) writes the observed class
  separately from the declaration and nulls the score on disagreement; a
  declaration cannot promote an observation to pristine.
- [eval/verification/verifier.py:445](../eval/verification/verifier.py#L445)
  dispatches answer, actor-workdir, service and explicit pristine routes, and
  records the executed route on returned verdicts at lines 476 and 480.
  [Line 238](../eval/verification/verifier.py#L238) handles the snapshot route
  separately, rejects incompatible declarations, then stamps `snapshot-service`.
  These are executor-route facts; only the pristine path also inspects the
  stopped backend configuration. A class alone does not attest all policy limits.
- [eval/run/runner.py:160](../eval/run/runner.py#L160) preserves admission and
  supplies `unknown` when a verdict lacks an observation (line 167).
  [Lines 1151 and 1200](../eval/run/runner.py#L1151) check cached and fresh trial
  classes, and [line 1327](../eval/run/runner.py#L1327) checks task classes for
  the run result. Missing classes are never presumed pristine.
- [eval/observe/tracing.py:395](../eval/observe/tracing.py#L395) checks trial
  classes and stores the progress score, class and per-trial classes at lines
  408-413. [harnessgrad/records.py:520](../harnessgrad/records.py#L520) preserves
  class, declaration and pristine evidence; [line 543](../harnessgrad/records.py#L543)
  saves task verdicts and per-trial cells (lines 598-603).
  [Line 161](../harnessgrad/records.py#L161) derives the curve class from task
  classes and stores it beside the score at lines 211-213.
- [eval/verification/isolation.py:20](../eval/verification/isolation.py#L20)
  refuses sets containing different classes, including known plus unknown.
  [harnessgrad/records.py:617](../harnessgrad/records.py#L617) applies that check
  before opening a curve history for writing.
  [isolation.py:27](../eval/verification/isolation.py#L27) additionally refuses
  unknown comparisons. The actual UI comparator independently checks the point
  and task classes at [web/src/logic/compare.ts:148](../web/src/logic/compare.ts#L148),
  invalidates mixed/unknown comparisons and suppresses score/test-rate deltas at
  [line 117](../web/src/logic/compare.ts#L117).

The exact remaining gap is `common_class(["unknown", "unknown"]) == "unknown"`
at `eval/verification/isolation.py:20-24`. That permits a homogeneous unknown set
to reach arithmetic and persistence at `runner.py:1199-1206,1327`,
`tracing.py:395-413` and `records.py:161-164,211-213,617-621`. Unknown-only cached
verdicts are also accepted at `runner.py:1151-1152`. This is aggregation of scores
whose topology was not observed, even though a later cross-run comparison refuses.
The existing option-B test explicitly accepts a scored unknown curve point at
[tests/quality/test_scorer_isolation_option_b.py:290](../tests/quality/test_scorer_isolation_option_b.py#L290).

[The source-only probe transcript](../reports/w-shared-source-probes.txt) reproduces
all of these cases with fabricated results: two unknown trials per task yield
`0.5`, unknown progress records carry `0.5`, unknown cache scores survive, and a
two-point unknown curve history is written. Unknown comparison and known-plus-
unknown aggregation do refuse. The transcript contains its complete exact Python
command; no actor or dataset scorer is launched.

There is a concrete producer without a topology observation: opt-in stepwise
ChessBench bypasses `_verify` at
[eval/run/runner.py:522](../eval/run/runner.py#L522), and
[eval/run/chess_stepwise.py:49](../eval/run/chess_stepwise.py#L49) returns a scored
verdict without `scorer_isolation_class` (lines 64-73). `run_one` consequently
records `unknown`. This source finding is not a live ChessBench measurement.

Unsupported policy-bearing production paths still refuse before backend dispatch:
[eval/run/runner.py:38](../eval/run/runner.py#L38) checks requested external policy
fields and emits `backend_observation_unavailable` at lines 78-84. The explicitly
bound unmeasured diagnostic exception is at lines 72-77. This review does not
bypass it. Closure would require refusing scored unknown aggregation at the
trial/cache/task/progress/curve boundaries, or collecting the missing trusted
observation before scoring; readable historical unknown records must not be
silently promoted. No implementation change is made here. Per-run observations
can accumulate only when a separately authorized run actually happens.

## CLOSED: pristine-verifier - opt-in runtime identity review completed

The explicit `pristine-offline` path does establish the requested fresh runtime
identity and isolation mechanism independently of the actor's executable state.
This closes its own source-review action, not dataset provisioning or live runtime
validation. Actor-produced answer data is intentionally accepted only through the
selected data projection; the runtime/toolchain is not recovered from actor state.

- **Independent declarations and identity.**
  [eval/verification/pristine.py:22](../eval/verification/pristine.py#L22)
  rejects declaration/closure paths inside the actor workdir and rejects symlink
  ancestors via `artifact_admission._root`. Lines 48-61 authenticate independently
  supplied manifest and contract pins, require explicit pristine class, bind the
  command and selected task, and verify local closure bytes.
  [eval/admission/scorer_bundle.py:26](../eval/admission/scorer_bundle.py#L26)
  requires a content-addressed `sha256:` image and pristine `network="none"`;
  [line 77](../eval/admission/scorer_bundle.py#L77) requires matching image,
  source and scorer across the two sealed contracts.
- **Fresh runtime and exact projections.**
  [pristine.py:31](../eval/verification/pristine.py#L31) creates a fresh UUID-based
  runtime name, initializes the observation to `unknown`, and uses an exclusive
  temporary platform projection (line 46).
  [Line 62](../eval/verification/pristine.py#L62) exports bounded selected data.
  [Line 67](../eval/verification/pristine.py#L67) creates a stopped runtime with
  `--pull=never`, pinned image, `--network none`, read-only root, dropped capabilities
  and no-new-privileges. Lines 76-82 add bounded tmpfs and exactly the two read-only
  binds `/control-input` and `/control-scorer`. No actor snapshot is used.
- **Observe, recheck, then start.**
  [pristine.py:87](../eval/verification/pristine.py#L87) obtains `docker inspect`
  from the created instance and calls `require_artifact_runtime` before the first
  `start` at line 96. That admission call rechecks both closure files and exported
  input bytes at
  [eval/admission/artifact_admission.py:378](../eval/admission/artifact_admission.py#L378).
  [Line 394](../eval/admission/artifact_admission.py#L394) checks image, sealed
  entrypoint/argv, environment, workdir, `created`/not-running state, network,
  security settings and bounded tmpfs. Lines 422-436 refuse extra, substituted,
  writable or incorrectly propagated projections and require the exact two binds.
  An unexpected image/network/mount raises before start; flags alone are not proof.
- **Wiring and result.**
  [eval/run/runner.py:525](../eval/run/runner.py#L525) passes the selected task ID
  to the verifier. [verifier.py:457](../eval/verification/verifier.py#L457)
  dispatches explicitly opted-in artifact commands with the `exit0` protocol to
  this backend; service/reward-file routes do not acquire a pristine label.
  [pristine.py:97](../eval/verification/pristine.py#L97) inspects the exited
  instance, rejects abnormal state, then records `pristine-offline` at line 104.
  Normal failing checks remain measured zero; errors remain unmeasured.
  Lines 112-122 attempt removal, preserve its receipt and null the score on cleanup
  failure. This records a removal-command result, not independent live absence proof.

Controls cover real local synthetic pass/fail scoring behind a Docker double,
runner-to-curve/saved-verdict propagation, incompatible image/network/extra-mount
refusal **before start**, command/task/pin binding and cleanup failure. The lower
admission controls reject changed projection/closure bytes, writable/substituted
mounts, actor interpreter/scorer/environment/cwd and already-started observations:
[option-B tests:129](../tests/quality/test_scorer_isolation_option_b.py#L129),
[artifact tests:219](../tests/quality/test_terminal_artifact_admission.py#L219),
[admission tests:277](../tests/quality/test_scorer_admission_codex173.py#L277).

Trusted pins, ownership of the external closure/contract paths, quiescence and
preservation of checked bytes through use remain caller obligations. This is not
a proof against a hostile Docker daemon, host or concurrent privileged writer.
Complete transitive image/ABI closure, official scorer origin, selected/private
input safety and dataset equivalence were not verified. Actual container/kernel
isolation, resource enforcement and cleanup were not exercised. Those per-run
observations accumulate only on separately authorized runs; the two code-solvable
shared blockers and dataset-specific gates remain open.

## Registry disposition and focused validation

The registry is active-only: remove only the reviewed `pristine-verifier` object
and its 23 references. Keep the other three shared objects byte-for-byte equivalent,
all dataset-own blockers/history/taxonomy and `production_authorized=false`.
`tests/quality/test_dataset_blockers.py` pins the remaining shared set for 2Wiki;
remove precisely `pristine-verifier` from that expected set and record this reason
in the same commit. The validator is unchanged. The separate
[23-dataset topology survey](../reports/w-shared-topology-survey.md) is report-only.

Commands and complete fresh output are saved in:

- [w-shared-focused-tests.txt](../reports/w-shared-focused-tests.txt):
  `python3 -m pytest -vv -s --basetemp=/tmp/hg-w-shared-pytest tests/quality/test_scorer_isolation_option_b.py tests/quality/test_terminal_artifact_admission.py tests/quality/test_scorer_bundle_codex170.py tests/quality/test_scorer_admission_codex173.py`
  — **148 passed**.
- [w-shared-web-tests.txt](../reports/w-shared-web-tests.txt), cwd `web`:
  `pnpm exec vitest run src/logic/compare.test.ts --reporter=verbose --no-cache`
  — **15 passed**. The offline dependency install lacked a cached package;
  an independent copy of the existing local dependency tree supplied Vitest 2.1.9.
  No package/lockfile change or dependency download.
- [w-shared-source-probes.txt](../reports/w-shared-source-probes.txt): exact
  rerunnable command and observed unknown-only aggregation gap, **exit 0**.
- [w-shared-registry-policy-tests.txt](../reports/w-shared-registry-policy-tests.txt):
  `python3 -m pytest -vv --basetemp=/tmp/hg-w-shared-registry-pytest tests/quality/test_dataset_blockers.py tests/quality/test_shared_admission_wiring_codex163.py tests/quality/test_runner_admission.py`
  — **351 passed**, including the updated registry pin and unsupported-policy
  refusal controls. Then `python3 tools/check_dataset_blockers.py`:
  **valid=true, 28 datasets, 3 shared blockers, 11 historical records,
  production_authorized=false**.

No whole-suite run, old receipt edit, benchmark result or production authorization
is implied. Total focused results: **499 Python tests and 15 web tests passed**.
