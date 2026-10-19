# `--resume`: continue an interrupted run instead of starting clean

## The problem this closes

Today `driver.py` has no resume path at all:

* reusing a `--run-id` means **start clean** (`driver.py`, the `reused id` note), and
* "staged IDs never resume" — a staged run refuses to adopt leftovers.

So a study that has already paid for nine hours of measurement loses **everything** if the
process dies, and an exam that was killed at 147 of 200 tasks keeps the 147 progress files
but can never turn them into a point. That is not a defensible product behaviour: the runs
this platform exists to make are long, and long runs break.

## What "resume" has to mean

Two different things, because the two sides fail differently:

1. **Train side** (`--rounds`): continue the improvement loop from the last *measured*
   round, reusing the curve, the saved harness state and the method's history.
2. **Eval side** (`--side eval`): finish a pool whose per-task progress files exist,
   scoring only the tasks that have no result yet, then write one point for the whole pool.

Neither may ever *re-measure* a state that is already on the curve, because a second
measurement of the same state is a different number and there is no honest way to pick one.

## Contract (train side)

`driver.py --resume --run-id R ...`

Preconditions, each a refusal with a sentence when violated:

| Check | Refusal reason |
| --- | --- |
| `runs/<R>/curve.jsonl` exists and holds ≥ 1 point | there is nothing to continue |
| the run's recorded `split_sha` equals this invocation's | the task set moved; the old points are not the same measurement |
| the run's recorded `platform_content_sha` equals the current tree's | the code moved; mixing two platforms in one curve is the confound the platform exists to remove |
| `state_after_round<last>/` exists | the state to continue from was not kept |
| `--rounds` > `last` | nothing to do; exit 0 with a note, not an error |

Behaviour when the checks pass:

* `start_steps = last`; the loop's step budget is **total measured rounds**, so re-running
  the same command twice is idempotent rather than doubling the curve.
* `base` and the round-0 traces are rebuilt **from the record** (`_base_from_point` +
  `round0_traces/`), exactly as `--h0-from` does for a donor run. Nothing is re-measured.
* The workspace is reused when it still holds the commit the last point names; otherwise
  `state_after_round<last>/` is staged into a fresh workspace and committed, and the new
  commit is recorded as `resumed.restaged_sha`. The old commits may be gone; the curve's
  recorded numbers are the record, not the git objects.
* `run_meta.json` gains `resumed: [{at, from_round, recorded_sha, restaged_sha,
  platform_content_sha}]`, and every point written after the resume carries
  `"resumed": true` so a stitched curve is never mistaken for a continuous one.
* The method sees `history=curve` (the whole curve), so a rule that reads its own history
  behaves as if it had never been interrupted.

Deliberately **not** preserved: an interrupted submission's unmeasured siblings. They were
never on the curve; the resume starts from the last state that *was* measured, and the
run_meta note says `dropped_pending: <n>` when a batch was cut short.

## Contract (eval side)

`driver.py --side eval --resume --run-id R ...`

* The run's `progress/round-0/*.json` are the per-task results. Tasks with a result are
  skipped; only the missing ones are evaluated.
* Refuse when `--from-run`/`--from-round`, the pool, the split or the state's
  `harness_sha` differ from what the interrupted run recorded — those would make one point
  out of two different measurements.
* The written point covers the **whole pool**, and carries `resumed_from_tasks: <n>` so a
  reader can see how much of it was scored by the resumed half.

## Rollout

1. Land this in a worktree (`/tmp/hg-resume`, branch `feat/resume`) — a platform edit during
   a live batch trips `PlatformTampered` for every run in flight, and the six trains now
   running must not be invalidated.
2. Tests: a red-first test per refusal row, plus one happy path per side. The happy path
   uses a two-round run that is killed after round 1 and resumed to round 2, asserting the
   curve has exactly two measured rounds and that no state was measured twice.
3. Record `platform_content_sha` at run start (a new field; old runs simply lack it, and
   resume refuses without it unless `--allow-unpinned-platform` is passed explicitly).
